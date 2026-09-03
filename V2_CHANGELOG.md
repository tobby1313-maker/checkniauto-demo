# Checkni Auto V2 changelog

## 2.0.0

### Product

- Replaced the developer-style analyzer console with a buyer-facing landing page and decision report.
- Added Slovak and Czech report/UI language selection.
- Added verdict, safety score, confidence, listing completeness, prioritized findings, photo findings, price assessment, 30,000 km cost reserve, seller questions, inspection checklist, sources and limitations.
- Added browser refresh recovery through persistent job IDs.
- Added print/PDF workflow and JSON export.

### Analysis pipeline

- Added deterministic normalization over Markdown and raw scraper JSON.
- Added weighted listing-completeness scoring and explicit missing-data findings.
- Runs visual analysis and grounded web research in parallel.
- Uses structured JSON output for every AI stage.
- Uses current configurable stable model identifiers instead of retired Gemini 2.0 fallbacks.
- Adds provider/model/key retry paths and a deterministic final-report fallback.
- Prevents unsupported market data from being presented with high confidence.

### Reliability and security

- Added background jobs with atomic JSON state files and SSE status updates.
- Added bounded concurrent/pending jobs.
- Added URL allowlisting to reduce SSRF risk.
- Added image extension and Pillow validation.
- Added upload size limits, security headers and beta rate limiting.
- Marks interrupted jobs as failed after a server restart so billing can avoid charging them.

### Deployment

- `Procfile` now launches `v2_app:app` with a threaded Gunicorn worker.
- Original `web_server.py` remains available for rollback.
