"""ScrapeLLM adapter (docs read 2026-11-15, docs.scrapellm.com). Consumer UI scrapes, async jobs only.

Engines used: perplexity, grok (also offers chatgpt, gemini, copilot, ai_overview; not used: those series
run on other providers, rule 7). Claude: not offered.

Calls: POST /scrapers/{engine}/jobs?prompt&country&bypass_cache=true -> 202 {job_id};
GET /jobs/{job_id} (no auth) -> status pending|running|done|failed, `result` = the scrape body.
The sync GET /scrapers/{engine} hits a 180 s gateway limit on Grok (504 on 10/10, 2026-11-15), so never used.
bypass_cache is ALWAYS true: a cached answer would hide run-to-run noise.
Price: 3 credits per successful request (Perplexity, Grok); failed jobs are refunded. No idempotency key.

Fields (verified on real records 2026-11-15):
  perplexity: result(_markdown), sources[{title,url,snippet}] = the sources shown with the answer (cited);
              related_questions. No queries, no model, no pages-read list -> retrieved None (unknown).
  grok:       result(_markdown) with inline [text](url) links, web_sources[{title,url,preview}] = pages its
              search returned (read), x_results (X posts), llm_model ("auto" = mode name, not a model -> None).
              cited = web_sources whose URL is linked in the answer.
"""
from __future__ import annotations

import os
import re

import httpx

from geo.collector.adapters.cloro_raw import clean_url
from geo.core.schema import NormalizedResponse, ProviderError, Retrieved, RunRequest, Source
from geo.parser.normalize import domain_of

API = "https://api.scrapellm.com"
ENGINES = {"perplexity", "grok"}
PERMANENT_STATUS = {400, 401, 403, 404, 422}
LINK = re.compile(r"\]\((https?://[^)\s]+)\)")


class ScrapeLLMAdapter:
    name = "scrapellm"
    engines = ENGINES

    def __init__(self, api_key: str | None = None, timeout: float = 60.0):
        self.api_key = api_key or os.environ.get("SCRAPELLM_API_KEY")
        self.client = httpx.Client(timeout=timeout)

    def supports(self, req: RunRequest) -> bool:
        return req.engine in self.engines and req.surface == "ui"

    def submit(self, req: RunRequest, idempotency_key: str, **params) -> str:
        if not self.api_key:
            raise ProviderError("SCRAPELLM_API_KEY is not set", permanent=True)
        q = {"prompt": req.prompt, "country": req.country, "bypass_cache": "true"}
        if req.engine == "perplexity" and req.web_search == "off":
            q["web_search"] = "false"
        r = self.client.post(f"{API}/scrapers/{req.engine}/jobs", params=q, headers={"X-API-Key": self.api_key})
        if r.status_code != 202:
            raise ProviderError(f"scrapellm {r.status_code}: {r.text[:300]}",
                                permanent=r.status_code in PERMANENT_STATUS, status=r.status_code)
        return r.json()["job_id"]

    def poll(self, task_id: str, req: RunRequest) -> NormalizedResponse | None:
        r = self.client.get(f"{API}/jobs/{task_id}")
        if r.status_code == 404:
            raise ProviderError(f"scrapellm job {task_id} not found", status=404, code="TASK_GONE")
        if r.status_code != 200:
            raise ProviderError(f"scrapellm poll {r.status_code}: {r.text[:200]}", status=r.status_code)
        job = r.json()
        if job.get("status") in ("pending", "running", "queued"):
            return None
        if job.get("status") != "done":
            raise ProviderError(f"scrapellm job {job.get('status')}: {str(job.get('error'))[:300]}",
                                code=job.get("status"))
        return self.normalize(req, job)

    def collect(self, req: RunRequest, **params) -> NormalizedResponse:
        raise ProviderError("scrapellm adapter is async only (submit/poll)", permanent=True)

    def normalize(self, req: RunRequest, job: dict) -> NormalizedResponse:
        """job = the whole GET /jobs/{id} body, stored as raw. Pure."""
        res = job.get("result") or {}
        text = res.get("result_markdown") or res.get("result")
        linked = {clean_url(u) for u in LINK.findall(text or "")}
        sources = retrieved = None
        triggered = None
        model = None
        if req.engine == "perplexity":
            if isinstance(res.get("sources"), list):
                sources = [Source(url=s.get("url") or "", domain=domain_of(s.get("url") or ""), title=s.get("title"),
                                  position=i + 1, cited_inline=None, snippet=s.get("snippet"))
                           for i, s in enumerate(res["sources"])]
                triggered = True if sources else None
        elif req.engine == "grok":
            if isinstance(res.get("web_sources"), list):
                retrieved = [Retrieved(url=s.get("url") or "", domain=domain_of(s.get("url") or ""), position=i + 1,
                                       cited=clean_url(s.get("url") or "") in linked, title=s.get("title"),
                                       snippet=s.get("preview"))
                             for i, s in enumerate(res["web_sources"])]
                triggered = True if retrieved else None
            sources = [Source(url=u, domain=domain_of(u), position=i + 1, cited_inline=True)
                       for i, u in enumerate(dict.fromkeys(LINK.findall(text or "")))]
            m = res.get("llm_model")
            model = m if m and m.lower() not in ("auto", "fast", "expert") else None
        return NormalizedResponse(
            provider=self.name, engine=req.engine, surface="ui", country=req.country, language=req.language,
            answer_text=text, sources=sources, search_queries=None, web_search_triggered=triggered,
            web_search_forced=True if req.engine == "perplexity" else None,
            model=model, retrieved=retrieved, entities=None, user_location=None,
            latency_ms=int(res["elapsed_ms"]) if res.get("elapsed_ms") else None,
            credits=res.get("credits_used"), raw=job)
