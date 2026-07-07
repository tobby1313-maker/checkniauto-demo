# Header Actions + Recent Analyses Dashboard

## Summary
- Add two permanent header buttons to the demo app: `Nová Analýza` and `Dashboard`.
- `Nová Analýza` returns the UI to the analyzer landing state, clears the current rendered result, and closes any dashboard overlay/detail state.
- `Dashboard` opens a right-side slide-over panel. Its default view is a newest-first list of recent ads that already have a saved `analysis_result.md`.
- Clicking a card opens a dedicated detail view inside the slide-over with listing summary data, numbered original-image gallery, rendered saved analysis, and an `Otvoriť v hlavnom výsledku` action to load that saved analysis back into the main result area.

## Implementation Changes
- Frontend:
  - Replace the temporary runtime-created `+ New listing` button with permanent header actions and add SK/EN translation keys for both buttons plus dashboard empty/error/detail labels.
  - Add a slide-over panel with three UI states: list, detail, and loading/error. On desktop it opens from the right; on mobile it becomes full-screen.
  - Extend client state to track `dashboardOpen`, `dashboardListings`, `dashboardSelectedSlug`, `currentAnalysisSlug`, and the latest saved analysis content.
  - Capture `slug` from demo SSE `status` and `done` events during analysis so the just-finished run can be reopened without re-analysis.
  - Reuse the existing markdown renderer for dashboard detail and for hydrating the main result panel when `Otvoriť v hlavnom výsledku` is clicked.
  - Show a small dashboard note that recent analyses are temporary and can disappear after demo cleanup.

- Backend:
  - Keep the private `/api/listings*` routes blocked in demo mode; add demo-safe read-only history routes instead.
  - Add `GET /api/demo/listings` for dashboard cards. Return only listings with a saved `analysis_result.md`, sorted newest first, with card-safe fields and prebuilt image URLs.
  - Add `GET /api/demo/listings/<slug>` for dashboard detail. Return parsed listing info, source URL, scraped time, ordered image URLs, and saved `analysis_content`.
  - Add a demo-safe image route such as `GET /api/demo/listings/<slug>/image/<filename>` so thumbnails and gallery images work without opening the private routes.
  - Factor shared file-loading/image-serving helpers so demo and private endpoints use the same logic and validation.

- Dashboard card/detail behavior:
  - List cards show first image, title, price, year, mileage, photo count, and scraped timestamp.
  - Detail view shows metadata first, then numbered original photos in stored order, then the rendered saved analysis below.
  - If a listing was cleaned up by TTL between list load and detail open, show a friendly “analysis no longer available” message and return to the list.

## Public API / Interface Additions
- `GET /api/demo/listings`
  - Returns: `slug`, `title`, `price`, `currency`, `year`, `mileage`, `vin`, `photos_count`, `scraped_at`, `sort_timestamp`, `first_image_url`, `has_analysis`.
- `GET /api/demo/listings/<slug>`
  - Returns: `slug`, `parsed`, `source_url`, `scraped_at`, `images` (ordered image URLs + filenames), `analysis_content`.
- Header UI adds permanent `Nová Analýza` and `Dashboard` actions.
- Dashboard detail adds `Otvoriť v hlavnom výsledku` to restore a saved analysis into the existing output panel with copy/download/PDF enabled.

## Test Plan
- Backend:
  - Verify demo listings endpoint returns only folders with `analysis_result.md`.
  - Verify newest-first ordering still prefers `scraped_at` and falls back to mtime.
  - Verify demo detail returns `404` for missing/expired listings or listings without saved analysis.
  - Verify demo image route rejects traversal and serves only files inside the target listing image folder.

- Frontend manual:
  - Run a supported URL analysis and confirm the finished item appears in Dashboard immediately.
  - Run a manual listing with uploaded photos and confirm the dashboard detail shows the gallery in the same order.
  - Open Dashboard, open a card, confirm saved analysis renders, then click `Otvoriť v hlavnom výsledku` and confirm the main result panel is populated without rerunning AI.
  - Use `Nová Analýza` from both result mode and dashboard-open state and confirm the app resets cleanly.
  - Check responsive behavior so the panel is usable on mobile and does not break the sticky header.

## Assumptions
- Dashboard scope is `finished analyses only`.
- The dashboard stays temporary; no change to `DEMO_JOB_TTL_MINUTES` or long-term persistence is included.
- v1 dashboard detail uses original listing photos, not the `.analysis_images` collage set.
- The main result area remains the source of truth for copy/Markdown/PDF actions; dashboard detail is for browsing and reopening saved analyses.