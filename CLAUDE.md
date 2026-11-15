# Arkenstone

Arkenstone is the core of a measurement-and-inference service for GEO (visibility of businesses inside AI answer engines:
ChatGPT, Gemini, AI Mode, Perplexity, Copilot). It is built first on the owner's own sites
(tachles-mashkanta, parties247, a solar site) and will later run for clients as a "service as a software".

The value of the product is **measurement quality and causal inference**, not data collection.
Collection is bought from providers; everything above it is ours.

Background documents (owner's Claude artifacts, read-only context):
- Research: https://claude.ai/artifact/WM9mbVjF6zNewCg3uc9zzL
- Service plan (measurement + inference design): https://claude.ai/artifact/63qt3AirLHXuEcAy8uw19B
- Business plan: https://claude.ai/artifact/5Pu2eHN6NeVZx7XPyMacHs

## Current phase: V0 – measurement

Goal of V0: know how noisy measurement is in Hebrew, and how many runs are needed to detect a
10 percentage-point change in citation/mention rate. Nothing else is built until that is answered.
The task list is in `docs/roadmap.md`. Start there.

## Architecture (see docs/architecture.md)

- Monorepo, Python 3.11+, modules with clear interfaces. **Not** separately deployed services.
- The database is the bus. Every run has a status and each stage moves runs forward:
  `planned → submitted → collected → parsed → scored`. The orchestrator is a loop/cron that runs
  each stage. Stages must be idempotent and resumable.
- Raw provider responses are stored forever, untouched. All metrics must be recomputable from raw.
- Generic code lives in `geo/`. Project-specific data lives in `projects/<name>/`
  (config, intents, sources, experiments, findings). Generic code never hardcodes a project.
- Every collection provider is an adapter in `geo/collector/adapters/` that maps to one
  normalized schema (`geo/core/schema.py`). Nothing outside the adapter knows which provider ran.

## Measurement rules (do not break)

1. Visibility is a distribution, never a single number. Every metric is reported with an interval.
2. A field a provider does not return is stored as `None` (unknown), never as empty/false.
   "No search queries returned" is not the same as "no search happened".
3. Always record: provider, engine, surface (consumer UI vs API), country, language, timestamp,
   model name when available, and whether web search was triggered / forced.
4. Never mix consumer-UI runs and API runs in the same metric.
5. Prompts sent to engines must be clean customer questions. Never append instructions like
   "tell me which sites you searched" to a measured prompt. Such probes are a separate run type.
6. Intent banks are versioned. Never edit a version that already has runs; create a new one.
7. Changing provider is a break in the series: run old and new in parallel for a few days first.
8. Keep the visibility stages apart, never merge them: searched → site read (retrieved) → site cited
   (linked) → site named. "Read but not cited" is its own outcome. Where an engine does not expose what it
   read, report "read: unknown", never assume.

## Conventions

- Secrets only in `.env` (see `.env.example`), never committed.
- Storage for V0: SQLite at `data/geo.db` (gitignored). Move to Postgres when there is a server.
- Keep modules small. Add a module only when the roadmap reaches it.
- Hebrew text: always UTF-8, never transliterate.

## Owner context

Solo builder, ~6–8 hours/week. Prefers concrete, closable tasks over open-ended ones:
every task in the roadmap should have a clear "done" condition.
