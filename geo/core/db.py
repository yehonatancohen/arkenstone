"""SQLite store for V0. The database is the bus: every run has a status and stages move it forward.

Statuses: planned -> submitted -> collected -> parsed -> scored  (or failed)
Raw provider payloads are stored untouched in `raw_json`.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from pathlib import Path

SCHEMA = """
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
"""


def connect(path: str | None = None) -> sqlite3.Connection:
    path = path or os.getenv("GEO_DB_PATH", "data/geo.db")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def plan_run(conn: sqlite3.Connection, req, provider: str, intent_version: str | None = None) -> str:
    run_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO run (id, project, intent_id, prompt_id, prompt, intent_version, engine, surface,
                            country, language, web_search, provider, status)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'planned')""",
        (run_id, req.project, req.intent_id, req.prompt_id, req.prompt, intent_version, req.engine,
         req.surface, req.country, req.language, req.web_search, provider),
    )
    conn.commit()
    return run_id


def set_status(conn: sqlite3.Connection, run_id: str, status: str, **fields) -> None:
    cols = ", ".join(f"{k} = ?" for k in fields)
    sql = f"UPDATE run SET status = ?, updated_at = CURRENT_TIMESTAMP{', ' + cols if cols else ''} WHERE id = ?"
    values = [status, *[json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                        for v in fields.values()], run_id]
    conn.execute(sql, values)
    conn.commit()


def runs_in(conn: sqlite3.Connection, status: str, project: str | None = None):
    if project:
        return conn.execute("SELECT * FROM run WHERE status = ? AND project = ?", (status, project)).fetchall()
    return conn.execute("SELECT * FROM run WHERE status = ?", (status,)).fetchall()
