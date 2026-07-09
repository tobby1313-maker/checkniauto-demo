# Refactoring Plan for Scrapper - DEMO

## 1. Extract `web_server.py` into modular structure
The 4,287-line monolith needs to be split into focused modules:
- `web_server/routes.py` — Flask route handlers
- `web_server/analysis.py` — Analysis pipeline logic
- `web_server/images.py` — Image processing utilities
- `web_server/validation.py` — Report validation logic
- `web_server/progress.py` — SSE progress tracking
- Keep `web_server.py` as minimal app entry point

## 2. Eliminate code duplication
- **Duplicate `_configure_console_encoding()` and `safe_log()`**: Both `web_server.py` and `llm_client.py` define identical functions. Extract to `scrapper_utils.py` and import from a single location.

## 3. Simplify nested retry logic
The Gemini retry loop in `_multi_model_analysis_events()` (lines ~3528-3605) repeats similar patterns 3+ times. Extract a `_gemini_with_key_fallback()` helper that encapsulates the retry-with-next-key logic used for both Gemini primary and OpenRouter fallback flows.

## 4. Consolidate markdown processing helpers
Group related helpers into a class or module:
- URL/redirect processing (`_resolve_annotation_redirects`, `_sanitize_source_item`, `_is_verified_public_url`)
- Markdown normalization (`_ensure_end_analysis_marker`, `_replace_photo_analysis_section`, `_normalize_report_headings`)
- Text compacting (`_compact_text_research_for_final`, `_compact_vision_for_final`, `_compact_risk_score_for_final`)

## 5. Add type hints to critical paths
Key functions currently lack type hints:
- `parse_car_info_md(md_text)` → `dict[str, Any]`
- `_read_listing_analysis_content(slug_dir, required=False)` → `str | None`
- Internal pipeline context builders