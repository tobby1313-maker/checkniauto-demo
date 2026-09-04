"""Cheap local image cataloguing used before paid vision inference."""
from __future__ import annotations

import math
import os
from typing import Any, Iterable

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".avif"}
PERCEPTUAL_DUPLICATE_DISTANCE = 4
DIFFERENCE_DUPLICATE_DISTANCE = 8
COLOR_DUPLICATE_DISTANCE = 58.0
COMBINED_SIMILARITY_DISTANCE = 0.115
MIN_DETAIL_ALTERNATE_DISTANCE = 0.012


def is_supported_image(filename: str) -> bool:
    return os.path.splitext(filename)[1].lower() in IMAGE_EXTENSIONS


def select_representative_indices(total: int, limit: int, end_position: float = 1.0) -> list[int]:
    """Return evenly distributed indices and preserve both gallery boundaries."""
    if total <= 0 or limit <= 0:
        return []
    if total <= limit:
        return list(range(total))
    if limit == 1:
        return [0]
    selected: list[int] = []
    used: set[int] = set()
    for position in ((end_position * index) / (limit - 1) for index in range(limit)):
        preferred = int(round((total - 1) * position))
        candidates = [preferred]
        for offset in range(1, total):
            candidates.extend((preferred - offset, preferred + offset))
        for candidate in candidates:
            if 0 <= candidate < total and candidate not in used:
                selected.append(candidate)
                used.add(candidate)
                break
    return sorted(selected)


def _pixels(image: Any) -> list[int]:
    getter = getattr(image, "get_flattened_data", None)
    return list(getter() if getter else image.getdata())


def average_hash(image: Any) -> int:
    small = image.convert("L").resize((8, 8))
    pixels = _pixels(small)
    average = sum(pixels) / max(1, len(pixels))
    value = 0
    for pixel in pixels:
        value = (value << 1) | int(pixel >= average)
    return value


def difference_hash(image: Any) -> int:
    small = image.convert("L").resize((9, 8))
    pixels = _pixels(small)
    value = 0
    for row in range(8):
        offset = row * 9
        for column in range(8):
            value = (value << 1) | int(pixels[offset + column] >= pixels[offset + column + 1])
    return value


def hash_distance(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def _colour_distance(left: tuple[float, float, float], right: tuple[float, float, float]) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right)))


def visual_distance(left: dict[str, Any], right: dict[str, Any]) -> float:
    ahash = hash_distance(left["average_hash"], right["average_hash"]) / 64.0
    dhash = hash_distance(left["difference_hash"], right["difference_hash"]) / 64.0
    colour = _colour_distance(left["mean_rgb"], right["mean_rgb"]) / (math.sqrt(3) * 255.0)
    return (0.45 * ahash) + (0.40 * dhash) + (0.15 * colour)


def looks_similar(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        hash_distance(left["average_hash"], right["average_hash"])
        <= PERCEPTUAL_DUPLICATE_DISTANCE
        and hash_distance(left["difference_hash"], right["difference_hash"])
        <= DIFFERENCE_DUPLICATE_DISTANCE
        and _colour_distance(left["mean_rgb"], right["mean_rgb"])
        <= COLOR_DUPLICATE_DISTANCE
    ) or visual_distance(left, right) <= COMBINED_SIMILARITY_DISTANCE


def _quality_features(image: Any) -> tuple[float, float, tuple[float, float, float]]:
    from PIL import ImageFilter, ImageStat

    probe = image.convert("RGB")
    probe.thumbnail((512, 512))
    gray = probe.convert("L")
    sharpness = float(ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES)).mean[0])
    contrast = float(ImageStat.Stat(gray).stddev[0])
    mean_rgb = tuple(float(value) for value in ImageStat.Stat(probe).mean[:3])
    return sharpness, contrast, mean_rgb  # type: ignore[return-value]


def _quality_score(width: int, height: int, sharpness: float, contrast: float) -> float:
    area_score = min(1.0, math.sqrt(max(1, width * height)) / 1800.0)
    sharpness_score = min(1.0, sharpness / 55.0)
    contrast_score = min(1.0, contrast / 70.0)
    return round((0.45 * area_score) + (0.40 * sharpness_score) + (0.15 * contrast_score), 5)


