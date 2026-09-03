from __future__ import annotations

import hashlib
import json
import math
import shutil
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageStat

from v2_config import (
    DUPLICATE_AHASH_DISTANCE,
    DUPLICATE_COLOR_DISTANCE,
    DUPLICATE_DHASH_DISTANCE,
    IMAGE_MAX_SIDE,
    IMAGE_QUALITY,
    MAX_DETAIL_IMAGES,
    MAX_GALLERY_IMAGES,
    MAX_OVERVIEW_IMAGES,
    MIN_DETAIL_IMAGES,
    OVERVIEW_COLLAGE_CAPACITY,
    OVERVIEW_COLLAGE_COLUMNS,
    OVERVIEW_COLLAGE_ROWS,
    OVERVIEW_COLLAGE_SIDE,
)

_ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".avif"}


def _natural_key(path: Path) -> tuple[Any, ...]:
    import re

    parts = re.split(r"(\d+)", path.name.lower())
    return tuple(int(part) if part.isdigit() else part for part in parts)


def _average_hash(image: Image.Image) -> int:
    small = image.convert("L").resize((8, 8), Image.Resampling.LANCZOS)
    pixels = list(small.getdata())
    average = sum(pixels) / max(1, len(pixels))
    value = 0
    for pixel in pixels:
        value = (value << 1) | int(pixel >= average)
    return value


def _difference_hash(image: Image.Image) -> int:
    small = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    pixels = list(small.getdata())
    value = 0
    for row in range(8):
        offset = row * 9
        for column in range(8):
            value = (value << 1) | int(pixels[offset + column] >= pixels[offset + column + 1])
    return value


def _hash_distance(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def _mean_rgb(image: Image.Image) -> tuple[float, float, float]:
    sample = image.convert("RGB").resize((32, 32), Image.Resampling.BILINEAR)
    means = ImageStat.Stat(sample).mean
    return float(means[0]), float(means[1]), float(means[2])


def _pixel_digest(image: Image.Image) -> str:
    canonical = ImageOps.fit(
        image.convert("RGB"),
        (192, 192),
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )
    return hashlib.sha256(canonical.tobytes()).hexdigest()


def _quality_score(image: Image.Image) -> float:
    width, height = image.size
    gray = image.convert("L").resize((256, 256), Image.Resampling.LANCZOS)
    edge_variance = float(ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES)).var[0])
    resolution = math.log2(max(2, width * height))
    return round(resolution + min(edge_variance, 2500.0) / 250.0, 4)


def _feature(image: Image.Image) -> dict[str, Any]:
    width, height = image.size
    return {
        "width": width,
        "height": height,
        "aspect": width / max(1, height),
        "ahash": _average_hash(image),
        "dhash": _difference_hash(image),
        "mean_rgb": _mean_rgb(image),
        "pixel_digest": _pixel_digest(image),
        "quality": _quality_score(image),
    }


def _color_distance(left: Iterable[float], right: Iterable[float]) -> float:
    return math.sqrt(sum((float(a) - float(b)) ** 2 for a, b in zip(left, right)))


