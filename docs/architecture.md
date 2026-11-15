# Architecture

## Modules

| Module | Responsibility | Phase |
| --- | --- | --- |
| `geo/core` | Normalized schema, database, run statuses | V0 |
| `geo/collector/adapters` | One adapter per provider (cloro, brightdata, openai_api ...) | V0 |
| `geo/parser` | Normalize URLs/domains, extract mentions, search queries, citations | V0 |
| `geo/estimator` | Rates with intervals, later hierarchical model + variance decomposition | V0 |
| `geo/orchestrator` | Moves runs through statuses | V0 |
| `geo/connectors` | GSC, GA4, Cloudflare AI-bot logs, change log, conversions | V0 (GSC, bots, changes) |
| `geo/profile` | Business profile: structured fact sheet with provenance + raw source store + "what AI thinks" | V1 (manual file in V0) |
| `geo/intents` | Profile + GSC + competitors → intents → prompt variants (LLM, human-approved) | V1 (manual in V0) |
| `geo/planner` | Which runs to execute this cycle, adaptive sampling under a budget | V1 |
| `geo/report` | Human-readable output | V1 |
| diagnosis / experiments / executor | Pipeline-stage diagnosis, pre-registered experiments, site changes | V2+ |

## Run lifecycle

```
planned → submitted → collected → parsed → scored
                 ↘ failed (with error, retried by orchestrator)
```

- `submitted` is a claim taken before the provider is called, so no run is paid for twice. A run stuck in
  `submitted` (crash mid-call) is released after 15 minutes (`db.release_stale`).
- `failed` runs are retried up to 3 attempts; permanent errors (bad request, no credits) are not retried.
- Async providers will return a task id at `submitted`; the orchestrator then polls until `collected`.
- Re-parsing old data = move runs back to `collected` (mentions/citations are replaced, never duplicated).
- Every run belongs to a **batch** (one planned set with fixed parameters, stored in `batch.params_json`).
  Metrics are computed per batch, never across batches.
- `orchestrator.run.tick()` runs every stage once; calling it again after any crash continues the work.
- Tested without network in `tests/test_pipeline.py` (fake provider): `python -m pytest`.

## Units

project → intent → prompt (variant) → run.
A run = prompt × engine × surface × country × language × time × provider.

## Normalized response (see geo/core/schema.py)

- `answer_text`
- `sources[]`: url, domain, title, position, cited_inline (bool | None)
- `search_queries[]` or None if the provider did not report them
- `web_search_triggered`: bool | None, and `web_search_forced`: bool
- `model`, `provider`, `engine`, `surface`, `country`, `language`, `collected_at`
- `raw`: the untouched provider payload

## Parsed tables (all rebuilt from raw by `scripts/renormalize.py`)

| Table | One row per | Meaning |
| --- | --- | --- |
| `citation` | shown source | what the user sees as a source (as the provider lists it) |
| `retrieval` | page the engine read | superset of citations; `cited` = shown as a citation (ChatGPT: linked in the text) |
| `search_query` | fan-out query | the engine's own searches, in order (ChatGPT only so far) |
| `engine_entity` | entity the engine tagged | businesses the engine itself named (ChatGPT) |
| `mention` | alias match in answer text | our own detection, project aliases |

The diagnostic chain per run: searched? → target read (retrieval)? → target cited? → target named?

## Metrics (V0)

- mention rate: share of runs where the business is named in the answer
- citation rate: share of runs where the business domain is in sources
- search trigger rate (only meaningful when search is not forced)
- fan-out coverage (later): share of fan-out queries where the site ranks
Each with a Wilson / Beta interval. Later: logistic mixed model with random effects for
prompt variant, day and engine.
