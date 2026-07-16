# Checkni Auto Public Demo

This folder contains the standalone public demo of Checkni Auto. It is separate
from the private `Scrapper/` app and is designed to expose only the demo
experience: submit a used-car listing, run AI analysis, view the final report,
and inspect token usage.

When `DEMO_MODE=true` the server blocks the private API surface and allows only
the demo UI, health check, token/progress endpoints, and `/api/demo/*` routes.

## What The Demo Does

The demo supports two input flows:

- URL analysis for `autobazar.eu`, `autobazar.sk`, `bazos.sk`, and `bazos.cz`.
- Manual analysis for any other marketplace by pasting listing text, entering a
  price, and uploading up to 12 photos.

The redesigned customer journey has three screens:

1. `/` — submit a supported marketplace URL or a manual listing.
2. `/analysis/<slug>` — a premium buyer summary with a qualitative verdict,
   priority findings, market context, likely costs, photos, and next actions.
3. `/analysis/<slug>/technical` — the evidence-led deep dive with listing facts,
   resolved component identity, VIN/recall context, verified sources, technical
   risks, comparables, cost ranges, photo findings, and a persistent checklist.

Both result pages consume `/api/demo/listings/<slug>/presentation`. This stable
presentation model is assembled from canonical pipeline artifacts and excludes
private numeric scores, diagnostic breakdowns, unverified URLs, and
background-only market links. Missing evidence is shown as unavailable instead
of being inferred. It also reads compatible embedded identity and market fields
from older completed jobs when their newer standalone artifacts are absent.
The recent-analyses drawer merges browser history with completed jobs returned
by the demo listings API, so saved server results remain discoverable after a
browser reset.

`mobile.de` is searched automatically as a background-only source for price
benchmarking. Its detail links are never shown to customers. If the portal
returns an access-denied response, the exact HTTP status and compact response
preview are saved in `market_research_mobile_de.md`; no blocked page is
treated as a valid comparable.

The analysis pipeline is:

1. Scrape or import the listing into a temporary `Auta/<slug>/` job folder.
2. Run a short, separate grounded identification pass for the exact listing
   combination: generation, engine, transmission, and drivetrain. Exact codes
   are `VERIFIED` only when VIN/document/OEM evidence supports them;
   specification matching alone remains `PROBABLE`, and unresolved variants
   remain `AMBIGUOUS` or `UNKNOWN`. The result is saved independently in
   `component_identity.json`, while the raw grounded response and its resolved
   citations remain in `component_identity_research.md`. A later long model
   response therefore cannot erase the identity or its source trail.
3. Run stateless Gemini grounded reliability research using that component
   identity. ADAC and TÜV findings are model/year context, while owner surveys
   and forums are recurring-problem hypotheses. Neither source type proves a
   defect on the listed vehicle or directly lowers its score.
   Market discovery does not use a language model. The backend derives a
   narrow make/model query from the scraped title and makes bounded direct
   searches on Bazos SK/CZ, Autobazar.sk, Autobazar.EU, and Sauto.cz, plus a
   separate Mobile.de background search. It parses
   visible result cards and keeps only the exact detail URL present in each
   card; it never constructs a detail URL from a template. The analyzed ad,
   non-vehicle offers, and model mismatches are removed before benchmarking.
   Each portal attempt records the search URL, number of cards, filters,
   accepted candidates, and request errors.
   Only these local SK/CZ portals can produce customer-facing links. Any
   other-country observation is background-only and must be Tier A with the
   same transmission, engine, and drivetrain, year within +/-1, and mileage
   within `max(25,000 km, 15%)` of the analyzed listing. Mobile.de is fetched
   only for background price evidence; its detail links are never shown to
   customers. AutoScout24 and Otomoto are not fetched directly. Local links
   appear only in the price and negotiation section.
   Structured candidates are deduplicated across portals by VIN or a
   conservative year/mileage/version/price/seller fingerprint, and cross-posts
   of the analyzed listing are removed. Non-EUR prices are normalized by ECB
   reference rates; CZK uses the average of available ECB observations from
   the latest 30-calendar-day window, while other currencies use the latest
   available reference rate. Local benchmark rows first apply the strict year
   and mileage limits, then widen the year range, and only then widen mileage.
   Foreign background rows never use those widened stages.
   Expanded records receive lower weights and wider price-classification
   thresholds. Three A/B retail observations from local or tightly matched
   background sources are still required; C-tier, net,
   auction, damaged, export-only, duplicate, and non-normalizable offers are
   excluded. If fewer than three verified A/B observations survive, the price
   conclusion remains unavailable. The full search audit is stored in
   `market_search_results.json`; staged acceptance, rejection reasons, weights,
   and diagnostic counts are stored in `market_benchmark.json`. Asking prices
   are not transaction prices, so market price never changes the technical
   risk verdict.
   If structured text/research JSON is invalid or truncated, the pipeline makes
   one compact recovery attempt without repeating web search. If recovery also
   fails, it continues with a schema-valid unavailable packet, explicitly marks
   the limitation, and emits no unsupported technical claims. A research
   delivery gate then marks the result incomplete whenever web findings,
   technical risks, or expected costs are empty and prevents a green verdict;
   such a result is a retry/no-charge candidate rather than a complete paid
   analysis. If grounded component identification exhausts every configured key
   and model, or grounded technical research returns no verifiable public source
   URL at all, the run now stops before the remaining paid phases. Diagnostics
   mark it `RETRY_REQUIRED` with `chargeable: false` so a billing layer can offer
   a free retry instead of delivering an empty report.
   Public comparable links use a stricter customer-facing filter than broad
   discovery: the detail URL must be verified, the visible engine,
   transmission, and drivetrain must match (Tier A), and the asking price must
   be within +/-20% of the analyzed listing price. If no offer passes all of
   these checks, the report publishes no comparable-ad link.
