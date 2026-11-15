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
    snippet: str | None = None
    pub_date: str | None = None     # ISO date when the engine reported one


@dataclass
class Retrieved:
    """A page the engine read/received from its search, whether or not it was shown as a source."""
    url: str
    domain: str
    position: int                   # order of first appearance in the engine's stream
    cited: bool                     # also in the shown sources list
    title: str | None = None
    snippet: str | None = None
    pub_date: str | None = None
    source_name: str | None = None


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
    retrieved: list[Retrieved] | None = None    # everything the engine read; None = not reported
    entities: list[dict] | None = None          # businesses/products the engine itself tagged: {name, type}
    user_location: str | None = None            # where the engine thought the user was (proxy geo)
    latency_ms: int | None = None   # provider-side time when reported, else client wall time
    credits: int | None = None      # provider cost units charged for this run, when reported
    raw: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProviderError(Exception):
    """Raised by adapters. `permanent` errors (bad request, no credits, bad key) are not retried."""

    def __init__(self, message: str, permanent: bool = False, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.permanent = permanent
        self.status = status
        self.code = code
