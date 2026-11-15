"""cloro adapter (https://docs.cloro.dev).

Based on public docs, Nov 2026 — VERIFY in the smoke test:
- POST https://api.cloro.dev/v1/monitor/{engine} with {"prompt", "country", "include": {...}}
- result.text, result.sources[], result.searchQueries[] (when include.searchQueries), result.model
- Runs through the real consumer UI via a proxy in the given country.
- IMPORTANT: the ChatGPT endpoint enables web search for every request, so search is FORCED.
  The search-trigger-rate metric is meaningless for these runs (web_search_forced=True).
"""
from __future__ import annotations

import os

import httpx

from geo.core.schema import NormalizedResponse, RunRequest, Source
from geo.parser.normalize import domain_of

BASE = "https://api.cloro.dev/v1/monitor"
ENGINE_PATH = {
    "chatgpt": "chatgpt",
    "gemini": "gemini",
    "perplexity": "perplexity",
    "copilot": "copilot",
    "ai_mode": "aimode",        # VERIFY path name in docs
    "ai_overview": "google",    # VERIFY
}


class CloroAdapter:
    name = "cloro"
    engines = set(ENGINE_PATH)

    def __init__(self, api_key: str | None = None, timeout: float = 180.0):
        self.api_key = api_key or os.environ["CLORO_API_KEY"]
        self.client = httpx.Client(timeout=timeout)

    def supports(self, req: RunRequest) -> bool:
        return req.engine in self.engines and req.surface == "ui"

    def collect(self, req: RunRequest) -> NormalizedResponse:
        body = {
            "prompt": req.prompt,
            "country": req.country,
            "include": {"searchQueries": True, "markdown": True, "rawResponse": False},
        }
        if req.web_search == "off":
            body["disableWebSearch"] = True  # VERIFY parameter name/placement
        r = self.client.post(
            f"{BASE}/{ENGINE_PATH[req.engine]}",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=body,
        )
        r.raise_for_status()
        payload = r.json()
        res = payload.get("result", {}) or {}

        sources = None
        if "sources" in res:
            sources = [
                Source(
                    url=s.get("url", ""),
                    domain=domain_of(s.get("url", "")),
                    title=s.get("title"),
                    position=s.get("position"),
                    cited_inline=None if "footnote" not in s else (not s.get("footnote")),
                )
                for s in (res.get("sources") or [])
            ]

        forced = req.engine == "chatgpt" and req.web_search != "off"
        return NormalizedResponse(
            provider=self.name,
            engine=req.engine,
            surface="ui",
            country=req.country,
            language=req.language,
            answer_text=res.get("text"),
            sources=sources,
            search_queries=res.get("searchQueries") if "searchQueries" in res else None,
            web_search_triggered=True if forced else None,
            web_search_forced=forced,
            model=res.get("model"),
            raw=payload,
        )