4. Run compact Research V2 normalization with Gemini by default. The model owns
   only evidence summaries, claims, unknowns, conflicts, technical risks, costs,
   flags, and source references. Gemini receives a low-state serving schema for
   the complete JSON envelope; the backend applies the stricter checked-in audit
   contract and array limits. URLs are stored once in the source registry and
   referenced by ID from findings and risks. Known provider enum aliases are
   normalized before strict nested validation. A technical claim is retained only
   when its source ID points to a verified, topic-matched evidence source; fixed
   numeric service intervals additionally require an official or regulatory
   source. Parts catalogs cannot establish defect prevalence, intervals, or repair
   costs. The backend merges these with canonical listing,
   identity, VIN, market, benchmark, and verdict data into the compatible
   `grok_research.json`. Grok and OpenRouter remain optional provider branches.
5. Run Gemini vision analysis on representative uploaded or scraped photos.
6. Calculate a deterministic backend listing-screening status. Numeric scores
   and score breakdowns remain private implementation artifacts. Customers see
   only the five-level screening verdict. Generic model risks create inspection
   actions; only evidence tied to this specific car can worsen the verdict.
7. Generate the final buyer-facing report. Gemini defaults to a stronger Flash
   model for this phase.
8. Save the public report and intermediate artifacts for the dashboard. When
   final synthesis names a structured, verified market comparable but omits
   its Markdown link, the public-output normalizer restores the exact ad link
   deterministically inside `Cena a vyjednávanie` only.

The public analysis pipeline does not load or update a vehicle knowledge base.
Model, engine, transmission, drivetrain, generation, recall, and cost context
is researched for each job through Gemini Google Search grounding. Comparable
SK/CZ asking prices are collected separately by deterministic local portal
search; foreign background observations are used only when they pass the tight
match gate above.
Generated job files are temporary runtime artifacts and are not treated as a
persistent cache; this keeps deployments compatible with ephemeral Render
filesystems.

## Backend Structure

```text
web_server.py                         compatibility import/startup entry point
scrapper_demo/
  app.py                              Flask application factory
  config.py                           validated environment configuration
  contracts.py                        shared typed boundary contracts
  ai_policy.py                         immutable phase policies and input budgets
  legacy_server.py                    compatibility composition and local handlers
  logging.py                          Unicode-safe console logging
  direct_market_search.py              deterministic local SK/CZ result-card search
  market_comparables.py              public-link selection and EU price benchmark
  progress.py                         process-local progress/rate/job state
  presentation.py                    customer-safe result-page view model
  scorecard.py                        private diagnostic score breakdown
  providers/                          Gemini, Grok, OpenRouter, retry, and errors
  routes/                             public/demo and private Flask blueprints
  services/analysis_pipeline.py       provider-neutral analysis orchestration
  services/image_service.py           image selection and collage preparation
  storage/listing_jobs.py             safe listing-job and artifact repository
  validation.py                       JSON/report validation
```

