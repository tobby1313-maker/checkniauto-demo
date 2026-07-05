#!/usr/bin/env python3
"""
Local web server for the Scrapper project.
Provides a REST API and serves the frontend HTML/JS/CSS.

Usage:
    python web_server.py
    # Then open http://localhost:5000
"""

import os
import sys
import json
import re
import subprocess
import threading
import time
import urllib.parse
import base64
import tempfile
import shutil
from datetime import datetime
from pathlib import Path
from collections import defaultdict

from flask import Flask, jsonify, send_from_directory, request, Response, stream_with_context
from werkzeug.utils import secure_filename

# LLM Client for AI analysis
from llm_client import analyze_with_llm, extract_kb_save_blocks, RateLimitError, ApiKeyError
from analysis_normalizer import normalize_analysis_markdown

# Alias for backward compatibility - backup API keys will be managed in llm_client.py
analyze_with_gemini = analyze_with_llm


def _configure_console_encoding():
    """Prefer UTF-8 console output, but never fail app startup over logging."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def safe_log(message):
    """Log text without crashing on Windows charmap consoles."""
    text = str(message)
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        safe_text = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
        print(safe_text)


_configure_console_encoding()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEMO_MODE = os.environ.get("DEMO_MODE", "true").lower() in {"1", "true", "yes", "on"}
DATA_DIR = os.environ.get("SCRAPPER_DATA_DIR") or os.path.join(tempfile.gettempdir(), "scrapper-demo")
AUTA_DIR = os.environ.get("SCRAPPER_AUTA_DIR") or os.path.join(DATA_DIR, "Auta")
KB_DIR = os.path.join(SCRIPT_DIR, "knowledge_base")
WEB_DIR = os.path.join(SCRIPT_DIR, "web")
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
LLM_IMAGE_END_POSITION = 0.98
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".avif"}
SUPPORTED_SCRAPER_HOSTS = ("autobazar.eu", "autobazar.sk", "bazos.sk", "bazos.cz")
UNSUPPORTED_DEMO_HOSTS = ("mobile.de",)
MAX_MANUAL_IMAGES = int(os.environ.get("DEMO_MAX_MANUAL_IMAGES", "12"))
DEMO_MAX_SCRAPED_IMAGES = max(1, int(os.environ.get("DEMO_MAX_SCRAPED_IMAGES", "20")))
DEMO_PROMPT_FILE = os.environ.get("DEMO_PROMPT_FILE", "analyze_prompt_v4_koyeb.txt")
DEMO_RATE_LIMIT_PER_IP = os.environ.get("DEMO_RATE_LIMIT_PER_IP", "3/day")
DEMO_MAX_CONCURRENT_JOBS = max(1, int(os.environ.get("DEMO_MAX_CONCURRENT_JOBS", "1")))
DEMO_JOB_TTL_MINUTES = max(5, int(os.environ.get("DEMO_JOB_TTL_MINUTES", "60")))
DEMO_SKIP_KB = os.environ.get("DEMO_SKIP_KB", "true").lower() in {"1", "true", "yes", "on"}
MAX_UPLOAD_BYTES = int(os.environ.get("DEMO_MAX_UPLOAD_MB", "24")) * 1024 * 1024

os.makedirs(AUTA_DIR, exist_ok=True)

app = Flask(__name__, static_folder=WEB_DIR, static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-demo-secret-change-me")

_demo_job_lock = threading.BoundedSemaphore(DEMO_MAX_CONCURRENT_JOBS)
_demo_rate_counts = defaultdict(int)
_demo_rate_lock = threading.Lock()


@app.before_request
def _demo_route_gate():
    if not DEMO_MODE:
        return None
    path = request.path.rstrip("/") or "/"
    allowed = (
        path == "/"
        or path == "/healthz"
        or path.startswith("/api/demo/")
    )
    if path.startswith("/api/") and not allowed:
        return jsonify({"error": "This route is disabled in demo mode."}), 404
    return None


# ─── Helpers ─────────────────────────────────────────────────────────

def parse_car_info_md(md_text):
    """Parse car_info.md into a structured dict for the API."""
    result = {
        "title": "",
        "price": 0,
        "currency": "EUR",
        "vin": "",
        "specs": {},
        "equipment": {},
        "seller": {},
        "location": "",
        "description": "",
        "photos_count": 0,
        "source_url": "",
        "scraped_at": "",
    }

    lines = md_text.split("\n")
    current_section = None
    current_subsection = None
    equipment_items = []

    for line in lines:
        # Title (first # heading)
        if line.startswith("# ") and not line.startswith("## "):
            result["title"] = line[2:].strip()

        # Source URL
        elif line.startswith("**Source:**"):
            result["source_url"] = line.replace("**Source:**", "").strip()

        # Scraped date (handles both "**Scraped:**" and "- **Scraped:**")
        elif "**Scraped:**" in line:
            result["scraped_at"] = line.split("**Scraped:**", 1)[-1].strip()

        # Price (handles both "- **Price:**" and "- **Current Price:**")
        elif "**Price:**" in line or "**Current Price:**" in line:
            price_text = line.split(":**")[-1].strip()
            price_match = re.search(r'(\d[\d\s]*)', price_text.replace("\u00a0", " "))
            if price_match:
                result["price"] = int(price_match.group(1).replace(" ", ""))
            if "EUR" in price_text:
                result["currency"] = "EUR"
            elif "€" in price_text:
                result["currency"] = "EUR"

        # VIN (handles both "**VIN:**" and "- **VIN:**")
        elif "**VIN:**" in line:
            result["vin"] = line.split("**VIN:**")[-1].strip()

        # Sections
        elif line.startswith("## "):
            current_section = line[3:].strip().lower()
            current_subsection = None
            if current_section == "specifications":
                result["specs"] = {}
            elif current_section == "equipment":
                result["equipment"] = {}
                equipment_items = []
            elif current_section == "seller":
                result["seller"] = {}
            elif current_section == "photos":
                # Parse photos count
                photo_match = re.search(r"(\d+)", line)
                if photo_match:
                    result["photos_count"] = int(photo_match.group(1))

        # Subsection (###)
        elif line.startswith("### "):
            current_subsection = line[4:].strip()
            if current_section == "equipment" and current_subsection:
                result["equipment"][current_subsection] = []
                equipment_items = result["equipment"][current_subsection]

        # List items in specifications (- **Key:** Value)
        elif current_section == "specifications" and line.startswith("- **") and "**:" in line:
            match = re.match(r'- \*\*(.+?)\*\*:\s*(.*)', line)
            if match:
                result["specs"][match.group(1)] = match.group(2).strip()

        # Table rows in specifications
        elif current_section == "specifications" and line.startswith("|") and "|" in line[1:]:
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if len(parts) == 2 and parts[0] not in ("Parameter", "---"):
                result["specs"][parts[0]] = parts[1]

        # List items in equipment
        elif current_section == "equipment" and line.startswith("- ") and current_subsection:
            equipment_items.append(line[2:].strip())

        # Seller info
        elif current_section == "seller" and line.startswith("- **"):
            match = re.match(r'- \*\*(.+?)\*\*:\s*(.*)', line)
            if match:
                result["seller"][match.group(1)] = match.group(2).strip()

        # Location
        elif current_section == "location" and line.startswith("- **"):
            match = re.match(r'- \*\*(.+?)\*\*:\s*(.*)', line)
            if match:
                result["location"] = match.group(2).strip()

        # Description / Seller Note
        elif current_section == "seller note (poznamka)" and line.strip() and not line.startswith("##"):
            if result["description"]:
                result["description"] += "\n" + line
            else:
                result["description"] = line

        # Photos count from line
        elif line.startswith("- **Downloaded:**"):
            count_match = re.search(r"(\d+)", line)
            if count_match:
                result["photos_count"] = int(count_match.group(1))

    return result


def _parse_scraped_timestamp(value):
    """Return a numeric timestamp for common scraped_at formats."""
    if not value:
        return 0

    value = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).timestamp()
        except ValueError:
            pass

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0


def _listing_sort_timestamp(parsed, car_info_path):
    """Sort by scraped_at first, then car_info.md mtime for older files without metadata."""
    scraped_ts = _parse_scraped_timestamp(parsed.get("scraped_at", ""))
    if scraped_ts:
        return scraped_ts

    try:
        return os.path.getmtime(car_info_path)
    except OSError:
        return 0


def get_listings():
    """Scan Auta/ directory and return list of all car listings."""
    listings = []
    if not os.path.isdir(AUTA_DIR):
        return listings

    for slug in sorted(os.listdir(AUTA_DIR), reverse=True):
        slug_dir = os.path.join(AUTA_DIR, slug)
        if not os.path.isdir(slug_dir):
            continue

        car_info_path = os.path.join(slug_dir, "car_info.md")
        if not os.path.exists(car_info_path):
            continue

        with open(car_info_path, "r", encoding="utf-8") as f:
            md_text = f.read()

        parsed = parse_car_info_md(md_text)

        # Get first image
        images_dir = os.path.join(slug_dir, "images")
        first_image = None
        if os.path.isdir(images_dir):
            images = sorted(os.listdir(images_dir))
            if images:
                first_image = images[0]

        # Get year from specs
        year = parsed["specs"].get("Year", "")
        mileage = parsed["specs"].get("Mileage", "")

        listings.append({
            "slug": slug,
            "title": parsed["title"],
            "price": parsed["price"],
            "currency": parsed["currency"],
            "year": year,
            "mileage": mileage,
            "vin": parsed["vin"],
            "photos_count": parsed["photos_count"],
            "first_image": first_image,
            "source_url": parsed["source_url"],
            "scraped_at": parsed["scraped_at"],
            "sort_timestamp": _listing_sort_timestamp(parsed, car_info_path),
        })

    # Newest first. Prefer explicit scraped_at; fall back to car_info.md mtime.
    listings.sort(key=lambda x: (x.get("sort_timestamp", 0), x.get("slug", "")), reverse=True)

    return listings


KB_CATEGORIES = [
    "engines",
    "transmissions",
    "generations",
    "electric_motors",
    "batteries",
    "charging",
    "hybrid_systems",
]

def get_kb_structure():
    """Get knowledge_base directory structure for all categories."""
    structure = {}
    if not os.path.isdir(KB_DIR):
        return structure

    for category in KB_CATEGORIES:
        cat_dir = os.path.join(KB_DIR, category)
        if not os.path.isdir(cat_dir):
            continue
        structure[category] = []

        for f in sorted(os.listdir(cat_dir)):
            if f.endswith(".json") and not f.startswith("_"):
                filepath = os.path.join(cat_dir, f)
                try:
                    with open(filepath, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                    structure[category].append({
                        "filename": f,
                        "aliases": data.get("aliases", []),
                        "last_updated": data.get("last_updated", ""),
                        "reliability_rating": data.get("reliability_rating", ""),
                    })
                except (json.JSONDecodeError, IOError):
                    structure[category].append({
                        "filename": f,
                        "aliases": [],
                        "last_updated": "",
                        "reliability_rating": "",
                    })

    return structure


# ─── API Routes ──────────────────────────────────────────────────────

def _is_supported_scraper_url(url):
    """Return True when the URL can be handled by the existing scraper scripts."""
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False

    host = (parsed.netloc or "").lower()
    return any(host == supported or host.endswith(f".{supported}") for supported in SUPPORTED_SCRAPER_HOSTS)


def _url_host(url):
    try:
        return (urllib.parse.urlparse(url).netloc or "").lower()
    except Exception:
        return ""


def _is_known_unsupported_demo_url(url):
    host = _url_host(url)
    return any(host == supported or host.endswith(f".{supported}") for supported in UNSUPPORTED_DEMO_HOSTS)


def _demo_client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.remote_addr or "unknown"


def _parse_daily_limit(value):
    match = re.match(r"^\s*(\d+)\s*/\s*day\s*$", value or "", re.I)
    if match:
        return int(match.group(1))
    try:
        return int(value)
    except (TypeError, ValueError):
        return 3


def _check_demo_access(data=None):
    return None


def _check_demo_rate_limit():
    limit = _parse_daily_limit(DEMO_RATE_LIMIT_PER_IP)
    if limit <= 0:
        return None
    key = f"{datetime.utcnow().strftime('%Y-%m-%d')}:{_demo_client_ip()}"
    with _demo_rate_lock:
        if _demo_rate_counts[key] >= limit:
            return jsonify({"error": f"Demo limit reached ({DEMO_RATE_LIMIT_PER_IP}). Try again later."}), 429
        _demo_rate_counts[key] += 1
    return None


def _safe_slug_dir(slug):
    safe_slug = _slugify(slug, "listing")
    path = os.path.abspath(os.path.join(AUTA_DIR, safe_slug))
    root = os.path.abspath(AUTA_DIR)
    if os.path.commonpath([root, path]) != root:
        raise ValueError("Unsafe listing path.")
    return path


def _cleanup_old_demo_jobs():
    cutoff = time.time() - (DEMO_JOB_TTL_MINUTES * 60)
    if not os.path.isdir(AUTA_DIR):
        return
    for name in os.listdir(AUTA_DIR):
        path = os.path.join(AUTA_DIR, name)
        try:
            if os.path.isdir(path) and os.path.getmtime(path) < cutoff:
                shutil.rmtree(path, ignore_errors=True)
        except Exception as exc:
            safe_log(f"Demo cleanup warning for {path}: {exc}")


def _demo_api_keys():
    keys = []
    for env_name in ("GEMINI_PRIMARY_API_KEY", "GEMINI_BACKUP_API_KEY"):
        value = os.environ.get(env_name, "").strip()
        if value and value not in keys:
            keys.append(value)
    return keys


def _demo_output_language(value):
    return "en" if (value or "").lower() == "en" else "sk"


def _slugify(value, fallback="manual-listing"):
    """Create a filesystem-friendly slug from user supplied text."""
    value = (value or "").strip().lower()
    value = re.sub(r"https?://", "", value)
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return value[:80] or fallback


def _unique_listing_slug(base_slug):
    """Avoid overwriting an existing listing folder."""
    base_slug = _slugify(base_slug)
    candidate = base_slug
    if not os.path.exists(os.path.join(AUTA_DIR, candidate)):
        return candidate

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{base_slug}-{timestamp}"


def _first_text_line(text):
    for line in (text or "").splitlines():
        cleaned = line.strip().lstrip("#").strip()
        if cleaned:
            return cleaned
    return "Manual listing"


def _extract_price_from_text(text):
    match = re.search(r"(\d[\d\s]{2,})\s*(?:eur|euro|€)", text or "", re.I)
    if not match:
        return 0
    try:
        return int(match.group(1).replace(" ", ""))
    except ValueError:
        return 0


def _format_manual_car_info_md(raw):
    title = raw["title"]
    source_url = raw.get("source_url") or raw.get("url", "")
    manual_text = raw.get("manual_text", "").strip()
    price = raw.get("price", 0)
    specs = raw.get("specs") or raw.get("parameters") or {}
    vin = raw.get("vin") or specs.get("VIN") or ""
    photos_count = raw.get("photos_count", 0)
    scraped_at = raw.get("scraped_at") or datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [
        f"# {title}",
        "",
        f"**Scraped:** {scraped_at}",
        "",
        "## Price",
        f"- **Price:** {price:,} EUR".replace(",", " ") if price else "- **Price:** Not provided",
        "",
        "## Specifications",
        "",
    ]

    for key, value in specs.items():
        if key == "VIN" or value in (None, ""):
            continue
        lines.append(f"- **{key}**: {value}")

    if vin:
        lines.append(f"- **VIN:** {vin}")

    if source_url:
        lines.insert(2, f"**Source:** {source_url}")

    lines.extend([
        "",
        "## Seller Note (Poznamka)",
        "",
        manual_text,
        "",
        "## Photos",
        f"- **Downloaded:** {photos_count}",
        "- See `images/` folder for uploaded photos.",
        "",
    ])
    return "\n".join(lines)


def _create_manual_listing_from_form(form, files):
    title = (form.get("title") or "").strip()
    price_text = (form.get("price") or "").strip().replace("\u00a0", " ").replace(" ", "")
    source_url = (form.get("source_url") or "").strip()
    manual_text = (form.get("manual_text") or "").strip()

    try:
        price = int(price_text)
    except ValueError:
        price = 0

    if price <= 0:
        raise ValueError("Price is required and must be greater than 0.")
    if not manual_text:
        raise ValueError("Manual listing text is required.")

    uploads = [f for f in files if f and f.filename]
    if len(uploads) > MAX_MANUAL_IMAGES:
        raise ValueError(f"Upload at most {MAX_MANUAL_IMAGES} images.")

    for uploaded in uploads:
        ext = os.path.splitext(uploaded.filename)[1].lower()
        if ext not in IMAGE_EXTENSIONS:
            raise ValueError(f"Unsupported image type: {uploaded.filename}")

    title = title or _first_text_line(manual_text)
    slug_seed = title or source_url or "manual-listing"
    slug = _unique_listing_slug(slug_seed)
    slug_dir = _safe_slug_dir(slug)
    images_dir = os.path.join(slug_dir, "images")
    os.makedirs(images_dir, exist_ok=True)

    saved_images = []
    for index, uploaded in enumerate(uploads, 1):
        original_name = secure_filename(uploaded.filename) or f"image_{index}.jpg"
        ext = os.path.splitext(original_name)[1].lower()
        stem = os.path.splitext(original_name)[0] or f"image_{index}"
        filename = f"{index:02d}_{_slugify(stem, f'image-{index}')}{ext}"
        filepath = os.path.join(images_dir, filename)
        uploaded.save(filepath)
        saved_images.append(filename)

    vin = ""
    try:
        from vin_utils import extract_vin_from_text
        vin = extract_vin_from_text(manual_text) or ""
    except Exception:
        vin = ""

    raw = {
        "url": source_url,
        "source_url": source_url,
        "title": title,
        "manual_text": manual_text,
        "price": price,
        "currency": "EUR",
        "vin": vin,
        "photos_count": len(saved_images),
        "images": saved_images,
        "source": "manual",
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }

    with open(os.path.join(slug_dir, "car_info.md"), "w", encoding="utf-8") as f:
        f.write(_format_manual_car_info_md(raw))

    with open(os.path.join(slug_dir, "raw_data.json"), "w", encoding="utf-8") as f:
        json.dump(raw, f, indent=2, ensure_ascii=False)

    try:
        from main import build_analysis_request, _run_vin_decoding
        _run_vin_decoding(slug_dir)
        build_analysis_request(SCRIPT_DIR, slug_dir, source_url or "Manual entry")
    except Exception as e:
        safe_log(f"Manual import post-processing warning: {e}")

    return slug, slug_dir, len(saved_images)


@app.route("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.route("/api/listings")
def api_listings():
    return jsonify(get_listings())


@app.route("/api/listings/<slug>")
def api_listing_detail(slug):
    slug_dir = os.path.join(AUTA_DIR, slug)
    if not os.path.isdir(slug_dir):
        return jsonify({"error": "Listing not found"}), 404

    car_info_path = os.path.join(slug_dir, "car_info.md")
    raw_data_path = os.path.join(slug_dir, "raw_data.json")
    vin_decoded_path = os.path.join(slug_dir, "vin_decoded.json")

    result = {"slug": slug}

    if os.path.exists(car_info_path):
        with open(car_info_path, "r", encoding="utf-8") as f:
            md_text = f.read()
        result["car_info_md"] = md_text
        result["parsed"] = parse_car_info_md(md_text)

    # Include decoded VIN data if available
    if os.path.exists(vin_decoded_path):
        try:
            with open(vin_decoded_path, "r", encoding="utf-8") as f:
                result["vin_decoded"] = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass

    if os.path.exists(raw_data_path):
        with open(raw_data_path, "r", encoding="utf-8") as f:
            result["raw_data"] = json.load(f)

    return jsonify(result)


@app.route("/api/listings/<slug>", methods=["PUT"])
def api_update_listing_detail(slug):
    """Update editable listing fields and rewrite car_info.md."""
    slug_dir = os.path.join(AUTA_DIR, slug)
    if not os.path.isdir(slug_dir):
        return jsonify({"error": "Listing not found"}), 404

    data = request.get_json() or {}
    title = (data.get("title") or "").strip()
    source_url = (data.get("source_url") or "").strip()
    description = (data.get("description") or "").strip()
    specs = data.get("specs") or {}

    if not title:
        return jsonify({"error": "Názov auta je povinný."}), 400

    try:
        price = int(str(data.get("price") or "").replace("\u00a0", " ").replace(" ", ""))
    except ValueError:
        price = 0
    if price <= 0:
        return jsonify({"error": "Cena je povinná a musí byť väčšia ako 0."}), 400

    cleaned_specs = {}
    if isinstance(specs, dict):
        for key, value in specs.items():
            key = str(key).strip()
            value = str(value).strip()
            if key and value:
                cleaned_specs[key] = value

    car_info_path = os.path.join(slug_dir, "car_info.md")
    raw_data_path = os.path.join(slug_dir, "raw_data.json")
    existing_raw = {}
    if os.path.exists(raw_data_path):
        try:
            with open(raw_data_path, "r", encoding="utf-8") as f:
                existing_raw = json.load(f)
        except (json.JSONDecodeError, IOError):
            existing_raw = {}

    existing_parsed = {}
    if os.path.exists(car_info_path):
        try:
            with open(car_info_path, "r", encoding="utf-8") as f:
                existing_parsed = parse_car_info_md(f.read())
        except IOError:
            existing_parsed = {}

    images_dir = os.path.join(slug_dir, "images")
    photos_count = 0
    if os.path.isdir(images_dir):
        photos_count = len([
            name for name in os.listdir(images_dir)
            if os.path.isfile(os.path.join(images_dir, name))
        ])

    vin = cleaned_specs.get("VIN") or data.get("vin") or ""
    if not vin:
        try:
            from vin_utils import extract_vin_from_text
            vin = extract_vin_from_text(description) or ""
        except Exception:
            vin = ""

    raw = {
        **existing_raw,
        "url": source_url,
        "source_url": source_url,
        "title": title,
        "manual_text": description,
        "description": description,
        "price": price,
        "currency": "EUR",
        "vin": vin,
        "specs": cleaned_specs,
        "parameters": cleaned_specs,
        "photos_count": photos_count,
        "source": existing_raw.get("source", "manual"),
        "scraped_at": existing_parsed.get("scraped_at") or existing_raw.get("scraped_at") or datetime.now().strftime("%Y-%m-%d %H:%M"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }

    with open(car_info_path, "w", encoding="utf-8") as f:
        f.write(_format_manual_car_info_md(raw))

    with open(raw_data_path, "w", encoding="utf-8") as f:
        json.dump(raw, f, indent=2, ensure_ascii=False)

    try:
        from main import build_analysis_request, _run_vin_decoding
        _run_vin_decoding(slug_dir)
        build_analysis_request(SCRIPT_DIR, slug_dir, source_url or "Manual entry")
    except Exception as e:
        safe_log(f"Listing update post-processing warning: {e}")

    return jsonify({
        "status": "ok",
        "message": "Údaje boli uložené.",
        "slug": slug,
    })


@app.route("/api/listings/<slug>/images")
def api_listing_images(slug):
    images_dir = os.path.join(AUTA_DIR, slug, "images")
    if not os.path.isdir(images_dir):
        return jsonify([])

    images = []
    for f in sorted(os.listdir(images_dir)):
        filepath = os.path.join(images_dir, f)
        if os.path.isfile(filepath):
            size_kb = os.path.getsize(filepath) / 1024
            images.append({
                "filename": f,
                "size_kb": round(size_kb, 1),
            })

    return jsonify(images)


@app.route("/api/listings/<slug>/image/<filename>")
def api_listing_image(slug, filename):
    images_dir = os.path.join(AUTA_DIR, slug, "images")
    if not os.path.isdir(images_dir):
        return jsonify({"error": "Not found"}), 404
    return send_from_directory(images_dir, filename)


@app.route("/api/listings/<slug>/analysis-images")
def api_listing_analysis_images(slug):
    slug_dir = os.path.join(AUTA_DIR, slug)
    if not os.path.isdir(slug_dir):
        return jsonify({"error": "Inzerát nenájdený"}), 404

    try:
        _, image_meta = prepare_llm_images(slug_dir)
    except ImportError:
        return jsonify({
            "original_count": 0,
            "selected_count": 0,
            "collage_count": 0,
            "collages": [],
            "error": "Pillow is not installed. Run: pip install -r requirements.txt",
        })

    collages = []
    for group in image_meta.get("collage_groups", []):
        collages.append({
            "filename": group["collage"],
            "url": f"/api/listings/{slug}/analysis-image/{urllib.parse.quote(group['collage'])}",
            "items": group["items"],
        })

    return jsonify({
        "original_count": image_meta.get("original_count", 0),
        "selected_count": image_meta.get("selected_count", 0),
        "collage_count": image_meta.get("collage_count", len(collages)),
        "collage_capacity": image_meta.get("collage_capacity", LLM_COLLAGE_COLUMNS * LLM_COLLAGE_ROWS),
        "collages": collages,
    })


@app.route("/api/listings/<slug>/analysis-image/<filename>")
def api_listing_analysis_image(slug, filename):
    if ".." in filename or "/" in filename or "\\" in filename:
        return jsonify({"error": "Invalid filename"}), 400

    analysis_dir = os.path.join(AUTA_DIR, slug, ".analysis_images")
    if not os.path.isdir(analysis_dir):
        return jsonify({"error": "Not found"}), 404
    return send_from_directory(analysis_dir, filename)


@app.route("/api/listings/<slug>/analysis")
def api_listing_analysis(slug):
    slug_dir = os.path.join(AUTA_DIR, slug)
    
    # Get prompt version from query parameter (default: v3 for Gemini/API)
    prompt_version = request.args.get("prompt_version", "v3")
    
    # Build dynamic analysis request with the selected prompt version
    system_prompt, user_content, _ = _build_analysis_payload(slug_dir, slug, prompt_version)
    if system_prompt is None:
        return jsonify({"error": user_content}), 400
    
    content = f"""# SYSTÉMOVÝ PROMPT (použitý pre analýzu):
{system_prompt}

