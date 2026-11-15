"""Normalized schema shared by every module. Adapters translate provider payloads into these types.

Rule: a field the provider did not return is None (unknown), never an empty default.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


@dataclass
class RunRequest:
    project: str
    intent_id: str
    prompt_id: str
    prompt: str
    engine: str              # chatgpt | gemini | ai_mode | perplexity | copilot ...
    country: str = "IL"
    language: str = "he"
    surface: str = "ui"      # ui (consumer interface) | api
    web_search: str = "natural"  # natural | forced | off


@dataclass
class Source:
    url: str
    domain: str
    title: str | None = None
    position: int | None = None
    cited_inline: bool | None = None


@dataclass
class NormalizedResponse:
    provider: str
    engine: str
    surface: str
    country: str
    language: str
    answer_text: str | None
    sources: list[Source] | None
    search_queries: list[str] | None
    web_search_triggered: bool | None
    web_search_forced: bool
    model: str | None
    collected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    raw: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
