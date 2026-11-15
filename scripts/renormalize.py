"""Rebuild normalized data and all parsed tables from stored raw responses. No provider calls, no cost.

  python scripts/renormalize.py --project parties247 [--batch <batch_id>]

Run after changing an adapter's normalize() or the parser, so old runs get the new fields.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from geo.core import db  # noqa: E402
from geo.orchestrator.run import parse_collected, renormalize  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--batch")
    a = ap.parse_args()
    cfg = yaml.safe_load((ROOT / "projects" / a.project / "project.yaml").read_text(encoding="utf-8"))
    conn = db.connect(str(ROOT / "data" / "geo.db"))
    n = renormalize(conn, a.project, a.batch)
    ents = {cfg["name"]: cfg["aliases"], **{c["name"]: c["aliases"] for c in cfg.get("competitors", [])}}
    print(f"renormalized {n}, parsed {parse_collected(conn, cfg['domain'], ents, cfg['name'], a.project, a.batch)}")
