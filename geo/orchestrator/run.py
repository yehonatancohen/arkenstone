"""Orchestrator: moves runs through statuses. Every stage reads only its input status, so the loop is
resumable and idempotent: run `tick()` again after any crash and it continues where it stopped.

    planned -> (claim) submitted -> (provider call) collected -> (parse) parsed
                                 \\-> failed -> retry_failed -> planned   (up to db.MAX_ATTEMPTS)

Provider calls run in a thread pool (`workers`); all database writes happen on the calling thread,
so SQLite never sees concurrent writers. Sync providers only for now; async submit/poll comes later.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from geo.collector.adapters import get_adapter
from geo.core import db
from geo.core.schema import ProviderError, RunRequest
from geo.parser.normalize import domain_of, find_mentions


def _request(row) -> RunRequest:
    return RunRequest(project=row["project"], intent_id=row["intent_id"], prompt_id=row["prompt_id"],
                      prompt=row["prompt"], engine=row["engine"], country=row["country"],
                      language=row["language"], surface=row["surface"], web_search=row["web_search"])


def _call(adapter, req: RunRequest, params: dict):
    t0 = time.monotonic()
    try:
        resp = adapter.collect(req, **params) if params else adapter.collect(req)
        if resp.latency_ms is None:
            resp.latency_ms = int((time.monotonic() - t0) * 1000)
        return resp, None
    except Exception as e:  # noqa: BLE001 - every failure is recorded on the run, never lost
        return None, e


def collect_planned(conn, project: str | None = None, batch_id: str | None = None,
                    workers: int = 1, limit: int | None = None, progress=None) -> dict[str, int]:
    rows = db.runs_in(conn, "planned", project, batch_id)[: limit or None]
    claimed = [r for r in rows if db.claim(conn, r["id"])]
    counts = {"collected": 0, "failed": 0}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {}
        for row in claimed:
            params = json.loads(row["params_json"] or "{}")
            futures[pool.submit(_call, get_adapter(row["provider"]), _request(row), params)] = row
        for fut in as_completed(futures):
            row = futures[fut]
            resp, err = fut.result()
            if err is None:
                norm = {k: v for k, v in resp.to_dict().items() if k != "raw"}
                db.set_status(conn, row["id"], "collected", raw_json=resp.raw, normalized_json=norm,
                              collected_at=resp.collected_at, model=resp.model,
                              latency_ms=resp.latency_ms, credits=resp.credits, error=None)
                counts["collected"] += 1
            else:
                permanent = isinstance(err, ProviderError) and err.permanent
                msg = ("[permanent] " if permanent else "") + repr(err)[:2000]
                db.set_status(conn, row["id"], "failed", error=msg)
                counts["failed"] += 1
            if progress:
                progress(row, err)
    return counts


def parse_collected(conn, target_domain: str, entities: dict[str, list[str]], target_name: str,
                    project: str | None = None, batch_id: str | None = None) -> int:
    """collected -> parsed. Re-parsing = set runs back to 'collected' and call again (rows are replaced)."""
    done = 0
    for row in db.runs_in(conn, "collected", project, batch_id):
        norm = json.loads(row["normalized_json"])
        with conn:  # one transaction per run: its mentions, citations and status change together
            for table in ("mention", "citation", "retrieval", "search_query", "engine_entity"):
                conn.execute(f"DELETE FROM {table} WHERE run_id = ?", (row["id"],))
            for i, (name, _pos) in enumerate(find_mentions(norm.get("answer_text"), entities)):
                conn.execute("INSERT INTO mention VALUES (?,?,?,?)",
                             (row["id"], name, int(name == target_name), i + 1))
            for s in norm.get("sources") or []:
                d = s.get("domain") or domain_of(s.get("url", ""))
                conn.execute("INSERT INTO citation VALUES (?,?,?,?,?)",
                             (row["id"], d, s.get("url"), s.get("position"), int(d == target_domain)))
            for r in norm.get("retrieved") or []:
                d = r.get("domain") or domain_of(r.get("url", ""))
                conn.execute("INSERT INTO retrieval VALUES (?,?,?,?,?,?,?,?)",
                             (row["id"], d, r.get("url"), r.get("position"), int(bool(r.get("cited"))),
                              int(d == target_domain), r.get("title"), r.get("pub_date")))
            for i, q in enumerate(norm.get("search_queries") or []):
                conn.execute("INSERT INTO search_query VALUES (?,?,?)", (row["id"], i + 1, q))
            target_aliases = {a.lower() for a in entities.get(target_name, [])} | {target_name.lower()}
            for i, ent in enumerate(norm.get("entities") or []):
                name = ent.get("name") or ""
                conn.execute("INSERT INTO engine_entity VALUES (?,?,?,?,?)",
                             (row["id"], i + 1, name, ent.get("type"),
                              int(name.lower() in target_aliases or any(a in name.lower() for a in target_aliases))))
            conn.execute("UPDATE run SET status = 'parsed', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (row["id"],))
        done += 1
    return done


def renormalize(conn, project: str | None = None, batch_id: str | None = None) -> int:
    """Rebuild normalized_json from the stored raw_json with the current adapter code, and send the runs
    back to `collected` for re-parsing. No provider call: this is how every metric stays recomputable."""
    sql, args = "SELECT * FROM run WHERE raw_json IS NOT NULL AND status IN ('collected', 'parsed')", []
    if project:
        sql += " AND project = ?"
        args.append(project)
    if batch_id:
        sql += " AND batch_id = ?"
        args.append(batch_id)
    done = 0
    for row in conn.execute(sql, args).fetchall():
        adapter = get_adapter(row["provider"])
        if not hasattr(adapter, "normalize"):
            continue
        resp = adapter.normalize(_request(row), json.loads(row["raw_json"]))
        norm = {k: v for k, v in resp.to_dict().items() if k != "raw"}
        old = json.loads(row["normalized_json"] or "{}")
        for k in ("collected_at", "latency_ms", "credits"):  # facts of the call, not of the payload
            norm[k] = old.get(k, row[k] if k in row.keys() else None)
        db.set_status(conn, row["id"], "collected", normalized_json=norm, model=resp.model)
        done += 1
    return done


def tick(conn, cfg: dict, project: str, batch_id: str | None = None, workers: int = 1,
         retry: bool = True, progress=None) -> dict:
    """One full pass of every stage. Safe to call repeatedly (cron / loop)."""
    released = db.release_stale(conn)
    collected = collect_planned(conn, project, batch_id, workers=workers, progress=progress)
    retried = 0
    if retry and (n := db.retry_failed(conn, batch_id)):
        retried = n
        more = collect_planned(conn, project, batch_id, workers=workers, progress=progress)
        collected = {k: collected[k] + more[k] for k in collected}
    entities = {cfg["name"]: cfg["aliases"], **{c["name"]: c["aliases"] for c in cfg.get("competitors", [])}}
    parsed = parse_collected(conn, cfg["domain"], entities, cfg["name"], project, batch_id)
    return {"released": released, **collected, "retried": retried, "parsed": parsed}
