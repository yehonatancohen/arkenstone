# Providers

| Provider | Engines | IL + Hebrew | Search forced? | Fan-out queries | Model | Multi-turn | Price/result | Fail rate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| cloro | ChatGPT, Gemini, AI Mode, Bing Copilot Search, AI Overview | IL on all 5 (checked `/v1/countries`) | ChatGPT forced (can disable); Gemini natural; Google/Bing search-based | ChatGPT only (+2 credits), often empty | ChatGPT only, often null | ChatGPT only (`prompt` array, ≤5) | 6–9 credits sync, 4–7 async | see observations |
| brightdata | ChatGPT, Gemini, AI Mode. Perplexity + Grok NOT in our account (Perplexity id from the product page hung >10 min), no Copilot | IL works (ChatGPT, Gemini, 2026-11-15) | ChatGPT natural (`web_search_triggered`) | not returned in our records | Gemini yes (Flash-Lite), ChatGPT null | additional_prompt (not used: rule 5) | $1.5/1k records, 5K free/month, failed = free | 0/21 failed |
| scrapellm | Perplexity, Grok (also ChatGPT, Gemini, Copilot, AIO: not used). No Claude | IL works | Perplexity always searches; Grok natural | no | Grok `llm_model` is the mode name ("auto") | no | 3 credits/run, failed refunded; 1,500 free trial | Perplexity 0/10, Grok 6/10 failed ("did not respond in time") |

## Decision 2026-11-15: primary provider per engine (owner)

Calibration is done on each engine's primary provider: calibrating on one provider and measuring on another is
useless (rule 7). Several providers are fine, but each engine has ONE primary provider for its series.

| Engine | Primary | Why / what is known | Secondary (diagnosis only, never mixed) |
| --- | --- | --- | --- |
| ChatGPT | brightdata | natural search, read (site level) / cited / named, free tier covers V0 | cloro: exact pages + fan-out queries |
| Gemini | brightdata | cited / named / model; read = unknown | cloro: raw stream gives read + proxy location |
| Perplexity | scrapellm | stable, ~10 s, sources shown; read = unknown | none |
| AI Mode | brightdata | not tested yet | cloro |
| Copilot, AI Overview | cloro | only provider with them; deferred | none |
| Grok | deferred | ScrapeLLM only 4/10 succeeded | none |
| Claude | deferred | no consumer-UI provider exists; only the API (separate surface, separate series) | none |

Bright Data is less **detailed** than cloro, not less **accurate**: for rates (searched / cited / named) it is as good
and more realistic (natural search). cloro stays as a lab tool for "why was the site not cited".
Risk: providers drop engines without notice (cloro dropped Perplexity and Grok in Sep-Oct 2026). Every provider sits
behind its own adapter so an engine can move; a move is a series break (run both in parallel first).

## cloro (docs read 2026-11-15, https://cloro.dev/docs)

**What it is:** real logged-out browser sessions on each engine's consumer web UI, through a proxy in the
requested country. No model choice anywhere. Logged-out = what an anonymous visitor sees, which may differ
from a logged-in user's answers (personalization, model tier). This is a measurement-scope fact to keep in mind.

**Engines and credits (sync; async is 2 cheaper)**

| Our engine | Endpoint | Credits | Returns beyond text + sources |
| --- | --- | --- | --- |
| chatgpt | `/v1/monitor/chatgpt` | 7, 9 with `searchQueries` | searchQueries, model (often empty), footnote per source (inline or not), entities, map, shopping |
| gemini | `/v1/monitor/gemini` | 6 | places, shopping. No searchQueries, no model |
| ai_mode | `/v1/monitor/aimode` | 6 | `hl` language param, places, videos, shopping, ads |
| copilot | `/v1/monitor/bing/aimode` | 6 | bing.com/copilotsearch, not the Copilot app |
| ai_overview | `/v1/monitor/google` + `include.aioverview` | 7 | full Google SERP too; AIO missing = Google showed none (still charged) |

Not available: Perplexity, Copilot app, Grok (discontinued), Claude, Meta AI, DeepSeek (login walls).

**Plans:** free = 500 credits, concurrency 1. Lite $30/mo = 37,500 credits ($0.80 per 1k), concurrency 10.
Hobby $100 = 250k credits. Credits expire each cycle. Only successful calls are charged.
At Lite prices a ChatGPT run is ~$0.007, a Gemini/AI Mode run ~$0.005 (sync).

**Limits:** concurrency per plan (N+1th call gets 429 at once, no queue). Sync calls up to 5 min; use a 300s+
client timeout. Async: `/v1/async/task` (+ `/batch`, up to 500), poll or webhook; results kept 24h.

