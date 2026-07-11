# Refactoring Plan for Scrapper - DEMO

## Objective

Split the current Flask monolith into focused, testable modules without changing
the public demo behavior, deployment entry point, generated artifacts, provider
fallback behavior, or deterministic risk verdicts.

This is an incremental migration. Every phase must leave the application in a
runnable and testable state. Do not combine all extractions into one large
rewrite.

## Compatibility Contract

The refactor must preserve:

- `gunicorn web_server:app` and `python web_server.py` entry points.
- Existing public URLs, HTTP methods, status codes, and response shapes.
- SSE fields and terminal behavior: `status`, `log`, `text`, `token_usage`,
  `slug`, `done`, and `error`.
- Environment-variable names, defaults, and provider model order.
- `SCRAPPER_AUTA_DIR/<slug>/` job layout and existing artifact filenames.
- The public/private route restrictions applied by `DEMO_MODE=true`.
- Rate limiting, single-job concurrency, progress mirroring, cancellation, and
  job cleanup behavior.
- Gemini primary/backup key handling and provider fallback behavior.
- Public report normalization, validation warnings, and deterministic backend
  verdict enforcement.
- Existing saved jobs created before the refactor.

Provider-neutral names may be used internally, but compatibility filenames such
as `grok_research.json` must remain until a separately planned migration exists.

## Target Structure

Do not create a `web_server/` package next to `web_server.py`; that name can
conflict with imports and the Gunicorn entry point. Use a distinct package:

```text
scrapper_demo/
  __init__.py
  app.py                    # create_app() and Flask configuration
  config.py                 # validated environment configuration
  routes/
    demo.py                 # public demo analysis and saved reports
    dashboard.py            # token/progress dashboard APIs
    private.py              # local/private routes
    health.py               # health check
  services/
    analysis_pipeline.py    # phase orchestration and SSE events
    listing_service.py      # listing/job use cases
    image_service.py        # image selection and collage preparation
    report_service.py       # final report construction and publication
  providers/
    gemini.py
    grok.py
    openrouter.py
    retry.py                # provider retry/key fallback policy
  storage/
    listing_jobs.py         # safe job paths and artifact persistence
  progress.py               # synchronized progress/concurrency state
  validation.py             # JSON/report contract validation
  types.py                  # shared TypedDict/dataclass definitions

web_server.py               # compatibility entry point exposing app
```

Existing cohesive modules such as `risk_scorer.py`, `analysis_normalizer.py`,
`token_tracker.py`, `vin_utils.py`, and the marketplace scrapers should not be
moved merely for structural symmetry. Move them only when a later phase has a
clear dependency or testing benefit.

## Phase 0 - Establish the Baseline

**Status:** Completed on 2026-07-11. The suite now runs 71 tests with one
documented expected failure for oversized-request semaphore cleanup. Baseline
details and the known defect are recorded in `REFACTORING_BASELINE.md`.

### Work

1. Run and record the complete existing test suite:
   - `test_demo_dashboard_api.py`
   - `test_analysis_normalizer.py`
   - `test_grounded_research.py`
   - `test_output_validation.py`
   - `test_risk_scorer.py`
2. Document any pre-existing failures before refactoring.
3. Add characterization tests for behavior not sufficiently protected:
   - Demo route allowlist and rejection of private API routes.
   - Public listing/detail/artifact response shapes.
   - SSE event fields, ordering, error handling, and final `done` event.
   - Manual input validation and upload limits.
   - Safe slug, artifact, and image path handling.
   - Global concurrency lock, progress mirroring, cancellation, and TTL cleanup.
   - Existing job-folder and artifact compatibility.
4. Mock scrapers and all external model providers. Automated tests must not
   require network access or real API keys.
5. Capture a representative successful pipeline fixture containing intermediate
   JSON, risk score, validation warnings, raw report, and public report.

### Completion criteria

- All existing behavior has a recorded baseline.
- Critical public contracts have characterization tests.
- Tests run deterministically without external services.
- No production code has been reorganized yet.

## Phase 1 - Introduce Package, Configuration, and App Factory

**Status:** Completed on 2026-07-11. `scrapper_demo/` now provides typed server
configuration and `create_app()`. The root `web_server.py` remains the compatible
Gunicorn/local entry point. Subsequent phases completed the runtime-state,
provider, and blueprint migrations that this phase intentionally deferred.

**Historical migration note (resolved):** At the end of Phase 1, route
registration, process-global state, and provider configuration still lived in
the compatibility module. Phases 3, 5, and 7 moved those responsibilities to
runtime state, provider adapters, and mode-aware blueprints respectively.

