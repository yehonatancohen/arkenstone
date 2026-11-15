"""SQLite store for V0. The database is the bus: every run has a status and stages move it forward.

    planned -> submitted -> collected -> parsed            (scored comes with the estimator stage)
        \\-> failed -> (retry) -> planned, until max attempts

- `submitted` is a claim: a run is marked before the provider is called, so two workers never pay
  for the same run. A run stuck in `submitted` (worker crashed mid-call) is released by `release_stale`.
- Every run belongs to a batch: one planned set with fixed parameters. Metrics are computed per batch,
  never across batches, so engines/providers/surfaces/intent versions are never mixed by accident.
- Raw provider payloads are stored untouched in `raw_json`.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS batch (
    id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    label TEXT,
    params_json TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS run (
    id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    intent_id TEXT NOT NULL,
    prompt_id TEXT NOT NULL,
    prompt TEXT NOT NULL,
    intent_version TEXT,
    engine TEXT NOT NULL,
    surface TEXT NOT NULL,
    country TEXT NOT NULL,
    language TEXT NOT NULL,
    web_search TEXT NOT NULL,
    provider TEXT,
    status TEXT NOT NULL,
    provider_task_id TEXT,
    error TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    raw_json TEXT,
    normalized_json TEXT
);
CREATE INDEX IF NOT EXISTS run_status ON run(status);

CREATE TABLE IF NOT EXISTS mention (
    run_id TEXT NOT NULL,
    entity TEXT NOT NULL,
    is_target INTEGER NOT NULL,
    position INTEGER
);

CREATE TABLE IF NOT EXISTS citation (
    run_id TEXT NOT NULL,
    domain TEXT NOT NULL,
    url TEXT,
    position INTEGER,
    is_target INTEGER NOT NULL
);
-- Everything the engine read (superset of citation). cited = also shown as a source.
CREATE TABLE IF NOT EXISTS retrieval (
    run_id TEXT NOT NULL,
    domain TEXT NOT NULL,
    url TEXT,
    position INTEGER,
    cited INTEGER NOT NULL,
    is_target INTEGER NOT NULL,
    title TEXT,
    pub_date TEXT
);

-- The engine's own fan-out search queries, in order.
CREATE TABLE IF NOT EXISTS search_query (
    run_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    query TEXT NOT NULL
);

-- Businesses/products the engine itself tagged as entities (ChatGPT), as opposed to our alias matching.
CREATE TABLE IF NOT EXISTS engine_entity (
    run_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    name TEXT NOT NULL,
    type TEXT,
    is_target INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS mention_run ON mention(run_id);
CREATE INDEX IF NOT EXISTS retrieval_run ON retrieval(run_id);
CREATE INDEX IF NOT EXISTS search_query_run ON search_query(run_id);
CREATE INDEX IF NOT EXISTS engine_entity_run ON engine_entity(run_id);
CREATE INDEX IF NOT EXISTS citation_run ON citation(run_id);
"""

# Columns added after the first schema. connect() adds any that are missing (SQLite has no IF NOT EXISTS here).
RUN_COLUMNS = {
    "batch_id": "TEXT",
    "attempts": "INTEGER NOT NULL DEFAULT 0",
    "submitted_at": "TEXT",
    "collected_at": "TEXT",
    "model": "TEXT",
    "latency_ms": "INTEGER",
    "credits": "INTEGER",
    "params_json": "TEXT",  # provider-request options for this run (e.g. include flags), for reproducibility
}

MAX_ATTEMPTS = 3


def connect(path: str | None = None) -> sqlite3.Connection:
    path = path or os.getenv("GEO_DB_PATH", "data/geo.db")
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    have = {r["name"] for r in conn.execute("PRAGMA table_info(run)")}
    for col, decl in RUN_COLUMNS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE run ADD COLUMN {col} {decl}")
    conn.execute("CREATE INDEX IF NOT EXISTS run_batch ON run(batch_id, status)")
    conn.commit()
    return conn


