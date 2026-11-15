"""Pipeline tests with a fake provider: no network, no credits.

Covers every status transition: planned -> submitted -> collected -> parsed, failures, retries,
permanent errors, a worker crash mid-call, re-parse idempotency, batch isolation and parallel collection.
"""
from __future__ import annotations

import json
import threading
import time

import pytest

from geo.collector import adapters
from geo.core import db
from geo.core.schema import NormalizedResponse, ProviderError, RunRequest, Source
from geo.orchestrator.run import collect_planned, parse_collected, tick

CFG = {
    "name": "תכלס משכנתא", "domain": "tachles-mashkanta.co.il",
    "aliases": ["תכל'ס משכנתא", "תכלס משכנתא"],
    "competitors": [{"name": "בנק לאומי", "aliases": ["בנק לאומי"]}],
}
PROMPT = "האם כדאי למחזר משכנתא עכשיו?"


class FakeAdapter:
    """Behaviour per prompt: script[prompt] = list of outcomes consumed per call ('ok', 'transient', 'permanent')."""
    name = "fake"
    engines = {"chatgpt", "gemini"}
    script: dict[str, list[str]] = {}
    calls: list[str] = []
    delay = 0.0
    lock = threading.Lock()

    def supports(self, req):
        return True

    def collect(self, req: RunRequest, **params) -> NormalizedResponse:
        with FakeAdapter.lock:
            FakeAdapter.calls.append(req.prompt)
            plan = FakeAdapter.script.get(req.prompt, [])
            outcome = plan.pop(0) if plan else "ok"
        time.sleep(FakeAdapter.delay)
        if outcome == "transient":
            raise ProviderError("cloro 500 INTERNAL_SERVER_ERROR", permanent=False, status=500)
        if outcome == "permanent":
            raise ProviderError("cloro 400 VALIDATION_ERROR", permanent=True, status=400)
        return NormalizedResponse(
            provider="fake", engine=req.engine, surface="ui", country=req.country, language=req.language,
            answer_text="לפי תכל'ס משכנתא ובנק לאומי, כדאי לבדוק.",
            sources=[Source(url="https://www.tachles-mashkanta.co.il/x", domain="tachles-mashkanta.co.il", position=1),
                     Source(url="https://www.gov.il/y", domain="gov.il", position=2)],
            search_queries=None,  # provider did not report them: must stay None, not []
            web_search_triggered=None, web_search_forced=False, model=None, credits=7,
            raw={"result": {"text": "raw untouched", "weird": [1, {"ע": "ב"}]}},
        )


@pytest.fixture(autouse=True)
def fake_provider(monkeypatch):
    monkeypatch.setitem(adapters.REGISTRY, "fake", FakeAdapter)
    FakeAdapter.script, FakeAdapter.calls, FakeAdapter.delay = {}, [], 0.0
    yield


@pytest.fixture
def conn(tmp_path):
    return db.connect(str(tmp_path / "geo.db"))


def plan(conn, batch, prompts, engine="chatgpt", reps=1):
    ids = []
    for i, p in enumerate(prompts):
        for _ in range(reps):
            req = RunRequest(project="t", intent_id=f"i{i}", prompt_id=f"i{i}-0", prompt=p, engine=engine)
            ids.append(db.plan_run(conn, req, "fake", intent_version="1", batch_id=batch))
    return ids


def test_happy_path_all_stages_and_storage(conn):
    b = db.create_batch(conn, "t", "happy", {"engine": "chatgpt"})
    plan(conn, b, [PROMPT], reps=3)
    out = tick(conn, CFG, "t", b)
    assert out["collected"] == 3 and out["parsed"] == 3 and out["failed"] == 0
    assert db.batch_status(conn, b) == {"parsed": 3}
    row = conn.execute("SELECT * FROM run WHERE batch_id = ? LIMIT 1", (b,)).fetchone()
    assert row["prompt"] == PROMPT                                  # Hebrew round-trips exactly
    assert json.loads(row["raw_json"]) == {"result": {"text": "raw untouched", "weird": [1, {"ע": "ב"}]}}
    norm = json.loads(row["normalized_json"])
    assert norm["search_queries"] is None and norm["model"] is None  # unknown stays unknown
    assert row["attempts"] == 1 and row["credits"] == 7 and row["collected_at"] and row["latency_ms"] is not None
    m = conn.execute("SELECT entity, is_target FROM mention WHERE run_id = ? ORDER BY position", (row["id"],)).fetchall()
    assert [(r["entity"], r["is_target"]) for r in m] == [("תכלס משכנתא", 1), ("בנק לאומי", 0)]
    c = conn.execute("SELECT domain, is_target FROM citation WHERE run_id = ? ORDER BY position", (row["id"],)).fetchall()
    assert [(r["domain"], r["is_target"]) for r in c] == [("tachles-mashkanta.co.il", 1), ("gov.il", 0)]