# DÁTA PRE ANALÝZU:
{user_content}
"""
    
    return jsonify({"content": content})



def _read_car_info_text(slug_dir):
    car_info_path = os.path.join(slug_dir, "car_info.md")
    if not os.path.exists(car_info_path):
        return ""
    with open(car_info_path, "r", encoding="utf-8") as f:
        return f.read()


def _normalize_analysis_for_slug(slug_dir, content):
    return normalize_analysis_markdown(content, _read_car_info_text(slug_dir))

@app.route("/api/listings/<slug>/analysis-result")
def api_listing_analysis_result(slug):
    slug_dir = os.path.join(AUTA_DIR, slug)
    result_path = os.path.join(slug_dir, "analysis_result.md")
    kb_autosave_path = os.path.join(slug_dir, "kb_autosave.json")
    if not os.path.exists(result_path):
        return jsonify({"error": "Result not found"}), 404

    with open(result_path, "r", encoding="utf-8") as f:
        content = _normalize_analysis_for_slug(slug_dir, f.read())

    # Check if there are KB blocks available to save
    kb_blocks = extract_kb_save_blocks(content)
    saved_kb = []
    if os.path.exists(kb_autosave_path):
        try:
            with open(kb_autosave_path, "r", encoding="utf-8") as f:
                saved_kb = json.load(f).get("saved", [])
        except Exception:
            saved_kb = []

    return jsonify({
        "content": content,
        "has_kb_blocks": len(kb_blocks) > 0 and not saved_kb,
        "saved_kb": saved_kb,
    })


@app.route("/api/listings/<slug>/analysis-result/export")
def api_listing_analysis_export(slug):
    slug_dir = os.path.join(AUTA_DIR, slug)
    result_path = os.path.join(slug_dir, "analysis_result.md")
    if not os.path.exists(result_path):
        return jsonify({"error": "Result not found"}), 404

    with open(result_path, "r", encoding="utf-8") as f:
        content = _normalize_analysis_for_slug(slug_dir, _strip_kb_section(f.read()))

    return jsonify({
        "title": slug.replace("-", " ").title(),
        "content": content,
    })


@app.route("/api/kb")
def api_kb():
    return jsonify(get_kb_structure())


@app.route("/api/kb/<category>/<filename>")
def api_kb_file(category, filename):
    # Security: prevent path traversal
    if ".." in filename or "/" in filename or "\\" in filename:
        return jsonify({"error": "Invalid filename"}), 400

    filepath = os.path.join(KB_DIR, category, filename)
    if not os.path.exists(filepath):
        return jsonify({"error": "File not found"}), 404

    with open(filepath, "r", encoding="utf-8") as f:
        content = json.load(f)

    return jsonify(content)


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
    pixels = list(small.getdata())
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


def _chunk_items(items, chunk_size):
    for start in range(0, len(items), chunk_size):
        yield items[start:start + chunk_size]


def prepare_llm_images(slug_dir):
    """
    Create analysis-only contact sheets and return API-ready base64 tuples.
    Originals in images/ are never modified.
    """
    images_dir = os.path.join(slug_dir, "images")
    if not os.path.isdir(images_dir):
        return [], {
            "original_count": 0,
            "selected_originals": [],
            "optimized_files": [],
        }

    originals = [
        f for f in sorted(os.listdir(images_dir))
        if _is_supported_image(f) and os.path.isfile(os.path.join(images_dir, f))
    ]
    selected_indices = _select_representative_indices(len(originals))
    analysis_dir = os.path.join(slug_dir, ".analysis_images")
    os.makedirs(analysis_dir, exist_ok=True)

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
                "number": len(selected_items) + 1,
                "original_name": original_name,
                "source_path": source_path,
            })
            selected_originals.append(original_name)
        except ImportError:
            raise
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
        "original_count": len(originals),
        "selected_originals": selected_originals,
        "optimized_files": optimized_files,
        "collage_count": len(image_data_list),
        "selected_count": len(selected_originals),
        "collage_capacity": chunk_size,
        "collage_groups": collage_groups,
    }


@app.route("/api/manual-listing", methods=["POST"])
def api_manual_listing():
    """Create a listing from pasted text and up to 12 uploaded images."""
    try:
        slug, _slug_dir, photos_count = _create_manual_listing_from_form(
            request.form,
            request.files.getlist("images"),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify({
        "status": "ok",
        "slug": slug,
        "message": "Manual listing imported successfully.",
        "photos_count": photos_count,
    })


@app.route("/api/scrape", methods=["POST"])
def api_scrape():
    data = request.get_json()
    if not data or "url" not in data:
        return jsonify({"error": "Missing 'url' in request body"}), 400

    url = data["url"].strip()
    if not url:
        return jsonify({"error": "URL is empty"}), 400

    if not _is_supported_scraper_url(url):
        return jsonify({
            "error": "Unsupported URL. Use manual import for this listing.",
            "unsupported": True,
            "supported_hosts": list(SUPPORTED_SCRAPER_HOSTS),
        }), 400

    def generate_progress():
        """Stream scraper output line by line to the frontend."""
        main_py = os.path.join(SCRIPT_DIR, "main.py")
        slug = None
        output_dir = None
        try:
            from main import derive_slug
            slug = derive_slug(url)
            output_dir = os.path.join(AUTA_DIR, slug)
        except Exception:
            pass

        try:
            env = os.environ.copy()
            env["SCRAPPER_AUTA_DIR"] = AUTA_DIR
            env.setdefault("DEMO_MAX_SCRAPED_IMAGES", str(DEMO_MAX_SCRAPED_IMAGES))
            process = subprocess.Popen(
                [sys.executable, main_py, url],
                cwd=SCRIPT_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
                env=env,
            )

            for line in process.stdout:
                line = line.rstrip()
                if line:
                    yield f"data: {json.dumps({'line': line})}\n\n"

            process.wait(timeout=120)

            if process.returncode == 0:
                payload = {
                    "done": True,
                    "message": "Scraping completed successfully!",
                }
                if slug and output_dir and os.path.exists(os.path.join(output_dir, "car_info.md")):
                    payload["slug"] = slug
                yield f"data: {json.dumps(payload)}\n\n"
            else:
                yield f"data: {json.dumps({'done': True, 'message': f'Scraping finished with exit code {process.returncode}'})}\n\n"

        except subprocess.TimeoutExpired:
            yield f"data: {json.dumps({'done': True, 'message': 'Scraping timed out after 120 seconds'})}\n\n"
            try:
                process.kill()
            except Exception:
                pass
        except Exception as e:
            yield f"data: {json.dumps({'done': True, 'message': f'Error: {str(e)}'})}\n\n"

        yield "data: [DONE]\n\n"

    return Response(
        stream_with_context(generate_progress()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ─── AI Analysis ────────────────────────────────────────────────────

def _build_analysis_payload(slug_dir, slug, prompt_version="v3", output_language="sk"):
    """Build the system prompt + user content for the LLM."""
    car_info_path = os.path.join(slug_dir, "car_info.md")
    
    # Select prompt file based on version
    prompt_files = {
        "v1": "analyze_prompt.txt",
        "v2": "analyze_prompt_v2.txt",
        "v3": "analyze_prompt_v3.txt",
        "demo": DEMO_PROMPT_FILE,
    }
    prompt_filename = prompt_files.get(prompt_version, "analyze_prompt_v3.txt")
    
    prompt_path = os.path.join(SCRIPT_DIR, prompt_filename)

    if not os.path.exists(car_info_path):
        return None, "Chyba: car_info.md neexistuje."
    if not os.path.exists(prompt_path):
        return None, f"Chyba: {prompt_filename} neexistuje."

    with open(prompt_path, "r", encoding="utf-8") as f:
        system_prompt = f.read()

    with open(car_info_path, "r", encoding="utf-8") as f:
        car_info = f.read()

    # Build KB section
    kb_section = ""
    try:
        from main import find_matching_kb_files
        matched = find_matching_kb_files(KB_DIR, car_info)
        if matched:
            kb_section = "\n\n## 💾 KNOWLEDGE BASE (cached component data):\n"
            for category, filepath in matched:
                with open(filepath, "r", encoding="utf-8") as f:
                    kb_section += f"\n### [{category.upper()}] {os.path.basename(filepath)}:\n```json\n{f.read()}\n```\n"
    except Exception:
        pass  # KB matching is optional
    if DEMO_MODE and DEMO_SKIP_KB:
        kb_section = ""

    try:
        image_data_list, image_meta = prepare_llm_images(slug_dir)
    except ImportError:
        image_data_list = []
        image_meta = {
            "original_count": 0,
            "selected_originals": [],
            "optimized_files": [],
            "collage_groups": [],
            "collage_count": 0,
            "selected_count": 0,
            "collage_capacity": LLM_COLLAGE_COLUMNS * LLM_COLLAGE_ROWS,
            "error": "Pillow is not installed. Run: pip install -r requirements.txt",
        }

    image_list = (
        f"\n\n## 📸 FOTOGRAFIE ({image_meta.get('collage_count', len(image_data_list))} koláží / attachmentov, "
        f"{image_meta.get('selected_count', 0)} vybraných fotiek z {image_meta['original_count']} originálov)\n"
    )
    if image_meta.get("error"):
        image_list += f"- ⚠️ {image_meta['error']}\n"
    for group in image_meta.get("collage_groups", []):
        item_list = ", ".join(
            f"Foto {item['number']:02d} = `{item['original_name']}`"
            for item in group["items"]
        )
        image_list += f"- {group['collage']} obsahuje: {item_list}\n"

    if image_meta.get("selected_count", len(image_data_list)) < image_meta["original_count"]:
        image_list += (
            f"\n⚠️ Poznámka: Do LLM bolo odoslaných {image_meta.get('selected_count', len(image_data_list))} "
            f"reprezentatívnych fotografií zlúčených do {image_meta.get('collage_count', len(image_data_list))} "
            f"koláží z {image_meta['original_count']} originálov. "
            "Originály zostávajú nezmenené v priečinku `images/`.\n"
        )

    user_content = f"""## DÁTA Z INZERÁTU:

