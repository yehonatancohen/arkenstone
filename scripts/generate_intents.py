"""Generate a candidate intent bank with Gemini, check it, compare it with the hand-written bank.

  python scripts/generate_intents.py --list-models
  python scripts/generate_intents.py --project tachles-mashkanta            # -> intents.generated.yaml
  python scripts/generate_intents.py --project tachles-mashkanta --check intents.yaml
  python scripts/generate_intents.py --project tachles-mashkanta --compare  # hand vs generated

Optional seeds: projects/<project>/sources/search_queries.txt (one real search query per line).
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import yaml
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from geo.intents.generate import check, generate, list_models, side_by_side  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="tachles-mashkanta")
    ap.add_argument("--model", default=None)
    ap.add_argument("--out", default="intents.generated.yaml")
    ap.add_argument("--list-models", action="store_true")
    ap.add_argument("--no-refine", action="store_true", help="skip the second (editor) pass")
    ap.add_argument("--check", metavar="FILE", help="only run rule checks on a bank file in the project dir")
    ap.add_argument("--compare", action="store_true", help="side by side: intents.yaml vs --out file")
    args = ap.parse_args()

    if args.list_models:
        print("\n".join(list_models()))
        return

    pdir = ROOT / "projects" / args.project
    recipe_text = (pdir / "intent_recipe.yaml").read_text(encoding="utf-8")
    recipe = yaml.safe_load(recipe_text)
    load = lambda name: yaml.safe_load((pdir / name).read_text(encoding="utf-8"))  # noqa: E731

    if args.check:
        problems = check(load(args.check), recipe)
        print("\n".join(problems) or "clean")
        return
    if args.compare:
        hand, gen = load("intents.yaml"), load(args.out)
        print("A = intents.yaml (hand)   B =", args.out)
        print(side_by_side(hand, gen, recipe))
        for label, bank in (("A", hand), ("B", gen)):
            problems = check(bank, recipe)
            print(f"\n{label} rule check: {len(problems)} problems")
            print("\n".join("  " + p for p in problems))
        return

    seeds_file = pdir / "sources" / "search_queries.txt"
    seeds = [s.strip() for s in seeds_file.read_text(encoding="utf-8").splitlines() if s.strip()] \
        if seeds_file.exists() else []
    brief_file = pdir / "market_brief.md"  # neutral brief; never the profile (it leaks selling points)
    brief = brief_file.read_text(encoding="utf-8")
    bank = generate(recipe_text, brief, seeds, model=args.model, refine=not args.no_refine, recipe=recipe)
    out = {"version": f"gen-{date.today().isoformat()}", "recipe_version": recipe.get("version"),
           "status": "candidate - needs human approval before runs", **bank}
    (pdir / args.out).write_text(yaml.safe_dump(out, allow_unicode=True, sort_keys=False), encoding="utf-8")
    g = bank["generator"]
    print(f"wrote {pdir / args.out}  ({len(bank.get('intents', []))} intents, model {g['model']}, "
          f"repairs {g['repairs']}, tokens in/out {g['tokens_in']}/{g['tokens_out']})")
    problems = check(bank, recipe)
    print("\n".join(problems) or "rule check: clean")


if __name__ == "__main__":
    main()