### Work

1. Create `scrapper_demo/` and a `create_app(config=None)` application factory.
2. Move environment parsing into `scrapper_demo/config.py`.
3. Represent configuration with a typed object or dataclass and validate numeric
   limits once during startup.
4. Keep `web_server.py` as a compatibility wrapper that exposes `app` and keeps
   the current local-run behavior.
5. Ensure the Flask secret, upload limit, static directory, and demo mode are
   configured through the factory.
6. Avoid importing or constructing provider clients during module import.

### Completion criteria

- Both existing entry points still work.
- Route behavior and environment defaults are unchanged.
- Tests can create isolated app instances with temporary storage and explicit
  configuration.
- No `web_server.py`/package naming collision exists.

## Phase 2 - Centralize Job Storage

**Status:** Completed on 2026-07-11. `ListingJobRepository` now owns job-root
resolution, slug normalization, public artifact names/order, safe artifact and
image paths, completed-job discovery, unique slugs, and TTL cleanup. Listing
text/JSON artifacts are written atomically by the Flask routes, analysis flows,
`main.py`, and all three scraper adapters. Existing job folders remain readable.
The full suite runs 89 tests with the one Phase 0 expected failure.

**Boundary note:** Binary image downloading and collage creation still operate
on directory paths because the image utility also supports arbitrary input
directories; they move behind the image service in Phase 4. `token_usage.json`
remains owned by `token_tracker.py` as an application ledger rather than a
listing-job artifact.

### Work

1. Introduce a listing-job repository in `storage/listing_jobs.py`.
2. Centralize:
   - Safe slug resolution.
   - Job directory creation and lookup.
   - Reading and writing text/JSON artifacts.
   - Completed-listing discovery and sorting.
   - Listing image and analysis-image paths.
   - Artifact allowlists.
   - TTL cleanup.
3. Use atomic writes for final JSON and report artifacts where practical.
4. Keep all existing directory names and filenames unchanged.
5. Add repository tests for traversal attempts, missing files, partial jobs,
   existing saved jobs, and cleanup.

### Completion criteria

- Routes and pipeline code no longer construct job paths independently.
- Existing job directories remain readable.
- Path-safety and persistence tests pass.

## Phase 3 - Extract Progress and Runtime State

**Status:** Completed on 2026-07-11. Each Flask application now owns a
`DemoRuntimeState` containing synchronized progress, a daily UTC rate limiter,
and a bounded job-concurrency gate. Factory-created apps no longer leak runtime
state into one another. Multipart parsing occurs before job-slot acquisition,
fixing the Phase 0 oversized-request semaphore leak. The full suite runs 94
tests with no failures or expected failures.

**Deployment constraint:** Runtime state is intentionally in memory and
process-local. The supported Procfile continues to use one Gunicorn worker.
Multiple workers or instances require a shared external rate/progress/job-state
backend and are not supported by this phase.

### Work

1. Move current-progress state, locks, rate-limit accounting, and job concurrency
   coordination into explicit state objects.
2. Preserve thread synchronization and current one-worker semantics.
3. Inject state into app instances so tests do not share global progress or rate
   limits.
4. Document that rate limiting and concurrency are process-local while the
   deployment uses one Gunicorn worker.
5. Do not claim multi-worker support unless shared external state is implemented.

### Completion criteria

- Progress and concurrency tests preserve current behavior.
- Separate test app instances do not leak state.
- The one-worker deployment constraint is documented.

## Phase 4 - Extract Pure Image and Validation Logic

**Status:** Completed on 2026-07-11. Image selection, perceptual duplicate
filtering, optimization, detail collages, full-gallery overview sheets, and LLM
payload metadata now live in `services/image_service.py`. Schema loading, soft
JSON validation, buyer-report validation, shared Markdown/URL parsing, heading
normalization, end-marker enforcement, and atomic warning persistence now live
in `validation.py`. The full suite runs 103 tests with no failures.

**Boundary note:** `web_server.py` retains compatibility aliases so existing
tests and callers keep their current helper names. Public report cleanup remains
in `analysis_normalizer.py`, final prompt compaction remains with synthesis
construction, and Gemini grounding redirect resolution remains in the provider
client as planned.

### Work

1. Move representative-image selection, optimization, hashing, overview sheets,
   and collage creation into `services/image_service.py`.
2. Move schema loading, soft JSON contract validation, final report validation,
   and validation-warning persistence into `validation.py`.
