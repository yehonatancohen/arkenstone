"""Calibration report: per engine, per domain (our site + whoever appears), how often it is read / cited, and how
much of the variation is real (differs by prompt or day) vs pure run-to-run noise.

For a domain and a stage, every run gives 0/1. Grouping runs by prompt:
  within  = mean of p_i(1-p_i) over prompts  -> noise: same prompt, same day, different answer
  between = variance of p_i over prompts      -> signal: some prompts favour the domain
  share_noise = within / (within + between)
Then by day the same way (day effect = drift). runs_for_mde gives a LOWER bound on runs per arm for a 10-point change.
Stages where the engine does not expose the data (read on Gemini/Perplexity) are reported as unknown, never 0.
"""
from __future__ import annotations

import json
from collections import defaultdict
from statistics import mean, pvariance

from geo.estimator.rates import runs_for_mde, wilson
from geo.parser.normalize import domain_of


def _runs(conn, project: str, engine: str):
    return conn.execute(
        "SELECT r.*, b.label FROM run r JOIN batch b ON b.id = r.batch_id WHERE b.project = ? AND b.label LIKE ? "
        "AND r.status = 'parsed'", (project, f"calib-{engine}-%")).fetchall()


def stage_sets(conn, row) -> dict[str, set[str] | None]:
    n = json.loads(row["normalized_json"])
    ret = conn.execute("SELECT domain, cited FROM retrieval WHERE run_id = ?", (row["id"],)).fetchall()
    read = {x["domain"] for x in ret} if n.get("retrieved") is not None else None
    if n.get("retrieved") is not None:
        cited = {x["domain"] for x in ret if x["cited"]}
    elif n.get("sources") is not None:
        cited = {domain_of(s.get("url", "")) for s in n["sources"] if s.get("cited_inline") is not False}
    else:
        cited = None
    return {"read": read, "cited": cited}


def _split(values_by_group: dict[str, list[int]]) -> float | None:
    ps = [mean(v) for v in values_by_group.values() if v]
    if len(ps) < 2:
        return None
    within = mean(p * (1 - p) for p in ps)
    between = pvariance(ps)
    return within / (within + between) if within + between > 0 else None


def build(conn, project: str, engine: str, focus: list[str], top: int = 8) -> dict:
    rows = _runs(conn, project, engine)
    per_run = [(r["prompt_id"], r["label"].rsplit("-", 3)[-3:], stage_sets(conn, r)) for r in rows]
    days = sorted({"-".join(d) for _, d, _ in per_run})
    out = {"engine": engine, "runs": len(rows), "days": days, "stages": {}}
    for stage in ("read", "cited"):
        known = [(pid, "-".join(d), s[stage]) for pid, d, s in per_run if s[stage] is not None]
        if not known:
            out["stages"][stage] = None
            continue
        counts = defaultdict(int)
        for _, _, s in known:
            for d in s:
                counts[d] += 1
        domains = list(dict.fromkeys(focus + [d for d, _ in sorted(counts.items(), key=lambda x: -x[1])[:top]]))
        res = []
        for d in domains:
            hits = sum(d in s for _, _, s in known)
            p, lo, hi = wilson(hits, len(known))
            by_prompt, by_day = defaultdict(list), defaultdict(list)
            for pid, day, s in known:
                by_prompt[pid].append(int(d in s))
                by_day[day].append(int(d in s))
            res.append({"domain": d, "hits": hits, "n": len(known), "p": p, "lo": lo, "hi": hi,
                        "noise_share_prompt": _split(by_prompt), "noise_share_day": _split(by_day),
                        "runs_for_10pt": runs_for_mde(p) if 0 < p < 0.9 else None})
        out["stages"][stage] = res
    return out


LABEL = {"read": "קרא", "cited": "ציטט"}


def render(rep: dict, focus: list[str]) -> str:
    lines = [f"\n== {rep['engine']}: {rep['runs']} ריצות, {len(rep['days'])} ימים ({', '.join(rep['days'])})"]
    for stage, res in rep["stages"].items():
        if res is None:
            lines.append(f"  {LABEL[stage]}: לא ידוע (הצ'אט לא חושף)")
            continue
        lines.append(f"  {LABEL[stage]}:  אתר | שיעור (טווח 95%) | חלק הרעש בתוך אותה שאלה | ריצות ל־10 נק'")
        for x in res:
            mark = "★" if x["domain"] in focus else " "
            ns = "—" if x["noise_share_prompt"] is None else f"{x['noise_share_prompt']:.0%}"
            rr = "—" if x["runs_for_10pt"] is None else str(x["runs_for_10pt"])
            lines.append(f"   {mark}{x['domain']:<28} {x['hits']:>3}/{x['n']:<3} {x['p']:4.0%} "
                         f"({x['lo']:.0%}–{x['hi']:.0%})  רעש {ns:>4}  {rr}")
    return "\n".join(lines)