{car_info}
{kb_section}{image_list}

---

## ✅ INŠTRUKCIA:
Analyzuj tento inzerát podľa systémového promptu vyššie. Použi všetky fázy analýzy a vygeneruj kompletný lokalizovaný výstup vrátane hodnotenia.
"""

    # OUTPUT_LANGUAGE is already handled by the system prompt (analyze_prompt_v4_koyeb.txt)
    # which reads the OUTPUT_LANGUAGE field from the application. No need to inject it here.
    # The v4 prompt already defines the exact output format with localized rating names.
    pass

    return system_prompt, user_content, image_data_list


def _trim_repeated_analysis_after_kb(text):
    """Stop Gemini if it restarts the full report after the KB update section."""
    heading_matches = list(re.finditer(r"#\s*(?:🚗\s*)?Analýza\s*:", text))
    if len(heading_matches) < 2:
        return text, False

    first_heading = heading_matches[0]
    for repeated_heading in heading_matches[1:]:
        kb_pos = text.find("## 💾 KNOWLEDGE BASE UPDATE", first_heading.end(), repeated_heading.start())
        if kb_pos != -1:
            return text[:repeated_heading.start()].rstrip() + "\n", True

    return text, False


def _strip_kb_section(text):
    """Return the public-facing analysis without KB save/update internals."""
    if not text:
        return ""

    # Try heading first (v2 prompt format)
    kb_match = re.search(r"\n?##\s*💾\s*KNOWLEDGE BASE UPDATE\b", text)
    if kb_match:
        text = text[:kb_match.start()]
    else:
        # Fallback: strip from first [SAVE AS knowledge_base/ block (v3 prompt format)
        kb_match = re.search(r"\n\[SAVE AS knowledge_base/", text)
        if kb_match:
            text = text[:kb_match.start()]

    text = text.replace("<!-- END_ANALYSIS -->", "")
    return text.rstrip() + "\n"


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok", "demo_mode": DEMO_MODE})


def _demo_analysis_events(slug, output_language="sk"):
    keys = _demo_api_keys()
    if not keys:
        yield f"data: {json.dumps({'error': 'Gemini API keys are not configured on the server.'})}\n\n"
        yield "data: [DONE]\n\n"
        return

    slug_dir = _safe_slug_dir(slug)
    if not os.path.isdir(slug_dir):
        yield f"data: {json.dumps({'error': 'Listing job not found.'})}\n\n"
        yield "data: [DONE]\n\n"
        return

    system_prompt, user_content, image_data_list = _build_analysis_payload(
        slug_dir,
        slug,
        prompt_version="demo",
        output_language=output_language,
    )
    if system_prompt is None:
        yield f"data: {json.dumps({'error': user_content})}\n\n"
        yield "data: [DONE]\n\n"
        return

    from llm_client import _call_gemini, run_grounded_web_research

    full_text = ""
    last_error = None
    for key_index, api_key in enumerate(keys, 1):
        label = "primary" if key_index == 1 else "backup"
        try:
            analysis_user_content = user_content
            yield f"data: {json.dumps({'status': f'Using {label} Gemini key.'})}\n\n"
            try:
                yield f"data: {json.dumps({'status': 'Running web verification...'})}\n\n"
                grounded_research = run_grounded_web_research(api_key, user_content)
                if grounded_research:
                    analysis_user_content = f"""{user_content}

