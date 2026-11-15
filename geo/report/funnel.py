"""Visibility funnel per engine: searched -> target read -> target cited -> target named (rule 8).

Each stage is a rate over the runs where that stage is KNOWN, with a Wilson interval; runs where it is
unknown (engine does not expose it) are counted separately, never assumed. Also lists who wins each stage.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from geo.estimator.rates import wilson
from geo.parser.normalize import domain_of


@dataclass
class Stage:
    name: str
    hits: int = 0
    known: int = 0
    unknown: int = 0

    def rate(self):
        return wilson(self.hits, self.known)


@dataclass
class EngineFunnel:
    engine: str
    runs: int = 0
    stages: dict[str, Stage] = field(default_factory=dict)
    params: set[str] = field(default_factory=set)
    read_positions: list[int] = field(default_factory=list)
    read_pages: list[int] = field(default_factory=list)
    top_read: Counter = field(default_factory=Counter)
    top_cited: Counter = field(default_factory=Counter)
    top_named: Counter = field(default_factory=Counter)


STAGES = ("searched", "read", "cited", "named")


def build(conn, batch_ids: list[str], target_domain: str) -> dict[str, EngineFunnel]:
    q = ",".join("?" * len(batch_ids))
    rows = conn.execute(f"SELECT * FROM run WHERE batch_id IN ({q}) AND status = 'parsed'", batch_ids).fetchall()
    out: dict[str, EngineFunnel] = {}
    for r in rows:
        f = out.setdefault(r["engine"], EngineFunnel(r["engine"], stages={s: Stage(s) for s in STAGES}))
        f.runs += 1
        f.params.add(f"{r['provider']} {r['surface']} {r['country']} {r['intent_version']} {r['params_json'] or '{}'}")
        n = json.loads(r["normalized_json"])
        rid = r["id"]

        def add(stage, value):
            st = f.stages[stage]
            if value is None:
                st.unknown += 1
            else:
                st.known += 1
                st.hits += int(bool(value))

        add("searched", n.get("web_search_triggered"))

        ret = conn.execute("SELECT domain, position, cited FROM retrieval WHERE run_id = ? ORDER BY position",
                           (rid,)).fetchall()
        if n.get("retrieved") is not None:
            mine = [x for x in ret if x["domain"] == target_domain]
            add("read", bool(mine))
            if mine:
                f.read_positions.append(mine[0]["position"])
            f.read_pages.append(len(ret))
            for d in {x["domain"] for x in ret}:
                f.top_read[d] += 1
            cited_domains = {x["domain"] for x in ret if x["cited"]}
        else:
            add("read", None)
            # Without the read set, "cited" = shown as a source (inline-linked where the engine says so).
            cited_domains = {domain_of(s.get("url", "")) for s in n.get("sources") or []
                             if s.get("cited_inline") is not False}
        if n.get("sources") is None and n.get("retrieved") is None:
            add("cited", None)
        else:
            add("cited", target_domain in cited_domains)
            for d in cited_domains:
                f.top_cited[d] += 1

        named_alias = conn.execute("SELECT entity, is_target FROM mention WHERE run_id = ?", (rid,)).fetchall()
        named_engine = conn.execute("SELECT name, is_target FROM engine_entity WHERE run_id = ?", (rid,)).fetchall()
        add("named", n.get("answer_text") is not None and
            (any(x["is_target"] for x in named_alias) or any(x["is_target"] for x in named_engine)))
        for name in {x["entity"] for x in named_alias} | {x["name"] for x in named_engine}:
            f.top_named[name] += 1
    return out


LABEL = {"searched": "חיפש באינטרנט", "read": "קרא את האתר", "cited": "ציטט (קישר)", "named": "הזכיר בשם"}


def render(funnels: dict[str, EngineFunnel], target_name: str) -> str:
    lines = []
    for f in funnels.values():
        lines.append(f"\n{target_name} | {f.engine} | {f.runs} ריצות")
        if len(f.params) > 1:
            lines.append("  ⚠ מעורבבות ריצות עם הגדרות שונות, לא להשוות כמדידה אחת:")
            lines.extend(f"     {p}" for p in sorted(f.params))
        prev = None
        drops = []
        for s in STAGES:
            st = f.stages[s]
            if st.known == 0:
                lines.append(f"  {LABEL[s]:<14} לא ידוע (הצ'אט לא חושף)")
                continue
            p, lo, hi = st.rate()
            extra = f"  +{st.unknown} לא ידוע" if st.unknown else ""
            if s == "read" and f.read_positions:
                extra += (f"  מקום ממוצע: {sum(f.read_positions) / len(f.read_positions):.0f} "
                          f"מתוך {sum(f.read_pages) / len(f.read_pages):.0f}")
            lines.append(f"  {LABEL[s]:<14} {st.hits}/{st.known}  {p:4.0%}  ({lo:.0%}–{hi:.0%}){extra}")
            if prev is not None:
                drops.append((prev - p, s))
            prev = p
        if drops:
            d, s = max(drops)
            if d > 0:
                lines.append(f"  → הנפילה הגדולה: לפני '{LABEL[s]}' (ירידה של {d:.0%})")
        for label, c in (("נקראו הכי הרבה", f.top_read), ("צוטטו הכי הרבה", f.top_cited),
                         ("הוזכרו בשם", f.top_named)):
            if c:
                lines.append(f"  {label}: " + ", ".join(f"{k} {v}/{f.runs}" for k, v in c.most_common(6)))
    return "\n".join(lines)