def _j(v):
    return json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v


def create_batch(conn: sqlite3.Connection, project: str, label: str, params: dict) -> str:
    batch_id = f"{label}-{uuid.uuid4().hex[:8]}"
    conn.execute("INSERT INTO batch (id, project, label, params_json) VALUES (?,?,?,?)",
                 (batch_id, project, label, _j(params)))
    conn.commit()
    return batch_id


def plan_run(conn: sqlite3.Connection, req, provider: str, intent_version: str | None = None,
             batch_id: str | None = None, params: dict | None = None) -> str:
    run_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO run (id, project, intent_id, prompt_id, prompt, intent_version, engine, surface,
                            country, language, web_search, provider, status, batch_id, params_json)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'planned', ?, ?)""",
        (run_id, req.project, req.intent_id, req.prompt_id, req.prompt, intent_version, req.engine,
         req.surface, req.country, req.language, req.web_search, provider, batch_id, _j(params or {})),
    )
    conn.commit()
    return run_id


def set_status(conn: sqlite3.Connection, run_id: str, status: str, **fields) -> None:
    cols = ", ".join(f"{k} = ?" for k in fields)
    sql = f"UPDATE run SET status = ?, updated_at = CURRENT_TIMESTAMP{', ' + cols if cols else ''} WHERE id = ?"
    conn.execute(sql, [status, *[_j(v) for v in fields.values()], run_id])
    conn.commit()


def claim(conn: sqlite3.Connection, run_id: str) -> bool:
    """planned -> submitted, atomically. False if another worker already took it."""
    cur = conn.execute(
        """UPDATE run SET status = 'submitted', attempts = attempts + 1,
                          submitted_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
           WHERE id = ? AND status = 'planned'""", (run_id,))
    conn.commit()
    return cur.rowcount == 1


def release_stale(conn: sqlite3.Connection, minutes: int = 15) -> int:
    """Runs left in `submitted` longer than `minutes` (worker died mid-call) go back to planned
    (or to failed once out of attempts). A sync provider call never takes longer than ~5 minutes."""
    cur = conn.execute(
        f"""UPDATE run SET status = CASE WHEN attempts >= ? THEN 'failed' ELSE 'planned' END,
                           error = COALESCE(error, '') || ' [released: stuck in submitted]',
                           updated_at = CURRENT_TIMESTAMP
            WHERE status = 'submitted' AND submitted_at < datetime('now', '-{int(minutes)} minutes')""",
        (MAX_ATTEMPTS,))
    conn.commit()
    return cur.rowcount


def retry_failed(conn: sqlite3.Connection, batch_id: str | None = None) -> int:
    """failed -> planned for runs with attempts left and a retryable error."""
    sql = """UPDATE run SET status = 'planned', updated_at = CURRENT_TIMESTAMP
             WHERE status = 'failed' AND attempts < ? AND COALESCE(error, '') NOT LIKE '%[permanent]%'"""
    args: list = [MAX_ATTEMPTS]
    if batch_id:
        sql += " AND batch_id = ?"
        args.append(batch_id)
    cur = conn.execute(sql, args)
    conn.commit()
    return cur.rowcount


def runs_in(conn: sqlite3.Connection, status: str, project: str | None = None, batch_id: str | None = None):
    sql, args = "SELECT * FROM run WHERE status = ?", [status]
    if project:
        sql += " AND project = ?"
        args.append(project)
    if batch_id:
        sql += " AND batch_id = ?"
        args.append(batch_id)
    return conn.execute(sql + " ORDER BY created_at, id", args).fetchall()


def batch_status(conn: sqlite3.Connection, batch_id: str) -> dict[str, int]:
    rows = conn.execute("SELECT status, COUNT(*) c FROM run WHERE batch_id = ? GROUP BY status", (batch_id,))
    return {r["status"]: r["c"] for r in rows}
