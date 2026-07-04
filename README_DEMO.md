# Checkni Auto Public V1

This folder is the standalone public v1 of Checkni Auto. It is intentionally separate from the private `Scrapper/` app and does not expose the dashboard, local history, settings, or knowledge-base workflow in the main UI.

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
python web_server.py
```

Open `http://localhost:5000`.

## Deploy Env

- `DEMO_MODE=true`
- `DEMO_SKIP_KB=true`
- `DEMO_PROMPT_FILE=analyze_prompt_v4_koyeb.txt`
- `FLASK_SECRET_KEY`
- `GEMINI_PRIMARY_API_KEY`
- `GEMINI_BACKUP_API_KEY`
- `DEMO_RATE_LIMIT_PER_IP=3/day`
- `DEMO_MAX_CONCURRENT_JOBS=1`
- `DEMO_JOB_TTL_MINUTES=60`
- `DEMO_MAX_MANUAL_IMAGES=12`
- `DEMO_MAX_UPLOAD_MB=24`
- `SCRAPPER_DATA_DIR` optional; defaults to the platform temp directory

## Public Demo API

- `GET /healthz`
- `POST /api/demo/analyze`
  - JSON: `{ "url": "...", "output_language": "sk" }`
  - Supports automatic scraping for `autobazar.eu`, `autobazar.sk`, `bazos.sk`, and `bazos.cz`.
- `POST /api/demo/analyze-manual`
  - Multipart form: `title`, `price`, `source_url`, `manual_text`, `images`, `output_language`.

Both analysis endpoints stream Server-Sent Events and keep Gemini keys server-side.
