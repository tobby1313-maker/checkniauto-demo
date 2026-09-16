"""Adaptive full-gallery coverage with bounded detailed vision inspection."""
from __future__ import annotations

import math
import os
import shutil
from typing import Any

from .image_catalog import (
    IMAGE_EXTENSIONS,
    average_hash,
    cluster_similar_items,
    detail_candidates,
    hash_distance,
    is_supported_image,
    scan_originals,
    select_representative_indices as _spread_indices,
    similarity_metadata,
)
from .image_collages import (
    LLM_COLLAGE_CELL_SIZE,
    LLM_COLLAGE_COLUMNS,
    LLM_COLLAGE_LABEL_HEIGHT,
    LLM_COLLAGE_MARGIN,
    LLM_COLLAGE_QUALITY,
    LLM_COLLAGE_ROWS,
    LLM_IMAGE_MAX_SIDE,
    LLM_IMAGE_QUALITY,
    LLM_OVERVIEW_CELL_MAX_SIZE,
    LLM_OVERVIEW_CELL_MIN_SIZE,
    LLM_OVERVIEW_LABEL_HEIGHT,
    LLM_OVERVIEW_MARGIN,
    chunk_items,
    create_llm_collage,
    create_llm_overview_sheet,
    encode_attachment,
    optimize_image_for_llm as _optimize,
    overview_grid_dimensions,
)

MAX_ANALYSIS_COLLAGES = 5
LLM_IMAGE_END_POSITION = 1.0
LLM_OVERVIEW_ATTACHMENTS = max(1, MAX_ANALYSIS_COLLAGES - 1)
MAX_ANALYSIS_IMAGES = MAX_ANALYSIS_COLLAGES * LLM_COLLAGE_COLUMNS * LLM_COLLAGE_ROWS


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


AI_MAX_VISION_ATTACHMENTS = _env_int("AI_MAX_VISION_ATTACHMENTS", 5, 1, 20)
OVERVIEW_TARGET_ITEMS_PER_SHEET = _env_int("AI_OVERVIEW_PHOTOS_PER_SHEET", 16, 8, 30)
DETAIL_ONLY_MAX_IMAGES = _env_int("AI_DETAIL_ONLY_MAX_IMAGES", 12, 4, 20)
MAX_DETAIL_COLLAGES_WITH_OVERVIEW = _env_int(
    "AI_MAX_DETAIL_COLLAGES_WITH_OVERVIEW", 3, 1, 5
)


def select_representative_indices(total: int, limit: int = MAX_ANALYSIS_IMAGES) -> list[int]:
    return _spread_indices(total, limit, LLM_IMAGE_END_POSITION)


def optimize_image_for_llm(source_path: str, output_path: str):
    return _optimize(source_path, output_path, average_hash)


def _vision_attachment_limit() -> int:
    return max(1, min(MAX_ANALYSIS_COLLAGES, AI_MAX_VISION_ATTACHMENTS))


def _deduplicate_originals(images_dir: str, originals: list[str], *, log: Any = None):
    """Compatibility helper; overview generation itself never drops cluster members."""
    readable, unreadable = scan_originals(images_dir, originals, log=log)
    clusters = cluster_similar_items(readable)
    _clusters, similar = similarity_metadata(clusters)
    representatives = sorted(
        (cluster["representative"] for cluster in clusters),
        key=lambda item: item["gallery_number"],
    )
    return representatives, similar, unreadable


def _empty_metadata(attachment_limit: int) -> dict[str, Any]:
    return {
        "photo_pipeline_version": 2,
        "coverage_mode": "none",
        "original_count": 0,
        "readable_count": 0,
        "unique_count": 0,
        "similarity_cluster_count": 0,
        "duplicate_count": 0,
        "similar_photo_count": 0,
        "unreadable_count": 0,
        "selected_originals": [],
        "selected_count": 0,
        "overview_count": 0,
        "overview_attachment_count": 0,
        "overview_image_count": 0,
        "overview_only_count": 0,
        "detail_count": 0,
        "detail_attachment_count": 0,
        "attachment_count": 0,
        "attachment_limit": attachment_limit,
        "overview_includes_all": False,
        "full_gallery_included": False,
        "deduplication_applied": False,
        "deduplication_mode": "cluster_for_detail_only",
        "all_source_photos_preserved": True,
        "all_source_photos_available_for_report": True,
        "selection_reason": "no_supported_images_found",
        "optimized_files": [],
        "collage_groups": [],
        "similarity_clusters": [],
        "gallery_manifest": [],
    }


