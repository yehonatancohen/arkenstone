"""Extract what cloro's `rawResponse` stream reveals beyond the documented fields.

The stream is the engine's own UI traffic, so its shape is undocumented and may change without notice.
Every extractor returns None when it finds nothing it recognizes ("unknown"), never an empty default,
and never raises: a changed stream must degrade to "unknown", not break collection.
Shapes verified on runs from 2026-11-15.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Iterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

GOOGLE_HOSTS = ("google.", "gstatic.com", "googleusercontent.com", "googleapis.com", "ggpht.com", "goo.gl")
# Gemini status lines shown while it browses (UI language follows the proxy country).
GEMINI_SEARCH_MARKERS = ("מחפש באינטרנט", "החיפוש באינטרנט הושלם", "Searching the web", "Search complete")
GEMINI_MODEL = re.compile(r"^‎?\s*(\d+(?:\.\d+)?\s+(?:Flash|Pro|Ultra|Nano)(?:[- ]Lite)?)\s*$")
LOCATION = re.compile(r"^([^,\d]{2,40}),\s*(ישראל|Israel)$")


def clean_url(url: str) -> str:
    """Drop tracking params (utm_*), text fragments and fragments, so the same page compares equal."""
    try:
        p = urlsplit(url)
    except ValueError:
        return url
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not k.lower().startswith("utm_")]
    return urlunsplit((p.scheme, p.netloc.lower(), p.path, urlencode(q), ""))


def _walk(o: Any, decode_strings: bool = False) -> Iterator[Any]:
    """Yield every node; optionally parse JSON hidden inside strings (Gemini nests JSON in strings)."""
    stack = [o]
    while stack:
        n = stack.pop()
        yield n
        if isinstance(n, dict):
            stack.extend(reversed(list(n.values())))
        elif isinstance(n, list):
            stack.extend(reversed(n))
        elif decode_strings and isinstance(n, str) and n[:1] in "[{" and len(n) > 2:
            try:
                stack.append(json.loads(n))
            except ValueError:
                pass


def _iso(ts) -> str | None:
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).date().isoformat() if ts else None
    except (TypeError, ValueError, OverflowError):
        return None


def chatgpt(raw_stream: list | None) -> dict:
    """-> {retrieved: [{url,title,snippet,pub_date,source_name}] | None}. Order = first appearance."""
    if not isinstance(raw_stream, list):
        return {"retrieved": None}
    seen: dict[str, dict] = {}
    found_groups = False
    for n in _walk(raw_stream):
        if isinstance(n, dict) and isinstance(n.get("search_result_groups"), list):
            found_groups = True
            for g in n["search_result_groups"]:
                for e in (g or {}).get("entries") or []:
                    url = (e or {}).get("url")
                    if not url:
                        continue
                    key = clean_url(url)
                    if key not in seen:
                        seen[key] = {"url": url, "title": e.get("title"), "snippet": e.get("snippet") or None,
                                     "pub_date": _iso(e.get("pub_date")), "source_name": e.get("source_name")}
    return {"retrieved": list(seen.values()) if found_groups else None}


def gemini(raw_stream: list | None) -> dict:
    """-> {retrieved, search_triggered, model, user_location}; each None when not found."""
    if not isinstance(raw_stream, list):
        return {"retrieved": None, "search_triggered": None, "model": None, "user_location": None}
    urls: dict[str, str] = {}
    searched = model = location = None
    for n in _walk(raw_stream, decode_strings=True):
        if not isinstance(n, str):
            continue
        if n.startswith("http"):
            host = urlsplit(n).netloc.lower()
            if not any(h in host for h in GOOGLE_HOSTS):
                urls.setdefault(clean_url(n), n.split("#")[0])
        elif any(m in n for m in GEMINI_SEARCH_MARKERS):
            searched = True
        elif model is None and (m := GEMINI_MODEL.match(n)):
            model = m.group(1)
        elif location is None and len(n) < 50 and (m := LOCATION.match(n.strip())):
            location = n.strip()
    return {
        # The whole stream was read, so no URLs in it means nothing was read (known empty), not unknown.
        "retrieved": [{"url": u, "title": None, "snippet": None, "pub_date": None, "source_name": None}
                      for u in urls.values()] if raw_stream else None,
        # The marker's absence is not proof of no search, but with no sources and no URLs at all it is
        # the best evidence available; record False only in that case.
        "search_triggered": searched,
        "model": model,
        "user_location": location,
    }
