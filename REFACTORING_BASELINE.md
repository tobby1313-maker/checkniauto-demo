# Refactoring Baseline

Recorded before structural refactoring of `Scrapper - DEMO`.

## Baseline run

- Date: 2026-07-11
- Runtime: Python 3.11-compatible project runtime
- Command:

```powershell
python -m unittest test_demo_dashboard_api.py test_analysis_normalizer.py test_grounded_research.py test_output_validation.py test_risk_scorer.py
```

- Result: 58 tests passed in 0.709 seconds.
- Pre-existing failures: none.
- External API calls: none.

Timing is informational and is not a refactoring acceptance criterion.

## Phase 0 additions

`test_phase0_contracts.py` adds characterization coverage for:

- Public demo route allowlisting and private API rejection.
- Saved-job response shapes and artifact filename/order compatibility.
- URL and manual-input validation.
- SSE response headers, payload fields, event ordering, errors, and terminal marker.
- Progress mirroring.
- Per-IP daily rate accounting.
- Job TTL cleanup.
- Semaphore release after validation errors and client disconnects.
- Oversized-request semaphore cleanup as a known expected failure.

All provider and post-processing work used by the new HTTP streaming tests is
mocked. The tests do not need network access or API keys.

## Phase 0 verification

Final command:

```powershell
python -m unittest test_demo_dashboard_api.py test_analysis_normalizer.py test_grounded_research.py test_output_validation.py test_risk_scorer.py test_phase0_contracts.py
```

Final result:

- 71 tests executed in 0.634 seconds.
- 70 tests passed normally.
- 1 known regression test is marked as an expected failure.
- No unexpected failures or errors.
- No production module was changed during Phase 0.

The baseline run reported a Python deprecation warning for `datetime.utcnow()`
in the daily rate-limit key. Phase 3 replaced this with timezone-aware UTC bucket
generation, and the warning no longer occurs.

## Representative successful job fixture

`test_fixtures/successful_pipeline/` captures the compatibility layout for a
completed analysis job:

- `car_info.md`
- `raw_data.json`
- `analysis_request.md`
- `vin_decoded.json`
- `web_research.md`
- `grok_research.json`
- `gemini_vision.json`
- `risk_score.json`
- `validation_warnings.json`
- `analysis_result_raw.md`
- `analysis_result.md`

The fixture is deterministic and intentionally contains no real customer data,
API output, API key, or network dependency.

## Resolved Phase 0 defect

The Phase 0 baseline found that an oversized multipart request could raise HTTP
413 after the concurrency semaphore was acquired, leaving later jobs blocked.
Phase 3 now parses multipart input before acquiring a job slot. The regression
test passes normally, and its `expectedFailure` marker has been removed.
