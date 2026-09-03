from __future__ import annotations

import concurrent.futures
import json
import time
from pathlib import Path
from typing import Any

from v2_ai import (
    analyze_photos,
    research_vehicle,
    synthesize_report,
    unavailable_photo,
    unavailable_research,
)
from v2_core import (
    MAX_VISION_IMAGES,
    SUPPORTED_HOSTS,
    TEXT_MODEL,
    VISION_MODEL,
    PipelineError,
    ProgressCallback,
    _emit,
    calculate_data_quality,
    is_supported_url,
    normalize_language,
    normalize_listing,
    prepare_images,
    scrape_listing,
    utc_now,
)
from v2_report import build_fallback_report, sanitize_report


def run_analysis_pipeline(
    job_id: str,
    job_dir: Path,
    language: str,
    callback: ProgressCallback | None = None,
    source_url: str = "",
    existing_listing_dir: Path | None = None,
) -> dict[str, Any]:
    started_at = time.monotonic()
    language = normalize_language(language)
    job_dir.mkdir(parents=True, exist_ok=True)

    if existing_listing_dir is None:
        listing_dir = scrape_listing(source_url, job_dir, callback)
    else:
        listing_dir = existing_listing_dir
        _emit(callback, "scraping", 28, "Manuálne údaje sú pripravené.")

    _emit(callback, "normalizing", 34, "Kontrolujem úplnosť a konzistenciu údajov.")
    listing = normalize_listing(listing_dir, source_url=source_url)
    (job_dir / "normalized_listing.json").write_text(
        json.dumps(listing, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _emit(
        callback,
        "normalizing",
        42,
        f"Úplnosť inzerátu: {listing['data_quality']['score']} %.",
        {
            "listing_preview": {
                key: value
                for key, value in listing.items()
                if key not in {"raw_listing", "description"}
            }
        },
    )

    images = prepare_images(listing_dir, job_dir)
    _emit(
        callback,
        "analysis",
        48,
        f"Spúšťam paralelnú kontrolu fotografií a webové overenie ({len(images)} fotiek).",
    )

    photo: dict[str, Any] = unavailable_photo("Fotografická analýza sa nespustila.")
    research: dict[str, Any] = unavailable_research("Webové overenie sa nespustilo.")

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            pool.submit(analyze_photos, listing, images, language): "photos",
            pool.submit(research_vehicle, listing, language): "research",
        }
        completed_count = 0
        for future in concurrent.futures.as_completed(futures):
            module = futures[future]
            completed_count += 1
            try:
                value = future.result()
                if module == "photos":
                    photo = value
                    message = "Fotografie sú vyhodnotené."
                else:
                    research = value
                    message = "Technické a trhové podklady sú pripravené."
            except Exception as exc:
                if module == "photos":
                    photo = unavailable_photo(str(exc), len(images))
                    message = "Fotografický modul nebol dostupný; pokračujem bez neho."
                else:
                    research = unavailable_research(str(exc))
                    message = "Webové overenie nebolo dostupné; pokračujem s údajmi inzerátu."
            progress = 60 if completed_count == 1 else 72
            _emit(callback, module, progress, message)

    (job_dir / "photo_analysis.json").write_text(
        json.dumps(photo, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (job_dir / "web_research.json").write_text(
        json.dumps(research, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    _emit(callback, "synthesis", 80, "Skladám rozhodovací report a kontrolujem jeho úplnosť.")
    try:
        raw_report = synthesize_report(listing, photo, research, language)
    except Exception:
        raw_report = build_fallback_report(listing, photo, research, language)
        raw_report["_fallback_used"] = True

    report = sanitize_report(
        raw_report,
        listing,
        photo,
        research,
        language,
        job_id,
        started_at,
    )
    (job_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _emit(callback, "complete", 100, "Analýza je hotová.", {"report": report})
    return report
