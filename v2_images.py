from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from v2_config import IMAGE_MAX_SIDE, IMAGE_QUALITY, MAX_VISION_IMAGES


def _average_hash(image: Image.Image) -> int:
    small = image.convert("L").resize((8, 8))
    pixels = list(small.getdata())
    average = sum(pixels) / max(1, len(pixels))
    value = 0
    for pixel in pixels:
        value = (value << 1) | int(pixel >= average)
    return value


def _hash_distance(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def _spread_indices(total: int, limit: int) -> list[int]:
    if total <= limit:
        return list(range(total))
    if limit <= 1:
        return [0]
    return sorted({round(index * (total - 1) / (limit - 1)) for index in range(limit)})


def prepare_images(listing_dir: Path, job_dir: Path) -> list[dict[str, Any]]:
    source_dir = listing_dir / "images"
    if not source_dir.exists():
        return []

    allowed = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".avif"}
    originals = sorted(
        p for p in source_dir.iterdir() if p.is_file() and p.suffix.lower() in allowed
    )
    if not originals:
        return []

    preferred = _spread_indices(len(originals), MAX_VISION_IMAGES)
    deferred = [index for index in range(len(originals)) if index not in preferred]
    output_dir = job_dir / "vision"
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    prepared: list[dict[str, Any]] = []
    hashes: list[int] = []
    for original_index in [*preferred, *deferred]:
        if len(prepared) >= MAX_VISION_IMAGES:
            break
        source = originals[original_index]
        try:
            with Image.open(source) as opened:
                image = ImageOps.exif_transpose(opened)
                image.load()
                hash_value = _average_hash(image)
                if any(_hash_distance(hash_value, existing) <= 3 for existing in hashes):
                    continue
                hashes.append(hash_value)
                image.thumbnail((IMAGE_MAX_SIDE, IMAGE_MAX_SIDE), Image.Resampling.LANCZOS)
                if image.mode != "RGB":
                    image = image.convert("RGB")
                number = len(prepared) + 1
                destination = output_dir / f"foto_{number:02d}.jpg"
                image.save(
                    destination,
                    format="JPEG",
                    quality=IMAGE_QUALITY,
                    optimize=True,
                    progressive=True,
                )
                prepared.append(
                    {
                        "number": number,
                        "label": f"Foto {number:02d}",
                        "original_name": source.name,
                        "path": str(destination),
                    }
                )
        except Exception:
            continue
    return prepared
