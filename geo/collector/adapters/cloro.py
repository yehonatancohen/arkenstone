"""cloro adapter (https://cloro.dev/docs, read 2026-11-15).

What cloro is: real logged-out browser sessions on each engine's consumer UI, through a proxy in `country`.
No model choice on any engine. Sync endpoints cost +2 credits over async.

Engines (our name -> cloro path, sync credits):
  chatgpt     /v1/monitor/chatgpt      7 (+2 with searchQueries). Web search FORCED unless disabled.
  gemini      /v1/monitor/gemini       6. Gemini decides whether to search. No searchQueries, no model.
  ai_mode     /v1/monitor/aimode       6. Google AI Mode, search-based. No searchQueries.
  copilot     /v1/monitor/bing/aimode  6. bing.com/copilotsearch (Copilot itself is discontinued on cloro).
  ai_overview /v1/monitor/google       7 with include.aioverview. Key absent = Google showed no AI Overview.
Perplexity, Copilot app, Grok, Claude: not available (login walls).

Field mapping notes: AI-answer sources use `label` (title) and `description` (snippet). ChatGPT sources
also have `footnote` (False = linked in the text). ChatGPT may return empty searchQueries/model in its
mobile-web format; `legacy: true` asks for the desktop format (best effort). Tested 2026-11-15:
legacy returns model + searchQueries (default) and ~2x the sources. Changing it is a series break (rule 7).
"""
from __future__ import annotations

import os
import time

import httpx

from geo.collector.adapters import cloro_raw
from geo.core.schema import NormalizedResponse, ProviderError, Retrieved, RunRequest, Source
from geo.parser.normalize import domain_of

BASE = "https://api.cloro.dev/v1/monitor"
ENGINE_PATH = {
    "chatgpt": "chatgpt",
    "gemini": "gemini",
    "ai_mode": "aimode",
    "copilot": "bing/aimode",
    "ai_overview": "google",
}
# Engines whose answer is always built from a web search (search-trigger rate is meaningless there).
SEARCH_BASED = {"ai_mode", "copilot", "ai_overview"}
PERMANENT_STATUS = {400, 401, 403, 404, 409, 410}


