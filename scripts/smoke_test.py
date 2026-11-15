"""V0 step 1 smoke test.

  python scripts/smoke_test.py --dry-check
  python scripts/smoke_test.py --project tachles-mashkanta --provider cloro --reps 3 [--limit 5]

Plans runs for every prompt variant in projects/<project>/intents.yaml, collects, parses and prints:
which fields came back, search-query language, citation/mention rates with intervals.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from geo.core import db  # noqa: E402
from geo.core.schema import RunRequest  # noqa: E402
from geo.estimator.rates import runs_for_mde, wilson  # noqa: E402
from geo.orchestrator.run import collect_planned, parse_collected  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
HEBREW = re.compile(r"[֐-׿]")


def load_project(name: str):
    pdir = ROOT / "projects" / name
    cfg = yaml.safe_load((pdir / "project.yaml").read_text(encoding="utf-8"))
    intents = yaml.safe_load((pdir / "intents.yaml").read_text(encoding="utf-8"))
    return cfg, intents


def plan(conn, project, cfg, intents, provider, engine, reps, limit):
    n = 0
    for intent in intents["intents"]:
        for j, prompt in enumerate(intent["prompts"]):
            if limit and n >= limit * reps:
                return n
            for _ in range(reps):
                req = RunRequest(project=project, intent_id=intent["id"], prompt_id=f"{intent['id']}-{j}",
                                 prompt=prompt, engine=engine, country=cfg.get("country", "IL"),
                                 language=cfg.get("language", "he"))
                db.plan_run(conn, req, provider, intent_version=str(intents.get("version")))
                n += 1
    return n


def summarize(conn, project, cfg):
    rows = conn.execute("SELECT * FROM run WHERE project = ? AND status IN ('parsed','failed')", (project,)).fetchall()
    parsed = [r for r in rows if r["status"] == "parsed"]
    failed = len(rows) - len(parsed)
    print(f"\nruns: {len(parsed)} parsed, {failed} failed")
    if not parsed:
        return
    norms = [json.loads(r["normalized_json"]) for r in parsed]
    field_presence = {k: sum(n.get(k) is not None for n in norms) for k in
                      ("answer_text", "sources", "search_queries", "model", "web_search_triggered")}
    print("fields returned (non-null / total):", {k: f"{v}/{len(norms)}" for k, v in field_presence.items()})

    queries = [q for n in norms for q in (n.get("search_queries") or [])]
    if queries:
        he = sum(bool(HEBREW.search(q)) for q in queries)
        print(f"search queries: {len(queries)} total, {he} in Hebrew, {len(queries) - he} not Hebrew")
        print("  examples:", queries[:5])

    ids = [r["id"] for r in parsed]
    q = ",".join("?" * len(ids))
    cited = conn.execute(f"SELECT COUNT(DISTINCT run_id) FROM citation WHERE is_target=1 AND run_id IN ({q})", ids).fetchone()[0]
    mentioned = conn.execute(f"SELECT COUNT(DISTINCT run_id) FROM mention WHERE is_target=1 AND run_id IN ({q})", ids).fetchone()[0]
    for label, k in (("citation rate", cited), ("mention rate", mentioned)):
        p, lo, hi = wilson(k, len(parsed))
        print(f"{label}: {p:.1%}  (95% CI {lo:.1%}–{hi:.1%})")

    top = conn.execute(f"SELECT domain, COUNT(*) c FROM citation WHERE run_id IN ({q}) GROUP BY domain ORDER BY c DESC LIMIT 10", ids).fetchall()
    print("top cited domains:", [(t["domain"], t["c"]) for t in top])

    base = max(0.05, cited / len(parsed))
    print(f"runs per arm to detect +10pt from {base:.0%} (lower bound): {runs_for_mde(base)}")


def main():
    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="tachles-mashkanta")
    ap.add_argument("--provider", default="cloro")
    ap.add_argument("--engine", default="chatgpt")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0, help="max prompt variants (0 = all)")
    ap.add_argument("--dry-check", action="store_true", help="one Hebrew prompt, print raw response keys")
    ap.add_argument("--summary-only", action="store_true")
    args = ap.parse_args()

    cfg, intents = load_project(args.project)
    conn = db.connect(str(ROOT / "data" / "geo.db"))

    if args.dry_check:
        from geo.collector.adapters import get_adapter
        prompt = intents["intents"][0]["prompts"][0]
        resp = get_adapter(args.provider).collect(RunRequest(args.project, "dry", "dry", prompt, args.engine,
                                                             country=cfg.get("country", "IL")))
        print("model:", resp.model, "| sources:", None if resp.sources is None else len(resp.sources),
              "| search_queries:", resp.search_queries)
        print("raw top-level keys:", list((resp.raw or {}).keys()),
              "| result keys:", list(((resp.raw or {}).get("result") or {}).keys()))
        print((resp.answer_text or "")[:500])
        return

    if not args.summary_only:
        n = plan(conn, args.project, cfg, intents, args.provider, args.engine, args.reps, args.limit)
        print(f"planned {n} runs")
        print(f"collected {collect_planned(conn, args.project)}")
        entities = {cfg["name"]: cfg["aliases"], **{c["name"]: c["aliases"] for c in cfg.get("competitors", [])}}
        print(f"parsed {parse_collected(conn, cfg['domain'], entities, cfg['name'], args.project)}")
    summarize(conn, args.project, cfg)


if __name__ == "__main__":
    main()
