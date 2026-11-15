"""Bright Data adapter: AI scrapers (Web Scraper API), docs read 2026-11-15 at docs.brightdata.com.

What it is: Bright Data drives each engine's consumer web UI and returns one record per prompt.
Logged-in vs logged-out session: not documented (unknown). Surface = consumer UI.

Datasets (official per-scraper JSON):
  chatgpt  gd_m7aof0k82r803d5bjm  input url=https://chatgpt.com/, prompt, country, index, web_search (default true:
           a permission, not a guarantee -> the record's web_search_triggered says whether a search ran).
           Output: answer_text(_markdown), citations, links_attached, search_sources(+_more), web_search_query,
           web_search_triggered, model, prompt_sent_at, response_raw.
  gemini   gd_mbz66arm2mf9cu856y  input url, prompt, country, index. Output: answer, citations, links_attached,
           model, response_raw. No search queries, no web_search_triggered.
  ai_mode  gd_mcswdt6z2elth3zqr2  input url, prompt, country, hl, index. Output: answer, citations,
           citations_mapping, links_attached. No model, no queries.
Perplexity and Grok: documented as currently unavailable. Copilot: not documented.

Calls: async POST /datasets/v3/trigger with ALL prompts of a batch in one input[] (field `index` maps a record
back to its run) -> snapshot_id; GET /datasets/v3/snapshot/{id} (202 while building). One download serves every
run of the snapshot, so the orchestrator's per-run poll() reads a per-process cache.

Every HTTP call to api.brightdata.com is appended to a ledger (BRIGHTDATA_LEDGER, default data/brightdata_calls.jsonl)
and refused once BRIGHTDATA_MAX_CALLS is reached: a hard spending guard that survives restarts.

Field semantics are NOT defined by the docs; our reading (verify on real records, rebuild with renormalize):
  retrieved = search_sources + search_sources_more + citations + links_attached (union, in that order)
  cited     = in citations or links_attached (shown/linked in the answer)
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from geo.core.schema import NormalizedResponse, ProviderError, Retrieved, RunRequest, Source
from geo.parser.normalize import domain_of

API = "https://api.brightdata.com"
DATASET = {"chatgpt": "gd_m7aof0k82r803d5bjm", "gemini": "gd_mbz66arm2mf9cu856y", "ai_mode": "gd_mcswdt6z2elth3zqr2"}
START_URL = {"chatgpt": "https://chatgpt.com/", "gemini": "https://gemini.google.com/", "ai_mode": "https://www.google.com/"}
SEARCH_BASED = {"ai_mode"}
PERMANENT_STATUS = {400, 401, 403, 404}

_SNAPSHOTS: dict[str, list | float] = {}  # snapshot_id -> records, or monotonic time of the last "not ready"


class BudgetExceeded(ProviderError):
    def __init__(self, used: int, cap: int):
        super().__init__(f"Bright Data call budget reached ({used}/{cap}); refusing the call", permanent=True)


def ledger_path() -> Path:
    return Path(os.getenv("BRIGHTDATA_LEDGER", "data/brightdata_calls.jsonl"))


def calls_used() -> int:
    p = ledger_path()
    return sum(1 for line in p.read_text(encoding="utf-8").splitlines() if line.strip()) if p.exists() else 0


class BrightDataAdapter:
    name = "brightdata"
    engines = set(DATASET)

    def __init__(self, api_key: str | None = None, timeout: float = 120.0):
        self.api_key = api_key or os.environ.get("BRIGHTDATA_API_KEY")
        self.client = httpx.Client(timeout=timeout)

    def supports(self, req: RunRequest) -> bool:
        return req.engine in self.engines and req.surface == "ui"

    # ---- HTTP with ledger + hard cap ----
    def _http(self, method: str, path: str, **kw) -> httpx.Response:
        if not self.api_key:
            raise ProviderError("BRIGHTDATA_API_KEY is not set", permanent=True)
        cap = int(os.getenv("BRIGHTDATA_MAX_CALLS", "12"))
        used = calls_used()
        if used >= cap:
            raise BudgetExceeded(used, cap)
        t0 = time.monotonic()
        entry = {"n": used + 1, "at": datetime.now(timezone.utc).isoformat(), "method": method, "path": path,
                 "params": kw.get("params")}
        try:
            r = self.client.request(method, API + path, headers={"Authorization": f"Bearer {self.api_key}"}, **kw)
            entry.update(status=r.status_code, ms=int((time.monotonic() - t0) * 1000))
            return r
        except httpx.HTTPError as e:
            entry.update(status=None, error=repr(e)[:300])
            raise
        finally:
            p = ledger_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _error(self, r: httpx.Response, what: str) -> ProviderError:
        return ProviderError(f"brightdata {what} {r.status_code}: {r.text[:400]}",
                             permanent=r.status_code in PERMANENT_STATUS, status=r.status_code)

    # ---- account ----
    def account_status(self) -> dict:
        """Balance (USD) and free-tier credits. Each is one call; 401/403 on free_tier = key lacks billing.read."""
        out = {}
        r = self._http("GET", "/customer/balance")
        out["balance"] = r.json() if r.status_code == 200 else {"status": r.status_code, "body": r.text[:200]}
        r = self._http("GET", "/trials/free_tier")
        out["free_tier"] = r.json() if r.status_code == 200 else {"status": r.status_code, "body": r.text[:200]}
        return out

    # ---- collection ----
    def input_for(self, req: RunRequest, index: int) -> dict:
        """Clean prompt only (rule 5): no additional_prompt, no require_sources."""
        item = {"url": START_URL[req.engine], "prompt": req.prompt, "country": req.country.lower(), "index": index}
        if req.engine == "chatgpt":
            item["web_search"] = req.web_search != "off"
        if req.engine == "ai_mode":
            item["hl"] = req.language
        return item

    def trigger(self, reqs: list[RunRequest]) -> str:
        """One async job for many prompts of ONE engine. Returns snapshot_id; record `index` i = reqs[i-1]."""
        engines = {r.engine for r in reqs}
        if len(engines) != 1:
            raise ValueError("one engine per trigger")
        engine = engines.pop()
        body = {"input": [self.input_for(r, i + 1) for i, r in enumerate(reqs)]}
        r = self._http("POST", "/datasets/v3/trigger",
                       params={"dataset_id": DATASET[engine], "include_errors": "true", "format": "json"}, json=body)
        if r.status_code != 200:
            raise self._error(r, "trigger")
        return r.json()["snapshot_id"]

    def download(self, snapshot_id: str, min_gap: float = 60.0) -> list | None:
        """Records of a snapshot, or None while it is still building. Cached per process."""
        cached = _SNAPSHOTS.get(snapshot_id)
        if isinstance(cached, list):
            return cached
        if isinstance(cached, float) and time.monotonic() - cached < min_gap:
            return None
        r = self._http("GET", f"/datasets/v3/snapshot/{snapshot_id}", params={"format": "json"})
        if r.status_code == 202:
            _SNAPSHOTS[snapshot_id] = time.monotonic()
            return None
        if r.status_code != 200:
            raise self._error(r, "snapshot")
        raw_dir = os.getenv("BRIGHTDATA_RAW_DIR")
        if raw_dir:  # untouched copy of the whole snapshot, before any splitting or mapping
            Path(raw_dir).mkdir(parents=True, exist_ok=True)
            (Path(raw_dir) / f"{snapshot_id}.json").write_bytes(r.content)
        data = r.json()
        records = data if isinstance(data, list) else [data]
        _SNAPSHOTS[snapshot_id] = records
        return records

    def poll(self, task_id: str, req: RunRequest) -> NormalizedResponse | None:
        """task_id = '<snapshot_id>#<index>'. None while building; ProviderError if the record is an error."""
        snapshot_id, _, idx = task_id.partition("#")
        records = self.download(snapshot_id)
        if records is None:
            return None
        rec = next((x for x in records if str(x.get("index")) == idx), None)
        if rec is None:  # index not echoed back (the documented example shows "index": null): match by prompt
            same = [x for x in records if (x.get("prompt") or (x.get("input") or {}).get("prompt")) == req.prompt]
            rec = same[0] if len(same) == 1 else None
        if rec is None:
            raise ProviderError(f"brightdata snapshot {snapshot_id}: no record with index {idx}", permanent=True)
        if rec.get("error") or rec.get("error_code"):
            raise ProviderError(f"brightdata record error: {rec.get('error_code')} {str(rec.get('error'))[:300]}",
                                code=rec.get("error_code"))
        return self.normalize(req, rec)  # credits: unknown (price per AI record not published)

    def collect(self, req: RunRequest, **params) -> NormalizedResponse:
        """Sync /scrape for a single prompt (1 min server limit, then 202 + snapshot_id)."""
        r = self._http("POST", "/datasets/v3/scrape",
                       params={"dataset_id": DATASET[req.engine], "include_errors": "true", "format": "json"},
                       json={"input": [self.input_for(req, 1)]})
        if r.status_code == 202:
            raise ProviderError(f"brightdata scrape still running: {r.text[:200]}", code="SNAPSHOT_PENDING")
        if r.status_code != 200:
            raise self._error(r, "scrape")
        data = r.json()
        rec = data[0] if isinstance(data, list) else data
        return self.normalize(req, rec)

    # ---- normalize (pure) ----
    def normalize(self, req: RunRequest, rec: dict) -> NormalizedResponse:
        e = req.engine

        def items(key):
            v = rec.get(key)
            return v if isinstance(v, list) else None

        def title(x):
            return x.get("title") or x.get("text") or x.get("label")

        citations, links = items("citations"), items("links_attached")
        shown_raw = [x for x in (citations or []) + (links or []) if isinstance(x, dict) and x.get("url")]
        sources = None
        if citations is not None or links is not None:
            sources, seen = [], set()
            for x in shown_raw:
                if x["url"] in seen:
                    continue
                seen.add(x["url"])
                sources.append(Source(url=x["url"], domain=domain_of(x["url"]), title=title(x),
                                      position=len(sources) + 1, cited_inline=True,
                                      snippet=x.get("description") or x.get("snippet")))

        retrieved = None
        if e == "chatgpt" and (items("search_sources") is not None or items("search_sources_more") is not None):
            shown = {x["url"] for x in shown_raw}
            retrieved, seen = [], set()
            for x in (items("search_sources") or []) + (items("search_sources_more") or []) + shown_raw:
                if not isinstance(x, dict) or not x.get("url") or x["url"] in seen:
                    continue
                seen.add(x["url"])
                retrieved.append(Retrieved(url=x["url"], domain=domain_of(x["url"]), position=len(retrieved) + 1,
                                           cited=x["url"] in shown, title=title(x),
                                           snippet=x.get("snippet") or x.get("description")))

        queries = rec.get("web_search_query")
        if isinstance(queries, str):
            queries = [queries]
        if not isinstance(queries, list) or e != "chatgpt":
            queries = None  # absent, or not exposed by this engine
        elif queries == [] and rec.get("web_search_triggered") is True:
            queries = None  # searched but no queries reported = unknown, not "none"

        if e == "chatgpt":
            triggered = rec.get("web_search_triggered") if isinstance(rec.get("web_search_triggered"), bool) else None
        elif e in SEARCH_BASED:
            triggered = True
        else:  # gemini: not exposed; shown sources imply a search
            triggered = True if sources else None

        answer = rec.get("answer_text") if rec.get("answer_text") is not None else rec.get("answer_text_markdown")
        return NormalizedResponse(
            provider=self.name, engine=e, surface="ui", country=req.country, language=req.language,
            answer_text=answer, sources=sources, search_queries=queries, web_search_triggered=triggered,
            web_search_forced=e in SEARCH_BASED,  # chatgpt web_search=true is a permission, not forced
            model=rec.get("model") or None, retrieved=retrieved,
            collected_at=rec.get("prompt_sent_at") or datetime.now(timezone.utc).isoformat(),
            raw=rec)
