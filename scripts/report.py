"""Funnel report for one or more batches: searched -> read -> cited -> named, per engine.

  python scripts/report.py --project parties247 --batch <id> [<id> ...]
  python scripts/report.py --project parties247 --label explore       # every batch whose label starts so
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from geo.core import db  # noqa: E402
from geo.report.funnel import build, render  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--batch", nargs="*", default=[])
    ap.add_argument("--label", help="include every batch of the project whose label starts with this")
    a = ap.parse_args()
    cfg = yaml.safe_load((ROOT / "projects" / a.project / "project.yaml").read_text(encoding="utf-8"))
    conn = db.connect(str(ROOT / "data" / "geo.db"))
    batches = list(a.batch)
    if a.label:
        batches += [r["id"] for r in conn.execute("SELECT id FROM batch WHERE project = ? AND label LIKE ?",
                                                    (a.project, a.label + "%"))]
    if not batches:
        sys.exit("no batches given or found")
    print(f"batches: {len(batches)}")
    print(render(build(conn, batches, cfg["domain"]), cfg["name"]))
