"""Image selection, optimization, and collage preparation for LLM analysis."""

from __future__ import annotations

import base64
import os


LLM_IMAGE_MAX_SIDE = 1280
LLM_IMAGE_QUALITY = 80
MAX_ANALYSIS_COLLAGES = 5
LLM_COLLAGE_COLUMNS = 2
LLM_COLLAGE_ROWS = 2
MAX_ANALYSIS_IMAGES = MAX_ANALYSIS_COLLAGES * LLM_COLLAGE_COLUMNS * LLM_COLLAGE_ROWS
LLM_COLLAGE_CELL_SIZE = 896
LLM_COLLAGE_LABEL_HEIGHT = 34
LLM_COLLAGE_MARGIN = 10
LLM_COLLAGE_QUALITY = 90
LLM_IMAGE_END_POSITION = 1.0
LLM_OVERVIEW_ATTACHMENTS = max(1, MAX_ANALYSIS_COLLAGES - 1)
LLM_OVERVIEW_CELL_MIN_SIZE = 150
LLM_OVERVIEW_CELL_MAX_SIZE = 280
LLM_OVERVIEW_LABEL_HEIGHT = 26
LLM_OVERVIEW_MARGIN = 6
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".avif"}


def _is_supported_image(filename):
    return os.path.splitext(filename)[1].lower() in IMAGE_EXTENSIONS


def _select_representative_indices(total, limit=MAX_ANALYSIS_IMAGES):
    """Select spread-out gallery positions, filling rounded duplicates nearby."""
    if total <= 0:
        return []
    if total <= limit:
        return list(range(total))

    selected = []
    used = set()
    if limit == 1:
        positions = [0.0]
    else:
        positions = [
            (LLM_IMAGE_END_POSITION * i) / (limit - 1)
            for i in range(limit)
        ]

    for position in positions:
        preferred = int(round((total - 1) * position))
        candidates = [preferred]
        for offset in range(1, total):
            candidates.extend((preferred - offset, preferred + offset))
        for candidate in candidates:
            if 0 <= candidate < total and candidate not in used:
                selected.append(candidate)
                used.add(candidate)
                break
        if len(selected) >= limit:
            break

    for candidate in range(total):
        if len(selected) >= limit:
            break
        if candidate not in used:
            selected.append(candidate)
            used.add(candidate)

    return sorted(selected)


def _average_hash(image):
    """Return a tiny average hash for near-duplicate filtering."""
    small = image.convert("L").resize((8, 8))
    get_pixels = getattr(small, "get_flattened_data", None)
    pixels = list(get_pixels() if get_pixels else small.getdata())
    avg = sum(pixels) / len(pixels)
    value = 0
    for pixel in pixels:
        value = (value << 1) | int(pixel >= avg)
    return value


def _hash_distance(left, right):
    return (left ^ right).bit_count()


def _optimize_image_for_llm(source_path, output_path):
    from PIL import Image, ImageOps

    with Image.open(source_path) as img:
        img = ImageOps.exif_transpose(img)
        img.thumbnail((LLM_IMAGE_MAX_SIDE, LLM_IMAGE_MAX_SIDE), Image.Resampling.LANCZOS)
        hash_value = _average_hash(img)

        has_alpha = img.mode in ("RGBA", "LA") or (
            img.mode == "P" and "transparency" in img.info
        )
        if has_alpha:
            png_path = os.path.splitext(output_path)[0] + ".png"
            img.save(png_path, format="PNG", optimize=True)
            return png_path, "image/png", hash_value

        if img.mode != "RGB":
            img = img.convert("RGB")
        img.save(
            output_path,
            format="JPEG",
            quality=LLM_IMAGE_QUALITY,
            optimize=True,
            progressive=True,
        )
        return output_path, "image/jpeg", hash_value