class CloroAdapter:
    name = "cloro"
    engines = set(ENGINE_PATH)

    def __init__(self, api_key: str | None = None, timeout: float = 320.0):
        self.api_key = api_key or os.environ.get("CLORO_API_KEY")  # only needed to call; normalize() works without
        self.client = httpx.Client(timeout=timeout)

    def supports(self, req: RunRequest) -> bool:
        return req.engine in self.engines and req.surface == "ui"

    def build_body(self, req: RunRequest, search_queries: bool = True, legacy: bool = True,
                   markdown: bool = True, raw_response: bool = True, html: bool = False) -> dict:
        e = req.engine
        if e == "ai_overview":
            return {"query": req.prompt, "country": req.country, "hl": req.language,
                    "include": {"aioverview": {"markdown": True} if markdown else True}}
        body: dict = {"prompt": req.prompt, "country": req.country, "include": {"markdown": markdown}}
        if html:
            body["include"]["html"] = True
        if raw_response and e in ("chatgpt", "gemini"):
            body["include"]["rawResponse"] = True  # chatgpt: inside the same +2 as searchQueries
        if e == "chatgpt":
            body["include"]["searchQueries"] = search_queries
            if legacy:
                body["legacy"] = True
            if req.web_search == "off":
                body["disableWebSearch"] = True
        if e == "ai_mode":
            body["hl"] = req.language
        return body

    def _post(self, path: str, body: dict) -> httpx.Response:
        if not self.api_key:
            raise ProviderError("CLORO_API_KEY is not set", permanent=True)
        for attempt in range(4):
            r = self.client.post(f"{BASE}/{path}", headers={"Authorization": f"Bearer {self.api_key}"}, json=body)
            if r.status_code == 429 or r.status_code == 503:  # rate/concurrency: wait for a free slot
                time.sleep(float(r.headers.get("Retry-After", 2 ** (attempt + 1))))
                continue
            return r
        return r

    def collect(self, req: RunRequest, **params) -> NormalizedResponse:
        r = self._post(ENGINE_PATH[req.engine], self.build_body(req, **params))
        if r.status_code != 200:
            try:
                err = r.json().get("error", {})
            except ValueError:
                err = {}
            raise ProviderError(f"cloro {r.status_code} {err.get('code')}: {err.get('message') or r.text[:300]}",
                                permanent=r.status_code in PERMANENT_STATUS, status=r.status_code,
                                code=err.get("code"))
        def to_int(h):
            v = r.headers.get(h)
            return int(v) if v and v.isdigit() else None

        resp = self.normalize(req, r.json())
        resp.latency_ms, resp.credits = to_int("X-Latency-Ms"), to_int("X-Credits-Charged")
        return resp

    def normalize(self, req: RunRequest, payload: dict) -> NormalizedResponse:
        """Payload -> NormalizedResponse. Pure: re-running it on stored raw_json rebuilds the same result."""
        res = payload.get("result") or {}
        if req.engine == "ai_overview":
            res = res.get("aioverview")  # None: Google showed no AI Overview for this query (a real outcome)

        e = req.engine
        if res is None:
            answer, sources, queries = "", [], None  # known: no AI answer shown; search queries unknown
        else:
            answer = res.get("text") if res.get("text") is not None else res.get("markdown")
            sources = None
            if "sources" in res:
                sources = [
                    Source(url=s.get("url", ""), domain=domain_of(s.get("url", "")),
                           title=s.get("label") or s.get("title"), position=s.get("position"),
                           cited_inline=None if "footnote" not in s else (not s.get("footnote")),
                           snippet=s.get("description") or None, pub_date=s.get("datePublished"))
                    for s in (res.get("sources") or [])
                ]
            queries = res.get("searchQueries") if "searchQueries" in res else None
            if e == "chatgpt" and queries == [] and req.web_search != "off":
                queries = None  # search was forced, so [] means "not reported" (known cloro gap), not "none"

        stream = (res or {}).get("rawResponse")
        extra = cloro_raw.chatgpt(stream) if e == "chatgpt" else cloro_raw.gemini(stream) if e == "gemini" else {}
        retrieved = None
        if extra.get("retrieved") is not None or sources:
            # Union of what the stream shows and the shown sources: a shown source was retrieved by definition.
            # "cited" = shown to the user as a citation. ChatGPT lists every page it used in `sources`
            # (footnote=True: used, not linked), so there only the inline-linked ones count as cited.
            has_inline = any(s.cited_inline is not None for s in sources or [])
            shown = {cloro_raw.clean_url(s.url) for s in sources or [] if not has_inline or s.cited_inline}
            items, seen = [], set()
            for it in (extra.get("retrieved") or []) + [
                    {"url": s.url, "title": s.title, "snippet": s.snippet, "pub_date": s.pub_date,
                     "source_name": None} for s in sources or []]:
                key = cloro_raw.clean_url(it["url"])
                if key in seen:
                    continue
                seen.add(key)
                items.append(Retrieved(url=it["url"], domain=domain_of(it["url"]), position=len(items) + 1,
                                       cited=key in shown, title=it.get("title"), snippet=it.get("snippet"),
                                       pub_date=it.get("pub_date"), source_name=it.get("source_name")))
            # Without a stream we only know the shown sources: that is not the full retrieved set.
            retrieved = items if extra.get("retrieved") is not None else None

        forced = (e == "chatgpt" and req.web_search != "off") or e in SEARCH_BASED
        if forced or sources or extra.get("search_triggered"):
            triggered = True   # sources or the UI's own "searching" line imply a search ran
        elif e == "gemini" and isinstance(stream, list) and stream and not extra.get("retrieved"):
            triggered = False  # full stream read: no search line, no sources, no URLs at all
        else:
            triggered = None
        ents = (res or {}).get("entities")
        return NormalizedResponse(
            provider=self.name, engine=e, surface="ui", country=req.country, language=req.language,
            answer_text=answer, sources=sources, search_queries=queries,
            web_search_triggered=triggered, web_search_forced=forced,
            model=(res or {}).get("model") or extra.get("model") or None,
            retrieved=retrieved,
            entities=[{"name": x.get("name"), "type": x.get("type")} for x in ents if x.get("name")]
            if isinstance(ents, list) else None,
            user_location=extra.get("user_location"),
            raw=payload,
        )