def scan_originals(
    images_dir: str,
    originals: Iterable[str],
    *,
    log: Any = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    from PIL import Image, ImageOps

    safe_log = log or (lambda _message: None)
    readable: list[dict[str, Any]] = []
    unreadable: list[str] = []
    for index, original_name in enumerate(originals):
        source_path = os.path.join(images_dir, original_name)
        try:
            with Image.open(source_path) as opened:
                image = ImageOps.exif_transpose(opened)
                image.load()
                width, height = image.size
                sharpness, contrast, mean_rgb = _quality_features(image)
                readable.append(
                    {
                        "gallery_number": index + 1,
                        "number": index + 1,
                        "original_name": original_name,
                        "source_path": source_path,
                        "width": int(width),
                        "height": int(height),
                        "average_hash": average_hash(image),
                        "difference_hash": difference_hash(image),
                        "mean_rgb": mean_rgb,
                        "quality_score": _quality_score(width, height, sharpness, contrast),
                    }
                )
        except Exception as exc:
            unreadable.append(original_name)
            safe_log(f"Warning: Could not read image {original_name}: {exc}")
    return readable, unreadable


def cluster_similar_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cluster near-similar photos but retain every member for overview sheets."""
    clusters: list[dict[str, Any]] = []
    for item in items:
        best: dict[str, Any] | None = None
        best_distance = float("inf")
        for cluster in clusters:
            similar_members = [member for member in cluster["members"] if looks_similar(item, member)]
            if not similar_members:
                continue
            distance = min(visual_distance(item, member) for member in similar_members)
            if distance < best_distance:
                best, best_distance = cluster, distance
        if best is None:
            clusters.append({"members": [item]})
        else:
            best["members"].append(item)

    for index, cluster in enumerate(clusters, start=1):
        members = sorted(cluster["members"], key=lambda value: value["gallery_number"])
        representative = max(
            members,
            key=lambda value: (value["quality_score"], value["width"] * value["height"]),
        )
        cluster.update(
            cluster_id=f"cluster_{index:03d}",
            members=members,
            representative=representative,
            first_gallery_number=members[0]["gallery_number"],
        )
        for member in members:
            member["cluster_id"] = cluster["cluster_id"]
            member["cluster_representative"] = representative["original_name"]
            member["distance_from_representative"] = round(
                visual_distance(member, representative), 5
            )
    return clusters


def similarity_metadata(
    clusters: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cluster_rows: list[dict[str, Any]] = []
    similar_rows: list[dict[str, Any]] = []
    for cluster in clusters:
        representative = cluster["representative"]
        members = cluster["members"]
        cluster_rows.append(
            {
                "cluster_id": cluster["cluster_id"],
                "representative": representative["original_name"],
                "member_count": len(members),
                "gallery_numbers": [member["gallery_number"] for member in members],
                "members": [member["original_name"] for member in members],
            }
        )
        for member in members:
            if member is representative:
                continue
            similar_rows.append(
                {
                    "original_name": member["original_name"],
                    "duplicate_of": representative["original_name"],
                    "cluster_id": cluster["cluster_id"],
                    "similarity_score": round(
                        max(0.0, 1.0 - member["distance_from_representative"]), 4
                    ),
                }
            )
    return cluster_rows, similar_rows


def detail_candidates(
    clusters: list[dict[str, Any]],
    capacity: int,
    *,
    end_position: float = 1.0,
) -> list[dict[str, Any]]:
    """Select diverse representatives and only materially different alternates."""
    if capacity <= 0 or not clusters:
        return []
    ordered = sorted(clusters, key=lambda value: value["first_gallery_number"])
    if len(ordered) > capacity:
        indices = select_representative_indices(len(ordered), capacity, end_position)
        return sorted(
            (ordered[index]["representative"] for index in indices),
            key=lambda value: value["gallery_number"],
        )

    selected = [cluster["representative"] for cluster in ordered]
    selected_names = {item["original_name"] for item in selected}
    remaining = capacity - len(selected)
    for round_number in (1, 2):
        if remaining <= 0:
            break
        for cluster in sorted(
            ordered,
            key=lambda value: (-len(value["members"]), value["first_gallery_number"]),
        ):
            if remaining <= 0:
                break
            if len(cluster["members"]) < (3 if round_number == 1 else 8):
                continue
            representative = cluster["representative"]
            candidates = [
                member
                for member in cluster["members"]
                if member["original_name"] not in selected_names
                and (
                    member["distance_from_representative"] >= MIN_DETAIL_ALTERNATE_DISTANCE
                    or member["quality_score"] > representative["quality_score"] + 0.06
                )
            ]
            if not candidates:
                continue
            candidate = max(
                candidates,
                key=lambda value: (
                    0.65 * value["quality_score"]
                    + 0.35 * min(1.0, value["distance_from_representative"] * 4.0),
                    value["width"] * value["height"],
                ),
            )
            selected.append(candidate)
            selected_names.add(candidate["original_name"])
            remaining -= 1
    return sorted(selected, key=lambda value: value["gallery_number"])