Factory-created demo apps register only the public blueprint. Private-mode apps
also register the private listing, analysis, and KB routes. Provider transports
and filesystem implementation details remain outside the route modules.

## Local Run

```powershell
cd "D:\VS Projekty\Scrapper - DEMO"
python -m pip install -r requirements.txt

$env:DEMO_MODE="true"
$env:DEMO_PROMPT_FILE="analyze_prompt_v4_koyeb.txt"
$env:FLASK_SECRET_KEY="change-me"
$env:ADMIN_DASHBOARD_TOKEN="change-this-admin-token"

$env:GEMINI_PRIMARY_API_KEY="..."
$env:GEMINI_BACKUP_API_KEY="..."
# Optional. If omitted, Gemini is used for text and final synthesis.
$env:GROK_API_KEY="..."
# Optional. Used for text/final synthesis when Grok is omitted.
$env:OPENROUTER_API_KEY="..."

python web_server.py
```

Runtime dependencies in `requirements.txt` use exact direct-version pins so
deployment upgrades are intentional and reviewable. Development-only tooling is
kept in `requirements-dev.txt`; install that file when running `mypy` locally.

### Application Factory

Tests and embedded deployments can create isolated applications without sharing
progress, rate-limit, or concurrency state:

```python
from scrapper_demo import create_app

app = create_app(
    {
        "DEMO_MODE": True,
        "SCRAPPER_AUTA_DIR": "/writable/jobs",
        "DEMO_RATE_LIMIT_PER_IP": "0",
    }
)
```

Use `DEMO_MODE=False` only for trusted local/private operation because it
registers the private blueprint.

Open `http://localhost:5000`.

The token/debug dashboard is available at:

```text
http://localhost:5000/token-dashboard.html
```

## Deployment

The demo is ready for platforms that use a `Procfile`:

```text
web: gunicorn --bind :$PORT --workers 1 --threads 4 --timeout 600 --graceful-timeout 30 web_server:app
```

The pinned runtime is `python-3.11.9`.

Recommended deployment environment:

```text
DEMO_MODE=true
DEMO_ANALYSIS_PROFILE=quality_optimized
DEMO_PROMPT_FILE=analyze_prompt_v4_koyeb.txt
FLASK_SECRET_KEY=<strong-random-secret>
ADMIN_DASHBOARD_TOKEN=<strong-random-admin-token>
GEMINI_PRIMARY_API_KEY=<server-side-key>
GEMINI_BACKUP_API_KEY=<optional-backup-key>
# Optional model routing overrides. Leave these unset unless you intentionally
# want to change the default chains:
# non-final: gemini-2.5-flash -> gemini-3.5-flash -> gemini-3.1-flash-lite
# final synthesis: gemini-3.5-flash -> gemini-2.5-flash -> gemini-3.1-flash-lite
GEMINI_GROUNDING_MODEL=gemini-2.5-flash
GEMINI_MARKET_GROUNDING_MODEL=gemini-2.5-flash
GEMINI_TEXT_RESEARCH_MODEL=gemini-2.5-flash
GEMINI_VISION_MODEL=gemini-2.5-flash
GEMINI_FINAL_MODEL=gemini-3.5-flash
GROK_API_KEY=<optional-text-provider-key>
OPENROUTER_API_KEY=<optional-text-provider-key>
SCRAPPER_DATA_DIR=<optional-writable-data-dir>
```

