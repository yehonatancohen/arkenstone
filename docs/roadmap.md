# Roadmap

## V0 – measurement (current)

Question V0 must answer: how many runs per intent are needed to detect a 10-point change
in citation/mention rate for Hebrew questions, and at what monthly cost?

### Step 1 – smoke test (this week)
- [ ] Owner: open a trial account with cloro (and optionally Bright Data), put keys in `.env`
- [ ] Verify cloro accepts `country: "IL"` and Hebrew prompts (`python scripts/smoke_test.py --dry-check`)
- [ ] Owner: replace the draft intents in `projects/tachles-mashkanta/intents.yaml` with 20 real ones,
      3 variants each
- [ ] Run `scripts/smoke_test.py --project tachles-mashkanta --provider cloro --reps 3` on 20 prompts
- [ ] Check which fields actually come back (sources, searchQueries, model). Record in docs/providers.md
- [x] Hand-written intent bank v1 (20 intents × 3 variants) from `intent_recipe.yaml`
- [ ] Gemini generator (`scripts/generate_intents.py`, pulled forward from V1 at owner's request):
      generate from the same recipe, compare with `--compare`. Needs `GEMINI_API_KEY`.
      Done when: a generated bank passes the rule check and the owner has read the side-by-side.
- Done when: raw responses for 20 prompts × 3 reps are in `data/geo.db` and the summary prints.

### Step 2 – provider checklist
For each provider record in `docs/providers.md`:
- engines, Israel/Hebrew support, is web search forced or natural, returns fan-out queries?,
  returns model?, multi-turn support?, price per result, latency, failure rate.
- [x] Implement `brightdata` adapter (it reports `web_search_triggered`, natural mode)
- [ ] Owner: replace `BRIGHTDATA_API_KEY` in `.env` (currently a copy of the Gemini key), then run the 4-prompt ChatGPT comparison (`scripts/brightdata_compare.py`)
- [ ] Run the same 20 prompts on both providers; compare overlap of cited domains
- Done when: the table is filled for 2 providers.

### Step 3 – baseline
- [ ] 10 runs per variant spread across 7 days (planner can be a simple script)
- [ ] Estimator: rates with intervals per intent; variance split variant vs day vs run (simple ANOVA-style first)
- [ ] Anchor panel: 10 intents in a domain we don't touch, run on the same schedule
- Done when: we can state the runs needed for a 10-point MDE and the monthly cost.

### Step 4 – site signals
- [ ] GSC connector (service account, daily snapshot into `site_signal`). Also export real queries to
      `projects/<p>/sources/search_queries.txt` as seeds for the intent generator.
- [ ] Cloudflare Worker that logs verified AI-bot requests (OAI-SearchBot, ChatGPT-User, PerplexityBot,
      Claude-SearchBot ...) into the DB; verify by published IP ranges
- [ ] Change log: the existing site-update loop writes a row before every change

## Open question (future) – what do people actually ask AI chats?
Google data (GSC, keyword volumes) shows what people type into Google. That is a hint, not the real
distribution of questions asked to ChatGPT/Gemini. Owner decided (2026-11-15) not to invest in Google
data now. Later candidates to look at, none chosen:
- Fan-out queries and follow-up suggestions that engines return (cloro `searchQueries`, `followUpForm`)
- AI-bot logs on our own sites (ChatGPT-User / Perplexity-User hit a page = a live user question needed it)
- Panel / clickstream products that sell prompt volumes; public chat datasets (Hebrew subset)
- An on-site chat or calculator search box on our own sites (questions in the user's own words)
Done when: one source is chosen and tested against the hand-written intent bank.

## V1 – diagnosis
Pipeline-stage breakdown per intent (search triggered → retrieved → cited), fan-out ranking check,
automated intents from profile, report.

## V2 – experiments
Pre-registered experiments, random page assignment / diff-in-diff, hierarchical model,
findings with expiry, promotion of findings across projects.

## V3 – design partners, V4 – agency
See business plan.
