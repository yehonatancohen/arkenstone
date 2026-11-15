"""Orchestrator: moves runs through statuses. V0 is synchronous; async submit/poll comes with Bright Data.

planned -> (collect) -> collected -> (parse) -> parsed
Each stage only reads runs in its input status, so the whole loop is resumable and idempotent.
"""
from __future__ import annotations

import json

from geo.collector.adapters import get_adapter
from geo.core import db
from geo.core.schema import RunRequest
from geo.parser.normalize import domain_of, find_mentions


def collect_planned(conn, project: str | None = None) -> int:
    done = 0
    for row in db.runs_in(conn, "planned", project):
        req = RunRequest(project=row["project"], intent_id=row["intent_id"], prompt_id=row["prompt_id"],
                         prompt=row["prompt"], engine=row["engine"], country=row["country"],
                         language=row["language"], surface=row["surface"], web_search=row["web_search"])
        adapter = get_adapter(row["provider"])
        try:
            resp = adapter.collect(req)
            db.set_status(conn, row["id"], "collected", raw_json=resp.raw,
                          normalized_json={k: v for k, v in resp.to_dict().items() if k != "raw"})
            done += 1
        except Exception as e:  # keep going; failed runs are retried later
            db.set_status(conn, row["id"], "failed", error=repr(e)[:2000])
    return done


def parse_collected(conn, target_domain: str, entities: dict[str, list[str]], target_name: str,
                    project: str | None = None) -> int:
    done = 0
    for row in db.runs_in(conn, "collected", project):
        norm = json.loads(row["normalized_json"])
        conn.execute("DELETE FROM mention WHERE run_id = ?", (row["id"],))
        conn.execute("DELETE FROM citation WHERE run_id = ?", (row["id"],))
        for i, (name, _pos) in enumerate(find_mentions(norm.get("answer_text"), entities)):
            conn.execute("INSERT INTO mention VALUES (?,?,?,?)", (row["id"], name, int(name == target_name), i + 1))
        for s in norm.get("sources") or []:
            d = s.get("domain") or domain_of(s.get("url", ""))
            conn.execute("INSERT INTO citation VALUES (?,?,?,?,?)",
                         (row["id"], d, s.get("url"), s.get("position"), int(d == target_domain)))
        db.set_status(conn, row["id"], "parsed")
        done += 1
    return done