If `SCRAPPER_DATA_DIR` is not set, the app stores demo jobs under the platform
temp directory in `scrapper-demo/Auta`.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `DEMO_MODE` | `true` | Restricts the app to public demo routes. |
| `DEMO_PROMPT_FILE` | `analyze_prompt_v4_koyeb.txt` | Prompt file used by demo analysis payloads. |
| `DEMO_SKIP_KB` | `true` | Prevents public demo analysis from reading or writing the private knowledge base. |
| `DEMO_ANALYSIS_PROFILE` | `quality_optimized` | Generation profile. `quality_optimized` uses Research V2, phase input/output ceilings, Gemini `countTokens`, safe ordered compaction, and minimal/off thinking for structured phases; `legacy` restores the previous research contract and generation limits. |
| `FLASK_SECRET_KEY` | `dev-demo-secret-change-me` | Flask secret; replace in every deployed environment. |
| `ADMIN_DASHBOARD_TOKEN` | empty | Required secret for the token dashboard, telemetry, diagnostic artifacts, raw results, and calibration exports. Protected routes return unavailable until configured. |
| `RISK_SCORER_V2_ACTIVE` | `false` | Activates the offline-calibrated gate scorer. Leave disabled until holdout acceptance criteria pass. |
| `GEMINI_PRIMARY_API_KEY` | empty | Required Gemini key for web research, vision, and Gemini fallback. |
| `GEMINI_BACKUP_API_KEY` | empty | Optional second Gemini key retried on key/quota failures. |
| `GEMINI_FLASH_MODEL` | `gemini-2.5-flash` | Base Gemini Flash model used by non-final phase defaults. |
| `GEMINI_ADVANCED_FLASH_MODEL` | `gemini-3.5-flash` | Stronger Gemini Flash model used by final synthesis defaults. |
| `GEMINI_FLASH_LITE_MODEL` | `gemini-3.1-flash-lite` | Base Gemini Flash Lite model used by low-cost extraction defaults. |
| `GEMINI_GROUNDING_MODEL` | `gemini-2.5-flash` | Primary Gemini model for grounded web research. |
| `GEMINI_MARKET_GROUNDING_MODEL` | value of `GEMINI_GROUNDING_MODEL` | Gemini model for the four portal-specific market-grounding passes. Override only when that model has a separate usable quota. |
| `GEMINI_TEXT_RESEARCH_MODEL` | `gemini-2.5-flash` | Primary Gemini model for structured text/listing research extraction. |
| `GEMINI_VISION_MODEL` | `gemini-2.5-flash` | Primary Gemini model for photo/vision extraction. |
| `GEMINI_FINAL_MODEL` | `gemini-3.5-flash` | Primary Gemini model for final buyer-facing synthesis. |
| `GROK_API_KEY` | empty | Optional Grok key for text/research and final synthesis. |
| `OPENROUTER_API_KEY` | empty | Optional OpenRouter key for text/research and final synthesis when Grok is not configured. One key can call multiple OpenRouter models; the request model id selects the model. |
| `OPENROUTER_MODEL` | `qwen/qwen3-next-80b-a3b-instruct:free` | Optional primary OpenRouter model id. |
| `OPENROUTER_FALLBACK_MODELS` | built-in free model list | Optional comma-separated OpenRouter model ids retried on rate-limit/unavailable responses. |
| `DEMO_RATE_LIMIT_PER_IP` | `3/day` | Daily demo analysis limit per client IP. Set `0` to disable. |
| `DEMO_MAX_CONCURRENT_JOBS` | `1` | Maximum simultaneous demo analyses. |
| `DEMO_JOB_TTL_MINUTES` | `60` | Age after which old demo job folders are cleaned up. |
| `DEMO_MAX_MANUAL_IMAGES` | `12` | Maximum images accepted by manual mode. |
| `DEMO_MAX_SCRAPED_IMAGES` | `0` | Scraped image download cap. `0` means no download cap. |
| `DEMO_MAX_UPLOAD_MB` | `24` | Total request upload limit in megabytes. |
| `SCRAPPER_DATA_DIR` | system temp dir | Base data folder for demo jobs and token usage. |
| `SCRAPPER_AUTA_DIR` | `<data-dir>/Auta` | Explicit listing/job storage folder. |
| `SCRAPPER_TOKEN_USAGE_PATH` | `<Auta>/token_usage.json` | Token usage JSON storage path. |
| `SCRAPPER_TOKEN_INPUT_COST_PER_1M` | `1.5` | Optional token cost estimate input rate. |
| `SCRAPPER_TOKEN_OUTPUT_COST_PER_1M` | `9.00` | Optional token cost estimate output rate. |
| `SCRAPPER_TOKEN_MODEL_RATES_JSON` | empty | Optional JSON map of exact per-model `input_per_1m` and `output_per_1m` rates; unknown models use the labeled fallback rates above. |
| `SCRAPPER_TOKEN_COST_CURRENCY` | `EUR` | Currency label for token cost estimates. |

