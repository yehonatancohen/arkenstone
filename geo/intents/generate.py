"""Generate an intent bank from a recipe with Gemini, and check a bank against its recipe.

The generator sees: the recipe, the business profile, and optional seed search queries
(e.g. from Google Search Console). It never sees a hand-written bank, so the two stay comparable.
Output is a CANDIDATE file for human approval, never written over intents.yaml.
"""
from __future__ import annotations

import difflib
import json
import os
import re

import httpx

API = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-3.5-flash-lite"  # override with GEMINI_MODEL; check names with list_models()
HEBREW = re.compile(r"[֐-׿]")
# Words that suggest a probe instead of a clean customer question (rule 5).
# A recommend-category question must ask for names (question and personal variants).
NAME_ASK = re.compile(r"מי |איזה|איזו|איפה|תמליץ|ממליץ|מומלצ|הכי טוב|הכי משתלם|למי ")
PROBE_WORDS = ("מקורות", "אתרים שחיפשת", "חיפשת", "תפרט את", "ציין את המקור", "source", "cite")

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "intents": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "id": {"type": "STRING", "description": "short english kebab-case id"},
                    "category": {"type": "STRING"},
                    "weight": {"type": "INTEGER"},
                    "prompts": {"type": "ARRAY", "items": {"type": "STRING"}},
                },
                "required": ["id", "category", "weight", "prompts"],
            },
        }
    },
    "required": ["intents"],
}


def _key() -> str:
    return os.environ["GEMINI_API_KEY"]


def list_models() -> list[str]:
    r = httpx.get(f"{API}/models", headers={"x-goog-api-key": _key()}, timeout=30)
    r.raise_for_status()
    return [m["name"].removeprefix("models/") for m in r.json().get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])]


def build_prompt(recipe_yaml: str, brief_md: str, seeds: list[str]) -> str:
    seed_block = ""
    if seeds:
        seed_block = (
            "\nReal Google search queries from this market (use them as topic hints and to shape the "
            "short 'search' variant; do not copy them as the whole set):\n" + "\n".join(f"- {s}" for s in seeds[:200])
        )
    return f"""You build an intent bank: questions real customers ask AI assistants (ChatGPT, Gemini) in this market.
Follow the recipe exactly: the category counts, the number of variants, the variant styles in the given order,
and every rule. Output JSON only.

MARKET BRIEF (who the customers are; you are writing THEIR questions):
{brief_md}

RECIPE (YAML):
{recipe_yaml}
{seed_block}
For each intent: id (english kebab-case), category (a recipe category id), weight (1-3, how close to money),
prompts (exactly variants_per_intent Hebrew strings, in the order of variant_styles)."""


REFINE = """You are a native Hebrew editor and a strict reviewer. Below is an intent bank and the recipe it must follow.
Fix it and return the full corrected bank as JSON in the same structure:
1. Fix every spelling, grammar and wording error. Each prompt must read like a real Israeli wrote it.
   Remove invented or wrong terms (e.g. fees or rules that do not exist in Israel).
2. Check every prompt against every rule in the recipe and rewrite any that breaks one.
3. Each intent's prompts must follow the variant_styles in order and keep the same need.
4. If two intents are the same need, replace one with a different need in the same category.
Change only what is needed.

RECIPE:
{recipe}

BANK:
{bank}"""


def _call(model: str, prompt: str, temperature: float) -> tuple[dict, dict]:
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "responseMimeType": "application/json",
                             "responseSchema": SCHEMA},
    }
    r = httpx.post(f"{API}/models/{model}:generateContent", headers={"x-goog-api-key": _key()},
                   json=body, timeout=180)
    r.raise_for_status()
    data = r.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    u = data.get("usageMetadata", {})
    usage = {"in": u.get("promptTokenCount", 0),
             "out": u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0),
             "model": data.get("modelVersion", model)}
    return json.loads(text), usage


REPAIR = """Fix ONLY these problems in the intent bank below and return the full bank as JSON, otherwise unchanged.
Problems:
{problems}

RECIPE:
{recipe}

BANK:
{bank}"""