---

## WEB VERIFICATION VIA GEMINI GOOGLE SEARCH

{grounded_research}

---

Use the web verification above only when it contains concrete evidence. Never invent URLs.
"""
            except Exception as grounding_error:
                safe_log(f"Demo grounding warning: {grounding_error}")
                yield f"data: {json.dumps({'status': 'Web verification unavailable; continuing with listing data.'})}\n\n"

            yield f"data: {json.dumps({'status': 'Generating analysis...'})}\n\n"
            for chunk in _call_gemini(api_key, system_prompt, analysis_user_content, image_data_list):
                full_text += chunk
                public_chunk = _strip_kb_section(full_text)
                already_public = _strip_kb_section(full_text[:-len(chunk)] if chunk else full_text)
                emit = public_chunk[len(already_public):]
                if emit:
                    yield f"data: {json.dumps({'text': emit})}\n\n"

            public_text = _normalize_analysis_for_slug(slug_dir, _strip_kb_section(full_text))
            with open(os.path.join(slug_dir, "analysis_result.md"), "w", encoding="utf-8") as f:
                f.write(public_text)
            yield f"data: {json.dumps({'done': True, 'slug': slug})}\n\n"
            yield "data: [DONE]\n\n"
            return
        except (ApiKeyError, RateLimitError) as exc:
            last_error = str(exc)
            if key_index < len(keys):
                yield f"data: {json.dumps({'status': f'{label.title()} key failed; trying backup key.'})}\n\n"
                continue
            yield f"data: {json.dumps({'error': last_error})}\n\n"
        except Exception as exc:
            last_error = str(exc)
            safe_log(f"Demo analysis error: {exc}")
            yield f"data: {json.dumps({'error': f'Analysis failed: {last_error}'})}\n\n"
        break

    yield "data: [DONE]\n\n"


def _stream_with_demo_limits(generator_factory):
    access_error = _check_demo_access(request.get_json(silent=True) or {})
    if access_error:
        return access_error
    rate_error = _check_demo_rate_limit()
    if rate_error:
        return rate_error
    if not _demo_job_lock.acquire(blocking=False):
        return jsonify({"error": "Another demo analysis is already running. Try again in a moment."}), 429

    def generate():
        try:
            _cleanup_old_demo_jobs()
            yield from generator_factory()
        finally:
            _demo_job_lock.release()

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/demo/analyze", methods=["POST"])
def api_demo_analyze():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    output_language = _demo_output_language(data.get("output_language"))
    if not url:
        return jsonify({"error": "URL is required."}), 400
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return jsonify({"error": "Enter a valid http(s) listing URL."}), 400
    if _is_known_unsupported_demo_url(url):
        return jsonify({"error": "Automatic mobile.de scraping is disabled in this demo. Use manual listing mode.", "unsupported": True}), 400
    if not _is_supported_scraper_url(url):
        return jsonify({"error": "This marketplace is not supported for automatic scraping. Use manual listing mode.", "unsupported": True}), 400

    def events():
        main_py = os.path.join(SCRIPT_DIR, "main.py")
        from main import derive_slug
        slug = derive_slug(url)
        env = os.environ.copy()
        env["SCRAPPER_AUTA_DIR"] = AUTA_DIR
        env.setdefault("DEMO_MAX_SCRAPED_IMAGES", str(DEMO_MAX_SCRAPED_IMAGES))
        yield f"data: {json.dumps({'status': 'Scraping listing...'})}\n\n"
        process = subprocess.Popen(
            [sys.executable, main_py, url],
            cwd=SCRIPT_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
            env=env,
        )
        try:
            for line in process.stdout:
                line = line.strip()
                if line:
                    yield f"data: {json.dumps({'log': line})}\n\n"
            process.wait(timeout=150)
        except subprocess.TimeoutExpired:
            process.kill()
            yield f"data: {json.dumps({'error': 'Scraping timed out.'})}\n\n"
            yield "data: [DONE]\n\n"
            return
        if process.returncode != 0:
            yield f"data: {json.dumps({'error': f'Scraper failed with exit code {process.returncode}.'})}\n\n"
            yield "data: [DONE]\n\n"
            return
        if not os.path.exists(os.path.join(_safe_slug_dir(slug), "car_info.md")):
            yield f"data: {json.dumps({'error': 'Scraper finished but did not create listing data.'})}\n\n"
            yield "data: [DONE]\n\n"
            return
        yield f"data: {json.dumps({'status': 'Listing ready. Starting AI analysis...', 'slug': slug})}\n\n"
        yield from _demo_analysis_events(slug, output_language)

    return _stream_with_demo_limits(events)


@app.route("/api/demo/analyze-manual", methods=["POST"])
def api_demo_analyze_manual():
    access_error = _check_demo_access()
    if access_error:
        return access_error
    rate_error = _check_demo_rate_limit()
    if rate_error:
        return rate_error
    if not _demo_job_lock.acquire(blocking=False):
        return jsonify({"error": "Another demo analysis is already running. Try again in a moment."}), 429

    output_language = _demo_output_language(request.form.get("output_language"))
    _cleanup_old_demo_jobs()
    try:
        slug, _slug_dir, photos_count = _create_manual_listing_from_form(
            request.form,
            request.files.getlist("images"),
        )
    except ValueError as exc:
        _demo_job_lock.release()
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        _demo_job_lock.release()
        safe_log(f"Manual demo import error: {exc}")
        return jsonify({"error": f"Manual listing import failed: {str(exc)}"}), 500

    def generate():
        try:
            yield f"data: {json.dumps({'status': f'Manual listing ready with {photos_count} photos.', 'slug': slug})}\n\n"
            yield from _demo_analysis_events(slug, output_language)
        finally:
            _demo_job_lock.release()

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/analyze/<slug>", methods=["POST"])
def api_analyze(slug):
    """
    Run AI analysis on a listing.
    Uses Google Gemini only.
    Expects JSON body: {"api_key": "..."}
    Returns SSE stream of the analysis text.
    """
    data = request.get_json()
    if not data or "api_key" not in data:
        return jsonify({"error": "Chýba API kľúč. Pridaj ho v Nastaveniach."}), 400

    api_key = data["api_key"].strip()
    if not api_key:
        return jsonify({"error": "Chýba Gemini API kľúč. Pridaj ho v Nastaveniach."}), 400

    slug_dir = os.path.join(AUTA_DIR, slug)
    if not os.path.isdir(slug_dir):
        return jsonify({"error": "Inzerát nenájdený"}), 404

    prompt_version = data.get("prompt_version", "v3")
    system_prompt, user_content, image_data_list = _build_analysis_payload(slug_dir, slug, prompt_version)
    if system_prompt is None:
        return jsonify({"error": user_content}), 400

    def generate():
        try:
            full_text = ""
            
            try:
                from llm_client import _call_gemini, run_grounded_web_research

                analysis_user_content = user_content
                if prompt_version == "v3":
                    yield f"data: {json.dumps({'status': 'Spustam webove overenie cez Gemini Google Search...'})}\n\n"
                    try:
                        grounded_research = run_grounded_web_research(api_key, user_content)
                        if grounded_research:
                            analysis_user_content = f"""{user_content}

