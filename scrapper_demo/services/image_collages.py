"""Rendering helpers for overview sheets and high-resolution 2x2 collages."""
from __future__ import annotations

import base64
import math
import os
from typing import Any

LLM_IMAGE_MAX_SIDE = 1280
LLM_IMAGE_QUALITY = 80
LLM_COLLAGE_COLUMNS = 2
LLM_COLLAGE_ROWS = 2
LLM_COLLAGE_CELL_SIZE = 896
LLM_COLLAGE_LABEL_HEIGHT = 34
LLM_COLLAGE_MARGIN = 10
LLM_COLLAGE_QUALITY = 90
LLM_OVERVIEW_CELL_MIN_SIZE = 150
LLM_OVERVIEW_CELL_MAX_SIZE = 280
LLM_OVERVIEW_LABEL_HEIGHT = 26
LLM_OVERVIEW_MARGIN = 6


def _font(size: int):
    from PIL import ImageFont

    for candidate in ("DejaVuSans.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(candidate, size)
        except Exception:
            pass
    return ImageFont.load_default()


def optimize_image_for_llm(source_path: str, output_path: str, average_hash: Any):
    from PIL import Image, ImageOps

    with Image.open(source_path) as opened:
        image = ImageOps.exif_transpose(opened)
        image.thumbnail((LLM_IMAGE_MAX_SIDE, LLM_IMAGE_MAX_SIDE), Image.Resampling.LANCZOS)
        hash_value = average_hash(image)
        has_alpha = image.mode in ("RGBA", "LA") or (
            image.mode == "P" and "transparency" in image.info
        )
        if has_alpha:
            png_path = os.path.splitext(output_path)[0] + ".png"
            image.save(png_path, format="PNG", optimize=True)
            return png_path, "image/png", hash_value
        if image.mode != "RGB":
            image = image.convert("RGB")
        image.save(
            output_path,
            format="JPEG",
            quality=LLM_IMAGE_QUALITY,
            optimize=True,
            progressive=True,
        )
        return output_path, "image/jpeg", hash_value


def create_llm_collage(items: list[dict[str, Any]], output_path: str):
    from PIL import Image, ImageDraw, ImageOps

    cell = LLM_COLLAGE_CELL_SIZE
    label_height = LLM_COLLAGE_LABEL_HEIGHT
    margin = LLM_COLLAGE_MARGIN
    width = LLM_COLLAGE_COLUMNS * cell + (LLM_COLLAGE_COLUMNS + 1) * margin
    height = LLM_COLLAGE_ROWS * (cell + label_height) + (LLM_COLLAGE_ROWS + 1) * margin
    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)
    font = _font(24)
    for slot, item in enumerate(items):
        row, column = divmod(slot, LLM_COLLAGE_COLUMNS)
        x = margin + column * (cell + margin)
        y = margin + row * (cell + label_height + margin)
        number = int(item.get("gallery_number") or item.get("number") or slot + 1)
        label = f"Foto {number:03d}: {item['original_name']}"
        draw.rectangle([x, y, x + cell, y + label_height], fill=(31, 41, 55))
        draw.text((x + 10, y + 5), label[:100], fill="white", font=font)
        image_y = y + label_height
        with Image.open(item["source_path"]) as opened:
            image = ImageOps.exif_transpose(opened)
            image.thumbnail((cell, cell), Image.Resampling.LANCZOS)
            if image.mode != "RGB":
                image = image.convert("RGB")
            background = Image.new("RGB", (cell, cell), (245, 245, 245))
            background.paste(image, ((cell - image.width) // 2, (cell - image.height) // 2))
            canvas.paste(background, (x, image_y))
        draw.rectangle([x, image_y, x + cell, image_y + cell], outline=(209, 213, 219), width=2)
    canvas.save(output_path, format="JPEG", quality=LLM_COLLAGE_QUALITY, optimize=True, progressive=True)
    return output_path, "image/jpeg"


def overview_grid_dimensions(item_count: int):
    if item_count <= 0:
        return 1, 1, LLM_OVERVIEW_CELL_MAX_SIZE
    columns = max(1, math.ceil(math.sqrt(item_count)))
    rows = math.ceil(item_count / columns)
    max_columns = max(1, 1800 // LLM_OVERVIEW_CELL_MIN_SIZE)
    if columns > max_columns:
        columns = max_columns
        rows = math.ceil(item_count / columns)
    cell = max(
        LLM_OVERVIEW_CELL_MIN_SIZE,
        min(LLM_OVERVIEW_CELL_MAX_SIZE, 1800 // max(1, columns)),
    )
    return columns, rows, cell


def create_llm_overview_sheet(items: list[dict[str, Any]], output_path: str):
    from PIL import Image, ImageDraw, ImageOps

    columns, rows, cell = overview_grid_dimensions(len(items))
    label_height = LLM_OVERVIEW_LABEL_HEIGHT
    margin = LLM_OVERVIEW_MARGIN
    width = columns * cell + (columns + 1) * margin
    height = rows * (cell + label_height) + (rows + 1) * margin
    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)
    font = _font(16)
    for slot, item in enumerate(items):
        row, column = divmod(slot, columns)
        x = margin + column * (cell + margin)
        y = margin + row * (cell + label_height + margin)
        label = f"Foto {int(item['gallery_number']):03d}"
        draw.rectangle([x, y, x + cell, y + label_height], fill=(17, 24, 39))
        draw.text((x + 6, y + 4), label, fill="white", font=font)
        image_y = y + label_height
        with Image.open(item["source_path"]) as opened:
            image = ImageOps.exif_transpose(opened)
            image.thumbnail((cell, cell), Image.Resampling.LANCZOS)
            if image.mode != "RGB":
                image = image.convert("RGB")
            background = Image.new("RGB", (cell, cell), (245, 245, 245))
            background.paste(image, ((cell - image.width) // 2, (cell - image.height) // 2))
            canvas.paste(background, (x, image_y))
        draw.rectangle([x, image_y, x + cell, image_y + cell], outline=(209, 213, 219), width=1)
    canvas.save(output_path, format="JPEG", quality=LLM_COLLAGE_QUALITY, optimize=True, progressive=True)
    return output_path, "image/jpeg"


def chunk_items(items: list[Any], chunk_size: int):
    size = max(1, chunk_size)
    for start in range(0, len(items), size):
        yield items[start : start + size]


def encode_attachment(path: str, mime_type: str) -> tuple[str, str, str]:
    with open(path, "rb") as handle:
        encoded = base64.b64encode(handle.read()).decode("utf-8")
    return os.path.basename(path), encoded, mime_type
