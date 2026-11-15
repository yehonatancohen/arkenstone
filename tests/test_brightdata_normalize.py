"""Bright Data record -> NormalizedResponse, on records shaped like the documented example (2026-11-15)."""
from __future__ import annotations

import pytest

from geo.collector.adapters import brightdata
from geo.collector.adapters.brightdata import BrightDataAdapter, BudgetExceeded
from geo.core.schema import RunRequest


def req(engine):
    return RunRequest(project="t", intent_id="i", prompt_id="i-1", prompt="איפה יש מסיבות?", engine=engine)


CHATGPT = {
    "prompt": "איפה יש מסיבות?", "answer_text": "תשובה", "model": "gpt-6", "web_search_triggered": True,
    "web_search_query": ["מסיבות תל אביב"],
    "citations": [{"title": "A", "url": "https://a.co.il/x", "position": 1}],
    "links_attached": [{"url": "https://b.co.il/", "text": "B", "position": 0}],
    "search_sources": [{"url": "https://c.co.il/", "title": "C"}, {"url": "https://a.co.il/x", "title": "A"}],
    "search_sources_more": [{"url": "https://d.co.il/", "title": "D"}],
    "index": 1, "prompt_sent_at": "2026-11-15T10:00:00Z",
}


def test_chatgpt_fields():
    n = BrightDataAdapter(api_key="x").normalize(req("chatgpt"), CHATGPT)
    assert n.model == "gpt-6" and n.search_queries == ["מסיבות תל אביב"] and n.web_search_triggered is True
    assert n.web_search_forced is False
    assert [s.domain for s in n.sources] == ["a.co.il", "b.co.il"]
    assert [(r.domain, r.cited) for r in n.retrieved] == [
        ("c.co.il", False), ("a.co.il", True), ("d.co.il", False), ("b.co.il", True)]
    assert n.raw is CHATGPT


def test_unknowns_stay_none():
    rec = {"answer_text": "x", "web_search_triggered": True, "web_search_query": []}
    n = BrightDataAdapter(api_key="x").normalize(req("chatgpt"), rec)
    assert n.sources is None and n.retrieved is None and n.search_queries is None and n.model is None
    g = BrightDataAdapter(api_key="x").normalize(req("gemini"), {"answer_text": "x", "citations": []})
    assert g.sources == [] and g.retrieved is None and g.web_search_triggered is None and g.search_queries is None


def test_budget_refuses(tmp_path, monkeypatch):
    led = tmp_path / "calls.jsonl"
    led.write_text('{"n":1}\n{"n":2}\n', encoding="utf-8")
    monkeypatch.setenv("BRIGHTDATA_LEDGER", str(led))
    monkeypatch.setenv("BRIGHTDATA_MAX_CALLS", "2")
    with pytest.raises(BudgetExceeded):
        BrightDataAdapter(api_key="x").account_status()
    assert brightdata.calls_used() == 2


def test_poll_maps_index_and_downloads_once(monkeypatch):
    monkeypatch.setattr(brightdata, "_SNAPSHOTS", {})
    a = BrightDataAdapter(api_key="x")
    calls = []

    def fake_http(method, path, **kw):
        calls.append(path)

        class R:
            status_code = 200

            def json(self):
                return [dict(CHATGPT, index=1), {"index": 2, "error": "boom", "error_code": "x"}]
        return R()
    monkeypatch.setattr(a, "_http", fake_http)
    assert a.poll("sd_1#1", req("chatgpt")).answer_text == "תשובה"
    with pytest.raises(Exception):
        a.poll("sd_1#2", req("chatgpt"))
    assert calls == ["/datasets/v3/snapshot/sd_1"]