def _near_duplicate(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left["pixel_digest"] == right["pixel_digest"]:
        return True

    aspect_delta = abs(float(left["aspect"]) - float(right["aspect"]))
    if aspect_delta > 0.035:
        return False

    if _color_distance(left["mean_rgb"], right["mean_rgb"]) > DUPLICATE_COLOR_DISTANCE:
        return False

    return (
        _hash_distance(int(left["ahash"]), int(right["ahash"]))
        <= DUPLICATE_AHASH_DISTANCE
        and _hash_distance(int(left["dhash"]), int(right["dhash"]))
        <= DUPLICATE_DHASH_DISTANCE
    )


def _safe_font(size: int) -> ImageFont.ImageFont:
    for candidate in ("DejaVuSans-Bold.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _save_gallery_copy(image: Image.Image, destination: Path) -> None:
    prepared = image.copy()
    prepared.thumbnail((IMAGE_MAX_SIDE, IMAGE_MAX_SIDE), Image.Resampling.LANCZOS)
    if prepared.mode != "RGB":
        prepared = prepared.convert("RGB")
    prepared.save(
        destination,
        format="JPEG",
        quality=IMAGE_QUALITY,
        optimize=True,
        progressive=True,
    )


def _spread_items(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if len(items) <= limit:
        return list(items)
    if limit <= 1:
        return [items[0]]

    selected_indices = {
        round(index * (len(items) - 1) / (limit - 1))
        for index in range(limit)
    }
    # A high-quality detail can be more useful than two adjacent generic views.
    by_quality = sorted(
        range(len(items)),
        key=lambda index: float(items[index]["_feature"]["quality"]),
        reverse=True,
    )
    for index in by_quality:
        if len(selected_indices) >= limit:
            break
        selected_indices.add(index)
    return [items[index] for index in sorted(selected_indices)[:limit]]


def _chunk(items: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _create_overview_sheet(
    items: list[dict[str, Any]],
    destination: Path,
    sheet_number: int,
) -> dict[str, Any]:
    side = OVERVIEW_COLLAGE_SIDE
    columns = OVERVIEW_COLLAGE_COLUMNS
    rows = OVERVIEW_COLLAGE_ROWS
    margin = max(10, side // 120)
    cell_width = (side - margin * (columns + 1)) // columns
    cell_height = (side - margin * (rows + 1)) // rows
    label_height = max(36, side // 38)
    image_height = cell_height - label_height

    canvas = Image.new("RGB", (side, side), "white")
    draw = ImageDraw.Draw(canvas)
    font = _safe_font(max(18, side // 64))
    small_font = _safe_font(max(14, side // 82))

    contains: list[str] = []
    for slot, item in enumerate(items):
        row = slot // columns
        column = slot % columns
        left = margin + column * (cell_width + margin)
        top = margin + row * (cell_height + margin)

        members = item.get("cluster_members", [])
        duplicate_suffix = f" (+{len(members) - 1} podobné)" if len(members) > 1 else ""
        label = f"{item['label']}{duplicate_suffix}"
        draw.rectangle(
            (left, top, left + cell_width, top + label_height),
            fill=(23, 31, 45),
        )
        draw.text((left + 10, top + 5), label, fill="white", font=font)

        with Image.open(item["gallery_path"]) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            image.thumbnail((cell_width, image_height), Image.Resampling.LANCZOS)
            background = Image.new("RGB", (cell_width, image_height), (244, 246, 248))
            x = (cell_width - image.width) // 2
            y = (image_height - image.height) // 2
            background.paste(image, (x, y))
            canvas.paste(background, (left, top + label_height))

        draw.rectangle(
            (left, top + label_height, left + cell_width, top + cell_height),
            outline=(199, 205, 214),
            width=2,
        )
        draw.text(
            (left + 10, top + cell_height - max(24, side // 70)),
            item["original_name"][:54],
            fill=(55, 65, 81),
            font=small_font,
        )
        contains.append(item["label"])

    canvas.save(
        destination,
        format="JPEG",
        quality=max(70, IMAGE_QUALITY),
        optimize=True,
        progressive=True,
    )
    return {
        "number": sheet_number,
        "label": f"Prehľad {sheet_number:02d}",
        "original_name": destination.name,
        "path": str(destination),
        "contains": contains,
    }


def prepare_gallery(listing_dir: Path, job_dir: Path) -> dict[str, Any]:
    """Inventory every listing photo, conservatively group duplicates and build overview sheets.

    No photo disappears from the manifest. A near duplicate can share the representative
    overview inspection of its cluster, while still remaining an explicit final-report item.
    """
    source_dir = listing_dir / "images"
    package: dict[str, Any] = {
        "gallery_total": 0,
        "gallery_unique": 0,
        "duplicate_count": 0,
        "overview_unique_count": 0,
        "overview_sheet_count": 0,
        "gallery": [],
        "clusters": [],
        "overview_images": [],
        "job_dir": str(job_dir),
    }
    if not source_dir.exists():
        return package

    originals = sorted(
        (
            path
            for path in source_dir.iterdir()
            if path.is_file() and path.suffix.lower() in _ALLOWED_SUFFIXES
        ),
        key=_natural_key,
    )[:MAX_GALLERY_IMAGES]
    if not originals:
        return package

    gallery_dir = job_dir / "gallery"
    overview_dir = job_dir / "vision_overview"
    detail_dir = job_dir / "vision_detail"
    for directory in (gallery_dir, overview_dir, detail_dir):
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, Any]] = []
    for index, source in enumerate(originals, start=1):
        try:
            with Image.open(source) as opened:
                image = ImageOps.exif_transpose(opened)
                image.load()
                if image.width < 32 or image.height < 32:
                    continue
                feature = _feature(image)
                photo_id = f"photo-{index:03d}"
                label = f"Foto {index:02d}" if index < 100 else f"Foto {index:03d}"
                gallery_path = gallery_dir / f"{photo_id}.jpg"
                _save_gallery_copy(image, gallery_path)
        except Exception:
            continue

        entries.append(
            {
                "id": photo_id,
                "label": label,
                "original_name": source.name,
                "source_path": str(source),
                "gallery_path": str(gallery_path),
                "width": int(feature["width"]),
                "height": int(feature["height"]),
                "cluster_id": "",
                "representative": False,
                "duplicate_of": "",
                "review_level": "inventory",
                "_feature": feature,
            }
        )

    clusters: list[list[dict[str, Any]]] = []
    for entry in entries:
        matched: list[dict[str, Any]] | None = None
        for cluster in clusters:
            if any(_near_duplicate(entry["_feature"], member["_feature"]) for member in cluster):
                matched = cluster
                break
        if matched is None:
            clusters.append([entry])
        else:
            matched.append(entry)

    representatives: list[dict[str, Any]] = []
    public_clusters: list[dict[str, Any]] = []
    for cluster_index, cluster in enumerate(clusters, start=1):
        cluster_id = f"cluster-{cluster_index:03d}"
        representative = max(
            cluster,
            key=lambda item: (
                float(item["_feature"]["quality"]),
                int(item["width"]) * int(item["height"]),
            ),
        )
        member_labels = [item["label"] for item in cluster]
        for item in cluster:
            item["cluster_id"] = cluster_id
            item["representative"] = item is representative
            item["duplicate_of"] = "" if item is representative else representative["label"]
        representative["cluster_members"] = member_labels
        representatives.append(representative)
        public_clusters.append(
            {
                "id": cluster_id,
                "representative": representative["label"],
                "members": member_labels,
            }
        )

    representatives.sort(key=lambda item: entries.index(item))
    overview_representatives = _spread_items(representatives, MAX_OVERVIEW_IMAGES)
    overview_refs = {item["label"] for item in overview_representatives}
    for entry in entries:
        if entry["representative"] and entry["label"] in overview_refs:
            entry["review_level"] = "overview"
        elif entry["duplicate_of"]:
            entry["review_level"] = "duplicate_reference"

    overview_images: list[dict[str, Any]] = []
    for sheet_number, sheet_items in enumerate(
        _chunk(overview_representatives, OVERVIEW_COLLAGE_CAPACITY),
        start=1,
    ):
        destination = overview_dir / f"overview_{sheet_number:02d}.jpg"
        overview_images.append(_create_overview_sheet(sheet_items, destination, sheet_number))

    package.update(
        {
            "gallery_total": len(entries),
            "gallery_unique": len(representatives),
            "duplicate_count": max(0, len(entries) - len(representatives)),
            "overview_unique_count": len(overview_representatives),
            "overview_sheet_count": len(overview_images),
            "gallery": entries,
            "clusters": public_clusters,
            "overview_images": overview_images,
        }
    )
    (job_dir / "gallery_manifest.json").write_text(
        json.dumps(public_gallery(package), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return package


def _normalize_photo_ref(value: Any) -> str:
    import re

    text = str(value or "").strip()
    match = re.search(r"(?:foto|photo)[\s_-]*0*(\d{1,3})", text, flags=re.I)
    if not match:
        return ""
    number = int(match.group(1))
    return f"Foto {number:02d}" if number < 100 else f"Foto {number:03d}"


def prepare_detail_images(
    package: dict[str, Any],
    requested_refs: Iterable[Any],
) -> list[dict[str, Any]]:
    """Choose flagged representatives plus a spread safety sample for detailed inspection."""
    gallery = [item for item in package.get("gallery", []) if isinstance(item, dict)]
    representatives = [item for item in gallery if item.get("representative")]
    if not representatives:
        return []

    by_label = {str(item.get("label")): item for item in representatives}
    selected: list[dict[str, Any]] = []
    selected_labels: set[str] = set()

    def add(item: dict[str, Any] | None) -> None:
        if not item or len(selected) >= MAX_DETAIL_IMAGES:
            return
        label = str(item.get("label") or "")
        if not label or label in selected_labels:
            return
        selected.append(item)
        selected_labels.add(label)

    for raw_ref in requested_refs:
        normalized = _normalize_photo_ref(raw_ref)
        direct = by_label.get(normalized)
        if direct:
            add(direct)
            continue
        # A model or caller can reference a duplicate; inspect its highest-quality representative.
        duplicate_entry = next(
            (item for item in gallery if str(item.get("label")) == normalized),
            None,
        )
        if duplicate_entry:
            add(by_label.get(str(duplicate_entry.get("duplicate_of") or normalized)))

    # Even a clean overview gets a small spread sample at full detail. Listing galleries
    # commonly place exterior, interior, dashboard and engine photos in different sections.
    ordered = sorted(representatives, key=lambda item: gallery.index(item))
    for item in _spread_items(ordered, min(MIN_DETAIL_IMAGES, len(ordered))):
        add(item)
    for item in sorted(
        ordered,
        key=lambda value: float(value.get("_feature", {}).get("quality", 0.0)),
        reverse=True,
    ):
        if len(selected) >= min(MIN_DETAIL_IMAGES, MAX_DETAIL_IMAGES):
            break
        add(item)

    detail_dir = Path(str(package.get("job_dir") or ".")) / "vision_detail"
    detail_dir.mkdir(parents=True, exist_ok=True)
    detail_images: list[dict[str, Any]] = []
    for number, item in enumerate(selected[:MAX_DETAIL_IMAGES], start=1):
        source = Path(str(item["source_path"]))
        destination = detail_dir / f"detail_{number:02d}_{item['id']}.jpg"
        try:
            with Image.open(source) as opened:
                image = ImageOps.exif_transpose(opened)
                image.load()
                _save_gallery_copy(image, destination)
        except Exception:
            continue
        detail_images.append(
            {
                "number": number,
                "label": item["label"],
                "original_name": item["original_name"],
                "path": str(destination),
            }
        )
    return detail_images


def reset_review_levels(package: dict[str, Any]) -> None:
    """Mark the gallery as inventoried only when the overview model did not complete."""
    for item in package.get("gallery", []):
        if isinstance(item, dict):
            item["review_level"] = "inventory"


def mark_detail_reviewed(
    package: dict[str, Any],
    reviewed_refs: Iterable[Any],
) -> None:
    reviewed = {_normalize_photo_ref(value) for value in reviewed_refs}
    reviewed.discard("")
    for item in package.get("gallery", []):
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "")
        representative = str(item.get("duplicate_of") or label)
        if label in reviewed or representative in reviewed:
            # Only the representative itself was inspected in high resolution. Duplicates
            # keep their explicit relationship rather than being overstated as detailed.
            item["review_level"] = "detail" if label in reviewed else "duplicate_reference"


def public_gallery(package: dict[str, Any]) -> dict[str, Any]:
    gallery = []
    for item in package.get("gallery", []):
        if not isinstance(item, dict):
            continue
        gallery.append(
            {
                "id": item.get("id"),
                "label": item.get("label"),
                "original_name": item.get("original_name"),
                "width": item.get("width"),
                "height": item.get("height"),
                "cluster_id": item.get("cluster_id"),
                "representative": bool(item.get("representative")),
                "duplicate_of": item.get("duplicate_of") or "",
                "review_level": item.get("review_level") or "inventory",
            }
        )

    overviewed_clusters = {
        item.get("cluster_id")
        for item in package.get("gallery", [])
        if isinstance(item, dict) and item.get("review_level") in {"overview", "detail"}
    }
    visually_covered = sum(
        1
        for item in package.get("gallery", [])
        if isinstance(item, dict)
        and (
            item.get("cluster_id") in overviewed_clusters
            or item.get("review_level") == "detail"
        )
    )
    total = int(package.get("gallery_total", len(gallery)) or 0)
    return {
        "gallery_total": total,
        "gallery_unique": int(package.get("gallery_unique", 0) or 0),
        "duplicate_count": int(package.get("duplicate_count", 0) or 0),
        "overview_unique_count": int(package.get("overview_unique_count", 0) or 0),
        "overview_sheet_count": int(package.get("overview_sheet_count", 0) or 0),
        "detail_count": sum(1 for item in gallery if item["review_level"] == "detail"),
        "visual_coverage_count": visually_covered,
        "visual_coverage_percent": round((visually_covered / total) * 100) if total else 0,
        "clusters": [
            cluster
            for cluster in package.get("clusters", [])
            if isinstance(cluster, dict)
        ],
        "gallery": gallery,
    }


# Compatibility for older callers. New code should use prepare_gallery().
def prepare_images(listing_dir: Path, job_dir: Path) -> list[dict[str, Any]]:
    return prepare_gallery(listing_dir, job_dir)["overview_images"]
