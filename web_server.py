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
import unicodedata
from datetime import datetime
from pathlib import Path
from collections import defaultdict

from flask import Flask, jsonify, send_from_directory, request, Response, stream_with_context
from werkzeug.utils import secure_filename

# LLM Client for AI analysis
from llm_client import (
    analyze_with_llm,
    analyze_with_grok,
    analyze_with_openrouter,
    extract_kb_save_blocks,
    RateLimitError,
    ApiKeyError,
    GrokApiKeyError,
    OpenRouterApiKeyError,
    GroundingTransientError,
)
from analysis_normalizer import normalize_analysis_markdown
from token_tracker import default_tracker, estimate_output_tokens, estimate_request_tokens

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
LLM_IMAGE_END_POSITION = 1.0
LLM_OVERVIEW_ATTACHMENTS = max(1, MAX_ANALYSIS_COLLAGES - 1)
LLM_OVERVIEW_CELL_MIN_SIZE = 150
LLM_OVERVIEW_CELL_MAX_SIZE = 280
LLM_OVERVIEW_LABEL_HEIGHT = 26
LLM_OVERVIEW_MARGIN = 6
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".avif"}
SUPPORTED_SCRAPER_HOSTS = ("autobazar.eu", "autobazar.sk", "bazos.sk", "bazos.cz")

# Known car brand names for listing content validation (lowercase).
# Full list covering all major global manufacturers.
CAR_BRANDS = {
    # USA
    "ford", "lincoln", "chevrolet", "gmc", "cadillac", "buick", "chrysler",
    "dodge", "jeep", "ram", "tesla", "rivian", "lucid", "karma",
    "hennessey", "rezvani", "czinger",
    # Japan
    "toyota", "lexus", "daihatsu", "honda", "acura", "nissan", "infiniti",
    "mazda", "mitsubishi", "subaru", "suzuki", "isuzu", "mitsuoka",
    # South Korea
    "hyundai", "kia", "genesis", "kg mobility", "ssangyong",
    # Germany
    "volkswagen", "vw", "audi", "porsche", "bmw", "mini", "mercedes-benz",
    "mercedes", "maybach", "smart", "opel", "alpina", "wiesmann",
    # United Kingdom
    "aston martin", "bentley", "rolls-royce", "rolls royce", "jaguar",
    "land rover", "land-rover", "lotus", "mclaren", "morgan", "caterham",
    "ariel", "ineos", "tvr",
    # France
    "renault", "peugeot", "citroën", "citroen", "ds automobiles", "ds",
    "alpine", "bugatti",
    # Italy
    "fiat", "abarth", "alfa romeo", "alfa-romeo", "lancia", "maserati",
    "ferrari", "lamborghini", "pagani", "dr automobiles",
    # Spain
    "seat", "cupra", "hispano suiza", "spania gta",
    # Sweden
    "volvo", "polestar", "koenigsegg",
    # Czech / Romania
    "škoda", "skoda", "dacia",
    # China
    "byd", "geely", "lynk & co", "zeekr", "chery", "exeed", "omoda",
    "jaecoo", "changan", "avatr", "deepal", "great wall", "haval",
    "tank", "ora", "wey", "s", "mg", "roewe", "maxus", "wuling",
    "baojun", "nio", "xpeng", "li auto", "leapmotor", "hongqi",
    "faw", "dongfeng", "voyah", "aito", "denza", "fangchengbao",
    "yangwang", "gac", "aion", "trumpchi", "jac", "jetour", "luxeed",
    # India
    "tata", "mahindra", "maruti suzuki",
    # Malaysia / Vietnam
    "proton", "perodua", "vinfast",
    # Russia / Eastern Europe
    "lada", "uaz", "gaz",
    # Turkey / Middle East
    "togg", "w motors", "ceer",
    # Croatia / Netherlands / Austria
    "rimac", "donkervoort", "ktm",
}
CAR_KEYWORDS = {
    "km", "motor", "servis", "výbava", "vybava", "najazdené", "najazdene",
    "prevodovka", "kombi", "sedan", "hatchback", "coupé", "coupe", "kupé",
    "diesel", "nafta", "benzín", "benzin", "tdi", "tfsi", "tsi", "bitdi",
    "klimatizácia", "klimatizacia", "tempomat", "cruise control",
    "airbag", "abs", "esp", "asr", "parkovacie senzory", "parkovacia kamera",
    "vyhrievanie", "kožené sedadlá", "koza", "led svetla",
    "homologácia", "homologacia", "stk", "ek", "emisná",
    "automatická", "automaticka", "manuálna", "manualna",
    "4x4", "quattro", "awd", "pohon",
}
UNSUPPORTED_DEMO_HOSTS = ("mobile.de",)
MAX_MANUAL_IMAGES = int(os.environ.get("DEMO_MAX_MANUAL_IMAGES", "12"))
# 0 means unlimited downloads; the LLM payload is capped separately by MAX_ANALYSIS_IMAGES.
DEMO_MAX_SCRAPED_IMAGES = max(0, int(os.environ.get("DEMO_MAX_SCRAPED_IMAGES", "0")))
DEMO_PROMPT_FILE = os.environ.get("DEMO_PROMPT_FILE", "analyze_prompt_v4_koyeb.txt")
DEMO_RATE_LIMIT_PER_IP = os.environ.get("DEMO_RATE_LIMIT_PER_IP", "3/day")
DEMO_MAX_CONCURRENT_JOBS = max(1, int(os.environ.get("DEMO_MAX_CONCURRENT_JOBS", "1")))
DEMO_JOB_TTL_MINUTES = max(5, int(os.environ.get("DEMO_JOB_TTL_MINUTES", "60")))
DEMO_SKIP_KB = os.environ.get("DEMO_SKIP_KB", "true").lower() in {"1", "true", "yes", "on"}
MAX_UPLOAD_BYTES = int(os.environ.get("DEMO_MAX_UPLOAD_MB", "24")) * 1024 * 1024
FINAL_LISTING_DESCRIPTION_CHARS = 900
MODEL_LISTING_DESCRIPTION_CHARS = 1400
FINAL_TEXT_FIELD_CHARS = 420
FINAL_WEB_RESEARCH_CHARS = 1800

os.makedirs(AUTA_DIR, exist_ok=True)