def test_second_tick_is_a_noop(conn):
    b = db.create_batch(conn, "t", "noop", {})
    plan(conn, b, [PROMPT])
    tick(conn, CFG, "t", b)
    out = tick(conn, CFG, "t", b)
    assert out["collected"] == 0 and out["parsed"] == 0
    assert len(FakeAdapter.calls) == 1                                # never pays twice


def test_reparse_replaces_rows_without_duplicates(conn):
    b = db.create_batch(conn, "t", "reparse", {})
    plan(conn, b, [PROMPT])
    tick(conn, CFG, "t", b)
    conn.execute("UPDATE run SET status = 'collected' WHERE batch_id = ?", (b,))
    conn.commit()
    assert parse_collected(conn, CFG["domain"], {CFG["name"]: CFG["aliases"]}, CFG["name"], "t", b) == 1
    assert conn.execute("SELECT COUNT(*) FROM citation").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM mention").fetchone()[0] == 1   # competitor list changed: 1 now
    assert len(FakeAdapter.calls) == 1                                # re-parse never calls the provider


def test_transient_error_is_retried(conn):
    b = db.create_batch(conn, "t", "retry", {})
    FakeAdapter.script = {PROMPT: ["transient"]}
    plan(conn, b, [PROMPT])
    out = tick(conn, CFG, "t", b)
    assert out["failed"] == 1 and out["retried"] == 1 and out["collected"] == 1
    row = conn.execute("SELECT * FROM run WHERE batch_id = ?", (b,)).fetchone()
    assert row["status"] == "parsed" and row["attempts"] == 2 and row["error"] is None


def test_permanent_error_is_not_retried(conn):
    b = db.create_batch(conn, "t", "perm", {})
    FakeAdapter.script = {PROMPT: ["permanent"]}
    plan(conn, b, [PROMPT])
    tick(conn, CFG, "t", b)
    tick(conn, CFG, "t", b)
    row = conn.execute("SELECT * FROM run WHERE batch_id = ?", (b,)).fetchone()
    assert row["status"] == "failed" and "[permanent]" in row["error"] and row["attempts"] == 1


def test_gives_up_after_max_attempts(conn):
    b = db.create_batch(conn, "t", "max", {})
    FakeAdapter.script = {PROMPT: ["transient"] * 10}
    plan(conn, b, [PROMPT])
    for _ in range(5):
        tick(conn, CFG, "t", b)
    row = conn.execute("SELECT * FROM run WHERE batch_id = ?", (b,)).fetchone()
    assert row["status"] == "failed" and row["attempts"] == db.MAX_ATTEMPTS
    assert len(FakeAdapter.calls) == db.MAX_ATTEMPTS


def test_crash_mid_call_is_recovered(conn):
    b = db.create_batch(conn, "t", "crash", {})
    (rid,) = plan(conn, b, [PROMPT])
    assert db.claim(conn, rid)                        # worker claimed it, then died before writing
    assert not db.claim(conn, rid)                    # nobody else can take it meanwhile
    assert collect_planned(conn, "t", b) == {"collected": 0, "failed": 0}
    assert db.release_stale(conn, minutes=15) == 0    # not stale yet
    conn.execute("UPDATE run SET submitted_at = datetime('now', '-20 minutes') WHERE id = ?", (rid,))
    conn.commit()
    out = tick(conn, CFG, "t", b)
    assert out["released"] == 1 and out["parsed"] == 1


def test_batches_are_isolated(conn):
    b1 = db.create_batch(conn, "t", "one", {"engine": "chatgpt"})
    b2 = db.create_batch(conn, "t", "two", {"engine": "gemini"})
    plan(conn, b1, [PROMPT])
    plan(conn, b2, [PROMPT], engine="gemini")
    tick(conn, CFG, "t", b1)
    assert db.batch_status(conn, b1) == {"parsed": 1}
    assert db.batch_status(conn, b2) == {"planned": 1}


def test_parallel_collection_claims_each_run_once(conn):
    b = db.create_batch(conn, "t", "par", {})
    prompts = [f"{PROMPT} {i}" for i in range(12)]
    plan(conn, b, prompts)
    FakeAdapter.delay = 0.2
    t0 = time.monotonic()
    out = collect_planned(conn, "t", b, workers=6)
    elapsed = time.monotonic() - t0
    assert out == {"collected": 12, "failed": 0}
    assert sorted(FakeAdapter.calls) == sorted(prompts)   # each run called exactly once
    assert elapsed < 12 * 0.2 / 2                         # really ran in parallel


def test_migration_adds_columns_to_old_database(tmp_path):
    import sqlite3
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.executescript(db.SCHEMA.replace("CREATE INDEX IF NOT EXISTS mention_run ON mention(run_id);", ""))
    old.execute("""INSERT INTO run (id, project, intent_id, prompt_id, prompt, engine, surface, country, language,
                   web_search, status) VALUES ('r1','t','i','p','שאלה','chatgpt','ui','IL','he','natural','planned')""")
    old.commit()
    old.close()
    conn = db.connect(path)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(run)")}
    assert set(db.RUN_COLUMNS) <= cols
    assert conn.execute("SELECT attempts FROM run WHERE id = 'r1'").fetchone()[0] == 0
