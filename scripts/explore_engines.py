"""Explore engines: the same prompts on every engine a provider offers, through the real pipeline.

  python scripts/explore_engines.py --prompts refinance-calculator:1 advisor-recommend:1 --dry
  python scripts/explore_engines.py --prompts refinance-calculator:1 advisor-recommend:1
  python scripts/explore_engines.py --report <batch_id>

--prompts takes intent_id:variant_index from the project's intent bank. --dry prints the plan and
the credit estimate without calling anything. One batch per engine, so engines are never mixed.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import yaml
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from geo.core import db  # noqa: E402
from geo.core.schema import RunRequest  # noqa: E402
from geo.orchestrator.run import collect_planned, run_until_done  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
# cloro credits per run with our default flags (docs 2026-11-15): async default, sync is +2
CREDITS = {"chatgpt": 7, "gemini": 4, "ai_mode": 4, "copilot": 4, "ai_overview": 5}


def report(conn, batch_ids: list[str], cfg: dict) -> None:
    for b in batch_ids:
        rows = conn.execute("SELECT * FROM run WHERE batch_id = ? ORDER BY prompt_id", (b,)).fetchall()
        if not rows:
            continue
        params = json.loads(rows[0]["params_json"] or "{}")
        print(f"\n=== {rows[0]['engine']}  ({b}{', ' + json.dumps(params) if params else ''})")
        for r in rows:
            if r["status"] != "parsed":
                print(f"  [{r['status']}] {r['prompt'][:40]}  {r['error'] or ''}")
                continue
            n = json.loads(r["normalized_json"])
            raw = json.loads(r["raw_json"])
            raw_res = (raw.get("result") if "result" in raw else raw.get("response")) or {}  # sync | async
            if r["engine"] == "ai_overview":
                raw_res = raw_res.get("aioverview") or {}
            srcs = n.get("sources")
            inline = sum(1 for s in srcs or [] if s.get("cited_inline"))
            domains = [s["domain"] for s in srcs or []]
            ments = [m["entity"] for m in conn.execute(
                "SELECT entity FROM mention WHERE run_id = ? ORDER BY position", (r["id"],))]
            target = any(d == cfg["domain"] for d in domains)
            print(f"  prompt: {r['prompt']}")
            print(f"    answer: {len(n.get('answer_text') or '')} chars | sources: "
                  f"{'None' if srcs is None else len(srcs)} ({inline} inline) | queries: {n.get('search_queries')}")
            print(f"    model: {n.get('model')} | search triggered: {n.get('web_search_triggered')} "
                  f"(forced {n.get('web_search_forced')}) | latency {r['latency_ms']} ms | credits {r['credits']}")
            ret = conn.execute("SELECT domain, position, cited FROM retrieval WHERE run_id = ? ORDER BY position",
                               (r["id"],)).fetchall()
            tgt = [(x["position"], "cited" if x["cited"] else "read only") for x in ret if x["domain"] == cfg["domain"]]
            ents = [x["name"] for x in conn.execute(
                "SELECT name FROM engine_entity WHERE run_id = ? ORDER BY position", (r["id"],))]
            print(f"    read: {len(ret) if n.get('retrieved') is not None else 'unknown'} pages, "
                  f"{sum(x['cited'] for x in ret)} cited | target: {tgt or 'not read'} | location: {n.get('user_location')}")
            print(f"    target in sources: {target} | our alias mentions: {ments} | engine entities: {ents}")
            print(f"    top domains: {Counter(domains).most_common(6)}")
            print(f"    raw result keys: {sorted(k for k, v in raw_res.items() if v not in (None, [], {}, ''))}")
            print(f"    answer head: {(n.get('answer_text') or '')[:160]!r}")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="tachles-mashkanta")
    ap.add_argument("--provider", default="cloro")
    ap.add_argument("--engines", nargs="+", default=list(CREDITS))
    ap.add_argument("--prompts", nargs="+", default=[], help="intent_id:variant_index")
    ap.add_argument("--params", default="{}", help='adapter options as JSON, e.g. \'{"legacy": true}\'')
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--report", nargs="+", metavar="BATCH_ID")
    args = ap.parse_args()

    pdir = ROOT / "projects" / args.project
    cfg = yaml.safe_load((pdir / "project.yaml").read_text(encoding="utf-8"))
    conn = db.connect(str(ROOT / "data" / "geo.db"))
    if args.report:
        report(conn, args.report, cfg)
        return

    bank = yaml.safe_load((pdir / "intents.yaml").read_text(encoding="utf-8"))
    by_id = {it["id"]: it for it in bank["intents"]}
    chosen = []
    for spec in args.prompts:
        iid, _, idx = spec.partition(":")
        chosen.append((iid, int(idx or 1), by_id[iid]["prompts"][int(idx or 1)]))
    params = json.loads(args.params)
    total = sum(CREDITS.get(e, 0) + (2 if params.get("sync") else 0) for e in args.engines) * len(chosen)
    print(f"{len(chosen)} prompts x {len(args.engines)} engines = {len(chosen) * len(args.engines)} runs, "
          f"~{total} credits")
    for iid, idx, p in chosen:
        print(f"  {iid}:{idx}  {p}")
    if args.dry:
        return

    batches = []
    for engine in args.engines:
        b = db.create_batch(conn, args.project, f"explore-{engine}", {
            "provider": args.provider, "engine": engine, "params": params,
            "intent_version": str(bank.get("version")), "prompts": args.prompts})
        for iid, idx, p in chosen:
            req = RunRequest(project=args.project, intent_id=iid, prompt_id=f"{iid}-{idx}", prompt=p, engine=engine,
                             country=cfg.get("country", "IL"), language=cfg.get("language", "he"))
            db.plan_run(conn, req, args.provider, intent_version=str(bank.get("version")), batch_id=b, params=params)
        batches.append(b)
    for b in batches:  # async: queue everything at cloro first, then collect
        print(f"{b}: {collect_planned(conn, args.project, b)}", flush=True)
    for b in batches:
        out = run_until_done(conn, cfg, args.project, b, workers=1,
                             progress=lambda row, err: print(f"  {row['engine']}: {'ok' if err is None else err}",
                                                             flush=True))
        print(f"{b}: {out}", flush=True)
    print("\nbatches:", " ".join(batches))
    report(conn, batches, cfg)


if __name__ == "__main__":
    main()
