"""ScrapeLLM job body -> NormalizedResponse, shapes from real jobs (2026-11-15)."""
from __future__ import annotations

from geo.collector.adapters.scrapellm import ScrapeLLMAdapter
from geo.core.schema import RunRequest


def req(engine):
    return RunRequest(project="t", intent_id="i", prompt_id="i-1", prompt="איפה יש מסיבות?", engine=engine)


def test_perplexity_sources_cited_read_unknown():
    job = {"status": "done", "result": {"result_markdown": "כן, [A](https://a.co.il/x) ([a.co.il][1])",
                                        "sources": [{"title": "A", "url": "https://a.co.il/x", "snippet": "s"},
                                                    {"title": "B", "url": "https://www.b.co.il/"}],
                                        "credits_used": 3, "elapsed_ms": 9100.5}}
    n = ScrapeLLMAdapter(api_key="x").normalize(req("perplexity"), job)
    assert [s.domain for s in n.sources] == ["a.co.il", "b.co.il"]
    assert n.retrieved is None and n.search_queries is None and n.model is None   # unknown, not empty
    assert n.web_search_triggered is True and n.credits == 3 and n.latency_ms == 9100


def test_grok_read_vs_cited_and_mode_is_not_model():
    job = {"status": "done", "result": {"result_markdown": "**[Popit](https://popit.events/explore/weekend)**",
                                        "web_sources": [{"title": "P", "url": "https://popit.events/explore/weekend"},
                                                        {"title": "C", "url": "https://www.c14.co.il/article/1"}],
                                        "llm_model": "auto", "x_results": []}}
    n = ScrapeLLMAdapter(api_key="x").normalize(req("grok"), job)
    assert [(r.domain, r.cited) for r in n.retrieved] == [("popit.events", True), ("c14.co.il", False)]
    assert [s.domain for s in n.sources] == ["popit.events"] and n.model is None


def test_missing_fields_stay_none():
    n = ScrapeLLMAdapter(api_key="x").normalize(req("perplexity"), {"status": "done", "result": {"result": "x"}})
    assert n.sources is None and n.web_search_triggered is None
