"""cloro payload -> NormalizedResponse, on synthetic payloads shaped like real ones (2026-11-15)."""
from __future__ import annotations

import json

from geo.collector.adapters.cloro import CloroAdapter
from geo.collector.adapters.cloro_raw import clean_url
from geo.core.schema import RunRequest


def req(engine):
    return RunRequest(project="t", intent_id="i", prompt_id="i-1", prompt="איפה יש מסיבות?", engine=engine)


def chatgpt_payload(stream=True):
    entries = [
        {"type": "search_result", "url": "https://a.co.il/x?utm_source=chatgpt.com", "title": "A", "snippet": "",
         "pub_date": 1794700800, "source_name": "אתר א", "attribution": "a.co.il"},
        {"type": "search_result", "url": "https://b.co.il/", "title": "B", "snippet": "s", "pub_date": None},
        {"type": "search_result", "url": "https://c.co.il/", "title": "C", "snippet": "", "pub_date": None},
    ]
    res = {
        "text": "תשובה עם א",
        "model": "gpt-6",
        "searchQueries": ["מסיבות סופ\"ש"],
        "entities": [{"type": "software", "name": "אתר א"}],
        # legacy mode: sources = every page used; footnote False = linked in the text
        "sources": [{"position": 1, "url": "https://a.co.il/x?utm_source=chatgpt.com", "label": "A", "footnote": False},
                    {"position": 2, "url": "https://b.co.il/", "label": "B", "footnote": True},
                    {"position": 3, "url": "https://a.co.il/x?utm_source=chatgpt.com", "label": "A", "footnote": True}],
    }
    if stream:
        res["rawResponse"] = [{"type": "resume"}, {"v": {"message": {"metadata": {
            "search_result_groups": [{"entries": entries[:2]}, {"entries": entries[2:]}]}}}}]
    return {"success": True, "result": res}


def test_chatgpt_retrieved_cited_and_extras():
    n = CloroAdapter(api_key="x").normalize(req("chatgpt"), chatgpt_payload())
    assert [r.domain for r in n.retrieved] == ["a.co.il", "b.co.il", "c.co.il"]
    assert [r.cited for r in n.retrieved] == [True, False, False]       # only inline-linked counts as cited
    assert n.retrieved[0].pub_date == "2026-11-15" and n.retrieved[0].source_name == "אתר א"
    assert n.search_queries == ['מסיבות סופ"ש'] and n.model == "gpt-6"
    assert n.entities == [{"name": "אתר א", "type": "software"}]
    assert n.web_search_triggered is True and n.web_search_forced is True


def test_chatgpt_without_stream_retrieved_is_unknown():
    n = CloroAdapter(api_key="x").normalize(req("chatgpt"), chatgpt_payload(stream=False))
    assert n.retrieved is None                     # shown sources alone are not the full retrieved set
    assert len(n.sources) == 3


def test_gemini_stream_extracts_model_location_search_and_urls():
    inner = json.dumps([["מחפש באינטרנט"], ["https://www.parties247.co.il/#:~:text=x", "https://www.parties247.co.il/"],
                        ["https://www.google.com/search?q=1", "‎3.5 Flash-Lite", "ירוחם, ישראל"],
                        ["https://zygo.co.il/"]], ensure_ascii=False)
    payload = {"result": {"text": "Parties24/7 ו-Zygo", "rawResponse": [58, [["wrb.fr", None, inner]]],
                          "sources": [{"position": 1, "url": "https://www.parties247.co.il/", "label": "P"}]}}
    n = CloroAdapter(api_key="x").normalize(req("gemini"), payload)
    assert [(r.domain, r.cited) for r in n.retrieved] == [("parties247.co.il", True), ("zygo.co.il", False)]
    assert n.model == "3.5 Flash-Lite" and n.user_location == "ירוחם, ישראל"
    assert n.web_search_triggered is True and n.web_search_forced is False


def test_gemini_no_search_is_false_only_with_full_stream():
    no_search = {"result": {"text": "תשובה מהזיכרון", "rawResponse": [10, [["wrb.fr", None, "[[\"טקסט\"]]"]]]}}
    assert CloroAdapter(api_key="x").normalize(req("gemini"), no_search).web_search_triggered is False
    no_stream = {"result": {"text": "תשובה"}}
    assert CloroAdapter(api_key="x").normalize(req("gemini"), no_stream).web_search_triggered is None


def test_ai_overview_absent_is_known_empty():
    n = CloroAdapter(api_key="x").normalize(req("ai_overview"), {"result": {"organicResults": []}})
    assert n.answer_text == "" and n.sources == [] and n.retrieved is None


def test_clean_url():
    assert clean_url("https://Www.A.co.il/p?utm_source=x&id=2#:~:text=y") == "https://www.a.co.il/p?id=2"


def test_changed_stream_shape_degrades_to_unknown():
    p = chatgpt_payload()
    p["result"]["rawResponse"] = [{"totally": "different"}, "x", 5]
    n = CloroAdapter(api_key="x").normalize(req("chatgpt"), p)
    assert n.retrieved is None and n.search_queries == ['מסיבות סופ"ש']
