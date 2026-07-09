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

Automatic `mobile.de` scraping exists in the broader scraper code, but it is
disabled in the public demo. Use manual mode for `mobile.de` listings.

The analysis pipeline is:

1. Scrape or import the listing into a temporary `Auta/<slug>/` job folder.
2. Run Gemini grounded web research for listing/model context when available.
3. Run text/research analysis with Grok when `GROK_API_KEY` is configured,
   otherwise OpenRouter when `OPENROUTER_API_KEY` is configured, otherwise
   Gemini.
4. Run Gemini vision analysis on representative uploaded or scraped photos.
5. Calculate a deterministic backend risk score in `risk_scorer.py`.
6. Generate the final buyer-facing report with the same text provider used in
   step 3.
7. Save the public report and intermediate artifacts for the dashboard.

## Local Run

```powershell
cd "D:\VS Projekty\Scrapper - DEMO"
python -m pip install -r requirements.txt

$env:DEMO_MODE="true"
$env:DEMO_SKIP_KB="true"
$env:DEMO_PROMPT_FILE="analyze_prompt_v4_koyeb.txt"
$env:FLASK_SECRET_KEY="change-me"

$env:GEMINI_PRIMARY_API_KEY="..."
$env:GEMINI_BACKUP_API_KEY="..."
# Optional. If omitted, Gemini is used for text and final synthesis.
$env:GROK_API_KEY="..."
# Optional. Used for text/final synthesis when Grok is omitted.
$env:OPENROUTER_API_KEY="..."

python web_server.py
```

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
DEMO_SKIP_KB=true
DEMO_PROMPT_FILE=analyze_prompt_v4_koyeb.txt
FLASK_SECRET_KEY=<strong-random-secret>
GEMINI_PRIMARY_API_KEY=<server-side-key>
GEMINI_BACKUP_API_KEY=<optional-backup-key>
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
| `DEMO_SKIP_KB` | `true` | Omits private knowledge-base matches from demo prompts. |
| `DEMO_PROMPT_FILE` | `analyze_prompt_v4_koyeb.txt` | Prompt file used by demo analysis payloads. |
| `GEMINI_PRIMARY_API_KEY` | empty | Required Gemini key for web research, vision, and Gemini fallback. |
| `GEMINI_BACKUP_API_KEY` | empty | Optional second Gemini key retried on key/quota failures. |
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
| `SCRAPPER_TOKEN_INPUT_COST_PER_1M` | `0` | Optional token cost estimate input rate. |
| `SCRAPPER_TOKEN_OUTPUT_COST_PER_1M` | `0` | Optional token cost estimate output rate. |
| `SCRAPPER_TOKEN_COST_CURRENCY` | `EUR` | Currency label for token cost estimates. |

## Public Demo UI

The main UI is `web/index.html`.

It provides:

- Slovak/English output language selector.
- Light/dark theme toggle.
- URL mode for supported marketplaces.
- Manual mode for unsupported marketplaces.
- Streaming progress overlay with cancel support.
- Final report view with reference photos.
- Copy text and export-to-PDF actions.
- Saved-analysis drawer backed by `/api/demo/listings`.

The token dashboard is `web/token-dashboard.html`. It shows recent model calls,
tokens by listing, tokens by model, live demo progress, and links to intermediate
analysis artifacts.

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

Lists available intermediate analysis files for a completed job.

Possible artifacts include:

- `web_research.md`
- `grok_research.json`
- `gemini_vision.json`
- `risk_score.json`
- `validation_warnings.json`
- `analysis_result_raw.md`
- `analysis_result.md`

### `GET /api/demo/listings/<slug>/artifacts/<filename>`

Serves one allowed artifact as plain text.

### `GET /api/demo/listings/<slug>/analysis-result/raw`

Returns the unstripped raw final model output when `analysis_result_raw.md`
exists.

### `GET /api/token-usage`

Returns token usage statistics from `token_tracker.py`.

Optional query:

```text
?limit=80
```

### `GET /api/demo/current-progress`

Returns the latest mirrored SSE progress for the token dashboard.

## Local Non-Demo Routes

The code still includes private/local routes such as `/api/scrape`,
`/api/listings`, `/api/analyze/<slug>`, `/api/kb`, and save-KB helpers. These
are blocked by the demo route gate when `DEMO_MODE=true` and should not be
treated as public demo API.

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
web_research.md
grok_research.json
gemini_vision.json
risk_score.json
validation_warnings.json
analysis_result_raw.md
analysis_result.md
images/
analysis_images/
```

`analysis_result.md` is the public report shown in the demo dashboard.
`analysis_result_raw.md` keeps the raw model output before public stripping.

## Models And Fallbacks

- Gemini is required.
- Gemini Flash Lite is used for grounded web research and compact text/research
  fallback.
- Gemini Flash is used for vision and final synthesis when Gemini is the text
  provider.
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
- Public demo mode hides private KB/listing management APIs.
- Demo jobs are rate-limited per IP and concurrency-limited globally.
- Old job folders are cleaned up based on `DEMO_JOB_TTL_MINUTES`.
- Manual uploads are constrained by image count, extension, and total request
  size.
- Final risk verdict is constrained by deterministic backend scoring, not only
  by model prose.

## Tests

Run the current demo tests from this folder:

```powershell
cd "D:\VS Projekty\Scrapper - DEMO"
python -m unittest test_demo_dashboard_api.py test_analysis_normalizer.py
```
