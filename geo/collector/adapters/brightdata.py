"""Bright Data adapter — TODO (roadmap V0 step 2).

Notes from public docs (Nov 2026), verify before implementing:
- ChatGPT Search scraper: POST /datasets/v3/scrape?dataset_id=gd_m7aof0k82r803d5bjm
  body {"input": [{"prompt": ..., "country": ...}]}; sync waits ~1 min then returns 202 + snapshot_id.
- Output includes `web_search_triggered` (canonical signal that a search ran -> natural mode),
  answer text, citations, links_attached. Follow-up prompts via `additional_prompt`.
- Separate scrapers/dataset ids for Perplexity, Gemini, Copilot, Google AI Mode, Grok.
- Pricing: per successful record.
"""
from __future__ import annotations

from geo.core.schema import NormalizedResponse, RunRequest


class BrightDataAdapter:
    name = "brightdata"
    engines = {"chatgpt", "perplexity", "gemini", "copilot", "ai_mode"}

    def supports(self, req: RunRequest) -> bool:
        return req.engine in self.engines and req.surface == "ui"

    def collect(self, req: RunRequest) -> NormalizedResponse:
        raise NotImplementedError("Implement in roadmap V0 step 2")
