# Arkenstone

Measurement and inference for visibility in AI answer engines. See `CLAUDE.md` for context and rules,
`docs/architecture.md` for design, `docs/roadmap.md` for the current tasks.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env   # add provider keys
python scripts/smoke_test.py --dry-check
python scripts/smoke_test.py --project tachles-mashkanta --provider cloro --reps 3 --limit 5
```