def _create_llm_collage(items, output_path):
    from PIL import Image, ImageDraw, ImageFont, ImageOps

    cell_size = LLM_COLLAGE_CELL_SIZE
    label_height = LLM_COLLAGE_LABEL_HEIGHT
    margin = LLM_COLLAGE_MARGIN
    columns = LLM_COLLAGE_COLUMNS
    rows = LLM_COLLAGE_ROWS
    width = (columns * cell_size) + ((columns + 1) * margin)
    height = (rows * (cell_size + label_height)) + ((rows + 1) * margin)
    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)

    try:
        font = ImageFont.truetype("arial.ttf", 24)
    except Exception:
        font = ImageFont.load_default()

    for slot, item in enumerate(items):
        row = slot // columns
        col = slot % columns
        x = margin + col * (cell_size + margin)
        y = margin + row * (cell_size + label_height + margin)

        label = f"Foto {item['number']:02d}: {item['original_name']}"
        draw.rectangle(
            [x, y, x + cell_size, y + label_height],
            fill=(31, 41, 55),
        )
        draw.text((x + 10, y + 5), label[:80], fill="white", font=font)

        image_box_y = y + label_height
        with Image.open(item["source_path"]) as img:
            img = ImageOps.exif_transpose(img)
            img.thumbnail((cell_size, cell_size), Image.Resampling.LANCZOS)
            if img.mode != "RGB":
                img = img.convert("RGB")

            bg = Image.new("RGB", (cell_size, cell_size), (245, 245, 245))
            paste_x = (cell_size - img.width) // 2
            paste_y = (cell_size - img.height) // 2
            bg.paste(img, (paste_x, paste_y))
            canvas.paste(bg, (x, image_box_y))

        draw.rectangle(
            [x, image_box_y, x + cell_size, image_box_y + cell_size],
            outline=(209, 213, 219),
            width=2,
        )

    canvas.save(
        output_path,
        format="JPEG",
        quality=LLM_COLLAGE_QUALITY,
        optimize=True,
        progressive=True,
    )
    return output_path, "image/jpeg"


