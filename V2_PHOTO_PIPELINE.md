# Checkni Auto V2 — photo coverage pipeline

## Product rule

The final report keeps an explicit manifest of every photo downloaded from the
listing. This does **not** mean that every photo is sent as a separate expensive
high-resolution vision input.

## Two-tier inspection

### 1. Full-gallery overview

1. Download the complete listing gallery up to the configured safety cap.
2. Create a stable `Foto NN` identity for every image.
3. Detect exact and very conservative near-duplicates.
4. Keep every original photo in `gallery_manifest.json`.
5. Select the best-quality representative from each duplicate cluster.
6. Place every unique representative into labelled 2x2 overview sheets.
7. Run one structured overview vision pass over those sheets.

A duplicate is never silently discarded. The manifest records its
`duplicate_of` relationship and the final report can explain that it shares the
overview evidence of the representative image.

### 2. Selective detail inspection

The overview model returns exact `Foto NN` detail candidates. The backend adds a
small spread safety sample so that exterior, interior, dashboard and later
gallery sections are not ignored when the overview looks clean.

Only those selected photos are sent individually at higher resolution. Detailed
findings replace coarse findings for the same photo references.

## Final report fields

`photo_analysis` contains:

- `gallery_total`
- `gallery_unique`
- `duplicate_count`
- `overview_unique_count`
- `overview_sheet_count`
- `detail_count`
- `visual_coverage_count`
- `visual_coverage_percent`
- `clusters`
- `gallery`
- structured findings and limitations

Every gallery entry includes:

- stable `id`
- `Foto NN` label
- original filename
- image dimensions
- duplicate cluster
- representative relationship
- review level: `inventory`, `overview`, `detail`, or `duplicate_reference`

## Defaults

```text
CHECKNI_MAX_GALLERY_IMAGES=80
CHECKNI_MAX_OVERVIEW_IMAGES=80
CHECKNI_MAX_DETAIL_IMAGES=8
CHECKNI_MIN_DETAIL_IMAGES=4
CHECKNI_OVERVIEW_COLLAGE_SIDE=1536
```

The defaults cover essentially all normal SK/CZ car-listing galleries while
keeping a hard abuse and resource cap. Increase the gallery cap only after
measuring scraper duration, storage and provider image limits.

## Safety properties

- Similarity grouping uses both average hash and difference hash, plus aspect
  ratio and mean-colour checks.
- Thresholds are deliberately conservative to avoid merging nearby but
  materially different angles.
- If the overview vision call fails, all photos remain in the report inventory
  but are marked as unreviewed. No visual claim is emitted.
- If only the detail pass fails, overview findings remain and the limitation is
  stated explicitly.