For a Google AI Studio/free Gemini key, keep `DEMO_ANALYSIS_PROFILE=quality_optimized`.
Gemini can spend hidden thinking tokens before emitting structured JSON or
completing the report, which may end in `MAX_TOKENS`. The quality profile uses
`thinkingBudget=0` for Gemini 2.5 extraction and the native `minimal` thinking
level for Gemini 3.x extraction. Final synthesis uses a small Gemini 2.5 budget or
Gemini 3.x `minimal` thinking, plus a 6,000-token safety ceiling shared by thinking
and visible output. Research V2 uses a 5,000-token hard ceiling to avoid paying
for a second request when a complete structured packet runs beyond its 2,200-token
soft target; vision uses a 4,000-token ceiling and 1,800-token target. Final synthesis
uses a soft 3,500-token visible-output target so report completeness wins over
aggressive shortening; the 6,000-token shared ceiling remains the safety bound. Text
and final inputs are checked with Gemini REST `countTokens` (including the
Research V2 response schema); a failed count falls
back to a labeled local estimate and does not stop the analysis. A backup Gemini
key is still used for authentication and quota
failures; `MAX_TOKENS` is a generation-budget condition, not a key-quota failure.
If final synthesis still reaches that shared limit, its partial text is discarded
and one compact complete-report recovery is attempted before the run fails.

## Public Demo UI

The main UI is `web/index.html`.

It provides:

- Slovak/English output language selector.
- Light/dark theme toggle.
- URL mode for supported marketplaces.
- Manual mode for unsupported marketplaces.
- Streaming progress overlay with cancel support.
- Final report view with reference photos.
- Five-level customer verdict without a numeric scorecard or calibration text.
- Pros and cons moved directly below the quick summary and displayed side by
  side on wider screens, without duplicating the original report sections.
- Copy text and export-to-PDF actions.
- Saved-analysis drawer backed by `/api/demo/listings`.

The token dashboard is `web/token-dashboard.html`. It requires the administrator
token and shows recent model calls,
tokens by listing, tokens by model, live demo progress, and links to intermediate
analysis artifacts. Each completed car also has an authenticated calibration-bundle
download. The ZIP is created on demand and contains scorer inputs and images, but
not the generated score or final report.

Administrator routes remain unavailable when `FLASK_SECRET_KEY` is empty or
still uses the documented development default, even if an admin token is set.

## Public Demo API

### `GET /healthz`

Returns basic server status:

```json
{ "status": "ok", "demo_mode": true }
```

### `POST /api/demo/analyze`

Runs scrape plus AI analysis for a supported listing URL.

Request:

```json
{
  "url": "https://auto.bazos.sk/inzerat/...",
  "output_language": "sk"
}
```

`output_language` accepts `sk` or `en`; any other value falls back to `sk`.

Response is a Server-Sent Events stream. Event payloads may contain:

- `status` for current phase.
- `log` for scraper output.
- `text` for streamed final report chunks.
- `token_usage` with approximate input/output token counts.
- `slug` for the generated listing job.
- `done` when analysis is complete.
- `error` when the job fails.

### `POST /api/demo/analyze-manual`

Runs manual import plus AI analysis.

Multipart form fields:

| Field | Required | Notes |
| --- | --- | --- |
| `title` | no | Falls back to the first line of `manual_text`. |
| `price` | yes | Integer EUR price, must be greater than `0`. |
| `source_url` | no | Original listing URL for reference. |
| `manual_text` | yes | Listing description, specs, VIN, equipment, seller notes. |
| `images` | no | Up to `DEMO_MAX_MANUAL_IMAGES`; supported image extensions only. |
| `output_language` | no | `sk` or `en`; defaults to `sk`. |

Response is the same SSE format as `/api/demo/analyze`.

### `GET /api/demo/listings`

Returns completed demo analyses only. Listings without `analysis_result.md` are
not exposed.

### `GET /api/demo/listings/<slug>`

Returns the saved public analysis, parsed listing data, source URL, scrape time,
and image URLs for a completed demo analysis.

### `GET /api/demo/listings/<slug>/image/<filename>`

Serves a listing image from the completed demo job. The route rejects path
traversal.

### `GET /api/demo/listings/<slug>/artifacts`

Lists available intermediate analysis files for a completed job. Administrator login is required.

Possible artifacts include:

- `listing_facts.json`
- `component_identity_research.md`
- `component_identity.json`
- `reliability_research.md`
- `market_research_sk_cz.md`
- `market_research.md`
- `market_search_results.json`
- `market_benchmark.json`
- `web_research.md`
- `grok_research.json`
- `gemini_vision.json`
- `risk_score.json`
- `validation_warnings.json`
- `analysis_diagnostics.json`
- `analysis_result_raw.md`
- `analysis_result.md`

