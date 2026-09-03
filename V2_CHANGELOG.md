# Checkni Auto V2 changelog

## 2.0.0

### Product

- Replaced the developer-style analyzer console with a buyer-facing landing page and decision report.
- Added Slovak and Czech report/UI language selection.
- Added verdict, safety score, confidence, listing completeness, prioritized findings, photo findings, price assessment, 30,000 km cost reserve, seller questions, inspection checklist, sources and limitations.
- Added browser refresh recovery through persistent job IDs.
- Added print/PDF workflow and JSON export.
- Added a collapsible final-report gallery containing every preserved listing photo and its inspection level.

### Analysis pipeline

- Added deterministic normalization over Markdown and raw scraper JSON.
- Added a V2 Bazoš SK/CZ scraper with correct CZK handling, domain-aware image URLs and parallel downloads.
- Added weighted listing-completeness scoring and explicit missing-data findings.
- Runs visual analysis and grounded web research in parallel.
- Uses structured JSON output for every AI stage.
- Uses current configurable stable model identifiers instead of retired Gemini 2.0 fallbacks.
- Adds provider/model/key retry paths and a deterministic final-report fallback.
- Prevents unsupported market data from being presented with high confidence.
- Verifies market comparables and web-risk URLs against actual Google Search citation annotations; three verified comparables are required for a supported market range.

### Full-gallery photo coverage

- Separates the complete gallery limit from overview and individual-detail limits.
- Keeps every successfully downloaded listing photo in the final manifest with a stable `Foto NN` reference.
- Groups only conservative near-duplicates using exact pixel digests, average hash, difference hash, aspect ratio and mean-colour distance.
- Uses the best-quality representative from every unique group in labelled 2×2 overview sheets.
- Runs selective high-resolution inspection on model-nominated risks plus a spread safety sample.
- Records `inventory`, `overview`, `detail` and `duplicate_reference` status for every photo.
- Preserves all photos without visual claims if the vision provider fails.

### Reliability and security

- Added background jobs with atomic JSON state files and SSE status updates.
- Added bounded concurrent/pending jobs.
- Added URL allowlisting to reduce SSRF risk.
- Added image extension and Pillow validation.
- Added upload size limits, security headers and beta rate limiting.
- Marks interrupted jobs as failed after a server restart so billing can avoid charging them.
- Serves optimized gallery photos only when their IDs are declared by a completed report.

### Deployment

- `Procfile` now launches `v2_entry:app` with a threaded Gunicorn worker.
- Original `web_server.py` remains available for rollback.