def generate(recipe_yaml: str, brief_md: str, seeds: list[str] | None = None,
             model: str | None = None, temperature: float = 0.7, refine: bool = True,
             recipe: dict | None = None, max_repairs: int = 2) -> dict:
    model = model or os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL
    bank, u1 = _call(model, build_prompt(recipe_yaml, brief_md, seeds or []), temperature)
    usage = [u1]
    if refine:
        bank, u2 = _call(model, REFINE.format(recipe=recipe_yaml, bank=json.dumps(bank, ensure_ascii=False)), 0.2)
        usage.append(u2)
    repairs = 0
    while recipe is not None and repairs < max_repairs:
        problems = check(bank, recipe)
        if not problems:
            break
        bank, u3 = _call(model, REPAIR.format(problems="\n".join(problems), recipe=recipe_yaml,
                                              bank=json.dumps(bank, ensure_ascii=False)), 0.2)
        usage.append(u3)
        repairs += 1
    bank["generator"] = {"model": u1["model"], "temperature": temperature, "refine": refine, "repairs": repairs,
                         "seed_queries": len(seeds or []),
                         "tokens_in": sum(x["in"] for x in usage), "tokens_out": sum(x["out"] for x in usage)}
    return bank


def check(bank: dict, recipe: dict) -> list[str]:
    """Rule checks against the recipe. Returns a list of problems (empty = clean)."""
    problems: list[str] = []
    intents = bank.get("intents", [])
    want = {c["id"]: c["count"] for c in recipe["categories"]}
    got: dict[str, int] = {}
    for it in intents:
        got[it.get("category")] = got.get(it.get("category"), 0) + 1
    if len(intents) != recipe["total_intents"]:
        problems.append(f"total intents {len(intents)} != {recipe['total_intents']}")
    for cat in set(want) | set(got):
        if want.get(cat, 0) != got.get(cat, 0):
            problems.append(f"category {cat}: {got.get(cat, 0)} != {want.get(cat, 0)}")
    ids = [it.get("id") for it in intents]
    for dup in {i for i in ids if ids.count(i) > 1}:
        problems.append(f"duplicate id {dup}")
    all_prompts: list[tuple[str, str]] = []
    for it in intents:
        ps = it.get("prompts", [])
        if len(ps) != recipe["variants_per_intent"]:
            problems.append(f"{it.get('id')}: {len(ps)} variants")
        for p in ps:
            if not HEBREW.search(p):
                problems.append(f"{it.get('id')}: not Hebrew: {p}")
            if any(w in p for w in PROBE_WORDS):
                problems.append(f"{it.get('id')}: looks like a probe, not a clean question: {p}")
            all_prompts.append((it.get("id"), p))
        if it.get("category") == "recommend":
            for p in ps[1:]:
                if not NAME_ASK.search(p):
                    problems.append(f"{it.get('id')}: recommend question does not ask for names: {p}")
        if ps and (len(ps[0].split()) > 6 or ps[0].rstrip().endswith("?")):
            problems.append(f"{it.get('id')}: search variant is not a short phrase: {ps[0]}")
    for i, (ia, a) in enumerate(all_prompts):
        for ib, b in all_prompts[i + 1:]:
            if difflib.SequenceMatcher(None, a, b).ratio() > 0.85:
                problems.append(f"near-duplicate: [{ia}] {a}  ~  [{ib}] {b}")
    return problems


def side_by_side(a: dict, b: dict, recipe: dict) -> str:
    """Per category, list intents of bank a and bank b (first non-search variant) for a human to read."""
    lines = []
    for c in recipe["categories"]:
        lines.append(f"\n=== {c['id']} ({c['count']}) ===")
        for label, bank in (("A", a), ("B", b)):
            for it in bank.get("intents", []):
                if it.get("category") == c["id"]:
                    p = (it.get("prompts") or [""])[1 if len(it.get("prompts", [])) > 1 else 0]
                    lines.append(f"  {label} {it['id']:<30} {p}")
    return "\n".join(lines)
