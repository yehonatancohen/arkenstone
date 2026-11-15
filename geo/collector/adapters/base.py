"""Adapter interface. One adapter per collection provider.

An adapter knows exactly one provider's request/response format and returns NormalizedResponse.
Nothing outside the adapter may depend on provider-specific fields (they stay in `raw`).
"""
from __future__ import annotations

from typing import Protocol

from geo.core.schema import NormalizedResponse, RunRequest


class Adapter(Protocol):
    name: str
    engines: set[str]

    def supports(self, req: RunRequest) -> bool: ...

    def collect(self, req: RunRequest) -> NormalizedResponse:
        """Synchronous collection. Async providers may implement submit/poll later."""
        ...