def _overview_grid_dimensions(item_count):
    if item_count <= 0:
        return 1, 1, LLM_OVERVIEW_CELL_MAX_SIZE

    columns = 1
    while columns * columns < item_count:
        columns += 1
    rows = (item_count + columns - 1) // columns

    max_columns = max(1, 1800 // LLM_OVERVIEW_CELL_MIN_SIZE)
    if columns > max_columns:
        columns = max_columns
        rows = (item_count + columns - 1) // columns

    cell_size = max(
        LLM_OVERVIEW_CELL_MIN_SIZE,
        min(LLM_OVERVIEW_CELL_MAX_SIZE, 1800 // max(1, columns)),
    )
    return columns, rows, cell_size


def _create_llm_overview_sheet(items, output_path):
    from PIL import Image, ImageDraw, ImageFont, ImageOps

    columns, rows, cell_size = _overview_grid_dimensions(len(items))
    label_height = LLM_OVERVIEW_LABEL_HEIGHT
    margin = LLM_OVERVIEW_MARGIN
    width = (columns * cell_size) + ((columns + 1) * margin)
    height = (rows * (cell_size + label_height)) + ((rows + 1) * margin)
    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)

    try:
        font = ImageFont.truetype("arial.ttf", 16)
    except Exception:
        font = ImageFont.load_default()

    for slot, item in enumerate(items):
        row = slot // columns
        col = slot % columns
        x = margin + col * (cell_size + margin)
        y = margin + row * (cell_size + label_height + margin)

        label = f"Foto {item['gallery_number']:03d}"
        draw.rectangle(
            [x, y, x + cell_size, y + label_height],
            fill=(17, 24, 39),
        )
        draw.text((x + 6, y + 4), label, fill="white", font=font)

        image_box_y = y + label_height
        with Image.open(item["source_path"]) as img:
            img = ImageOps.exif_transpose(img)
            img.thumbnail((cell_size, cell_size), Image.Resampling.LANCZOS)
            if img.mode != "RGB":
                img = img.convert("RGB")

            bg = Image.new("RGB", (cell_size, cell_size), (245, 245, 245))
            paste_x = (cell_size - img.width) // 2
            paste_y = (cell_size - img.height) // 2
            bg.paste(img, (paste_x, paste_y))
            canvas.paste(bg, (x, image_box_y))

        draw.rectangle(
            [x, image_box_y, x + cell_size, image_box_y + cell_size],
            outline=(209, 213, 219),
            width=1,
        )

    canvas.save(
        output_path,
        format="JPEG",
        quality=LLM_COLLAGE_QUALITY,
        optimize=True,
        progressive=True,
    )
    return output_path, "image/jpeg"


def _chunk_items(items, chunk_size):
    for start in range(0, len(items), chunk_size):
        yield items[start:start + chunk_size]


def prepare_llm_images(slug_dir, *, log=None):
    """
    Create analysis-only contact sheets and return API-ready base64 tuples.
    Originals in images/ are never modified.
    """
    safe_log = log or (lambda _message: None)
    images_dir = os.path.join(slug_dir, "images")
    if not os.path.isdir(images_dir):
        return [], {
            "coverage_mode": "none",
            "original_count": 0,
            "selected_originals": [],
            "selected_count": 0,
            "overview_count": 0,
            "detail_count": 0,
            "overview_includes_all": False,
            "full_gallery_included": False,
            "optimized_files": [],
            "collage_groups": [],
        }

    originals = [
        f for f in sorted(os.listdir(images_dir))
        if _is_supported_image(f) and os.path.isfile(os.path.join(images_dir, f))
    ]

    try:
        from PIL import Image  # noqa
        _have_pillow = True
    except ImportError:
        _have_pillow = False
        safe_log("Pillow not installed - sending original photos directly to Gemini (no collaging).")

    if not _have_pillow:
        _mimes = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".bmp": "image/bmp", ".avif": "image/avif"}
        _out = []
        _sent = 0
        for _f in originals:
            if _sent >= MAX_ANALYSIS_IMAGES:
                break
            _fp = os.path.join(images_dir, _f)
            _mt = _mimes.get(os.path.splitext(_f)[1].lower(), "image/jpeg")
            try:
                with open(_fp, "rb") as _fb:
                    _out.append((_f, base64.b64encode(_fb.read()).decode("utf-8"), _mt))
                _sent += 1
            except Exception as _e:
                safe_log(f"Warning: could not read {_f}: {_e}")
        return _out, {
            "coverage_mode": "raw_limited",
            "original_count": len(originals),
            "selected_originals": [_n for _n, _, _ in _out],
            "selected_count": _sent,
            "overview_count": 0,
            "detail_count": _sent,
            "overview_includes_all": False,
            "full_gallery_included": _sent == len(originals),
            "collage_count": 0,
            "optimized_files": [_n for _n, _, _ in _out],
            "collage_groups": [],
            "error": "Pillow missing; sent original photos.",
        }

    analysis_dir = os.path.join(slug_dir, ".analysis_images")
    os.makedirs(analysis_dir, exist_ok=True)

    if len(originals) > MAX_ANALYSIS_IMAGES:
        overview_items = [
            {
                "gallery_number": idx + 1,
                "number": idx + 1,
                "original_name": original_name,
                "source_path": os.path.join(images_dir, original_name),
            }
            for idx, original_name in enumerate(originals)
        ]
        detail_indices = _select_representative_indices(
            len(originals),
            limit=LLM_COLLAGE_COLUMNS * LLM_COLLAGE_ROWS,
        )
        detail_items = [
            {
                "gallery_number": idx + 1,
                "number": idx + 1,
                "original_name": originals[idx],
                "source_path": os.path.join(images_dir, originals[idx]),
            }
            for idx in detail_indices
        ]

        image_data_list = []
        optimized_files = []
        collage_groups = []
        overview_groups = []
        chunk_size = (len(overview_items) + LLM_OVERVIEW_ATTACHMENTS - 1) // LLM_OVERVIEW_ATTACHMENTS

        for overview_number, overview_group in enumerate(_chunk_items(overview_items, chunk_size), start=1):
            output_name = f"overview_{overview_number:02d}_full_gallery.jpg"
            output_path = os.path.join(analysis_dir, output_name)
            try:
                collage_path, mime_type = _create_llm_overview_sheet(overview_group, output_path)
                with open(collage_path, "rb") as f:
                    img_base64 = base64.b64encode(f.read()).decode("utf-8")
                collage_name = os.path.basename(collage_path)
                optimized_files.append(collage_name)
                group_meta = {
                    "collage": collage_name,
                    "type": "overview",
                    "covers_full_gallery": False,
                    "coverage_scope": "full_gallery_chunk",
                    "items": [
                        {
                            "number": item["gallery_number"],
                            "original_name": item["original_name"],
                        }
                        for item in overview_group
                    ],
                }
                overview_groups.append(group_meta)
                collage_groups.append(group_meta)
                image_data_list.append((collage_name, img_base64, mime_type))
            except Exception as e:
                item_names = ", ".join(item["original_name"] for item in overview_group)
                safe_log(f"Warning: Could not create overview sheet from {item_names}: {e}")

        if detail_items and len(image_data_list) < MAX_ANALYSIS_COLLAGES:
            output_name = "detail_01_representative_llm.jpg"
            output_path = os.path.join(analysis_dir, output_name)
            try:
                collage_path, mime_type = _create_llm_collage(detail_items, output_path)
                with open(collage_path, "rb") as f:
                    img_base64 = base64.b64encode(f.read()).decode("utf-8")
                collage_name = os.path.basename(collage_path)
                optimized_files.append(collage_name)
                group_meta = {
                    "collage": collage_name,
                    "type": "detail",
                    "covers_full_gallery": False,
                    "items": [
                        {
                            "number": item["gallery_number"],
                            "original_name": item["original_name"],
                        }
                        for item in detail_items
                    ],
                }
                collage_groups.append(group_meta)
                image_data_list.append((collage_name, img_base64, mime_type))
            except Exception as e:
                item_names = ", ".join(item["original_name"] for item in detail_items)
                safe_log(f"Warning: Could not create representative detail collage from {item_names}: {e}")

        overview_originals = [
            item["original_name"]
            for group in overview_groups
            for item in group["items"]
        ]
        overview_covered_count = len(overview_originals)
        full_gallery_included = overview_covered_count == len(originals)

        return image_data_list, {
            "coverage_mode": "full_gallery_overview",
            "original_count": len(originals),
            "selected_originals": overview_originals,
            "detail_originals": [item["original_name"] for item in detail_items],
            "selected_count": overview_covered_count,
            "overview_count": len(overview_groups),
            "detail_count": len(detail_items),
            "overview_includes_all": full_gallery_included,
            "full_gallery_included": full_gallery_included,
            "collage_count": len(image_data_list),
            "collage_capacity": LLM_COLLAGE_COLUMNS * LLM_COLLAGE_ROWS,
            "optimized_files": optimized_files,
            "collage_groups": collage_groups,
            "overview_groups": overview_groups,
        }

    selected_indices = _select_representative_indices(len(originals))
    selected_items = []
    selected_originals = []
    optimized_files = []
    collage_groups = []
    seen_hashes = []
    deferred_indices = [i for i in range(len(originals)) if i not in selected_indices]

    for idx in selected_indices + deferred_indices:
        if len(selected_items) >= MAX_ANALYSIS_IMAGES:
            break

        original_name = originals[idx]
        source_path = os.path.join(images_dir, original_name)

        try:
            from PIL import Image, ImageOps
            with Image.open(source_path) as img:
                img = ImageOps.exif_transpose(img)
                hash_value = _average_hash(img)
            if any(_hash_distance(hash_value, previous) <= 4 for previous in seen_hashes):
                continue

            seen_hashes.append(hash_value)
            selected_items.append({
                "number": idx + 1,
                "original_name": original_name,
                "source_path": source_path,
            })
            selected_originals.append(original_name)
        except Exception as e:
            safe_log(f"Warning: Could not read image {original_name}: {e}")

    image_data_list = []
    chunk_size = LLM_COLLAGE_COLUMNS * LLM_COLLAGE_ROWS
    for collage_number, collage_items in enumerate(_chunk_items(selected_items, chunk_size), start=1):
        if collage_number > MAX_ANALYSIS_COLLAGES:
            break

        output_name = f"collage_{collage_number:02d}_llm.jpg"
        output_path = os.path.join(analysis_dir, output_name)
        try:
            collage_path, mime_type = _create_llm_collage(collage_items, output_path)
            with open(collage_path, "rb") as f:
                img_base64 = base64.b64encode(f.read()).decode("utf-8")
            collage_name = os.path.basename(collage_path)
            optimized_files.append(collage_name)
            collage_groups.append({
                "collage": collage_name,
                "type": "detail",
                "covers_full_gallery": len(selected_originals) == len(originals),
                "items": [
                    {
                        "number": item["number"],
                        "original_name": item["original_name"],
                    }
                    for item in collage_items
                ],
            })
            image_data_list.append((collage_name, img_base64, mime_type))
        except ImportError:
            raise
        except Exception as e:
            item_names = ", ".join(item["original_name"] for item in collage_items)
            safe_log(f"Warning: Could not create image collage from {item_names}: {e}")

    return image_data_list, {
        "coverage_mode": "detail_all" if len(selected_originals) == len(originals) else "detail_limited",
        "original_count": len(originals),
        "selected_originals": selected_originals,
        "detail_originals": selected_originals,
        "optimized_files": optimized_files,
        "collage_count": len(image_data_list),
        "selected_count": len(selected_originals),
        "overview_count": 0,
        "detail_count": len(selected_originals),
        "overview_includes_all": False,
        "full_gallery_included": len(selected_originals) == len(originals),
        "collage_capacity": chunk_size,
        "collage_groups": collage_groups,
    }

is_supported_image = _is_supported_image
select_representative_indices = _select_representative_indices
average_hash = _average_hash
hash_distance = _hash_distance
optimize_image_for_llm = _optimize_image_for_llm
create_llm_collage = _create_llm_collage
overview_grid_dimensions = _overview_grid_dimensions
create_llm_overview_sheet = _create_llm_overview_sheet
chunk_items = _chunk_items