def _manifest(
    originals: list[str],
    readable: list[dict[str, Any]],
    unreadable: list[str],
    overview_names: set[str],
    detail_names: set[str],
) -> list[dict[str, Any]]:
    by_name = {item["original_name"]: item for item in readable}
    rows: list[dict[str, Any]] = []
    for number, name in enumerate(originals, start=1):
        item = by_name.get(name)
        if item is None:
            rows.append(
                {
                    "gallery_number": number,
                    "original_name": name,
                    "inspection_level": "unreadable" if name in unreadable else "not_in_ai_payload",
                    "overview_included": False,
                    "detail_inspected": False,
                    "source_preserved": True,
                }
            )
            continue
        overview, detail = name in overview_names, name in detail_names
        rows.append(
            {
                "gallery_number": number,
                "original_name": name,
                "cluster_id": item.get("cluster_id", ""),
                "similar_to": "" if item.get("cluster_representative") == name else item.get("cluster_representative", ""),
                "width": item["width"],
                "height": item["height"],
                "quality_score": item["quality_score"],
                "inspection_level": (
                    "overview_and_detail" if overview and detail else "detail" if detail
                    else "overview_only" if overview else "not_in_ai_payload"
                ),
                "overview_included": overview,
                "detail_inspected": detail,
                "source_preserved": True,
            }
        )
    return rows


def _raw_fallback(images_dir: str, originals: list[str], limit: int, safe_log: Any):
    mimes = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".webp": "image/webp", ".bmp": "image/bmp", ".avif": "image/avif",
    }
    attachments, failed, sent = [], [], []
    for name in originals[:limit]:
        try:
            attachments.append(
                encode_attachment(
                    os.path.join(images_dir, name),
                    mimes.get(os.path.splitext(name)[1].lower(), "image/jpeg"),
                )
            )
            sent.append(name)
        except Exception as exc:
            failed.append(name)
            safe_log(f"Warning: could not read {name}: {exc}")
    metadata = _empty_metadata(limit)
    metadata.update(
        coverage_mode="raw_limited",
        original_count=len(originals),
        readable_count=len(originals) - len(failed),
        unique_count=len(originals) - len(failed),
        similarity_cluster_count=len(originals) - len(failed),
        unreadable_count=len(failed),
        unreadable_files=failed,
        selected_originals=sent,
        selected_count=len(sent),
        detail_originals=sent,
        detail_count=len(sent),
        detail_attachment_count=len(attachments),
        attachment_count=len(attachments),
        full_gallery_included=len(sent) == len(originals),
        deduplication_mode="unavailable_without_pillow",
        selection_reason="pillow_unavailable_raw_attachment_fallback",
        collage_count=len(attachments),
        optimized_files=sent,
        gallery_manifest=[
            {
                "gallery_number": index + 1,
                "original_name": name,
                "inspection_level": "detail" if name in sent else "not_in_ai_payload",
                "overview_included": False,
                "detail_inspected": name in sent,
                "source_preserved": True,
            }
            for index, name in enumerate(originals)
        ],
        error="Pillow missing; sent a limited set of original photos.",
    )
    return attachments, metadata


def _append_sheet(
    attachments: list[tuple[str, str, str]],
    optimized: list[str],
    groups: list[dict[str, Any]],
    items: list[dict[str, Any]],
    path: str,
    kind: str,
    safe_log: Any,
) -> bool:
    try:
        generated, mime = (
            create_llm_overview_sheet(items, path)
            if kind == "overview"
            else create_llm_collage(items, path)
        )
        attachments.append(encode_attachment(generated, mime))
    except Exception as exc:
        safe_log(f"Warning: Could not create {kind} sheet {os.path.basename(path)}: {exc}")
        return False
    optimized.append(os.path.basename(generated))
    groups.append(
        {
            "collage": os.path.basename(generated),
            "type": kind,
            "covers_full_gallery": False,
            "coverage_scope": "full_gallery_chunk" if kind == "overview" else "cluster_selected_detail",
            "items": [
                {
                    "number": item["gallery_number"],
                    "original_name": item["original_name"],
                    "cluster_id": item.get("cluster_id", ""),
                }
                for item in items
            ],
        }
    )
    return True