### `GET /api/demo/listings/<slug>/artifacts/<filename>`

Serves one allowed artifact as plain text. Administrator login is required.

### `GET /api/demo/listings/<slug>/analysis-result/raw`

Returns the unstripped raw final model output when `analysis_result_raw.md`
exists. Administrator login is required.

### `GET /api/token-usage`

Returns token usage statistics from `token_tracker.py`. Administrator login is required.

Optional query:

```text
?limit=80
```

### `GET /api/demo/current-progress`

Returns the latest mirrored SSE progress for the token dashboard. Administrator login is required.

### Administrator calibration and debugging exports

Sign in at `/admin/login` and open `/token-dashboard.html`. Each completed car
has two administrator-only exports:

- **Download calibration bundle** creates a blind reviewer package. It excludes
  `risk_score.json` and both final reports, but includes scorer inputs, original
  and analysis images, an empty expert label, per-phase diagnostics, an always-
  present validation-warning artifact, and versioned prompt/policy/schema
  snapshots under `reproducibility/`.
- **Download debugging bundle** includes the complete artifact chain, including
  `analysis_request.md`, `risk_score.json`, raw final model output, and the
  public report. It also includes `vision_provider_attempts.json`, which records
  sanitized model/finish/token diagnostics and the raw initial/recovery outputs
  needed to debug truncated provider JSON. It has no expert label and must not
  be shown to a reviewer before independent labelling.

  The debugging bundle also includes the sanitized `text_research_provider_attempts.json`
  and `ai_usage_summary.json` artifacts. They contain per-attempt usage, retry,
  grounding, coverage, duration, and estimated-cost telemetry without API keys or
  secrets. The raw `research_model_output.json` Research V2 packet is debugging-
  only; it is excluded from the blind calibration bundle, which receives only the
  backend-merged canonical `grok_research.json`.

Diagnostic artifact routes, raw results, telemetry, and both exports share the
same signed administrator session. The endpoints are
`/api/admin/calibration-bundles/<slug>` and
`/api/admin/debugging-bundles/<slug>`.

Render does not retain a calibration dataset. Download selected bundles before
`DEMO_JOB_TTL_MINUTES` cleanup, label them offline, and import them with:

```powershell
python -m scrapper_demo.calibration_cli import calibration-car.zip D:\private-calibration
python -m scrapper_demo.calibration_cli validate-labels D:\private-calibration
python -m scrapper_demo.calibration_cli evaluate D:\private-calibration --split tuning
python -m scrapper_demo.calibration_cli report D:\private-calibration --split holdout `
  --json-output evaluation.json --markdown-output evaluation.md