**Known gaps:** ChatGPT `searchQueries` / `model` sometimes empty (mobile-web format; `legacy: true` may help).
Gemini returns a short canned answer with no sources in <2% of calls (retry works ~60%).
Source fields are `label` (title) and `description` (snippet), not `title`.

## Observations (our runs)

### 2026-11-15: first exploration (11 runs, 77 credits, all succeeded)
2 prompts (`refinance-calculator:1` "יש מחשבון שבודק אם כדאי למחזר משכנתא?", `advisor-recommend:1`
"מי יועץ המשכנתאות הכי טוב בישראל?") x 5 engines, country IL. Batches `explore-*` in `data/geo.db`.

| Engine | Answer chars | Sources | Inline cites | Queries | Model | Latency | Credits |
| --- | --- | --- | --- | --- | --- | --- | --- |
| chatgpt (default) | 860–930 | 3–16 | 3 | not returned | null | 23–48 s | 9 |
| chatgpt `legacy` | 924 | 31 | 2 | 1, Hebrew | gpt-6 | 36 s | 9 |
| gemini | 2000–2170 | 0–11 | n/a | n/a | n/a | 35–58 s | 6 |
| ai_mode | 1100–1360 | 3 | n/a | n/a | n/a | 12–14 s | 6 |
| copilot (Bing) | 1800–2090 | 10–11 | n/a | n/a | n/a | 27–31 s | 6 |
| ai_overview | 850–930 | 4 | n/a | n/a | n/a | 26–43 s | 7 |

Findings:
- Hebrew and IL work on all 5 engines; answers are in natural Hebrew.
- **ChatGPT needs `legacy: true`** for model and search queries (now the adapter default). Without it the
  +2 credits for searchQueries buy nothing. Legacy also lists ~2x the sources (used-but-not-linked ones).
- ChatGPT fan-out: a single long Hebrew query, mixing the question with "בנק ישראל" and "2026".
- Gemini answered the calculator question with **zero sources** (natural mode: no search, from memory),
  but searched for the advisor question. So "search triggered" really varies there.
- tachles-mashkanta: not cited and not named in any of the 11 answers.
- Recurring domains across engines: themarker.com (every engine), midrag.co.il, moti.org.il, boi.org.il,
  wisecard.co.il, supermarker.co.il, mashcantaman.co.il, bizreviews.co.il, mylist.co.il.
- For "who is the best advisor" every engine refuses to name one person and points to review sites
  (midrag, bizreviews) instead: the answer is a list of *sources of trust*, not of advisors.

### 2026-11-15: what `rawResponse` adds (ChatGPT +0 on top of searchQueries, Gemini free)
Now on by default for chatgpt and gemini. Extracted by `geo/collector/adapters/cloro_raw.py`.

| Field | ChatGPT | Gemini | AI Mode / Copilot / AIO |
| --- | --- | --- | --- |
| pages read (retrieved), incl. not cited | yes, with title, snippet, publish date | yes, URLs only | no |
| which were cited | yes (inline-linked) | yes (shown sources) | shown sources only |
| fan-out queries | yes (documented field) | no | no |
| model | yes (gpt-6) | yes, from UI text (3.5 Flash-Lite) | no |
| search triggered | forced | yes, from the UI's "searching" line | always (search-based) |
| engine-named entities | yes (`entities`) | no | no |
| user location the engine assumed | no | yes (e.g. ירוחם, נתניה: varies per run) | no |

Notes:
- In ChatGPT legacy mode `sources` already lists every page used (with repeats); `footnote=false` marks the
  ones linked in the text. "Cited" must mean linked, otherwise everything read looks cited.
- Example (parties247, "where can I see all weekend parties"): ChatGPT read 19 pages, linked 5;
  parties247 was read (positions 16-17) but not linked. Gemini read 9, showed 6, parties247 cited and named.
- Size: ~140 KB (ChatGPT) and ~380 KB (Gemini) of raw per run. Fine for SQLite at V0 volumes.
- The stream is undocumented UI traffic: extractors return "unknown" when the shape changes (tested).

### 2026-11-15: async is the default
`/v1/async/task` + poll, verified end to end: ChatGPT 7 credits, Gemini 4 (sync 9 / 6). Tasks queue at cloro,
so concurrency limits do not block us; results kept ~24 h. The poll payload carries the engine result under
`response` (not `result`), plus `task.latencyMs` and `credits.creditsCharged`. Same `idempotencyKey` again
returns 409 with the existing `taskId`: we use it to never pay twice after a crash (tested).
Use `{"sync": true}` in run params to force the blocking endpoint.

Seen on parties247 "איפה יש מסיבות הלילה בתל אביב?": **ChatGPT's fan-out query was in English**
("Tel Aviv nightlife parties events tonight ...") for a Hebrew question, so it read mostly English sites
(happytlv, secrettelaviv). Gemini placed the user in אום אל-פחם this time (ירוחם, נתניה before):
the proxy location varies per run and is a noise source to measure.

