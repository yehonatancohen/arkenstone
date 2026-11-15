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
- Done when: raw responses for 20 prompts × 3 reps are in `data/geo.db` and the summary prints.

### Step 2 – provider checklist
For each provider record in `docs/providers.md`:
- engines, Israel/Hebrew support, is web search forced or natural, returns fan-out queries?,
  returns model?, multi-turn support?, price per result, latency, failure rate.
- [ ] Implement `brightdata` adapter (it reports `web_search_triggered`, natural mode)
- [ ] Run the same 20 prompts on both providers; compare overlap of cited domains
- Done when: the table is filled for 2 providers.

### Step 3 – baseline
- [ ] 10 runs per variant spread across 7 days (planner can be a simple script)
- [ ] Estimator: rates with intervals per intent; variance split variant vs day vs run (simple ANOVA-style first)
- [ ] Anchor panel: 10 intents in a domain we don't touch, run on the same schedule
- Done when: we can state the runs needed for a 10-point MDE and the monthly cost.

### Step 4 – site signals
- [ ] GSC connector (service account, daily snapshot into `site_signal`)
- [ ] Cloudflare Worker that logs verified AI-bot requests (OAI-SearchBot, ChatGPT-User, PerplexityBot,
      Claude-SearchBot ...) into the DB; verify by published IP ranges
- [ ] Change log: the existing site-update loop writes a row before every change

## V1 – diagnosis
Pipeline-stage breakdown per intent (search triggered → retrieved → cited), fan-out ranking check,
automated intents from profile, report.

## V2 – experiments
Pre-registered experiments, random page assignment / diff-in-diff, hierarchical model,
findings with expiry, promotion of findings across projects.

## V3 – design partners, V4 – agency
See business plan.