3. Keep public markdown normalization in `analysis_normalizer.py`.
4. Keep Gemini grounding redirect resolution with the Gemini provider; it is
   provider transport behavior, not general markdown processing.
5. Keep final-context compaction with report/prompt construction rather than
   creating a generic utility collection.

### Completion criteria

- Extracted functions are independently testable.
- Image metadata and generated attachment behavior are unchanged.
- Validation remains intentionally soft for model JSON and produces the same
  warning artifact behavior.

## Phase 5 - Separate Provider Adapters and Retry Policy

**Status:** Completed on 2026-07-12. Provider transports now live in
`scrapper_demo/providers/`, shared provider errors and retry policy are
centralized, `llm_client.py` is a compatibility facade, and the remaining
analysis pipeline locals use neutral names while preserving artifact filenames
such as `grok_research.json`. The full suite now runs 113 tests.

### Work

1. Split Gemini, Grok, and OpenRouter transport code into provider modules.
2. Move the existing `_collect_gemini_with_key_fallback()` behavior into
   `providers/retry.py`; do not create a second competing helper.
3. Make retry policy explicit for:
   - Model fallback on the same key.
   - Same-key transient retries.
   - Primary-to-backup Gemini key fallback.
   - OpenRouter-to-Gemini fallback.
   - Failures after partial streamed output.
4. Preserve token tracking for every provider call.
5. Create one provider exception hierarchy and remove the duplicate
   `GrokApiKeyError` declaration.
6. Rename provider-specific local variables to neutral concepts such as
   `text_research_result`, while preserving external artifact compatibility.

### Completion criteria

- Provider and fallback behavior has focused unit tests.
- The analysis service depends on provider interfaces rather than HTTP details.
- Model order, key behavior, streaming semantics, and token accounting match the
  baseline.

## Phase 6 - Extract the Analysis Pipeline

**Status:** Completed on 2026-07-12. The orchestration now lives only in
`scrapper_demo/services/analysis_pipeline.py`; `web_server.py` composes explicit
storage, provider, token, validation, image, reporting, and configuration
dependencies and retains only a thin compatibility facade. SSE payloads use
central event builders, the service runs without a Flask request/application
context, and focused tests cover early failures plus successful phase ordering
and artifact publication. The full suite now runs 116 tests.

### Work

1. Move `_multi_model_analysis_events()` into
   `services/analysis_pipeline.py`.
2. Represent pipeline phases explicitly:
   - Grounded web research.
   - Structured text/research analysis.
   - Vision analysis.
   - Photo VIN injection.
   - Deterministic risk scoring.
   - Final synthesis.
   - Normalization and validation.
   - Artifact publication.
3. Introduce typed SSE event builders instead of assembling event dictionaries
   inconsistently throughout the generator.
4. Inject storage, providers, token tracking, progress state, and configuration.
5. Preserve graceful continuation rules when research or vision is unavailable.
6. Keep `risk_scorer.py` independent and authoritative for the allowed final
   verdict.

### Completion criteria

- The pipeline can be tested without a Flask request context.
- Phase ordering, artifacts, SSE events, fallback behavior, and final verdict
  match the baseline fixture.
- Pipeline failures cleanly release concurrency state.

## Phase 7 - Split Routes into Blueprints

**Status:** Completed on 2026-07-12. Public/demo and private route contracts now
live in `scrapper_demo/routes/` and are registered as Flask blueprints. Factory
apps register only the public blueprint in demo mode and both blueprints in
private mode, while the route gate remains active as defense in depth. The root
`web_server.py` is now a compatibility startup/import shim; legacy composition
helpers live in `scrapper_demo/legacy_server.py`, and registered route adapters
contain no provider or filesystem implementation details. URL, method, response,
static frontend, and SSE contract tests pass. The full suite now runs 117 tests.

### Work

1. Move thin HTTP handlers into separate Flask blueprints.
2. Route handlers should validate HTTP input, call services, and translate
   results into HTTP/SSE responses; they should not contain analysis logic.
3. Register public demo blueprints in demo mode.
4. Prefer not registering private blueprints in demo mode, while retaining the
   route gate as defense in depth during migration.
5. Preserve route paths, methods, status codes, MIME types, and response bodies.
6. Keep static frontend serving compatible with current URLs.

### Completion criteria

- Public API contract tests pass without changes.
- Private routes are unavailable in demo mode.
- `web_server.py` contains only compatibility startup code.
- Route modules contain no provider or filesystem implementation details.

## Phase 8 - Consolidate Logging and Add Types