```

The reviewer edits only `expert_label.json`. Schema v2 can additionally record
the expert-expected generation, engine code, transmission code, drivetrain,
identity confidence, and verification source. Identity accuracy is reported
separately from verdict agreement, including any incorrectly asserted
`VERIFIED` identity. Rule changes belong in the shared,
versioned `risk_policy_v2.json` and must be based on repeated tuning-set errors,
not individual makes, models, or listing slugs.

Calibration labels use the language-independent `expected_status`, not the
rendered Slovak or English verdict text. The customer-facing labels are:

| Status | Slovak | English |
| --- | --- | --- |
| `WORTH_INSPECTING` | 🟢 STOJÍ ZA OBHLIADKU | 🟢 WORTH CHECKING OUT |
| `INSPECT_WITH_RESERVATIONS` | 🟡 NAJPRV PREVERIŤ | 🟡 VERIFY FIRST |
| `RESOLVE_BEFORE_PROCEEDING` | 🟠 RIEŠIŤ LEN S VÝHRADAMI | 🟠 PROCEED WITH RESERVATIONS |
| `HIGH_RISK` | 🔴 SKÔR NERIEŠIŤ | 🔴 PROBABLY SKIP |
| `DO_NOT_PROCEED` | ⛔ RUKY PREČ | ⛔ WALK AWAY |

These labels describe whether the listing is worth pursuing toward verification
and inspection. They are not a guarantee of the vehicle's hidden condition or
future ownership outcome.

## Local Non-Demo Routes

Private-mode blueprint registration retains local routes such as `/api/scrape`,
`/api/listings`, `/api/analyze/<slug>`, `/api/kb`, and save-KB helpers. These
are not registered by factory-created demo apps and remain protected by the
demo route gate in the compatibility app. They are retained for tested local
compatibility and should not be treated as public demo API.

## Generated Job Files

Each analysis job writes files under:

```text
<SCRAPPER_AUTA_DIR>/<slug>/
```

Typical files:

```text
car_info.md
raw_data.json
vin_decoded.json
analysis_request.md
listing_facts.json
component_identity_research.md
component_identity.json
reliability_research.md
market_research_sk_cz.md
market_research_mobile_de.md
market_research_otomoto_pl.md
market_research_autoscout.md
market_research.md
market_search_results.json
market_benchmark.json
web_research.md
grok_research.json
gemini_vision.json
vision_provider_attempts.json
risk_score.json
validation_warnings.json
analysis_diagnostics.json
analysis_result_raw.md
analysis_result.md
images/
analysis_images/
```

`analysis_result.md` is the public report shown in the demo dashboard.
`analysis_result_raw.md` keeps the raw model output before public stripping.
`research_model_output.json` is an administrator/debugging-only raw Research V2
packet and is not exposed as a public artifact or blind calibration input.

## Models And Fallbacks

- Gemini is required.
- Non-final Gemini phases use this default model order on the primary key:
  `gemini-2.5-flash`, then `gemini-3.5-flash`, then
  `gemini-3.1-flash-lite`.
- Final synthesis uses this default model order on the primary key:
  `gemini-3.5-flash`, then `gemini-2.5-flash`, then
  `gemini-3.1-flash-lite`.
- Each Gemini phase can be overridden independently with
  `GEMINI_GROUNDING_MODEL`, `GEMINI_MARKET_GROUNDING_MODEL`, `GEMINI_TEXT_RESEARCH_MODEL`,
  `GEMINI_VISION_MODEL`, and `GEMINI_FINAL_MODEL`.
- Grok is optional. If `GROK_API_KEY` is set, Grok handles text/research and
  final synthesis while Gemini still handles web research and vision.
- OpenRouter is optional. If `OPENROUTER_API_KEY` is set and Grok is not set,
  OpenRouter handles text/research and final synthesis. A single OpenRouter key
  is shared across models; the app chooses the model via the request payload.
  The default free-model order is `qwen/qwen3-next-80b-a3b-instruct:free`,
  `google/gemma-4-26b-a4b-it:free`, `openai/gpt-oss-20b:free`,
  `meta-llama/llama-3.3-70b-instruct:free`,
  `nvidia/nemotron-3-super-120b-a12b:free`, then `openrouter/free`.
- If a primary Gemini key fails before producing output, the backup key is
  retried when configured.

## Safety And Limits

- API keys stay server-side.
- Token telemetry, diagnostic artifacts, raw results, and calibration exports
  require the administrator session.
- Public demo mode hides private KB/listing management APIs.
- Demo jobs are rate-limited per IP and concurrency-limited within the server
  process.
- Progress, rate counters, and concurrency slots are held in synchronized
  process-local memory. The supported deployment therefore uses one Gunicorn
  worker. Multi-worker or multi-instance deployment requires a shared state
  backend.
- Old job folders are cleaned up based on `DEMO_JOB_TTL_MINUTES`.
- Manual uploads are constrained by image count, extension, and total request
  size.
- The final listing-screening verdict is constrained by deterministic backend
  gates, not only by model prose.

## Compatibility Boundaries

- `web_server.py` remains the required local and Gunicorn import target.
- `scrapper_demo/legacy_server.py` retains tested local/private handlers and
  helper names used by existing integrations.
- `llm_client.py` remains a provider compatibility facade for existing imports.
- Existing artifact names such as `grok_research.json` remain unchanged even
  when Gemini or OpenRouter supplies the text/research phase.
- These boundaries should be removed only together with their callers and
  characterization tests; they are not undocumented migration leftovers.

## Tests

Run the complete unit, contract, integration, and mocked smoke suite from this
folder. Tests do not require real provider keys:

```powershell
cd "D:\VS Projekty\Scrapper - DEMO"
python -m unittest
```

Optional gradual type checking:

```powershell
python -m pip install -r requirements-dev.txt
python -m mypy
```
