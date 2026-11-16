"""Calibration (noise test): one round per day, the same prompts on each engine's primary provider.

  python scripts/calibrate.py --project parties247 --dry     # plan + cost, no calls
  python scripts/calibrate.py --project parties247           # run today's round (skips if already run today)
  python scripts/calibrate.py --project parties247 --status  # rounds done per engine
  python scripts/calibrate.py --project parties247 --report  # noise per domain (read / cited)

Plan lives in projects/<p>/calibration.yaml. Each round = one batch per engine, labelled calib-<engine>-<YYYY-MM-DD>,
so engines and providers are never mixed in one batch (rules 4 and 7). Safe to rerun: a day that already has a
batch for an engine is resumed (run_until_done), not planned twice.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import yaml
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from geo.core import db  # noqa: E402
from geo.core.schema import RunRequest  # noqa: E402
from geo.orchestrator.run import collect_planned, run_until_done  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
COST = {"brightdata": ("records", 1), "scrapellm": ("credits", 3), "cloro": ("credits", 7)}


def prompts_for(bank: dict, variants) -> list[tuple[str, int, str]]:
    out = []
    for it in bank["intents"]:
        idxs = range(len(it["prompts"])) if variants == "all" else [i for i in variants if i < len(it["prompts"])]
        out += [(it["id"], i, it["prompts"][i]) for i in idxs]
    return out


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--engines", nargs="+", help="subset of the plan's engines")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--report", action="store_true", help="noise report per domain (our site + top domains)")
    ap.add_argument("--day", default=date.today().isoformat(), help="round label (default today)")
    args = ap.parse_args()

    pdir = ROOT / "projects" / args.project
    cfg = yaml.safe_load((pdir / "project.yaml").read_text(encoding="utf-8"))
    plan = yaml.safe_load((pdir / "calibration.yaml").read_text(encoding="utf-8"))
    bank = yaml.safe_load((pdir / "intents.yaml").read_text(encoding="utf-8"))
    version = str(bank.get("version"))
    conn = db.connect(str(ROOT / "data" / "geo.db"))
    engines = {e: v for e, v in plan["engines"].items() if not args.engines or e in args.engines}

    if args.report:
        from geo.report import calibration
        focus = [cfg["domain"]] + [d for d in cfg.get("competitor_domains") or []]
        for e in engines:
            print(calibration.render(calibration.build(conn, args.project, e, focus), focus))
        return

    if args.status:
        for e in engines:
            rows = conn.execute("SELECT b.label, r.status, COUNT(*) n FROM batch b JOIN run r ON r.batch_id = b.id "
                                "WHERE b.project = ? AND b.label LIKE ? GROUP BY b.label, r.status ORDER BY b.label",
                                (args.project, f"calib-{e}-%")).fetchall()
            days = sorted({r["label"] for r in rows})
            print(f"{e}: {len(days)}/{plan['rounds']} rounds")
            for r in rows:
                print(f"   {r['label']}  {r['status']}: {r['n']}")
        return

    batches = []
    for e, spec in engines.items():
        chosen = prompts_for(bank, spec.get("variants", "all"))
        n = len(chosen) * spec["reps"]
        unit, per = COST[spec["provider"]]
        print(f"{e} via {spec['provider']}: {len(chosen)} prompts x {spec['reps']} reps = {n} runs, "
              f"~{n * per} {unit} this round, ~{n * per * plan['rounds']} for {plan['rounds']} rounds")
        if args.dry:
            continue
        label = f"calib-{e}-{args.day}"
        old = conn.execute("SELECT id FROM batch WHERE project = ? AND label = ?", (args.project, label)).fetchone()
        if old:
            print(f"  {label} exists: resuming {old['id']}")
            batches.append(old["id"])
            continue
        done = conn.execute("SELECT COUNT(*) n FROM batch WHERE project = ? AND label LIKE ?",
                            (args.project, f"calib-{e}-%")).fetchone()["n"]
        if done >= plan["rounds"]:
            print(f"  {e}: all {plan['rounds']} rounds done, skipping (calibration finished)")
            continue
        b = db.create_batch(conn, args.project, label, {"provider": spec["provider"], "engine": e, "kind": "calibration",
                                                        "intent_version": version, "reps": spec["reps"]})
        # an id-unique label keeps create_batch's random suffix out of the lookup above
        conn.execute("UPDATE batch SET label = ? WHERE id = ?", (label, b))
        conn.commit()
        for rep in range(spec["reps"]):
            for iid, idx, p in chosen:
                req = RunRequest(project=args.project, intent_id=iid, prompt_id=f"{iid}-{idx}", prompt=p, engine=e,
                                 country=cfg.get("country", "IL"), language=cfg.get("language", "he"))
                # no per-run params: they are sent to the adapter and would split the funnel's settings check
                db.plan_run(conn, req, spec["provider"], intent_version=version, batch_id=b)
        batches.append(b)
    if args.dry:
        return
    for b in batches:
        print(f"{b}: submitted {collect_planned(conn, args.project, b, workers=4)}", flush=True)
    for b in batches:
        out = run_until_done(conn, cfg, args.project, b, workers=4, poll_every=20,
                             progress=lambda row, err: err and print(f"  {row['engine']} fail: {str(err)[:120]}",
                                                                     flush=True))
        print(f"{b}: {json.dumps(out)}", flush=True)


if __name__ == "__main__":
    main()