---

## 🌐 WEBOVÉ OVERENIE CEZ GEMINI GOOGLE SEARCH

{grounded_research}

---

## ✅ DOPLŇUJÚCA INŠTRUKCIA PRE FINÁLNU ANALÝZU:
Použi webové overenie vyššie ako zdroj s dôkazom `Web / Google Search`.
Cituj konkrétne URL iba z tejto sekcie. Ak webové overenie niečo nenašlo, nepredstieraj opak.
"""
                            yield f"data: {json.dumps({'status': 'Webove overenie hotove. Spustam finalnu obrazovu analyzu...'})}\n\n"
                    except Exception as grounding_error:
                        grounding_warning = (
                            "⚠️ Webové overenie cez Google Search sa nepodarilo. "
                            f"Pokračujem bez online zdrojov. Detail: {str(grounding_error)}"
                        )
                        safe_log(grounding_warning)
                        analysis_user_content = f"""{user_content}

---

## 🌐 WEBOVÉ OVERENIE CEZ GEMINI GOOGLE SEARCH

{grounding_warning}

---

## ✅ DOPLŇUJÚCA INŠTRUKCIA PRE FINÁLNU ANALÝZU:
Online zdroje nie sú dostupné. Nepredstieraj webové overenie ani nevymýšľaj URL.
"""
                        yield f"data: {json.dumps({'status': grounding_warning})}\n\n"

                for chunk in _call_gemini(api_key, system_prompt, analysis_user_content, image_data_list):
                    candidate_text = full_text + chunk
                    trimmed_text, stopped_repetition = _trim_repeated_analysis_after_kb(candidate_text)
                    chunk_to_emit = trimmed_text[len(full_text):]
                    if chunk_to_emit:
                        full_text = trimmed_text
                        yield f"data: {json.dumps({'text': chunk_to_emit})}\n\n"
                    if stopped_repetition:
                        safe_log("Stopped repeated analysis loop after KB update.")
                        yield f"data: {json.dumps({'status': 'Zastavil som opakovanie analýzy po KB sekcii.'})}\n\n"
                        break

                # Save complete result
                full_text, _ = _trim_repeated_analysis_after_kb(full_text)
                full_text = _normalize_analysis_for_slug(slug_dir, full_text)
                result_path = os.path.join(slug_dir, "analysis_result.md")
                with open(result_path, "w", encoding="utf-8") as f:
                    f.write(full_text)

                # Check for KB save blocks
                kb_blocks = extract_kb_save_blocks(full_text)
                saved_kb = []
                if kb_blocks:
                    try:
                        saved_kb = _save_kb_blocks(kb_blocks)
                        if saved_kb:
                            kb_autosave_path = os.path.join(slug_dir, "kb_autosave.json")
                            with open(kb_autosave_path, "w", encoding="utf-8") as f:
                                json.dump(
                                    {
                                        "saved_at": datetime.now().isoformat(timespec="seconds"),
                                        "saved": saved_kb,
                                    },
                                    f,
                                    indent=2,
                                    ensure_ascii=False,
                                )
                            saved_names = ", ".join(
                                f"{item['category']}/{item['filename']}"
                                for item in saved_kb
                            )
                            yield f"data: {json.dumps({'status': f'KB záznamy automaticky uložené: {saved_names}'})}\n\n"
                    except Exception as e:
                        safe_log(f"KB autosave error: {e}")
                        yield f"data: {json.dumps({'status': f'⚠️ Analýza je hotová, ale automatické uloženie KB zlyhalo: {str(e)}'})}\n\n"

                yield f"data: {json.dumps({'done': True, 'has_kb_blocks': len(kb_blocks) > 0, 'saved_kb': saved_kb})}\n\n"
                
            except ApiKeyError as e:
                yield f"data: {json.dumps({'error': str(e), 'api_key_error': True})}\n\n"
                return
            except RateLimitError as e:
                yield f"data: {json.dumps({'error': str(e), 'rate_limited': True})}\n\n"
                return
            except ConnectionError as e:
                yield f"data: {json.dumps({'error': str(e), 'connection_error': True})}\n\n"
                return
            except Exception as e:
                # Catch any other exceptions during API call
                import traceback
                error_detail = f"{str(e)}\n{traceback.format_exc()}"
                safe_log(f"ERROR in generate(): {error_detail}")  # Debug log
                yield f"data: {json.dumps({'error': f'❌ Chyba API: {str(e)}'})}\n\n"
                return

        except Exception as e:
            # Catch any exceptions in the generator itself
            import traceback
            error_detail = f"{str(e)}\n{traceback.format_exc()}"
            safe_log(f"CRITICAL ERROR in generate(): {error_detail}")  # Debug log
            yield f"data: {json.dumps({'error': f'❌ Kritická chyba: {str(e)}'})}\n\n"

        yield "data: [DONE]\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/api/analyze/<slug>/save-pasted-result", methods=["POST"])
def api_save_pasted_result(slug):
    """
    Save a manually pasted ChatGPT analysis result as analysis_result.md.
    Expects JSON body: {"content": "full markdown text from ChatGPT"}
    """
    data = request.get_json()
    if not data or "content" not in data:
        return jsonify({"error": "Chýba 'content' v požiadavke."}), 400

    content = data["content"].strip()
    if not content:
        return jsonify({"error": "Obsah je prázdny."}), 400

    slug_dir = os.path.join(AUTA_DIR, slug)
    if not os.path.isdir(slug_dir):
        return jsonify({"error": "Inzerát nenájdený"}), 404

    content = _normalize_analysis_for_slug(slug_dir, content)
    result_path = os.path.join(slug_dir, "analysis_result.md")
    with open(result_path, "w", encoding="utf-8") as f:
        f.write(content)

    # Check for KB save blocks
    kb_blocks = extract_kb_save_blocks(content)

    return jsonify({
        "status": "ok",
        "message": "✅ Výsledok z ChatGPT bol uložený ako analysis_result.md!",
        "has_kb_blocks": len(kb_blocks) > 0
    })


@app.route("/api/listings/<slug>/open-folder", methods=["GET"])
def api_open_folder(slug):
    """
    Open the listing's images folder in Windows Explorer.
    """
    slug_dir = os.path.join(AUTA_DIR, slug)
    if not os.path.isdir(slug_dir):
        return jsonify({"error": "Inzerát nenájdený"}), 404

    images_dir = os.path.join(slug_dir, "images")
    if not os.path.isdir(images_dir):
        return jsonify({"error": "Priečinok s fotkami neexistuje"}), 404

    try:
        import subprocess
        subprocess.Popen(['explorer', images_dir])
        return jsonify({
            "status": "ok",
            "message": "📂 Priečinok s fotkami bol otvorený v Prieskumníkovi.",
            "path": images_dir
        })
    except Exception as e:
        return jsonify({"error": f"❌ Nepodarilo sa otvoriť priečinok: {str(e)}"}), 500


@app.route("/api/analyze/<slug>/save-kb", methods=["POST"])
def api_save_kb(slug):
    """
    Parse analysis_result.md for [SAVE AS knowledge_base/...] blocks
    and save them to the knowledge base.
    """
    slug_dir = os.path.join(AUTA_DIR, slug)
    result_path = os.path.join(slug_dir, "analysis_result.md")

    if not os.path.exists(result_path):
        return jsonify({"error": "Najprv spusti analýzu."}), 400

    with open(result_path, "r", encoding="utf-8") as f:
        text = f.read()

    blocks = extract_kb_save_blocks(text)
    if not blocks:
        return jsonify({"error": "Žiadne [SAVE AS] bloky nenájdené v analýze.", "saved": []}), 200

    saved = _save_kb_blocks(blocks)
    if saved:
        kb_autosave_path = os.path.join(slug_dir, "kb_autosave.json")
        with open(kb_autosave_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "saved_at": datetime.now().isoformat(timespec="seconds"),
                    "saved": saved,
                },
                f,
                indent=2,
                ensure_ascii=False,
            )
    return jsonify({"status": "ok", "saved": saved})


def _save_kb_blocks(blocks):
    """Save extracted KB blocks and rebuild the KB index."""
    saved = []
    for block in blocks:
        category = block["category"]
        filename = block["filename"]
        data = block["data"]

        # Security: prevent path traversal
        if ".." in filename or "/" in filename or "\\" in filename:
            continue

        cat_dir = os.path.join(KB_DIR, category)
        os.makedirs(cat_dir, exist_ok=True)

        filepath = os.path.join(cat_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        saved.append({"category": category, "filename": filename})

    # Update index.json with new aliases
    _update_kb_index(saved, blocks)

    # Also run update_index.py for full index rebuild
    try:
        update_script = os.path.join(SCRIPT_DIR, "update_index.py")
        if os.path.exists(update_script):
            result = subprocess.run(
                [sys.executable, update_script],
                cwd=SCRIPT_DIR,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                safe_log(f"  KB index rebuilt via update_index.py")
            else:
                safe_log(f"  update_index.py exited with code {result.returncode}: {result.stderr[:200]}")
    except Exception as e:
        safe_log(f"  update_index.py error: {e}")

    return saved


@app.route("/api/test-api-key", methods=["POST"])
@app.route("/api/test-backup-key", methods=["POST"])
def api_test_api_key():
    """Test a Google Gemini API key."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Chybajuce data v poziadavke."}), 400

    api_key = data.get("api_key", "").strip()
    if not api_key:
        return jsonify({"error": "API kluc je prazdny."}), 400

    system_prompt = "You are a helpful assistant. Please respond to the user's request."
    user_content = "Hello, please confirm you received this test message."

    try:
        for _ in analyze_with_gemini(api_key, system_prompt, user_content):
            pass
        return jsonify({
            "status": "success",
            "key_type": "gemini",
            "message": "Gemini API kluc funguje spravne!",
            "result": "Test message successful - API is working correctly.",
        })
    except RateLimitError as e:
        return jsonify({
            "status": "error",
            "key_type": "gemini",
            "message": f"Gemini limit: {str(e)}",
            "result": "API quota exceeded",
        }), 400
    except ApiKeyError as e:
        return jsonify({
            "status": "error",
            "key_type": "gemini",
            "message": str(e),
            "result": "Invalid API key",
        }), 400
    except Exception as e:
        return jsonify({
            "status": "error",
            "key_type": "gemini",
            "message": f"Chyba: {str(e)}",
            "result": str(e),
        }), 500


def _update_kb_index(saved_blocks, blocks):
    """Update knowledge_base/index.json with new aliases from saved blocks."""
    index_path = os.path.join(KB_DIR, "index.json")
    index = {}
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            index = json.load(f)

    for block in blocks:
        category = block["category"]
        filename = block["filename"]
        data = block["data"]
        aliases = data.get("aliases", [])

        if category not in index:
            index[category] = {}

        for alias in aliases:
            if alias and alias not in index[category]:
                index[category][alias] = filename

    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)


# ─── Main ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  Scrapper Web Server")
    print("=" * 60)
    print(f"  Auta dir:     {AUTA_DIR}")
    print(f"  Knowledge DB: {KB_DIR}")
    print(f"  Web root:     {WEB_DIR}")
    print()
    print(f"  Open: http://localhost:5000")
    print("=" * 60)
    app.run(host="0.0.0.0", port=5000, debug=True)
