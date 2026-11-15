"""Small Bright Data trial, same clean prompts as the cloro exploration batches. Every HTTP call is counted.

  python scripts/brightdata_compare.py status                  # 2 calls: balance + free-tier credits
  python scripts/brightdata_compare.py trigger --engine chatgpt  # 1 call: one job with all prompts
  python scripts/brightdata_compare.py collect                 # 1 call per pending snapshot (202 = not ready)
  python scripts/brightdata_compare.py ledger                  # print the call log (no call)

Data and .env come from ARKENSTONE_HOME (default: repo root), so a worktree can use the main checkout's DB.
Collect never retries failed runs (a retry would be a new paid call); retry by hand if wanted.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ROOT = Path(__file__).resolve().parents[1]
HOME = Path(os.getenv("ARKENSTONE_HOME", ROOT))
load_dotenv(HOME / ".env")
os.environ.setdefault("BRIGHTDATA_LEDGER", str(HOME / "data" / "brightdata_calls.jsonl"))
os.environ.setdefault("BRIGHTDATA_RAW_DIR", str(HOME / "data" / "brightdata_raw"))

from geo.collector.adapters.brightdata import BrightDataAdapter, calls_used  # noqa: E402
from geo.core import db  # noqa: E402
from geo.core.schema import RunRequest  # noqa: E402
from geo.orchestrator.run import parse_collected, poll_submitted  # noqa: E402

# Same prompts as the cloro explore-* batches
PROMPTS = {"tachles-mashkanta": ["refinance-calculator:1", "advisor-recommend:1"],
           "parties247": ["parties-weekend-israel:1", "parties-tonight-tlv:1"]}


def project(name):
    pdir = ROOT / "projects" / name
    cfg = yaml.safe_load((pdir / "project.yaml").read_text(encoding="utf-8"))
    bank = yaml.safe_load((pdir / "intents.yaml").read_text(encoding="utf-8"))
    return cfg, bank


def cmd_trigger(conn, engine):
    planned = []  # (run_id, RunRequest)
    for name, specs in PROMPTS.items():
        cfg, bank = project(name)
        by_id = {it["id"]: it for it in bank["intents"]}
        params = {"web_search": True} if engine == "chatgpt" else {}
        b = db.create_batch(conn, name, f"bd-{engine}", {"provider": "brightdata", "engine": engine,
                            "params": params, "intent_version": str(bank.get("version")), "prompts": specs})
        for spec in specs:
            iid, _, idx = spec.partition(":")
            req = RunRequest(project=name, intent_id=iid, prompt_id=f"{iid}-{idx}", prompt=by_id[iid]["prompts"][int(idx)],
                             engine=engine, country=cfg.get("country", "IL"), language=cfg.get("language", "he"),
                             web_search="natural")  # web_search=true is a permission, not forced
            rid = db.plan_run(conn, req, "brightdata", intent_version=str(bank.get("version")), batch_id=b,
                              params=params)
            if not db.claim(conn, rid):
                raise SystemExit(f"could not claim {rid}")
            planned.append((rid, req))
        print(f"batch {b} ({name})")
    try:
        sid = BrightDataAdapter().trigger([r for _, r in planned])
    except Exception as e:  # never leave claimed runs that a later tick could re-collect synchronously
        for rid, _ in planned:
            db.set_status(conn, rid, "failed", error="[permanent] trigger: " + repr(e)[:1500])
        raise
    for i, (rid, _) in enumerate(planned):
        conn.execute("UPDATE run SET provider_task_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (f"{sid}#{i + 1}", rid))
    conn.commit()
    print(f"snapshot {sid}: {len(planned)} prompts; calls used {calls_used()}")


def cmd_collect(conn):
    rows = conn.execute("SELECT DISTINCT batch_id, project FROM run WHERE provider = 'brightdata' "
                        "AND status IN ('submitted', 'collected')").fetchall()
    for r in rows:
        out = poll_submitted(conn, r["project"], r["batch_id"], workers=1)
        cfg, _ = project(r["project"])
        entities = {cfg["name"]: cfg["aliases"], **{c["name"]: c["aliases"] for c in cfg.get("competitors", [])}}
        parsed = parse_collected(conn, cfg["domain"], entities, cfg["name"], r["project"], r["batch_id"])
        print(f"{r['batch_id']} ({r['project']}): {out}, parsed {parsed}")
    print(f"calls used {calls_used()}")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["status", "trigger", "collect", "ledger"])
    ap.add_argument("--engine", choices=["chatgpt", "gemini", "ai_mode"])
    args = ap.parse_args()
    conn = db.connect(str(HOME / "data" / "geo.db"))
    if args.cmd == "status":
        print(json.dumps(BrightDataAdapter().account_status(), indent=1))
        print(f"calls used {calls_used()}")
    elif args.cmd == "trigger":
        cmd_trigger(conn, args.engine)
    elif args.cmd == "collect":
        cmd_collect(conn)
    else:
        p = Path(os.environ["BRIGHTDATA_LEDGER"])
        print(p.read_text(encoding="utf-8") if p.exists() else "(no calls yet)")


if __name__ == "__main__":
    main()