## Bright Data (docs read 2026-11-15, docs.brightdata.com; adapter `geo/collector/adapters/brightdata.py`)

**Engines:** ChatGPT Search `gd_m7aof0k82r803d5bjm`, Gemini `gd_mbz66arm2mf9cu856y`, Google AI Mode
`gd_mcswdt6z2elth3zqr2`. Perplexity and Grok: "currently unavailable" in the docs. Copilot: not documented.
So Bright Data covers no engine that cloro lacks today.

**Fields (per-scraper JSON; item shapes not documented, the two example pages disagree):**

| Field | ChatGPT | Gemini | AI Mode |
| --- | --- | --- | --- |
| answer text / markdown / raw | yes | yes | yes |
| citations, links_attached | yes | yes | yes (+ citations_mapping) |
| pages read (search_sources, _more) | yes | no | no |
| fan-out queries (web_search_query) | yes | no | no |
| web_search_triggered | yes (natural mode) | no | n/a (search-based) |
| model | yes (example shows null or gpt-4o) | yes | no |
| response_raw | yes | yes | no |
| logged-in or logged-out UI | unknown | unknown | unknown |

Our mapping (an assumption, check on the first real records): retrieved = search_sources + search_sources_more +
shown; cited = in citations or links_attached.

**Calls:** async `POST /datasets/v3/trigger?dataset_id=..&include_errors=true` with all prompts in `input[]`
(`index` maps record to run, prompt as fallback) -> snapshot_id; `GET /datasets/v3/snapshot/{id}` (202 = building).
Sync `/scrape` has a 1 min limit then returns 202. Every call is logged to `data/brightdata_calls.jsonl` and refused
past `BRIGHTDATA_MAX_CALLS` (default 12). Raw snapshots kept untouched in `data/brightdata_raw/`.
Script: `scripts/brightdata_compare.py status|trigger --engine X|collect|ledger`.

**Free tier:** 5,000 credits/month, no card. The ChatGPT page says "up to 5,000 ChatGPT records". The Gemini and
AI Mode pages do not state free coverage (only "Web Scraper API" generically). Without deposited funds requests error,
no charge ("hard stop"). Account API: `GET /customer/balance`, `GET /trials/free_tier` (key needs billing.read).

### 2026-11-15: first attempt blocked
3 calls, all 401 "Auth method is not supported" (balance, free_tier, ChatGPT trigger). Cause: `BRIGHTDATA_API_KEY`
in `.env` holds the same value as `GEMINI_API_KEY`, i.e. not a Bright Data key. Nothing delivered, nothing charged.
The 4 ChatGPT runs are stored as failed (batches `bd-chatgpt-*`). Next: paste the real API key (control panel,
Account settings -> API key; give it billing.read too so usage can be checked), then `trigger --engine chatgpt`.

### 2026-11-15: head to head with cloro (ChatGPT, 10 prompts, 5 per site, 1 run each)
Account became active a few minutes after "Customer is not active" (HTTP 400): activation lag, not a setting.
- Searched: 10/10 on both (Bright Data natural, cloro forced).
- Read: cloro 96 site-visits, Bright Data 71. Read sets per prompt overlap only 1/3 to 1/2: single runs are noisy.
- parties247 read/cited/named: cloro 3/1/1 of 5, Bright Data 1/1/1. tachles-mashkanta 0 on both.
- **Pages read are in the raw stream:** `search_sources` came back null, but `response_raw` (ChatGPT v2 stream, ~5 MB)
  has a "Searched N websites" reasoning event with one favicon per site. Icon count == N on all 11 records (up to 14).
  So read is known at SITE level (adapter `read_domains`), not page level. No fan-out queries, no model.
- Gemini (10 prompts): answer, citations (0-7), model "Flash-Lite". No raw stream, no search flag -> read unknown;
  2 mortgage prompts had no sources at all (answered from memory? cannot tell).

## ScrapeLLM (docs read 2026-11-15, docs.scrapellm.com; adapter `geo/collector/adapters/scrapellm.py`)
Async only: `POST /scrapers/{engine}/jobs?prompt&country&bypass_cache=true` -> job_id, `GET /jobs/{id}` (no auth).
`bypass_cache=true` always: a cached answer would hide noise. Sync GET times out at 180 s on Grok (504, 10/10).
- Perplexity (10 prompts, IL): 10/10 ok, ~10 s, 5-10 sources each, related questions. No queries, model, read list.
  Neither site appeared in any answer. Top sources: mako, tickchak, timeout, my-mashkanta, calcalist.
- Grok: 4/10 ok via jobs (rest "Scraper did not respond in time"). When ok: 18-36 web sources + X posts.