**Status:** Completed on 2026-07-12. Unicode-safe console setup and output now
live only in `scrapper_demo/logging.py`; the server, CLI, provider adapters, and
compatibility facade share that implementation. `scrapper_demo/contracts.py`
defines listing, pipeline input/artifact, provider, retry-key, risk-score, SSE,
and repository protocol contracts, which are applied at the corresponding
stable boundaries. A gradual `mypy.ini` and `requirements-dev.txt` introduce
optional type checking without making runtime installation depend on it. No
circular imports were introduced, and the full suite now runs 119 tests.

### Work

1. Replace duplicate console setup and `safe_log()` implementations with one
   small logging module, or standard Python logging configured at startup.
2. Do not create a broad `scrapper_utils.py` dumping ground.
3. Add type definitions for:
   - Parsed listing data.
   - Pipeline inputs and artifacts.
   - Provider results and exceptions.
   - Risk-score result.
   - SSE payloads.
4. Prefer `TypedDict`, dataclasses, protocols, and precise optional types over
   widespread `dict[str, Any]`.
5. Add a type checker only after the module boundaries stabilize; introduce it
   gradually without blocking unrelated runtime fixes.

### Completion criteria

- Duplicate logging helpers are removed.
- Critical interfaces have useful types.
- No circular imports are introduced.

## Phase 9 - Resolve Stale and Legacy Behavior

**Status:** Completed on 2026-07-12. The absent `Mobile_de.py` branch and all CLI
claims of automatic Mobile.de support were removed; Mobile.de remains supported
through manual import only. The unreachable pre-refactor analysis pipeline and
unused Gemini retry compatibility aliases were removed. Private KB routes are
retained because local-mode callers and contract tests still require them, while
`DEMO_SKIP_KB` now prevents the public demo pipeline from writing KB blocks.
Runtime and development dependencies use exact direct-version pins, including
the Python 3.11-compatible deployment server and optional type checker. The
frontend monolith was not changed, and the full suite now runs 123 tests.

### Work

1. Resolve the `main.py` Mobile.de branch referencing the absent `Mobile_de.py`:
   either remove demo support claims from the CLI or restore it only as a
   separately approved feature. Public demo behavior remains manual-only.
2. Review private knowledge-base routes and helpers. Remove them from the demo
   package only after confirming they are not required for local compatibility.
3. Remove obsolete aliases and compatibility shims only when no callers remain.
4. Review dependency versions and choose a reproducible pinning strategy.
5. Do not refactor `web/index.html` in this backend migration. Create a separate
   frontend plan if that monolith is to be split.

### Completion criteria

- No code advertises unavailable scraper files.
- Legacy behavior is either tested and retained or explicitly removed.
- Backend refactoring has not silently expanded into a frontend rewrite.

## Phase 10 - Final Verification and Documentation

**Status:** Completed on 2026-07-12. The complete unit, contract, integration,
and mocked smoke suite passes with 126 tests. Mocked manual and URL flows,
legacy-job loading, new artifact publication, cancellation, rate limiting,
concurrency release, cleanup, and error SSE behavior are covered. The local
entry point invokes the packaged compatibility app, the Procfile target imports
and passes `/healthz`, and `README_DEMO.md` documents the final structure,
factory usage, configuration, test commands, process-local one-worker limit,
dependency policy, and intentionally retained compatibility boundaries. No
real provider calls or frontend refactor were performed.

### Work

1. Run the complete unit and characterization test suite.
2. Perform local smoke tests for URL and manual analysis using mocked or approved
   provider access.
3. Verify the production Gunicorn command and health check.
4. Verify that old saved jobs load and new jobs create the expected artifacts.
5. Verify cancellation, rate limiting, concurrency release, cleanup, and error
   SSE behavior.
6. Update `README_DEMO.md` with:
   - New module structure.
   - Complete test command.
   - App-factory usage where relevant.
   - One-worker/process-local state limitation.
   - Current configuration variables and defaults.
7. Remove temporary compatibility code only if deployment and tests no longer
   require it.

### Completion criteria

- All tests pass.
- Public API and artifact contracts are unchanged.
- Both local and Gunicorn entry points start correctly.
- Documentation matches the final structure.
- No temporary migration task remains undocumented.

## Working Rules for Every Phase

- Make one architectural change at a time.
- Run the relevant focused tests after each extraction and the full suite at the
  end of each phase.
- Keep commits small enough to review and revert independently.
- Do not mix behavior changes with file moves unless the behavior change is
  explicitly listed in that phase.
- Preserve unrelated working-tree changes.
- Stop and update this plan if an extraction reveals a new dependency that would
  reverse the intended dependency direction.