def prepare_llm_images(slug_dir: str, *, log: Any = None):
    """Keep all photos in overview; spend detail vision only on diverse candidates."""
    safe_log = log or (lambda _message: None)
    limit = _vision_attachment_limit()
    images_dir = os.path.join(str(slug_dir), "images")
    if not os.path.isdir(images_dir):
        return [], _empty_metadata(limit)
    originals = [
        name for name in sorted(os.listdir(images_dir))
        if is_supported_image(name) and os.path.isfile(os.path.join(images_dir, name))
    ]
    if not originals:
        return [], _empty_metadata(limit)
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        return _raw_fallback(images_dir, originals, limit, safe_log)

    analysis_dir = os.path.join(str(slug_dir), ".analysis_images")
    if os.path.isdir(analysis_dir):
        shutil.rmtree(analysis_dir)
    os.makedirs(analysis_dir, exist_ok=True)
    readable, unreadable = scan_originals(images_dir, originals, log=safe_log)
    if not readable:
        metadata = _empty_metadata(limit)
        metadata.update(
            original_count=len(originals), unreadable_count=len(unreadable),
            unreadable_files=unreadable, selection_reason="all_supported_images_unreadable",
            gallery_manifest=_manifest(originals, [], unreadable, set(), set()),
        )
        return [], metadata

    clusters = cluster_similar_items(readable)
    cluster_rows, similar_rows = similarity_metadata(clusters)
    similar_count = len(similar_rows)
    detail_capacity = limit * LLM_COLLAGE_COLUMNS * LLM_COLLAGE_ROWS
    use_overview = (
        len(readable) > min(DETAIL_ONLY_MAX_IMAGES, detail_capacity)
        or (similar_count > 0 and len(readable) > 4)
    )
    attachments: list[tuple[str, str, str]] = []
    optimized: list[str] = []
    groups: list[dict[str, Any]] = []
    overview_names: set[str] = set()
    detail_names: set[str] = set()
    overview_attachments = detail_attachments = 0

    if use_overview:
        overview_target = 1 if limit == 1 else min(
            max(1, math.ceil(len(readable) / OVERVIEW_TARGET_ITEMS_PER_SHEET)),
            max(1, limit - 1),
        )
        for number, items in enumerate(
            chunk_items(readable, math.ceil(len(readable) / overview_target)), start=1
        ):
            if len(attachments) >= overview_target:
                break
            path = os.path.join(analysis_dir, f"overview_{number:02d}_full_gallery.jpg")
            if _append_sheet(attachments, optimized, groups, items, path, "overview", safe_log):
                overview_attachments += 1
                overview_names.update(item["original_name"] for item in items)

        remaining = max(0, limit - len(attachments))
        wanted = min(
            MAX_DETAIL_COLLAGES_WITH_OVERVIEW,
            max(1, math.ceil(min(len(clusters), 12) / 4)),
        )
        detail_budget = min(remaining, wanted)
        selected = detail_candidates(clusters, detail_budget * 4, end_position=LLM_IMAGE_END_POSITION)
        for number, items in enumerate(chunk_items(selected, 4), start=1):
            if detail_attachments >= detail_budget:
                break
            path = os.path.join(analysis_dir, f"detail_{number:02d}_selected.jpg")
            if _append_sheet(attachments, optimized, groups, items, path, "detail", safe_log):
                detail_attachments += 1
                detail_names.update(item["original_name"] for item in items)
    else:
        for number, items in enumerate(chunk_items(readable[:detail_capacity], 4), start=1):
            if len(attachments) >= limit:
                break
            path = os.path.join(analysis_dir, f"collage_{number:02d}_llm.jpg")
            if _append_sheet(attachments, optimized, groups, items, path, "detail", safe_log):
                detail_attachments += 1
                detail_names.update(item["original_name"] for item in items)

    covered = overview_names | detail_names
    overview_all = len(overview_names) == len(readable) and bool(readable)
    detail_all = len(detail_names) == len(readable) and not unreadable
    full_gallery = len(covered) == len(readable) == len(originals) and not unreadable
    coverage_mode = (
        "detail_all" if detail_all and not use_overview
        else "full_gallery_overview" if overview_all and full_gallery
        else "detail_limited"
    )
    for group in groups:
        if group["type"] == "overview":
            group["covers_full_gallery"] = overview_all and full_gallery
        elif group["type"] == "detail":
            group["covers_full_gallery"] = detail_all and not use_overview

    selected_originals = [item["original_name"] for item in readable if item["original_name"] in covered]
    detail_originals = [item["original_name"] for item in readable if item["original_name"] in detail_names]
    overview_originals = [item["original_name"] for item in readable if item["original_name"] in overview_names]
    metadata = _empty_metadata(limit)
    metadata.update(
        coverage_mode=coverage_mode,
        original_count=len(originals),
        readable_count=len(readable),
        unique_count=len(clusters),
        similarity_cluster_count=len(clusters),
        duplicate_count=similar_count,
        similar_photo_count=similar_count,
        duplicate_files=similar_rows,
        unreadable_count=len(unreadable),
        unreadable_files=unreadable,
        selected_originals=selected_originals,
        selected_count=len(selected_originals),
        overview_originals=overview_originals,
        overview_count=overview_attachments,
        overview_attachment_count=overview_attachments,
        overview_image_count=len(overview_names),
        overview_only_count=len(overview_names - detail_names),
        detail_originals=detail_originals,
        detail_count=len(detail_names),
        detail_attachment_count=detail_attachments,
        attachment_count=len(attachments),
        overview_includes_all=overview_all,
        full_gallery_included=full_gallery,
        deduplication_applied=True,
        selection_reason=(
            "all_readable_photos_in_overview_with_cluster_selected_details"
            if use_overview and overview_all
            else "all_readable_photos_in_detail_collages" if detail_all
            else "best_effort_overview_and_detail_with_generation_failures"
        ),
        collage_count=len(attachments),
        collage_capacity=4,
        optimized_files=optimized,
        collage_groups=groups,
        similarity_clusters=cluster_rows,
        gallery_manifest=_manifest(originals, readable, unreadable, overview_names, detail_names),
    )
    return attachments, metadata


# Compatibility exports expected by legacy_server.py and the existing tests.
create_llm_collage = create_llm_collage
create_llm_overview_sheet = create_llm_overview_sheet
overview_grid_dimensions = overview_grid_dimensions
chunk_items = chunk_items
