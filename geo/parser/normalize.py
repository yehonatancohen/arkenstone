"""Parsing helpers: domain normalization and simple mention detection.

V0 mention detection is alias matching against project config. An LLM-based extractor
(entities, position, tone) replaces it in V1; keep this as a fallback/baseline to compare against.
"""
from __future__ import annotations

import re

import tldextract


def domain_of(url: str) -> str:
    if not url:
        return ""
    ext = tldextract.extract(url)
    return ".".join(p for p in (ext.domain, ext.suffix) if p).lower()


def find_mentions(text: str | None, entities: dict[str, list[str]]) -> list[tuple[str, int]]:
    """entities: {canonical_name: [aliases...]} -> [(canonical_name, char_position)] sorted by position."""
    if not text:
        return []
    hits = []
    lowered = text.lower()
    for name, aliases in entities.items():
        positions = [m.start() for a in aliases for m in re.finditer(re.escape(a.lower()), lowered)]
        if positions:
            hits.append((name, min(positions)))
    return sorted(hits, key=lambda h: h[1])