app = Flask(__name__, static_folder=WEB_DIR, static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-demo-secret-change-me")

_demo_job_lock = threading.BoundedSemaphore(DEMO_MAX_CONCURRENT_JOBS)
_demo_rate_counts = defaultdict(int)
_demo_rate_lock = threading.Lock()
_current_progress = {"status": "", "log_lines": [], "done": False}
_progress_lock = threading.Lock()


def _set_current_progress(status=None, line=None, done=False, reset=False):
    """Store live demo progress for the token dashboard polling endpoint."""
    with _progress_lock:
        if reset:
            _current_progress["status"] = ""
            _current_progress["log_lines"] = []
            _current_progress["done"] = False
        if status is not None:
            _current_progress["status"] = str(status)
        if line:
            _current_progress.setdefault("log_lines", []).append(str(line))
            _current_progress["log_lines"] = _current_progress["log_lines"][-120:]
        _current_progress["done"] = bool(done)


def _track_demo_sse_progress(event_text):
    """Mirror demo SSE status/log events into the polling progress endpoint."""
    if not event_text:
        return
    for raw_line in str(event_text).splitlines():
        if not raw_line.startswith("data: "):
            continue
        payload = raw_line[6:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue

        if data.get("error"):
            message = "ERROR: " + str(data["error"])
            _set_current_progress(status=message, line=message, done=True)
            continue

        if data.get("status"):
            status = str(data["status"])
            _set_current_progress(status=status, line=status, done=bool(data.get("done")))

        for key in ("log", "line"):
            if data.get(key):
                _set_current_progress(line=str(data[key]), done=bool(data.get("done")))

        if data.get("token_usage"):
            usage = data["token_usage"] or {}
            token_line = "Tokens sent: ~{0}, received: ~{1}".format(
                usage.get("input_tokens", 0),
                usage.get("output_tokens", 0),
            )
            _set_current_progress(line=token_line, done=False)

        if data.get("done"):
            _set_current_progress(status="Done", line="Done", done=True)


@app.before_request
def _demo_route_gate():
    if not DEMO_MODE:
        return None
    path = request.path.rstrip("/") or "/"
    allowed = (
        path == "/"
        or path == "/token-dashboard.html"
        or path == "/healthz"
        or path == "/api/token-usage"
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


def _read_text_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _listing_car_info_path(slug_dir):
    return os.path.join(slug_dir, "car_info.md")


def _listing_analysis_result_path(slug_dir):
    return os.path.join(slug_dir, "analysis_result.md")


ANALYSIS_ARTIFACTS = {
    "grok_research.json",
    "gemini_vision.json",
    "risk_score.json",
    "web_research.md",
    "validation_warnings.json",
    "analysis_result_raw.md",
    "analysis_result.md",
}


def _listing_images_dir(slug_dir):
    return os.path.join(slug_dir, "images")


def _listing_dir_or_raise(slug):
    slug_dir = _safe_slug_dir(slug)
    if not os.path.isdir(slug_dir):
        raise FileNotFoundError("Listing not found")
    return slug_dir


def _load_listing_parsed(slug_dir):
    car_info_path = _listing_car_info_path(slug_dir)
    if not os.path.exists(car_info_path):
        raise FileNotFoundError("Listing data not found")
    md_text = _read_text_file(car_info_path)
    return md_text, parse_car_info_md(md_text), car_info_path


def _ordered_listing_images(slug_dir):
    images_dir = _listing_images_dir(slug_dir)
    if not os.path.isdir(images_dir):
        return []
    return [
        name for name in sorted(os.listdir(images_dir))
        if _is_supported_image(name) and os.path.isfile(os.path.join(images_dir, name))
    ]


def _listing_image_url(route_prefix, slug, filename):
    return f"{route_prefix}/{urllib.parse.quote(slug)}/image/{urllib.parse.quote(filename)}"


def _listing_artifact_url(route_prefix, slug, filename):
    return f"{route_prefix}/{urllib.parse.quote(slug)}/artifacts/{urllib.parse.quote(filename)}"


def _read_listing_analysis_content(slug_dir, required=False):
    result_path = _listing_analysis_result_path(slug_dir)
    if not os.path.exists(result_path):
        if required:
            raise FileNotFoundError("Analysis result not found")
        return None
    text = _read_text_file(result_path)
    # Strip internal END_ANALYSIS marker before public display
    text = re.sub(r'\n*\s*<!--\s*END_ANALYSIS\s*-->\s*\n*', '', text).rstrip()
    if text:
        text = text + "\n"
    return normalize_analysis_markdown(text, _read_car_info_text(slug_dir))


def _build_listing_summary(slug, slug_dir, image_route_prefix="/api/listings"):
    _md_text, parsed, car_info_path = _load_listing_parsed(slug_dir)
    images = _ordered_listing_images(slug_dir)
    first_image = images[0] if images else None
    has_analysis = os.path.exists(_listing_analysis_result_path(slug_dir))

    summary = {
        "slug": slug,
        "title": parsed["title"],
        "price": parsed["price"],
        "currency": parsed["currency"],
        "year": parsed["specs"].get("Year", ""),
        "mileage": parsed["specs"].get("Mileage", ""),
        "vin": parsed["vin"],
        "photos_count": parsed["photos_count"] or len(images),
        "first_image": first_image,
        "source_url": parsed["source_url"],
        "scraped_at": parsed["scraped_at"],
        "sort_timestamp": _listing_sort_timestamp(parsed, car_info_path),
        "has_analysis": has_analysis,
    }
    if first_image:
        summary["first_image_url"] = _listing_image_url(image_route_prefix, slug, first_image)
    else:
        summary["first_image_url"] = None
    return summary


def _build_listing_detail_payload(slug, image_route_prefix="/api/listings", require_analysis=False):
    slug_dir = _listing_dir_or_raise(slug)
    car_info_md, parsed, _car_info_path = _load_listing_parsed(slug_dir)
    images = _ordered_listing_images(slug_dir)
    payload = {
        "slug": slug,
        "car_info_md": car_info_md,
        "parsed": parsed,
        "source_url": parsed.get("source_url", ""),
        "scraped_at": parsed.get("scraped_at", ""),
        "images": [
            {
                "filename": filename,
                "url": _listing_image_url(image_route_prefix, slug, filename),
            }
            for filename in images
        ],
    }
    analysis_content = _read_listing_analysis_content(slug_dir, required=require_analysis)
    if analysis_content is not None:
        payload["analysis_content"] = analysis_content
    return payload


def _send_listing_image_file(slug, filename):
    if ".." in filename or "/" in filename or "\\" in filename:
        return jsonify({"error": "Invalid filename"}), 400

    slug_dir = _listing_dir_or_raise(slug)
    images_dir = _listing_images_dir(slug_dir)
    if not os.path.isdir(images_dir):
        return jsonify({"error": "Not found"}), 404
    if not _is_supported_image(filename):
        return jsonify({"error": "Not found"}), 404

    image_path = os.path.abspath(os.path.join(images_dir, filename))
    if os.path.commonpath([os.path.abspath(images_dir), image_path]) != os.path.abspath(images_dir):
        return jsonify({"error": "Invalid filename"}), 400
    if not os.path.isfile(image_path):
        return jsonify({"error": "Not found"}), 404
    return send_from_directory(images_dir, filename)


def _list_listing_artifacts(slug, route_prefix="/api/listings"):
    slug_dir = _listing_dir_or_raise(slug)
    artifacts = []
    for filename in sorted(ANALYSIS_ARTIFACTS):
        path = os.path.join(slug_dir, filename)
        if os.path.isfile(path):
            artifacts.append(
                {
                    "filename": filename,
                    "url": _listing_artifact_url(route_prefix, slug, filename),
                    "size": os.path.getsize(path),
                    "modified_at": datetime.fromtimestamp(os.path.getmtime(path)).isoformat(timespec="seconds"),
                }
            )
    return artifacts


def _send_listing_artifact_file(slug, filename):
    if filename not in ANALYSIS_ARTIFACTS:
        return jsonify({"error": "Artifact not allowed"}), 400

    slug_dir = _listing_dir_or_raise(slug)
    artifact_path = os.path.abspath(os.path.join(slug_dir, filename))
    if os.path.commonpath([os.path.abspath(slug_dir), artifact_path]) != os.path.abspath(slug_dir):
        return jsonify({"error": "Invalid artifact"}), 400
    if not os.path.isfile(artifact_path):
        return jsonify({"error": "Artifact not found"}), 404

    with open(artifact_path, "r", encoding="utf-8") as f:
        content = f.read()
    return Response(
        content,
        mimetype="text/plain; charset=utf-8",
        headers={"Cache-Control": "no-cache"},
    )


def get_listings(require_analysis=False, image_route_prefix="/api/listings"):
    """Scan Auta/ directory and return listing summaries."""
    listings = []
    if not os.path.isdir(AUTA_DIR):
        return listings

    for slug in sorted(os.listdir(AUTA_DIR), reverse=True):
        slug_dir = os.path.join(AUTA_DIR, slug)
        if not os.path.isdir(slug_dir):
            continue
        try:
            summary = _build_listing_summary(slug, slug_dir, image_route_prefix=image_route_prefix)
        except FileNotFoundError:
            continue

        if require_analysis and not summary["has_analysis"]:
            continue
        listings.append(summary)

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

def _is_car_listing(car_info_text):
    """
    Lightweight check whether scraped listing content looks like a car ad.
    Returns (is_car: bool, reason: str|None).
    Uses 4 checks; at least 2 must pass to be considered a car listing.
    """
    if not car_info_text or not car_info_text.strip():
        return False, "Scraped listing data is empty."

    text_lower = car_info_text.lower()
    checks_passed = 0
    reasons = []

    # Check 1: Car brand in title (first # heading)
    title_line = ""
    for line in car_info_text.splitlines():
        if line.startswith("# ") and not line.startswith("## "):
            title_line = line[2:].strip().lower()
            break
    if title_line:
        for brand in CAR_BRANDS:
            if brand in title_line:
                checks_passed += 1
                reasons.append(f"brand '{brand}' found in title")
                break

    # Check 2: Price > 0
    price_match = re.search(r'\*\*Price:\*\*\s*([\d\s]+)', car_info_text)
    if price_match:
        try:
            price = int(price_match.group(1).replace(" ", ""))
            if price > 0:
                checks_passed += 1
                reasons.append(f"price {price} EUR found")
        except ValueError:
            pass

    # Check 3: Car-specific specs present (mileage, year, engine, fuel, transmission, VIN)
    spec_keywords = ["mileage", "year", "engine", "fuel", "transmission", "vin", "najazdene", "rok", "motor"]
    spec_found = any(kw in text_lower for kw in spec_keywords)
    if spec_found:
        checks_passed += 1
        reasons.append("car specs detected")

    # Check 4: Car-related keywords in description
    desc_section = ""
    in_desc = False
    for line in car_info_text.splitlines():
        if line.strip().startswith("## Seller Note") or line.strip().startswith("## Description"):
            in_desc = True
            continue
        if line.startswith("## ") and in_desc:
            break
        if in_desc:
            desc_section += line.lower() + " "
    if desc_section:
        keyword_matches = [kw for kw in CAR_KEYWORDS if kw in desc_section]
        if len(keyword_matches) >= 2:
            checks_passed += 1
            reasons.append(f"car keywords in description: {', '.join(keyword_matches[:4])}")

    is_car = checks_passed >= 2
    if is_car:
        return True, None
    return False, (
        f"This doesn't appear to be a car listing (only {checks_passed}/4 car indicators matched). "
        "Please enter a URL for a car advertisement."
    )


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


def _demo_grok_api_key():
    return os.environ.get("GROK_API_KEY", "").strip()


def _demo_openrouter_api_key():
    return os.environ.get("OPENROUTER_API_KEY", "").strip()


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


@app.route("/token-dashboard.html")
def token_dashboard():
    return send_from_directory(WEB_DIR, "token-dashboard.html")


@app.route("/api/token-usage")
def api_token_usage():
    try:
        limit = int(request.args.get("limit", "50"))
    except ValueError:
        limit = 50
    return jsonify(default_tracker.get_stats(recent_limit=limit))


@app.route("/api/demo/current-progress")
def api_demo_current_progress():
    with _progress_lock:
        return jsonify(dict(_current_progress))


@app.route("/api/listings")
def api_listings():
    return jsonify(get_listings())


@app.route("/api/demo/listings")
def api_demo_listings():
    return jsonify(get_listings(require_analysis=True, image_route_prefix="/api/demo/listings"))


@app.route("/api/listings/<slug>")
def api_listing_detail(slug):
    try:
        result = _build_listing_detail_payload(slug)
        slug_dir = _listing_dir_or_raise(slug)
    except FileNotFoundError:
        return jsonify({"error": "Listing not found"}), 404

    raw_data_path = os.path.join(slug_dir, "raw_data.json")
    vin_decoded_path = os.path.join(slug_dir, "vin_decoded.json")

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


@app.route("/api/demo/listings/<slug>")
def api_demo_listing_detail(slug):
    try:
        payload = _build_listing_detail_payload(
            slug,
            image_route_prefix="/api/demo/listings",
            require_analysis=True,
        )
    except FileNotFoundError:
        return jsonify({"error": "Saved analysis not found"}), 404
    return jsonify(payload)


@app.route("/api/listings/<slug>/artifacts")
def api_listing_artifacts(slug):
    try:
        return jsonify({"slug": slug, "artifacts": _list_listing_artifacts(slug)})
    except FileNotFoundError:
        return jsonify({"error": "Listing not found"}), 404


@app.route("/api/demo/listings/<slug>/artifacts")
def api_demo_listing_artifacts(slug):
    try:
        return jsonify({"slug": slug, "artifacts": _list_listing_artifacts(slug, route_prefix="/api/demo/listings")})
    except FileNotFoundError:
        return jsonify({"error": "Saved analysis not found"}), 404


@app.route("/api/listings/<slug>/artifacts/<filename>")
def api_listing_artifact(slug, filename):
    try:
        return _send_listing_artifact_file(slug, filename)
    except FileNotFoundError:
        return jsonify({"error": "Listing not found"}), 404


@app.route("/api/demo/listings/<slug>/artifacts/<filename>")
def api_demo_listing_artifact(slug, filename):
    try:
        return _send_listing_artifact_file(slug, filename)
    except FileNotFoundError:
        return jsonify({"error": "Saved analysis not found"}), 404


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
    try:
        slug_dir = _listing_dir_or_raise(slug)
    except FileNotFoundError:
        return jsonify([])
    images_dir = _listing_images_dir(slug_dir)
    if not os.path.isdir(images_dir):
        return jsonify([])

    images = []
    for filename in _ordered_listing_images(slug_dir):
        filepath = os.path.join(images_dir, filename)
        if os.path.isfile(filepath):
            size_kb = os.path.getsize(filepath) / 1024
            images.append({
                "filename": filename,
                "size_kb": round(size_kb, 1),
            })

    return jsonify(images)


@app.route("/api/listings/<slug>/image/<filename>")
def api_listing_image(slug, filename):
    try:
        return _send_listing_image_file(slug, filename)
    except FileNotFoundError:
        return jsonify({"error": "Not found"}), 404


@app.route("/api/demo/listings/<slug>/image/<filename>")
def api_demo_listing_image(slug, filename):
    try:
        return _send_listing_image_file(slug, filename)
    except FileNotFoundError:
        return jsonify({"error": "Not found"}), 404


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


def _public_analysis_markdown(text, slug_dir):
    return normalize_analysis_markdown(text, _read_car_info_text(slug_dir))


@app.route("/api/listings/<slug>/analysis-result")
def api_listing_analysis_result(slug):
    slug_dir = os.path.join(AUTA_DIR, slug)
    result_path = os.path.join(slug_dir, "analysis_result.md")
    kb_autosave_path = os.path.join(slug_dir, "kb_autosave.json")
    if not os.path.exists(result_path):
        return jsonify({"error": "Result not found"}), 404

    with open(result_path, "r", encoding="utf-8") as f:
        raw_content = f.read()
    # Strip internal END_ANALYSIS marker before public API response
    content = _public_analysis_markdown(re.sub(r'\n*\s*<!--\s*END_ANALYSIS\s*-->\s*\n*', '', raw_content).rstrip(), slug_dir)

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


@app.route("/api/listings/<slug>/analysis-result/raw")
def api_listing_analysis_result_raw(slug):
    slug_dir = os.path.join(AUTA_DIR, slug)
    raw_path = os.path.join(slug_dir, "analysis_result_raw.md")
    if not os.path.exists(raw_path):
        return jsonify({"error": "Raw result not found"}), 404

    with open(raw_path, "r", encoding="utf-8") as f:
        content = f.read()

    return jsonify({
        "content": content,
    })


@app.route("/api/demo/listings/<slug>/analysis-result/raw")
def api_demo_listing_analysis_result_raw(slug):
    slug_dir = os.path.join(AUTA_DIR, slug)
    raw_path = os.path.join(slug_dir, "analysis_result_raw.md")
    if not os.path.exists(raw_path):
        return jsonify({"error": "Raw result not found"}), 404

    with open(raw_path, "r", encoding="utf-8") as f:
        content = f.read()

    return jsonify({
        "content": content,
    })


@app.route("/api/listings/<slug>/analysis-result/export")
def api_listing_analysis_export(slug):
    slug_dir = os.path.join(AUTA_DIR, slug)
    result_path = os.path.join(slug_dir, "analysis_result.md")
    if not os.path.exists(result_path):
        return jsonify({"error": "Result not found"}), 404

    with open(result_path, "r", encoding="utf-8") as f:
        stripped_content = _public_analysis_markdown(_strip_kb_section(f.read()), slug_dir)

    embedded_content = _embed_collage_images(stripped_content, slug)

    return jsonify({
        "title": slug.replace("-", " ").title(),
        "content": embedded_content,
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


def prepare_llm_images(slug_dir):
    """
    Create analysis-only contact sheets and return API-ready base64 tuples.
    Originals in images/ are never modified.
    """
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
    data = request.get_json(silent=True) or {}
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

        process = None
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
            if process:
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
        return None, "Chyba: car_info.md neexistuje.", []
    if not os.path.exists(prompt_path):
        return None, f"Chyba: {prompt_filename} neexistuje.", []

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
            "coverage_mode": "none",
            "original_count": 0,
            "selected_originals": [],
            "optimized_files": [],
            "collage_groups": [],
            "collage_count": 0,
            "selected_count": 0,
            "overview_count": 0,
            "detail_count": 0,
            "overview_includes_all": False,
            "full_gallery_included": False,
            "collage_capacity": LLM_COLLAGE_COLUMNS * LLM_COLLAGE_ROWS,
            "error": "Pillow is not installed. Run: pip install -r requirements.txt",
        }

    image_list = (
        f"\n\n## 📸 FOTOGRAFIE ({image_meta.get('collage_count', len(image_data_list))} koláží / attachmentov, "
        f"{image_meta.get('selected_count', 0)} vybraných fotiek z {image_meta['original_count']} originálov)\n"
    )
    image_list += (
        f"- Coverage mode: {image_meta.get('coverage_mode', 'unknown')}\n"
        f"- Full gallery included in image payload: {bool(image_meta.get('full_gallery_included'))}\n"
        f"- Overview sheets include all originals: {bool(image_meta.get('overview_includes_all'))}\n"
        f"- Overview sheets: {image_meta.get('overview_count', 0)}, detail photos: {image_meta.get('detail_count', 0)}\n"
    )
    if image_meta.get("error"):
        image_list += f"- ⚠️ {image_meta['error']}\n"
    for group in image_meta.get("collage_groups", []):
        group_type = group.get("type", "detail")
        item_list = ", ".join(
            f"Foto {item['number']:02d} = `{item['original_name']}`"
            for item in group["items"]
        )
        image_list += f"- {group['collage']} ({group_type}) obsahuje: {item_list}\n"

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

    user_content += (
        "\n\n---\n\n"
        "## OUTPUT_LANGUAGE:\n"
        f"{_demo_output_language(output_language)}\n\n"
        "## DEMO INSTRUCTION:\n"
        "Use the localized report schema for OUTPUT_LANGUAGE (`sk` or `en`) exactly as defined in the system prompt. "
        "Preserve the required Markdown structure: emoji headings, valid Markdown tables, valid lists, and localized rating names. "
        "Do not use raw color labels like YELLOW/GREEN/RED as ratings. Do not include knowledge_base save blocks, "
        "internal API details, access-code state, or debugging metadata.\n"
    )

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

    return text.rstrip() + "\n"


def _embed_collage_images(content, slug):
    """
    Embed collage images into the analysis content for PDF/HTML export.
    Returns the content with image markdown references added.
    """
    analysis_dir = os.path.join(AUTA_DIR, slug, ".analysis_images")
    if not os.path.isdir(analysis_dir):
        return content

    collage_files = sorted([
        f for f in os.listdir(analysis_dir)
        if f.endswith(".jpg") or f.endswith(".png")
    ])

    if not collage_files:
        return content

    # Build image section to prepend to the analysis
    image_section = "\n\n## 📸 Fotografie z inzerátu\n\n"
    for collage in collage_files:
        image_section += f"![{collage}]({slug}/analysis-image/{collage})\n\n"

    return image_section + content


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok", "demo_mode": DEMO_MODE})


def _build_text_research_context(car_info_text, output_language="sk", web_research_text=""):
    kb_section = ""
    try:
        from main import find_matching_kb_files
        matched = find_matching_kb_files(KB_DIR, car_info_text)
        if matched:
            kb_section = "\n\n## Knowledge base matches\n"
            for category, filepath in matched:
                with open(filepath, "r", encoding="utf-8") as f:
                    kb_section += f"\n### [{category}] {os.path.basename(filepath)}\n```json\n{f.read()}\n```\n"
    except Exception as exc:
        safe_log(f"KB matching warning: {exc}")

    if DEMO_MODE and DEMO_SKIP_KB:
        kb_section = ""

    web_section = ""
    if web_research_text:
        web_section = f"\n\n## Provided web research results\n{web_research_text}"

    return f"""## Listing data
{car_info_text}
{kb_section}{web_section}

## Output language
{_demo_output_language(output_language)}
"""


def _clip_text(value, max_chars=FINAL_TEXT_FIELD_CHARS):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _limit_mapping(value, max_items=24, max_chars=FINAL_TEXT_FIELD_CHARS):
    if not isinstance(value, dict):
        return {}
    result = {}
    for key, item in value.items():
        if len(result) >= max_items:
            break
        key_text = _clip_text(key, 80)
        item_text = _clip_text(item, max_chars)
        if key_text and item_text:
            result[key_text] = item_text
    return result


def _compact_value(value, max_chars=FINAL_TEXT_FIELD_CHARS):
    if isinstance(value, dict):
        return {
            _clip_text(key, 80): _compact_value(item, max_chars)
            for key, item in value.items()
            if item not in (None, "", [], {})
        }
    if isinstance(value, list):
        return [_compact_value(item, max_chars) for item in value if item not in (None, "", [], {})]
    if isinstance(value, str):
        return _clip_text(value, max_chars)
    return value


def _limited_list(value, max_items=6, max_chars=FINAL_TEXT_FIELD_CHARS, prefer_concerns=False):
    if not isinstance(value, list):
        return []
    items = value
    if prefer_concerns:
        concern_words = ("concern", "problem", "risk", "high", "medium", "serious", "vaz", "rizik", "problem")
        items = sorted(
            value,
            key=lambda item: any(word in json.dumps(item, ensure_ascii=False).lower() for word in concern_words),
            reverse=True,
        )
    return [_compact_value(item, max_chars) for item in items[:max_items]]


def _vision_item_text(item):
    if isinstance(item, dict):
        return json.dumps(item, ensure_ascii=False).lower()
    return str(item or "").lower()


def _is_positive_vision_item(item):
    text = _vision_item_text(item)
    positive_markers = (
        "dobrom stave",
        "dobry stav",
        "vyzera v dobrom stave",
        "primerane ciste",
        "bez zjavnych",
        "bez viditelnych",
        "bezne zahyby",
        "konzistentne",
        "consistent",
    )
    return any(marker in text for marker in positive_markers)


def _vision_severity_rank(item):
    if not isinstance(item, dict):
        return 1
    severity = str(item.get("severity") or "").strip().lower()
    return {
        "serious": 4,
        "high": 4,
        "medium": 3,
        "minor": 2,
        "low": 2,
        "unknown": 1,
    }.get(severity, 1)


def _balanced_vision_list(value, max_items=6, max_chars=FINAL_TEXT_FIELD_CHARS):
    if not isinstance(value, list):
        return []
    concerns = []
    positives = []
    neutral = []
    for item in value:
        bucket = positives if _is_positive_vision_item(item) else concerns
        if bucket is concerns and _vision_severity_rank(item) <= 1:
            neutral.append(item)
            continue
        bucket.append(item)

    concerns = sorted(concerns, key=_vision_severity_rank, reverse=True)
    positives = sorted(positives, key=_vision_severity_rank, reverse=True)
    neutral = sorted(neutral, key=_vision_severity_rank, reverse=True)

    selected = []
    concern_count_used = 0
    if concerns:
        concern_quota = max_items - 1 if positives and max_items > 1 else max_items
        selected.extend(concerns[:concern_quota])
        concern_count_used = min(len(concerns), concern_quota)
    remaining = max_items - len(selected)
    if positives and remaining > 0:
        selected.extend(positives[:1])
        remaining = max_items - len(selected)
    if remaining > 0:
        for item in concerns[concern_count_used:]:
            if len(selected) >= max_items:
                break
            if item not in selected:
                selected.append(item)
        remaining = max_items - len(selected)
    if remaining > 0:
        for item in positives[1:] + neutral:
            if len(selected) >= max_items:
                break
            if item not in selected:
                selected.append(item)

    return [_compact_value(item, max_chars) for item in selected[:max_items]]


def _compact_json_for_prompt(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _safe_model_json(value):
    from risk_scorer import parse_model_json

    parsed = parse_model_json(value)
    if parsed:
        return parsed
    return {"raw_preview": _clip_text(value, 1200)}


def _schema_required_fields(schema_name):
    schema_path = os.path.join(SCRIPT_DIR, "schemas", schema_name)
    try:
        with open(schema_path, "r", encoding="utf-8") as schema_file:
            schema = json.load(schema_file)
    except (OSError, json.JSONDecodeError) as exc:
        return [], f"Could not read schema {schema_name}: {exc}"
    required = schema.get("required") if isinstance(schema, dict) else None
    return list(required or []), None


def _soft_validate_json_contract(artifact_name, value, schema_name):
    """Return non-blocking warnings for model/backend JSON artifacts."""
    from risk_scorer import parse_model_json

    warnings = []
    parsed = parse_model_json(value)
    if not parsed:
        warnings.append(
            {
                "artifact": artifact_name,
                "type": "json_parse",
                "message": f"{artifact_name} did not contain a JSON object.",
            }
        )
        return warnings

    if parsed.get("_parse_error"):
        warnings.append(
            {
                "artifact": artifact_name,
                "type": "json_parse",
                "message": f"{artifact_name} could not be parsed as clean JSON.",
            }
        )

    required, schema_warning = _schema_required_fields(schema_name)
    if schema_warning:
        warnings.append(
            {
                "artifact": artifact_name,
                "type": "schema_load",
                "message": schema_warning,
            }
        )
        return warnings

    missing = [field for field in required if field not in parsed]
    if missing:
        warnings.append(
            {
                "artifact": artifact_name,
                "type": "schema_required",
                "message": f"{artifact_name} is missing required fields: {', '.join(missing)}.",
                "fields": missing,
            }
        )

    return warnings


def _normalize_claim_text(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    return re.sub(r"\s+", " ", text)


def _normalize_report_structure_text(value):
    return "\n".join(_normalize_claim_text(line) for line in str(value or "").splitlines())


def _normalize_heading_key(value):
    normalized = _normalize_claim_text(value)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


FORBIDDEN_REPORT_CLAIMS = (
    ("verified_online_vin", r"\bvin\b.{0,50}\b(overeny|verified|confirmed)\b.{0,50}\b(online|internet)"),
    ("confirmed_no_accident", r"\b(nebolo|not)\b.{0,30}\b(havarovane|accident)"),
    ("confirmed_accident", r"\b(bolo|was)\b.{0,30}\b(havarovane|accident)"),
    ("guaranteed_buy", r"\b(garantovana kupa|guaranteed buy|bez rizika|without risk)\b"),
    ("definite_odometer_claim", r"\b(kilometre|odometer|mileage)\b.{0,40}\b(urcite|definitely|sto[cč]ene|rolled back|prave|genuine)\b"),
)

INTERNAL_REPORT_LABELS = (
    ("Dôkaz", "dokaz"),
    ("Istota", "istota"),
    ("Evidence", "evidence"),
    ("Confidence", "confidence"),
)

REQUIRED_REPORT_SECTION_KEYS = (
    "rychle zhrnutie",
    "data z inzeratu",
    "vin a transparentnost",
    "webove overenie",
    "cena a vyjednavanie",
    "ocakavane naklady na najblizsich 30 000 km",
    "analyza fotografii",
    "klady",
    "zapory rizika",
    "otazky pre predajcu a kontrola pri obhliadke",
    "zaverecne odporucanie",
)

REPORT_HEADING_EMOJIS = {
    "rychle zhrnutie": "## 📋 Rýchle zhrnutie",
    "data z inzeratu": "## 🧾 Dáta z inzerátu",
    "vin a transparentnost": "## 🔍 VIN a transparentnosť",
    "webove overenie": "## 🌐 Webové overenie",
    "technicke rizika modelu a komponentov": "## 🔧 Technické riziká modelu a komponentov",
    "cena a vyjednavanie": "## 💰 Cena a vyjednávanie",
    "ocakavane naklady na najblizsich 30 000 km": "## 🛠️ Očakávané náklady na najbližších 30 000 km",
    "analyza fotografii": "## 📸 Analýza fotografií",
    "klady": "## ✅ Klady",
    "zapory rizika": "## ❌ Zápory / riziká",
    "otazky pre predajcu a kontrola pri obhliadke": "## ❓ Otázky pre predajcu a kontrola pri obhliadke",
    "zaverecne odporucanie": "## 🏁 Záverečné odporúčanie",
    "quick summary": "## 📋 Quick Summary",
    "listing data": "## 🧾 Listing Data",
    "vin and transparency": "## 🔍 VIN and Transparency",
    "web verification": "## 🌐 Web Verification",
    "technical risks": "## 🔧 Technical Risks",
    "price and negotiation": "## 💰 Price and Negotiation",
    "expected costs over the next 30 000 km": "## 🛠️ Expected Costs Over the Next 30,000 km",
    "expected costs over the next 30 000 miles": "## 🛠️ Expected Costs Over the Next 30,000 Miles",
    "photo analysis": "## 📸 Photo Analysis",
    "pros": "## ✅ Pros",
    "cons risks": "## ❌ Cons / Risks",
    "questions for the seller and inspection checklist": "## ❓ Questions for the Seller and Inspection Checklist",
    "final recommendation": "## 🏁 Final Recommendation",
}


UNVERIFIED_URL_HOSTS = (
    "vertexaisearch.cloud.google.com",
    "example.com",
    "example.org",
    "example.net",
)

MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def _is_verified_public_url(url):
    text = str(url or "").strip()
    if not text:
        return False
    try:
        parsed = urllib.parse.urlparse(text)
    except Exception:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    host = parsed.netloc.lower()
    return not any(host == blocked or host.endswith(f".{blocked}") for blocked in UNVERIFIED_URL_HOSTS)


def _markdown_links(text):
    return MARKDOWN_LINK_RE.findall(str(text or ""))


def _sanitize_source_item(value):
    item = _compact_value(value)
    if not isinstance(item, dict):
        return item
    url = item.get("source_url") or item.get("url")
    if url and _is_verified_public_url(url):
        item["source_url"] = url
        item["verified_url"] = True
        item.pop("url", None)
    elif "source_url" in item or "url" in item or "verified_url" in item:
        item["source_url"] = ""
        item["verified_url"] = False
        item.pop("url", None)
    return item


def _contains_public_internal_label(normalized_text, normalized_label):
    table_label = rf"(^|\n)\s*\|[^\n]*\b{re.escape(normalized_label)}\b[^\n]*\|"
    bold_label = rf"(^|\n)\s*(?:[-*]\s*)?\*\*\s*{re.escape(normalized_label)}\s*:\s*\*\*"
    heading_label = rf"(^|\n)\s*#{1,6}\s+{re.escape(normalized_label)}\b"
    return any(
        re.search(pattern, normalized_text)
        for pattern in (table_label, bold_label, heading_label)
    )


def _soft_validate_final_report(report_text, backend_verdict):
    """Return non-blocking warnings for the generated buyer report."""
    warnings = []
    text = str(report_text or "")
    if backend_verdict and str(backend_verdict) not in text:
        warnings.append(
            {
                "artifact": "analysis_result.md",
                "type": "verdict_lock",
                "message": "Final report does not contain the backend allowed verdict.",
                "expected_verdict": str(backend_verdict),
            }
        )

    if "<!-- END_ANALYSIS -->" not in text:
        warnings.append(
            {
                "artifact": "analysis_result.md",
                "type": "missing_end_marker",
                "message": "Final report is missing <!-- END_ANALYSIS -->.",
            }
        )

    section_keys = _report_section_keys(text)
    missing_sections = [key for key in REQUIRED_REPORT_SECTION_KEYS if key not in section_keys]
    if missing_sections:
        warnings.append(
            {
                "artifact": "analysis_result.md",
                "type": "missing_required_sections",
                "message": "Final report is missing required sections: " + ", ".join(missing_sections) + ".",
                "sections": missing_sections,
            }
        )

    normalized = _normalize_claim_text(text)
    for claim_id, pattern in FORBIDDEN_REPORT_CLAIMS:
        if re.search(pattern, normalized):
            warnings.append(
                {
                    "artifact": "analysis_result.md",
                    "type": "forbidden_claim",
                    "claim": claim_id,
                    "message": f"Final report may contain an unsupported high-confidence claim: {claim_id}.",
                }
            )

    normalized_structure = _normalize_report_structure_text(text)
    for label, normalized_label in INTERNAL_REPORT_LABELS:
        if _contains_public_internal_label(normalized_structure, normalized_label):
            warnings.append(
                {
                    "artifact": "analysis_result.md",
                    "type": "internal_label",
                    "label": label,
                    "message": f"Final public report contains internal label: {label}.",
                }
            )

    for label, url in _markdown_links(text):
        if not _is_verified_public_url(url):
            warnings.append(
                {
                    "artifact": "analysis_result.md",
                    "type": "unverified_public_link",
                    "label": label,
                    "url": url,
                    "message": "Final public report contains an unverified or placeholder Markdown link.",
                }
            )

    return warnings


def _ensure_end_analysis_marker(report_text):
    text = str(report_text or "").rstrip()
    has_marker = "<!-- END_ANALYSIS -->" in text
    if not has_marker:
        text = text + "\n\n<!-- END_ANALYSIS -->"
    return text


def _write_validation_warnings(slug_dir, warnings):
    if not warnings:
        return None
    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "warnings": warnings,
    }
    path = os.path.join(slug_dir, "validation_warnings.json")
    with open(path, "w", encoding="utf-8") as warnings_file:
        json.dump(payload, warnings_file, indent=2, ensure_ascii=False)
        warnings_file.write("\n")
    for warning in warnings:
        safe_log(f"Validation warning: {warning.get('message', warning)}")
    return path


def _equipment_summary(equipment):
    if not isinstance(equipment, dict):
        return {}
    summary = {}
    for category, items in equipment.items():
        if not isinstance(items, list) or not items:
            continue
        summary[_clip_text(category, 80)] = {
            "count": len(items),
            "examples": [_clip_text(item, 80) for item in items[:6]],
        }
    return summary


def _listing_context_object(car_info_text, description_chars=FINAL_LISTING_DESCRIPTION_CHARS):
    parsed = parse_car_info_md(car_info_text)
    return {
        "title": parsed.get("title"),
        "price": parsed.get("price"),
        "currency": parsed.get("currency"),
        "vin": parsed.get("vin"),
        "source_url": parsed.get("source_url"),
        "scraped_at": parsed.get("scraped_at"),
        "photos_count": parsed.get("photos_count"),
        "location": parsed.get("location"),
        "specs": _limit_mapping(parsed.get("specs"), max_items=28, max_chars=180),
        "seller": _limit_mapping(parsed.get("seller"), max_items=10, max_chars=180),
        "equipment_summary": _equipment_summary(parsed.get("equipment")),
        "description_excerpt": _clip_text(parsed.get("description"), description_chars),
    }


def _listing_context_text(car_info_text, description_chars=MODEL_LISTING_DESCRIPTION_CHARS):
    return _compact_json_for_prompt(
        _listing_context_object(car_info_text, description_chars=description_chars)
    )


def _web_research_evidence(web_research_text, max_chars=FINAL_WEB_RESEARCH_CHARS):
    if not web_research_text:
        return ""
    lines = [line.strip() for line in web_research_text.splitlines() if line.strip()]
    evidence_lines = [
        line
        for line in lines
        if "http://" in line or "https://" in line or "citacia" in line.lower() or "zdroj" in line.lower()
    ]
    if not evidence_lines:
        evidence_lines = lines[-8:]
    return _clip_text("\n".join(evidence_lines[:12]), max_chars)


def _web_research_context(web_research_text, max_chars=FINAL_WEB_RESEARCH_CHARS):
    if not web_research_text:
        return {
            "verified_source_lines": [],
            "unverified_source_notes": [],
            "evidence_excerpt": "",
        }

    verified_lines = []
    unverified_notes = []
    for line in [line.strip() for line in web_research_text.splitlines() if line.strip()]:
        links = _markdown_links(line)
        verified_urls = [url for _label, url in links if _is_verified_public_url(url)]
        if verified_urls:
            verified_lines.append(line)
            continue
        lowered = line.lower()
        if (
            "url citacia nie je overitelna" in lowered
            or "url citácia nie je overiteľná" in lowered
            or any(host in lowered for host in UNVERIFIED_URL_HOSTS)
        ):
            unverified_notes.append(
                re.sub(r"\((https?://[^)\s]+)\)", "(URL nie je priamo overitelna)", line)
            )

    safe_excerpt = "\n".join(verified_lines[:8] + unverified_notes[:6])
    return {
        "verified_source_lines": [_clip_text(line, 260) for line in verified_lines[:8]],
        "unverified_source_notes": [_clip_text(line, 220) for line in unverified_notes[:6]],
        "evidence_excerpt": _clip_text(safe_excerpt, max_chars),
    }


def _source_labels_from_url(url):
    if not _is_verified_public_url(url):
        return set()
    host = urllib.parse.urlparse(str(url).strip()).netloc.lower()
    if not host:
        return set()
    labels = {host}
    if host.startswith("www."):
        labels.add(host[4:])
    return {label.strip() for label in labels if label.strip()}


def _register_verified_source(source_map, label, url):
    if not _is_verified_public_url(url):
        return
    labels = set()
    if label:
        labels.add(str(label).strip().lower())
    labels.update(_source_labels_from_url(url))
    for source_label in labels:
        if source_label and source_label not in source_map:
            source_map[source_label] = str(url).strip()


def _verified_source_map(web_research_text, grok_research_json_text):
    source_map = {}

    for label, url in _markdown_links(web_research_text):
        _register_verified_source(source_map, label, url)

    data = _safe_model_json(grok_research_json_text)
    for key in ("web_research_findings", "technical_risks"):
        for item in data.get(key) or []:
            if not isinstance(item, dict):
                continue
            url = item.get("source_url") or item.get("url")
            label = (
                item.get("source")
                or item.get("source_name")
                or item.get("source_title")
                or item.get("title")
                or item.get("label")
            )
            _register_verified_source(source_map, label, url)

    return source_map


def _relink_web_research_line(line, source_map):
    updated = str(line)
    if _markdown_links(updated):
        return updated

    for label, url in source_map.items():
        pattern = rf"\(({re.escape(label)})\)"
        updated = re.sub(
            pattern,
            lambda match: f"([{match.group(1)}]({url}))",
            updated,
            flags=re.IGNORECASE,
        )
    return updated


def _restore_clickable_web_research_links(report_text, grok_research_json_text, web_research_text):
    source_map = _verified_source_map(web_research_text, grok_research_json_text)
    if not source_map:
        return report_text

    section_match = re.search(
        r"(^##\s+(?:Webov[eé]\s+overenie|Web Verification)\s*$)(.*?)(?=^\s*##\s+|\Z)",
        str(report_text or ""),
        re.MULTILINE | re.DOTALL,
    )
    if not section_match:
        return report_text

    section_body = section_match.group(2)
    relinked_lines = [_relink_web_research_line(line, source_map) for line in section_body.splitlines()]
    relinked_body = "\n".join(relinked_lines)
    if relinked_body == section_body:
        return report_text

    return (
        str(report_text or "")[: section_match.start(2)]
        + relinked_body
        + str(report_text or "")[section_match.end(2) :]
    )


def _normalize_report_headings(report_text):
    normalized_lines = []
    for line in str(report_text or "").splitlines():
        heading_match = re.match(r"^(\s*##\s+)(.+?)\s*$", line)
        bare_heading_match = None
        if not heading_match:
            bare_heading_match = re.match(r"^\s*([📋🧾🔍🌐🔧💰🛠️📸✅❌❓🏁].+?)\s*$", line)
            if not bare_heading_match:
                normalized_lines.append(line)
                continue

        heading_text = heading_match.group(2) if heading_match else bare_heading_match.group(1)
        heading_key = _normalize_heading_key(heading_text)
        normalized_lines.append(REPORT_HEADING_EMOJIS.get(heading_key, line))

    return "\n".join(normalized_lines)


def _report_section_keys(report_text):
    keys = set()
    for line in str(report_text or "").splitlines():
        heading_match = re.match(r"^\s*##\s+(.+?)\s*$", line)
        bare_heading_match = None
        if not heading_match:
            bare_heading_match = re.match(r"^\s*([📋🧾🔍🌐🔧💰🛠️📸✅❌❓🏁].+?)\s*$", line)
        if heading_match or bare_heading_match:
            heading_text = heading_match.group(1) if heading_match else bare_heading_match.group(1)
            keys.add(_normalize_heading_key(heading_text))
    return keys


def _vision_observation_bullet(item):
    if not isinstance(item, dict):
        text = str(item or "").strip()
        return f"- {text}" if text else ""
    label = str(item.get("photo_label") or "").strip()
    observation = str(item.get("observation") or "").strip()
    relevance = str(item.get("buyer_relevance") or "").strip()
    parts = []
    if observation:
        parts.append(observation)
    if relevance:
        parts.append(relevance)
    if not parts:
        return ""
    prefix = f"**{label}:** " if label else ""
    return f"- {prefix}{' '.join(parts)}"


def _high_confidence_dashboard_note(item):
    if not isinstance(item, dict):
        return False
    confidence = _normalize_claim_text(item.get("confidence"))
    if "nizka" in confidence or "low" in confidence:
        return False
    return bool(str(item.get("observation") or "").strip())


def _photo_analysis_lines_from_vision(vision_result_json, output_language="sk"):
    data = _safe_model_json(vision_result_json)
    if not data.get("photos_provided"):
        return [
            "- Fotografie neboli poskytnuté alebo neboli spoľahlivo analyzovateľné."
            if _demo_output_language(output_language) == "sk"
            else "- Photos were not provided or could not be analyzed reliably."
        ]

    lines = []
    for item in data.get("exterior_observations") or []:
        bullet = _vision_observation_bullet(item)
        if bullet:
            lines.append(bullet)
    for item in data.get("interior_observations") or []:
        bullet = _vision_observation_bullet(item)
        if bullet:
            lines.append(bullet)
    for item in data.get("visible_red_flags") or []:
        bullet = _vision_observation_bullet(item)
        if bullet:
            lines.append(bullet)
    for item in data.get("dashboard_or_warning_lights") or []:
        if _high_confidence_dashboard_note(item):
            bullet = _vision_observation_bullet(item)
            if bullet:
                lines.append(bullet)

    view_coverage = data.get("view_coverage") if isinstance(data.get("view_coverage"), dict) else {}
    missing_views = []
    for key, label_sk, label_en in (
        ("engine_bay", "motorový priestor", "engine bay"),
        ("underbody", "podvozok", "underbody"),
    ):
        if str(view_coverage.get(key) or "").strip().lower() == "missing":
            missing_views.append(label_sk if _demo_output_language(output_language) == "sk" else label_en)
    if missing_views:
        if _demo_output_language(output_language) == "sk":
            lines.append(
                "- **Chýbajúce pohľady:** "
                + ", ".join(missing_views)
                + " nie sú na fotkách viditeľné, preto ich stav nemožno spoľahlivo posúdiť."
            )
        else:
            lines.append(
                "- **Missing views:** "
                + ", ".join(missing_views)
                + " are not visible in the photos, so their condition cannot be assessed reliably."
            )

    return lines


def _replace_photo_analysis_section(report_text, vision_result_json, output_language="sk"):
    body_lines = _photo_analysis_lines_from_vision(vision_result_json, output_language)
    if not body_lines:
        return report_text
    heading = "## 📸 Analýza fotografií" if _demo_output_language(output_language) == "sk" else "## 📸 Photo Analysis"
    new_section = heading + "\n\n" + "\n".join(body_lines) + "\n\n"
    next_section = (
        r"^\s*(?:##\s+|✅\s+|❌\s+|❓\s+|🏁\s+|💰\s+|🛠️\s+|🌐\s+|🔧\s+|📋\s+|🧾\s+|🔍\s+)"
        r"|^\s*<!--\s*END_ANALYSIS\s*-->"
    )
    pattern = re.compile(
        r"(^##\s+[^\n]*(?:Analýza fotografií|Photo Analysis)[^\n]*\n)(.*?)(?="
        + next_section
        + r"|\Z)",
        flags=re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    if pattern.search(str(report_text or "")):
        return pattern.sub(new_section, str(report_text or ""), count=1)
    end_marker = "<!-- END_ANALYSIS -->"
    if end_marker in str(report_text or ""):
        return str(report_text or "").replace(end_marker, new_section + end_marker)
    return str(report_text or "").rstrip() + "\n\n" + new_section


def _compact_text_research_for_final(grok_research_json_text):
    data = _safe_model_json(grok_research_json_text)
    return {
        "listing_facts": _compact_value(data.get("listing_facts")),
        "missing_or_uncertain_data": _limited_list(data.get("missing_or_uncertain_data"), 6, prefer_concerns=True),
        "consistency_checks": _limited_list(data.get("consistency_checks"), 6, prefer_concerns=True),
        "vin_check": _compact_value(data.get("vin_check")),
        "knowledge_base_findings": _limited_list(data.get("knowledge_base_findings"), 6, prefer_concerns=True),
        "web_research_findings": [
            _sanitize_source_item(item)
            for item in _limited_list(data.get("web_research_findings"), 8, prefer_concerns=True)
        ],
        "technical_risks": [
            _sanitize_source_item(item)
            for item in _limited_list(data.get("technical_risks"), 6, prefer_concerns=True)
        ],
        "market_assessment": _compact_value(data.get("market_assessment")),
        "expected_costs": _limited_list(data.get("expected_costs"), 6, prefer_concerns=True),
        "text_research_risk_flags": _limited_list(data.get("text_research_risk_flags"), 8, prefer_concerns=True),
        "parse_error": data.get("_parse_error", False),
        "raw_preview": data.get("_raw_preview"),
    }


def _compact_vision_for_final(vision_result_json):
    data = _safe_model_json(vision_result_json)
    return {
        "photos_provided": data.get("photos_provided"),
        "photo_coverage": _compact_value(data.get("photo_coverage")),
        "view_coverage": _compact_value(data.get("view_coverage")),
        "photo_limitations": _limited_list(data.get("photo_limitations"), 5, 220),
        "exterior_observations": _balanced_vision_list(data.get("exterior_observations"), 8),
        "interior_observations": _balanced_vision_list(data.get("interior_observations"), 6),
        "dashboard_or_warning_lights": _balanced_vision_list(data.get("dashboard_or_warning_lights"), 4),
        "visible_red_flags": _limited_list(data.get("visible_red_flags"), 6, prefer_concerns=True),
        "mileage_wear_consistency": _compact_value(data.get("mileage_wear_consistency")),
        "visual_verdict": _clip_text(data.get("visual_verdict"), 220),
        "parse_error": data.get("_parse_error", False),
        "raw_preview": data.get("_raw_preview"),
    }


def _compact_risk_score_for_final(risk_score_json):
    data = _safe_model_json(risk_score_json)
    return {
        "risk_score": data.get("risk_score"),
        "allowed_final_verdict": data.get("allowed_final_verdict"),
        "applied_rules": _limited_list(data.get("applied_rules"), 10, 260, prefer_concerns=True),
        "override_rules_applied": _limited_list(data.get("override_rules_applied"), 6, 260, prefer_concerns=True),
        "missing_data_flags": _limited_list(data.get("missing_data_flags"), 12, 80),
        "buyer_priority_checks": _limited_list(data.get("buyer_priority_checks"), 8, 220, prefer_concerns=True),
    }


def _build_final_synthesis_context(
    output_language,
    car_info_text,
    grok_research_json_text,
    vision_result_json,
    risk_score_json,
    web_research_text,
    image_meta=None,
):
    compact_payload = {
        "output_language": _demo_output_language(output_language),
        "listing": _listing_context_object(car_info_text),
        "text_research": _compact_text_research_for_final(grok_research_json_text),
        "vision": _compact_vision_for_final(vision_result_json),
        "image_payload": _compact_value(image_meta or {}),
        "backend_risk_score": _compact_risk_score_for_final(risk_score_json),
        "web_research": _web_research_context(web_research_text),
    }
    return (
        "Use only this compact structured context. "
        "Do not infer missing details from omitted raw text. "
        "If image_payload.full_gallery_included is true, do not describe a view as missing from the listing "
        "unless vision.view_coverage says that view is absent from the full-gallery overview.\n\n"
        + _compact_json_for_prompt(compact_payload)
    )


def _no_photos_vision_result(message="Fotografie neboli poskytnute."):
    return json.dumps(
        {
            "source_role": "vision",
            "photos_provided": False,
            "photo_coverage": {
                "coverage_mode": "none",
                "original_count": 0,
                "analyzed_count": 0,
                "full_gallery_overview": False,
                "notes": [message],
            },
            "view_coverage": {
                "exterior": "unknown",
                "interior": "unknown",
                "dashboard": "unknown",
                "engine_bay": "unknown",
                "tires": "unknown",
                "underbody": "unknown",
            },
            "photo_limitations": [message],
            "exterior_observations": [],
            "interior_observations": [],
            "dashboard_or_warning_lights": [],
            "visible_red_flags": [],
            "mileage_wear_consistency": {
                "assessment": "cannot_assess",
                "explanation": message,
                "confidence": "Nizka",
            },
            "visual_verdict": "Nedostatocne fotografie",
            "must_not_infer": [
                "accident history",
                "service history",
                "hidden defects",
                "odometer fraud",
                "market price",
                "overall buying verdict",
            ],
        },
        ensure_ascii=False,
    )


def _stream_text_model(provider, api_key, system_prompt, user_content, listing_slug=None):
    if provider == "grok":
        yield from analyze_with_grok(api_key, system_prompt, user_content, listing_slug=listing_slug)
        return
    if provider == "openrouter":
        yield from analyze_with_openrouter(api_key, system_prompt, user_content, listing_slug=listing_slug)
        return

    from llm_client import _call_gemini
    yield from _call_gemini(api_key, system_prompt, user_content, image_data_list=None, listing_slug=listing_slug)


def _model_display_name(provider):
    if provider == "grok":
        return "Grok"
    if provider == "openrouter":
        return "OpenRouter"
    return "Gemini"


def _gemini_key_entries(gemini_keys):
    """Normalize one or more Gemini keys into labeled, de-duplicated entries."""
    if isinstance(gemini_keys, str):
        raw_keys = [gemini_keys]
    else:
        raw_keys = list(gemini_keys or [])

    entries = []
    seen = set()
    for key in raw_keys:
        key = (key or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        index = len(entries)
        label = "primary" if index == 0 else "backup" if index == 1 else f"backup {index}"
        entries.append({"key": key, "label": label})
    return entries


def _gemini_retry_status(failed_entry, next_entry, phase_name, exc):
    safe_log(
        f"Gemini {failed_entry['label']} key failed during {phase_name}: {exc}. "
        f"Trying {next_entry['label']} key."
    )
    return (
        f"Gemini {failed_entry['label']} key failed during {phase_name}. "
        f"Trying {next_entry['label']} Gemini key..."
    )


def _collect_gemini_with_key_fallback(
    key_entries,
    phase_name,
    stream_factory,
    retry_exceptions=(ApiKeyError, RateLimitError),
    same_key_retries=0,
    same_key_retry_exceptions=None,
):
    """Collect a non-user-visible Gemini stream with optional same-key retry and key fallback."""
    if same_key_retry_exceptions is None:
        same_key_retry_exceptions = retry_exceptions

    last_exc = None
    for index, entry in enumerate(key_entries):
        for attempt in range(same_key_retries + 1):
            chunks = []
            try:
                for chunk in stream_factory(entry["key"]):
                    chunks.append(chunk)
                return "".join(chunks), entry
            except retry_exceptions as exc:
                if chunks:
                    raise
                last_exc = exc
                if attempt < same_key_retries and isinstance(exc, same_key_retry_exceptions):
                    safe_log(
                        f"Gemini {entry['label']} key failed during {phase_name}: {exc}. "
                        "Retrying same key."
                    )
                    status = (
                        f"Gemini {entry['label']} key failed during {phase_name}. "
                        "Retrying the same Gemini key..."
                    )
                    yield f"data: {json.dumps({'status': status})}\n\n"
                    time.sleep(1)
                    continue
                if index >= len(key_entries) - 1:
                    raise
                status = _gemini_retry_status(entry, key_entries[index + 1], phase_name, exc)
                yield f"data: {json.dumps({'status': status})}\n\n"
                break

    if last_exc:
        raise last_exc
    return "", None


def _inject_photo_vin_into_pipeline(slug_dir, car_info_text, grok_research_json_text, vision_result_json, car_info_path):
    """
    If the vision model found a visible_vin in photos but the listing text has no VIN,
    inject it into the pipeline by:
    1. Updating grok_research_json_text (in-memory variable — caller must handle assignment)
    2. Updating car_info.md on disk
    3. Re-running VIN decoding
    Returns a status note string, or None if no injection was needed.
    """
    from risk_scorer import parse_model_json
    vision_data = parse_model_json(vision_result_json)
    if not vision_data:
        return None

    visible_vin = str(vision_data.get("visible_vin") or "").strip().upper()
    if not visible_vin:
        return None

    # Validate VIN format roughly (17 chars, no I/O/Q)
    import re
    vin_pattern = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
    if not vin_pattern.match(visible_vin):
        safe_log(f"Photo VIN '{visible_vin}' does not match expected format; skipping injection.")
        return None

    # Check if listing text already has this VIN
    if visible_vin in car_info_text.upper():
        safe_log(f"Photo VIN '{visible_vin}' already present in listing text; no injection needed.")
        return None

    # Check if listing already has *any* VIN
    existing_vin_match = re.search(r"\b[A-HJ-NPR-Z0-9]{17}\b", car_info_text.upper())
    if existing_vin_match:
        safe_log(f"Listing already has VIN '{existing_vin_match.group(0)}'; not overwriting with photo VIN '{visible_vin}'.")
        return None

    # 1. Update car_info.md on disk — inject VIN line into Specifications section
    updated_lines = []
    vin_injected = False
    in_specs = False
    for line in car_info_text.splitlines():
        if line.strip().lower().startswith("## specifications") or line.strip().lower().startswith("## parameters"):
            in_specs = True
            updated_lines.append(line)
            continue
        if in_specs and line.startswith("## "):
            # We reached the next section without finding a VIN line
            if not vin_injected:
                updated_lines.append(f"- **VIN:** {visible_vin}")
                vin_injected = True
            in_specs = False
        if in_specs and "vin" in line.lower() and "**" in line:
            # VIN line already present (should not happen given earlier checks)
            vin_injected = True
        updated_lines.append(line)
    if in_specs and not vin_injected:
        updated_lines.append(f"- **VIN:** {visible_vin}")
        vin_injected = True

    try:
        updated_car_info = "\n".join(updated_lines)
        with open(car_info_path, "w", encoding="utf-8") as f:
            f.write(updated_car_info)
    except IOError as exc:
        safe_log(f"Could not update car_info.md with photo VIN: {exc}")
        return None

    # 2. Re-run VIN decoding
    try:
        from main import _run_vin_decoding
        _run_vin_decoding(slug_dir)
    except Exception as exc:
        safe_log(f"VIN decoding after photo injection warning: {exc}")

    output_language_sk = "sk"  # Status text in Slovak is fine
    note = (
        f"✅ VIN {visible_vin} nájdený na fotkách a pridaný do pipeline."
        if output_language_sk == "sk"
        else f"✅ VIN {visible_vin} found in photos and injected into pipeline."
    )
    safe_log(note)
    return note


def _multi_model_analysis_events(slug, grok_key, gemini_keys, output_language="sk", openrouter_key=""):
    """Run separated text/research, Gemini vision, scoring, and final synthesis."""
    slug_dir = _safe_slug_dir(slug)
    if not os.path.isdir(slug_dir):
        yield f"data: {json.dumps({'error': 'Listing job not found.'})}\n\n"
        return

    car_info_path = os.path.join(slug_dir, "car_info.md")
    if not os.path.exists(car_info_path):
        yield f"data: {json.dumps({'error': 'car_info.md not found.'})}\n\n"
        return

    from llm_client import GEMINI_FLASH_LITE_MODEL, GEMINI_FLASH_MODEL, _call_gemini, run_grounded_web_research
    gemini_key_entries = _gemini_key_entries(gemini_keys)
    if not gemini_key_entries:
        yield f"data: {json.dumps({'error': 'Gemini API keys are not configured on the server.'})}\n\n"
        return

    with open(car_info_path, "r", encoding="utf-8") as f:
        car_info_text = f.read()
    model_listing_context = _listing_context_text(car_info_text)
    grounding_listing_context = _listing_context_text(car_info_text, description_chars=700)
    validation_warnings = []

    web_research_text = ""
    try:
        yield f"data: {json.dumps({'status': 'Preparing web research via Gemini Google Search...'})}\n\n"
        grounded, _grounding_key = yield from _collect_gemini_with_key_fallback(
            gemini_key_entries,
            "web research",
            lambda key: [
                run_grounded_web_research(
                    key,
                    grounding_listing_context,
                    model=GEMINI_FLASH_LITE_MODEL,
                    listing_slug=slug,
                )
            ],
            retry_exceptions=(ApiKeyError, RateLimitError, GroundingTransientError),
            same_key_retries=1,
            same_key_retry_exceptions=(GroundingTransientError,),
        )
        if grounded:
            web_research_text = grounded
            with open(os.path.join(slug_dir, "web_research.md"), "w", encoding="utf-8") as f:
                f.write(grounded)
            yield f"data: {json.dumps({'status': 'Web research ready for text/research analysis.'})}\n\n"
    except Exception as exc:
        safe_log(f"Web research warning: {exc}")
        yield f"data: {json.dumps({'status': 'Web research unavailable; continuing with listing data.'})}\n\n"

    if grok_key:
        text_provider = "grok"
        text_api_key = grok_key
    elif openrouter_key:
        text_provider = "openrouter"
        text_api_key = openrouter_key
    else:
        text_provider = "gemini"
        text_api_key = ""
    text_model_name = _model_display_name(text_provider)

    yield f"data: {json.dumps({'status': f'Phase 1/4: {text_model_name} text and research analysis...'})}\n\n"
    grok_text_prompt_path = os.path.join(SCRIPT_DIR, "prompts", "grok_text_research_system.md")
    if not os.path.exists(grok_text_prompt_path):
        yield f"data: {json.dumps({'error': 'grok_text_research_system.md not found.'})}\n\n"
        return
    with open(grok_text_prompt_path, "r", encoding="utf-8") as f:
        grok_text_system_prompt = f.read()

    grok_research_json_text = ""
    grok_text_content = _build_text_research_context(model_listing_context, output_language, web_research_text)
    input_tokens = estimate_request_tokens(grok_text_system_prompt, grok_text_content)
    yield f"data: {json.dumps({'token_usage': {'input_tokens': input_tokens, 'output_tokens': 0}})}\n\n"
    if text_provider == "gemini":
        grok_research_json_text, _text_key = yield from _collect_gemini_with_key_fallback(
            gemini_key_entries,
            "text/research analysis",
            lambda key: _call_gemini(
                key,
                grok_text_system_prompt,
                grok_text_content,
                image_data_list=None,
                model=GEMINI_FLASH_LITE_MODEL,
                listing_slug=slug,
            ),
        )
    else:
        try:
            for chunk in _stream_text_model(text_provider, text_api_key, grok_text_system_prompt, grok_text_content, listing_slug=slug):
                grok_research_json_text += chunk
        except (RateLimitError, ConnectionError) as exc:
            if text_provider != "openrouter":
                raise
            safe_log(f"OpenRouter text/research failed; falling back to Gemini: {exc}")
            yield f"data: {json.dumps({'status': 'OpenRouter text/research unavailable; falling back to Gemini.'})}\n\n"
            text_provider = "gemini"
            text_api_key = ""
            text_model_name = _model_display_name(text_provider)
            grok_research_json_text, _text_key = yield from _collect_gemini_with_key_fallback(
                gemini_key_entries,
                "text/research analysis",
                lambda key: _call_gemini(
                    key,
                    grok_text_system_prompt,
                    grok_text_content,
                    image_data_list=None,
                    model=GEMINI_FLASH_LITE_MODEL,
                    listing_slug=slug,
                ),
            )
    with open(os.path.join(slug_dir, "grok_research.json"), "w", encoding="utf-8") as f:
        f.write(grok_research_json_text)
    validation_warnings.extend(
        _soft_validate_json_contract(
            "grok_research.json",
            grok_research_json_text,
            "grok_research.schema.json",
        )
    )
    yield f"data: {json.dumps({'status': f'{text_model_name} text/research JSON saved.'})}\n\n"

    yield f"data: {json.dumps({'status': 'Phase 2/4: Gemini vision analysis...'})}\n\n"
    vision_prompt_path = os.path.join(SCRIPT_DIR, "prompts", "gemini_vision_system.md")
    if not os.path.exists(vision_prompt_path):
        yield f"data: {json.dumps({'error': 'gemini_vision_system.md not found.'})}\n\n"
        return
    with open(vision_prompt_path, "r", encoding="utf-8") as f:
        vision_system_prompt = f.read()

    vision_result_json = ""
    image_data_list, _image_meta = prepare_llm_images(slug_dir)
    if image_data_list:
        image_payload_context = _compact_json_for_prompt(_image_meta)
        vision_language = "Slovak" if _demo_output_language(output_language) == "sk" else "English"
        vision_content = (
            "Analyze only the attached vehicle photos/collages. "
            f"Write all human-readable JSON string values in {vision_language}. "
            "Use listing text only for labels and mileage context.\n"
            "Image payload metadata follows. If full_gallery_included is true, overview sheets cover the full listing gallery; "
            "do not mark a buyer-relevant view as missing from the listing unless it is absent from those overview sheets. "
            "Use 'not assessable in detail' for views visible only in overview thumbnails.\n\n"
            f"IMAGE_PAYLOAD_METADATA:\n{image_payload_context}\n\n"
            f"{model_listing_context}"
        )
        try:
            vision_result_json, _vision_key = yield from _collect_gemini_with_key_fallback(
                gemini_key_entries,
                "vision analysis",
                lambda key: _call_gemini(
                    key,
                    vision_system_prompt,
                    vision_content,
                    image_data_list=image_data_list,
                    model=GEMINI_FLASH_MODEL,
                    listing_slug=slug,
                    allow_image_text_fallback=False,
                ),
            )
        except Exception as exc:
            safe_log(f"Gemini vision error: {exc}")
            vision_result_json = _no_photos_vision_result("Fotografie sa nepodarilo spolahlivo analyzovat.")
            yield f"data: {json.dumps({'status': 'Gemini vision failed; continuing without reliable photo analysis.'})}\n\n"
    else:
        vision_result_json = _no_photos_vision_result()
        yield f"data: {json.dumps({'status': 'No photos available for Gemini vision.'})}\n\n"

    with open(os.path.join(slug_dir, "gemini_vision.json"), "w", encoding="utf-8") as f:
        f.write(vision_result_json)
    validation_warnings.extend(
        _soft_validate_json_contract(
            "gemini_vision.json",
            vision_result_json,
            "gemini_vision.schema.json",
        )
    )
    yield f"data: {json.dumps({'status': 'Gemini vision JSON saved.'})}\n\n"

    # ── Inject VIN from photos into pipeline if found in vision but missing from listing ──
    injected_vin_note = _inject_photo_vin_into_pipeline(
        slug_dir, car_info_text, grok_research_json_text, vision_result_json,
        car_info_path
    )
    if injected_vin_note:
        # Reload car_info_text for downstream stages
        with open(car_info_path, "r", encoding="utf-8") as f:
            car_info_text = f.read()
        # Also patch grok_research_json_text so risk scoring and final synthesis see the VIN
        grok_data = _safe_model_json(grok_research_json_text)
        vision_parsed_for_vin = _safe_model_json(vision_result_json)
        photo_vin = str(vision_parsed_for_vin.get("visible_vin") or "").strip().upper()
        if not grok_data.get("_parse_error") and photo_vin:
            if "vin_check" not in grok_data or not isinstance(grok_data.get("vin_check"), dict):
                grok_data["vin_check"] = {}
            try:
                from vin_utils import validate_vin
                decoded = validate_vin(photo_vin)
            except Exception:
                decoded = {}
            grok_data["vin_check"]["vin_present"] = True
            grok_data["vin_check"]["format_check"] = "ok" if decoded.get("valid") else "problem"
            grok_data["vin_check"]["decoded_information"] = decoded.get("validation_message", "")
            grok_data["vin_check"]["online_history"] = "requires_manual_verification"
            grok_data["vin_check"]["notes"] = "VIN was not in listing text; found in photos by Gemini vision."
            if "listing_facts" not in grok_data or not isinstance(grok_data.get("listing_facts"), dict):
                grok_data["listing_facts"] = {}
            grok_data["listing_facts"]["vin"] = photo_vin
            grok_research_json_text = _compact_json_for_prompt(grok_data)
        yield f"data: {json.dumps({'status': injected_vin_note})}\n\n"

    yield f"data: {json.dumps({'status': 'Phase 3/4: Backend deterministic risk scoring...'})}\n\n"
    from risk_scorer import calculate_risk_score
    risk_score = calculate_risk_score(
        grok_research_json_text,
        vision_result_json,
        listing_text=car_info_text,
    )
    risk_score_json = json.dumps(risk_score, indent=2, ensure_ascii=False)
    with open(os.path.join(slug_dir, "risk_score.json"), "w", encoding="utf-8") as f:
        f.write(risk_score_json)
    validation_warnings.extend(
        _soft_validate_json_contract(
            "risk_score.json",
            risk_score_json,
            "risk_score.schema.json",
        )
    )
    verdict = risk_score.get("allowed_final_verdict", "unknown")
    yield f"data: {json.dumps({'status': f'Backend risk score saved: {verdict}'})}\n\n"

    yield f"data: {json.dumps({'status': f'Phase 4/4: {text_model_name} final synthesis...'})}\n\n"
    final_synthesis_prompt_path = os.path.join(SCRIPT_DIR, "prompts", "grok_final_synthesis_system.md")
    if not os.path.exists(final_synthesis_prompt_path):
        yield f"data: {json.dumps({'error': 'grok_final_synthesis_system.md not found.'})}\n\n"
        return
    with open(final_synthesis_prompt_path, "r", encoding="utf-8") as f:
        final_system_prompt = f.read()

    final_content = _build_final_synthesis_context(
        output_language,
        car_info_text,
        grok_research_json_text,
        vision_result_json,
        risk_score_json,
        web_research_text,
        _image_meta,
    )

    full_report = ""
    output_tokens = 0
    next_token_update = 250
    final_input_tokens = estimate_request_tokens(final_system_prompt, final_content)
    yield f"data: {json.dumps({'token_usage': {'input_tokens': final_input_tokens, 'output_tokens': output_tokens}})}\n\n"
    if text_provider == "gemini":
        final_done = False
        for index, entry in enumerate(gemini_key_entries):
            attempt_text = ""
            attempt_output_tokens = 0
            try:
                for chunk in _call_gemini(
                    entry["key"],
                    final_system_prompt,
                    final_content,
                    image_data_list=None,
                    model=GEMINI_FLASH_MODEL,
                    listing_slug=slug,
                ):
                    attempt_text += chunk
                    attempt_output_tokens += estimate_output_tokens(chunk)
                    if chunk:
                        yield f"data: {json.dumps({'text': chunk})}\n\n"
                    if attempt_output_tokens >= next_token_update:
                        yield f"data: {json.dumps({'token_usage': {'input_tokens': final_input_tokens, 'output_tokens': attempt_output_tokens}})}\n\n"
                        next_token_update += 250
                full_report = attempt_text
                output_tokens = attempt_output_tokens
                final_done = True
                break
            except (ApiKeyError, RateLimitError) as exc:
                if attempt_text or index >= len(gemini_key_entries) - 1:
                    raise
                status = _gemini_retry_status(entry, gemini_key_entries[index + 1], "final synthesis", exc)
                yield f"data: {json.dumps({'status': status})}\n\n"

        if not final_done:
            raise RateLimitError("Gemini final synthesis failed for all configured API keys.")
    else:
        try:
            for chunk in _stream_text_model(text_provider, text_api_key, final_system_prompt, final_content, listing_slug=slug):
                full_report += chunk
                output_tokens += estimate_output_tokens(chunk)
                if chunk:
                    yield f"data: {json.dumps({'text': chunk})}\n\n"
                if output_tokens >= next_token_update:
                    yield f"data: {json.dumps({'token_usage': {'input_tokens': final_input_tokens, 'output_tokens': output_tokens}})}\n\n"
                    next_token_update += 250
        except (RateLimitError, ConnectionError) as exc:
            if text_provider != "openrouter" or full_report:
                raise
            safe_log(f"OpenRouter final synthesis failed; falling back to Gemini: {exc}")
            yield f"data: {json.dumps({'status': 'OpenRouter final synthesis unavailable; falling back to Gemini.'})}\n\n"
            final_done = False
            for index, entry in enumerate(gemini_key_entries):
                attempt_text = ""
                attempt_output_tokens = 0
                try:
                    for chunk in _call_gemini(
                        entry["key"],
                        final_system_prompt,
                        final_content,
                        image_data_list=None,
                        model=GEMINI_FLASH_MODEL,
                        listing_slug=slug,
                    ):
                        attempt_text += chunk
                        attempt_output_tokens += estimate_output_tokens(chunk)
                        if chunk:
                            yield f"data: {json.dumps({'text': chunk})}\n\n"
                        if attempt_output_tokens >= next_token_update:
                            yield f"data: {json.dumps({'token_usage': {'input_tokens': final_input_tokens, 'output_tokens': attempt_output_tokens}})}\n\n"
                            next_token_update += 250
                    full_report = attempt_text
                    output_tokens = attempt_output_tokens
                    final_done = True
                    break
                except (ApiKeyError, RateLimitError) as gemini_exc:
                    if attempt_text or index >= len(gemini_key_entries) - 1:
                        raise
                    status = _gemini_retry_status(entry, gemini_key_entries[index + 1], "final synthesis", gemini_exc)
                    yield f"data: {json.dumps({'status': status})}\n\n"

            if not final_done:
                raise RateLimitError("Gemini final synthesis failed for all configured API keys.")

    with open(os.path.join(slug_dir, "analysis_result_raw.md"), "w", encoding="utf-8") as f:
        f.write(full_report)
    public_text = _normalize_report_headings(
        _restore_clickable_web_research_links(
            _ensure_end_analysis_marker(_public_analysis_markdown(_strip_kb_section(full_report), slug_dir)),
            grok_research_json_text,
            web_research_text,
        )
    )
    public_text = _replace_photo_analysis_section(public_text, vision_result_json, output_language)
    with open(os.path.join(slug_dir, "analysis_result.md"), "w", encoding="utf-8") as f:
        f.write(public_text)
    validation_warnings.extend(_soft_validate_final_report(public_text, verdict))
    warnings_path = _write_validation_warnings(slug_dir, validation_warnings)
    if warnings_path:
        yield f"data: {json.dumps({'status': f'Analysis completed with {len(validation_warnings)} validation warning(s).'})}\n\n"

    kb_blocks = extract_kb_save_blocks(full_report)
    saved_kb = []
    if kb_blocks:
        try:
            saved_kb = _save_kb_blocks(kb_blocks)
            if saved_kb:
                with open(os.path.join(slug_dir, "kb_autosave.json"), "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "saved_at": datetime.now().isoformat(timespec="seconds"),
                            "saved": saved_kb,
                        },
                        f,
                        indent=2,
                        ensure_ascii=False,
                    )
        except Exception as exc:
            safe_log(f"KB autosave error: {exc}")

    yield f"data: {json.dumps({'done': True, 'slug': slug, 'has_kb_blocks': len(kb_blocks) > 0, 'saved_kb': saved_kb})}\n\n"


def _demo_analysis_events(slug, output_language="sk"):
    grok_key = _demo_grok_api_key()
    openrouter_key = _demo_openrouter_api_key()
    keys = _demo_api_keys()
    if not keys:
        yield f"data: {json.dumps({'error': 'Gemini API keys are not configured on the server.'})}\n\n"
        yield "data: [DONE]\n\n"
        return

    if grok_key:
        text_provider = "Grok"
    elif openrouter_key:
        text_provider = "OpenRouter"
    else:
        text_provider = "Gemini"
    backup_status = " Backup Gemini retry is enabled." if len(keys) > 1 else ""
    yield f"data: {json.dumps({'status': f'Using {text_provider} for text/final synthesis and Gemini for vision.{backup_status}'})}\n\n"
    try:
        yield from _multi_model_analysis_events(slug, grok_key, keys, output_language, openrouter_key=openrouter_key)
    except (ApiKeyError, GrokApiKeyError, OpenRouterApiKeyError, RateLimitError) as exc:
        yield f"data: {json.dumps({'error': str(exc)})}\n\n"
    except Exception as exc:
        safe_log(f"Demo multi-model analysis error: {exc}")
        yield f"data: {json.dumps({'error': f'Analysis failed: {str(exc)}'})}\n\n"

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
            _set_current_progress(status="Starting analysis...", line="Starting analysis...", done=False, reset=True)
            _cleanup_old_demo_jobs()
            for event_text in generator_factory():
                _track_demo_sse_progress(event_text)
                yield event_text
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
        process = None
        try:
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
        except Exception as e:
            yield f"data: {json.dumps({'error': f'Failed to start scraper: {str(e)}'})}\n\n"
            yield "data: [DONE]\n\n"
            return
        try:
            for line in process.stdout:
                line = line.strip()
                if line:
                    yield f"data: {json.dumps({'log': line})}\n\n"
            process.wait(timeout=150)
        except subprocess.TimeoutExpired:
            if process:
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
        # Validate that scraped content looks like a car listing before spending tokens
        slug_dir = _safe_slug_dir(slug)
        car_info_path = os.path.join(slug_dir, "car_info.md")
        car_info_text_validation = _read_text_file(car_info_path)
        is_car, reject_reason = _is_car_listing(car_info_text_validation)
        if not is_car:
            yield f"data: {json.dumps({'error': reject_reason})}\n\n"
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
            _set_current_progress(status="Starting manual analysis...", line="Starting manual analysis...", done=False, reset=True)
            first_event = f"data: {json.dumps({'status': f'Manual listing ready with {photos_count} photos.', 'slug': slug})}\n\n"
            _track_demo_sse_progress(first_event)
            yield first_event
            for event_text in _demo_analysis_events(slug, output_language):
                _track_demo_sse_progress(event_text)
                yield event_text
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
    Run AI analysis on a listing using the separated pipeline:
      1. Text + research, Grok if available, otherwise Gemini
      2. Gemini vision
      3. Backend deterministic scoring
      4. Final synthesis, same text provider as step 1

    Optional JSON body: {"grok_api_key": "...", "openrouter_api_key": "...", "gemini_api_key": "..."}
    Returns SSE stream of progress and final report.
    """
    data = request.get_json(silent=True) or {}
    demo_gemini_keys = _demo_api_keys()
    grok_key = (data.get("grok_api_key") or _demo_grok_api_key()).strip()
    openrouter_key = (data.get("openrouter_api_key") or _demo_openrouter_api_key()).strip()
    provided_gemini_key = (data.get("gemini_api_key") or "").strip()
    gemini_keys = _gemini_key_entries(
        ([provided_gemini_key] if provided_gemini_key else []) + demo_gemini_keys
    )

    if not gemini_keys:
        return jsonify({"error": "Chýba Gemini API kľúč (GEMINI_API_KEY)."}), 400
    gemini_key = gemini_keys[0]["key"]

    slug_dir = os.path.join(AUTA_DIR, slug)
    if not os.path.isdir(slug_dir):
        return jsonify({"error": "Inzerát nenájdený"}), 404

    def generate():
        try:
            yield from _multi_model_analysis_events(
                slug,
                grok_key,
                [entry["key"] for entry in gemini_keys],
                openrouter_key=openrouter_key,
            )
            return

            from llm_client import _call_gemini, _call_grok, run_grounded_web_research

            # Read listing data
            car_info_path = os.path.join(slug_dir, "car_info.md")
            if not os.path.exists(car_info_path):
                yield f"data: {json.dumps({'error': 'car_info.md not found.'})}\n\n"
                return
            with open(car_info_path, "r", encoding="utf-8") as f:
                car_info_text = f.read()

            # ── Stage 1: Grok Text + Research ──
            yield f"data: {json.dumps({'status': '📝 Fáza 1/3: Textová analýza cez Grok...'})}\n\n"

            # Read the Grok text research prompt
            grok_text_prompt_path = os.path.join(SCRIPT_DIR, "prompts", "grok_text_research_system.md")
            if not os.path.exists(grok_text_prompt_path):
                yield f"data: {json.dumps({'error': 'grok_text_research_system.md not found.'})}\n\n"
                return
            with open(grok_text_prompt_path, "r", encoding="utf-8") as f:
                grok_text_system_prompt = f.read()

            grok_text_content = f"Listing data:\n\n{car_info_text}"

            grok_research_json_text = ""
            try:
                for chunk in _call_grok(grok_key, grok_text_system_prompt, grok_text_content, listing_slug=slug):
                    grok_research_json_text += chunk
                # Save intermediate JSON
                grok_json_path = os.path.join(slug_dir, "grok_research.json")
                with open(grok_json_path, "w", encoding="utf-8") as f:
                    f.write(grok_research_json_text)
                yield f"data: {json.dumps({'status': '✅ Textová analýza hotová.'})}\n\n"
            except (GrokApiKeyError, ApiKeyError) as e:
                yield f"data: {json.dumps({'error': f'Grok API chyba: {str(e)}'})}\n\n"
                return
            except Exception as e:
                yield f"data: {json.dumps({'error': f'Grok text analysis failed: {str(e)}. Skúšam Gemini fallback...'})}\n\n"
                # Fallback: use Gemini for text too
                grok_research_json_text = f'{{"error": "Grok unavailable", "fallback": true, "message": "{str(e)}"}}'

            # ── Stage 1b: Web Research via Gemini (optional, runs in parallel conceptually) ──
            web_research_text = ""
            try:
                yield f"data: {json.dumps({'status': '🌐 Spúšťam webové overenie cez Gemini...'})}\n\n"
                grounded = run_grounded_web_research(gemini_key, car_info_text, listing_slug=slug)
                if grounded:
                    web_research_text = grounded
                    web_research_path = os.path.join(slug_dir, "web_research.md")
                    with open(web_research_path, "w", encoding="utf-8") as f:
                        f.write(grounded)
                    yield f"data: {json.dumps({'status': '✅ Webové overenie hotové.'})}\n\n"
            except Exception as e:
                safe_log(f"Web research warning: {e}")
                yield f"data: {json.dumps({'status': 'Webové overenie nedostupné, pokračujem bez neho.'})}\n\n"

            # ── Stage 2: Gemini Vision ──
            yield f"data: {json.dumps({'status': '📸 Fáza 2/3: Vizuálna analýza cez Gemini...'})}\n\n"

            # Read the Gemini vision prompt
            vision_prompt_path = os.path.join(SCRIPT_DIR, "prompts", "gemini_vision_system.md")
            if not os.path.exists(vision_prompt_path):
                yield f"data: {json.dumps({'error': 'gemini_vision_system.md not found.'})}\n\n"
                return
            with open(vision_prompt_path, "r", encoding="utf-8") as f:
                vision_system_prompt = f.read()

            # Prepare image collages
            image_data_list, _image_meta = prepare_llm_images(slug_dir)
            image_payload_context = _compact_json_for_prompt(_image_meta)
            vision_language = "Slovak" if _demo_output_language(output_language) == "sk" else "English"
            vision_content = (
                "Analyze only the attached vehicle photos/collages. "
                f"Write all human-readable JSON string values in {vision_language}. "
                "If full_gallery_included is true, overview sheets cover the full listing gallery; "
                "do not mark a buyer-relevant view as missing from the listing unless it is absent from those overview sheets. "
                "Use 'not assessable in detail' for views visible only in overview thumbnails.\n\n"
                f"IMAGE_PAYLOAD_METADATA:\n{image_payload_context}\n\n"
                f"Listing details:\n\n{car_info_text}"
            )

            vision_result_json = ""
            if image_data_list:
                try:
                    for chunk in _call_gemini(
                        gemini_key,
                        vision_system_prompt,
                        vision_content,
                        image_data_list=image_data_list,
                        listing_slug=slug,
                        allow_image_text_fallback=False,
                    ):
                        vision_result_json += chunk
                    # Save intermediate JSON
                    vision_json_path = os.path.join(slug_dir, "gemini_vision.json")
                    with open(vision_json_path, "w", encoding="utf-8") as f:
                        f.write(vision_result_json)
                    yield f"data: {json.dumps({'status': '✅ Vizuálna analýza hotová.'})}\n\n"
                except Exception as e:
                    safe_log(f"Gemini vision error: {e}")
                    vision_result_json = '{"photos_provided": false, "visual_verdict": "Nedostatočné fotografie", "photo_limitations": ["Fotografie sa nepodarilo spoľahlivo analyzovať."]}'
                    yield f"data: {json.dumps({'status': '⚠️ Vizuálna analýza zlyhala, pokračujem bez nej.'})}\n\n"
            else:
                vision_result_json = '{"photos_provided": false, "visual_verdict": "Nedostatočné fotografie", "photo_limitations": []}'
                yield f"data: {json.dumps({'status': 'ℹ️ Žiadne fotografie na analýzu.'})}\n\n"

            # ── Stage 3: Grok Final Synthesis ──
            yield f"data: {json.dumps({'status': '📋 Fáza 3/3: Generujem finálnu správu cez Grok...'})}\n\n"

            final_synthesis_prompt_path = os.path.join(SCRIPT_DIR, "prompts", "grok_final_synthesis_system.md")
            if not os.path.exists(final_synthesis_prompt_path):
                yield f"data: {json.dumps({'error': 'grok_final_synthesis_system.md not found.'})}\n\n"
                return
            with open(final_synthesis_prompt_path, "r", encoding="utf-8") as f:
                final_system_prompt = f.read()

            web_section = ""
            if web_research_text:
                web_section = f"\n## Webové overenie\n{web_research_text}\n"

            final_content = f"""## Original listing data
{car_info_text}

## Grok text/research JSON
{grok_research_json_text}

## Gemini vision JSON
{vision_result_json}

## Image payload metadata
{_compact_json_for_prompt(_image_meta)}
{web_section}
"""

            full_report = ""
            try:
                for chunk in _call_grok(grok_key, final_system_prompt, final_content, listing_slug=slug):
                    full_report += chunk
                    yield f"data: {json.dumps({'text': chunk})}\n\n"
            except (GrokApiKeyError, ApiKeyError) as e:
                yield f"data: {json.dumps({'error': f'Grok final synthesis chyba: {str(e)}'})}\n\n"
                return
            except Exception as e:
                yield f"data: {json.dumps({'error': f'Final synthesis failed: {str(e)}'})}\n\n"
                return

            # Save result
            result_path = os.path.join(slug_dir, "analysis_result.md")
            raw_path = os.path.join(slug_dir, "analysis_result_raw.md")
            with open(raw_path, "w", encoding="utf-8") as f:
                f.write(full_report)
            public_text = _normalize_report_headings(
                _restore_clickable_web_research_links(
                    _ensure_end_analysis_marker(_strip_kb_section(full_report)),
                    grok_research_json_text,
                    web_research_text,
                )
            )
            public_text = _replace_photo_analysis_section(public_text, vision_result_json, output_language)
            with open(result_path, "w", encoding="utf-8") as f:
                f.write(public_text)

            # Check for KB save blocks
            kb_blocks = extract_kb_save_blocks(full_report)
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

            yield f"data: {json.dumps({'done': True, 'has_kb_blocks': len(kb_blocks) > 0, 'saved_kb': saved_kb})}\n\n"

        except Exception as e:
            import traceback
            error_detail = f"{str(e)}\n{traceback.format_exc()}"
            safe_log(f"CRITICAL ERROR in api_analyze generate(): {error_detail}")
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
