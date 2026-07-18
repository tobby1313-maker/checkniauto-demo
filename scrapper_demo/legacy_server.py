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
import time
import urllib.parse
import html
import hmac
import unicodedata
from datetime import datetime
from pathlib import Path

from flask import (
    Response,
    current_app,
    has_app_context,
    jsonify,
    redirect,
    request,
    send_from_directory,
    session,
    stream_with_context,
)
from werkzeug.utils import secure_filename

from scrapper_demo.app import create_app
from scrapper_demo.config import DemoServerConfig
from scrapper_demo.contracts import ParsedListingData
from scrapper_demo.progress import RUNTIME_STATE_KEY
from scrapper_demo.providers import retry as provider_retry
from scrapper_demo.providers.errors import (
    ApiKeyError,
    GrokApiKeyError,
    OpenRouterApiKeyError,
    RateLimitError,
)
from scrapper_demo.providers.gemini import (
    GEMINI_FINAL_FALLBACK_MODELS,
    GEMINI_FINAL_MODEL,
    GEMINI_GROUNDING_MODEL,
    GEMINI_TEXT_RESEARCH_MODEL,
    GEMINI_VISION_MODEL,
    analyze as analyze_with_llm,
    count_tokens as count_gemini_tokens,
    grounded_research as run_grounded_web_research,
    stream_generate as _call_gemini,
)
from scrapper_demo.providers.grok import (
    analyze as analyze_with_grok,
    stream_generate as _call_grok,
)
from scrapper_demo.providers.openrouter import analyze as analyze_with_openrouter
from scrapper_demo.services import image_service
from scrapper_demo.calibration import create_calibration_bundle, create_debugging_bundle
from scrapper_demo.logging import configure_console_encoding, safe_log
from scrapper_demo.market_comparables import (
    customer_link_priority,
    is_customer_facing_market_comparable,
)
from scrapper_demo.presentation import build_presentation_payload
from scrapper_demo.services.analysis_pipeline import (
    AnalysisPipelineDependencies,
    multi_model_analysis_events,
)
from scrapper_demo.routes import create_private_blueprint, create_public_blueprint
from scrapper_demo import validation as report_validation
from scrapper_demo.storage import (
    ARTIFACT_LABELS as JOB_ARTIFACT_LABELS,
    PUBLIC_ARTIFACTS,
    ListingJobRepository,
    atomic_write_text,
)

from llm_client import extract_kb_save_blocks
from analysis_normalizer import add_verified_comparable_links, normalize_analysis_markdown
from token_tracker import default_tracker, estimate_output_tokens, estimate_request_tokens

configure_console_encoding()

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_CONFIG = DemoServerConfig.from_env(SCRIPT_DIR)
DEMO_MODE = SERVER_CONFIG.demo_mode
DATA_DIR = SERVER_CONFIG.data_dir
AUTA_DIR = SERVER_CONFIG.auta_dir
KB_DIR = SERVER_CONFIG.knowledge_base_dir
WEB_DIR = SERVER_CONFIG.web_dir
LLM_IMAGE_MAX_SIDE = image_service.LLM_IMAGE_MAX_SIDE
LLM_IMAGE_QUALITY = image_service.LLM_IMAGE_QUALITY
MAX_ANALYSIS_COLLAGES = image_service.MAX_ANALYSIS_COLLAGES
AI_MAX_VISION_ATTACHMENTS = image_service.AI_MAX_VISION_ATTACHMENTS
LLM_COLLAGE_COLUMNS = image_service.LLM_COLLAGE_COLUMNS
LLM_COLLAGE_ROWS = image_service.LLM_COLLAGE_ROWS
MAX_ANALYSIS_IMAGES = image_service.MAX_ANALYSIS_IMAGES
LLM_COLLAGE_CELL_SIZE = image_service.LLM_COLLAGE_CELL_SIZE
LLM_COLLAGE_LABEL_HEIGHT = image_service.LLM_COLLAGE_LABEL_HEIGHT
LLM_COLLAGE_MARGIN = image_service.LLM_COLLAGE_MARGIN
LLM_COLLAGE_QUALITY = image_service.LLM_COLLAGE_QUALITY
LLM_IMAGE_END_POSITION = image_service.LLM_IMAGE_END_POSITION
LLM_OVERVIEW_ATTACHMENTS = image_service.LLM_OVERVIEW_ATTACHMENTS
LLM_OVERVIEW_CELL_MIN_SIZE = image_service.LLM_OVERVIEW_CELL_MIN_SIZE
LLM_OVERVIEW_CELL_MAX_SIZE = image_service.LLM_OVERVIEW_CELL_MAX_SIZE
LLM_OVERVIEW_LABEL_HEIGHT = image_service.LLM_OVERVIEW_LABEL_HEIGHT
LLM_OVERVIEW_MARGIN = image_service.LLM_OVERVIEW_MARGIN
IMAGE_EXTENSIONS = image_service.IMAGE_EXTENSIONS
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
MAX_MANUAL_IMAGES = SERVER_CONFIG.demo_max_manual_images
# 0 means unlimited downloads; the LLM payload is capped separately by MAX_ANALYSIS_IMAGES.
DEMO_MAX_SCRAPED_IMAGES = SERVER_CONFIG.demo_max_scraped_images
DEMO_PROMPT_FILE = SERVER_CONFIG.demo_prompt_file
DEMO_RATE_LIMIT_PER_IP = SERVER_CONFIG.demo_rate_limit_per_ip
DEMO_MAX_CONCURRENT_JOBS = SERVER_CONFIG.demo_max_concurrent_jobs
DEMO_JOB_TTL_MINUTES = SERVER_CONFIG.demo_job_ttl_minutes
DEMO_SKIP_KB = SERVER_CONFIG.demo_skip_kb
MAX_UPLOAD_BYTES = SERVER_CONFIG.max_upload_bytes
FINAL_LISTING_DESCRIPTION_CHARS = 900
MODEL_LISTING_DESCRIPTION_CHARS = 1400
FINAL_TEXT_FIELD_CHARS = 420
FINAL_WEB_RESEARCH_CHARS = 2800
FINAL_CONTEXT_MAX_CHARS = 26000

app = create_app(
    SERVER_CONFIG.as_flask_mapping(),
    register_legacy_routes=False,
    import_name="web_server",
)


def _runtime_config(name, legacy_value):
    """Use factory-app config while preserving legacy global patch compatibility."""
    if has_app_context() and current_app._get_current_object() is not app:
        return current_app.config.get(name, legacy_value)
    return legacy_value


def _runtime_auta_dir():
    return str(_runtime_config("SCRAPPER_AUTA_DIR", AUTA_DIR))


def _runtime_kb_dir():
    return str(_runtime_config("SCRAPPER_KB_DIR", KB_DIR))


def _runtime_web_dir():
    return str(_runtime_config("SCRAPPER_WEB_DIR", WEB_DIR))


def _job_repository():
    return ListingJobRepository(_runtime_auta_dir())


def _runtime_state():
    selected_app = current_app._get_current_object() if has_app_context() else app
    return selected_app.extensions[RUNTIME_STATE_KEY]


def _set_current_progress(status=None, line=None, done=False, reset=False):
    """Store live demo progress for the token dashboard polling endpoint."""
    _runtime_state().progress.update(
        status=status,
        line=line,
        done=done,
        reset=reset,
    )


def _track_demo_sse_progress(event_text):
    """Mirror demo SSE status/log events into the polling progress endpoint."""
    _runtime_state().progress.track_sse(event_text)


def _demo_route_gate():
    if not _runtime_config("DEMO_MODE", DEMO_MODE):
        return None
    path = request.path.rstrip("/") or "/"
    allowed = (
        path == "/"
        or path == "/token-dashboard.html"
        or path == "/healthz"
        or path == "/api/token-usage"
        or path.startswith("/api/demo/")
        or path.startswith("/api/admin/")
        or path.startswith("/admin/")
    )
    if path.startswith("/api/") and not allowed:
        return jsonify({"error": "This route is disabled in demo mode."}), 404
    return None


# ─── Helpers ─────────────────────────────────────────────────────────

def parse_car_info_md(md_text: str) -> ParsedListingData:
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
        elif current_section == "specifications" and line.startswith("- **") and "**" in line:
            match = re.match(r'- \*\*(.+?)\*\*:?\s*(.*)', line)
            if match:
                key = match.group(1).rstrip(":").strip()
                result["specs"][key] = match.group(2).strip()

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

    if not result["price"] and result["description"]:
        price_match = re.search(
            r"(?i)(?:cena|price)\s*:\s*(\d{1,3}(?:[\s\u00a0.]\d{3})+|\d+)\s*(?:€|eur)(?:\s|$)",
            result["description"],
        )
        if price_match:
            result["price"] = int(re.sub(r"\D", "", price_match.group(1)))
            result["currency"] = "EUR"

    if str(result["vin"] or "").strip().upper() in {
        "N/A", "NA", "NONE", "NULL", "UNKNOWN", "NEUVEDENE", "NEUVEDENÉ"
    }:
        result["vin"] = ""
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


ANALYSIS_ARTIFACTS = PUBLIC_ARTIFACTS
ARTIFACT_LABELS = JOB_ARTIFACT_LABELS


def _listing_images_dir(slug_dir):
    return os.path.join(slug_dir, "images")


def _listing_dir_or_raise(slug):
    return str(_job_repository().job_dir(slug, require=True))


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
    repository = _job_repository()
    try:
        image_path = repository.image_path(slug, filename)
    except ValueError:
        return jsonify({"error": "Invalid filename"}), 400
    repository.job_dir(slug, require=True)
    images_dir = repository.images_dir(slug)
    if not os.path.isdir(images_dir):
        return jsonify({"error": "Not found"}), 404
    if not _is_supported_image(filename):
        return jsonify({"error": "Not found"}), 404
    if not os.path.isfile(image_path):
        return jsonify({"error": "Not found"}), 404
    return send_from_directory(str(images_dir), filename)


def _list_listing_artifacts(slug, route_prefix="/api/listings"):
    repository = _job_repository()
    repository.job_dir(slug, require=True)
    artifacts = []
    for path in repository.available_artifacts(slug):
        filename = path.name
        artifacts.append(
            {
                "filename": filename,
                "label": ARTIFACT_LABELS.get(filename, filename),
                "url": _listing_artifact_url(route_prefix, slug, filename),
                "size": path.stat().st_size,
                "modified_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
            }
        )
    return artifacts


def _send_listing_artifact_file(slug, filename):
    repository = _job_repository()
    try:
        artifact_path = repository.artifact_path(slug, filename, public_only=True)
    except ValueError:
        return jsonify({"error": "Artifact not allowed"}), 400
    repository.job_dir(slug, require=True)
    if not os.path.isfile(artifact_path):
        return jsonify({"error": "Artifact not found"}), 404
    content = artifact_path.read_text(encoding="utf-8")
    if filename.lower().endswith(".md") and request.args.get("raw", "").lower() not in {"1", "true", "yes"}:
        return Response(
            _render_markdown_artifact_preview(filename, content),
            mimetype="text/html; charset=utf-8",
            headers={"Cache-Control": "no-cache"},
        )
    return Response(
        content,
        mimetype="text/plain; charset=utf-8",
        headers={"Cache-Control": "no-cache"},
    )


def get_listings(require_analysis=False, image_route_prefix="/api/listings"):
    """Scan Auta/ directory and return listing summaries."""
    listings = []
    repository = _job_repository()
    required_artifact = "analysis_result.md" if require_analysis else None
    for slug, slug_path in repository.iter_job_directories(require_artifact=required_artifact):
        slug_dir = str(slug_path)
        try:
            summary = _build_listing_summary(slug, slug_dir, image_route_prefix=image_route_prefix)
        except FileNotFoundError:
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
    kb_dir = _runtime_kb_dir()
    if not os.path.isdir(kb_dir):
        return structure

    for category in KB_CATEGORIES:
        cat_dir = os.path.join(kb_dir, category)
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
    configured_limit = str(_runtime_config("DEMO_RATE_LIMIT_PER_IP", DEMO_RATE_LIMIT_PER_IP))
    limit = _parse_daily_limit(configured_limit)
    if not _runtime_state().rate_limiter.allow(_demo_client_ip(), limit):
        return jsonify({"error": f"Demo limit reached ({configured_limit}). Try again later."}), 429
    return None


def _safe_slug_dir(slug):
    return str(_job_repository().job_dir(slug))


def _cleanup_old_demo_jobs():
    ttl_minutes = int(_runtime_config("DEMO_JOB_TTL_MINUTES", DEMO_JOB_TTL_MINUTES))
    _job_repository().cleanup_expired(ttl_minutes)


def _demo_api_keys():
    keys = []
    for env_name in (
        "GEMINI_PRIMARY_API_KEY",
        "GEMINI_BACKUP_API_KEY",
        "GEMINI_SECOND_BACKUP_API_KEY",
    ):
        configured = _runtime_config(env_name, os.environ.get(env_name, ""))
        value = str(configured or "").strip()
        if value and value not in keys:
            keys.append(value)
    return keys


def _demo_grok_api_key():
    return str(_runtime_config("GROK_API_KEY", os.environ.get("GROK_API_KEY", "")) or "").strip()


def _demo_openrouter_api_key():
    return str(_runtime_config("OPENROUTER_API_KEY", os.environ.get("OPENROUTER_API_KEY", "")) or "").strip()


def _demo_output_language(value):
    normalized = str(value or "").strip().lower()
    if normalized in {"cs", "cz"}:
        return "cs"
    return "en" if normalized == "en" else "sk"


def _localized(language, *, sk, cs, en):
    return {"sk": sk, "cs": cs, "en": en}.get(_demo_output_language(language), sk)


def _slugify(value, fallback="manual-listing"):
    """Create a filesystem-friendly slug from user supplied text."""
    value = (value or "").strip().lower()
    value = re.sub(r"https?://", "", value)
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return value[:80] or fallback


def _unique_listing_slug(base_slug):
    """Avoid overwriting an existing listing folder."""
    return _job_repository().unique_slug(
        _slugify(base_slug),
        timestamp=datetime.now().strftime("%Y%m%d-%H%M%S"),
    )


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
    max_manual_images = int(_runtime_config("DEMO_MAX_MANUAL_IMAGES", MAX_MANUAL_IMAGES))
    if len(uploads) > max_manual_images:
        raise ValueError(f"Upload at most {max_manual_images} images.")

    for uploaded in uploads:
        ext = os.path.splitext(uploaded.filename)[1].lower()
        if ext not in IMAGE_EXTENSIONS:
            raise ValueError(f"Unsupported image type: {uploaded.filename}")

    title = title or _first_text_line(manual_text)
    slug_seed = title or source_url or "manual-listing"
    slug = _unique_listing_slug(slug_seed)
    repository = _job_repository()
    slug_dir = str(repository.job_dir(slug, create=True))
    images_dir = str(repository.images_dir(slug, create=True))

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

    repository.write_text(slug, "car_info.md", _format_manual_car_info_md(raw))
    repository.write_json(slug, "raw_data.json", raw)

    try:
        from main import build_analysis_request, _run_vin_decoding
        _run_vin_decoding(slug_dir)
        build_analysis_request(SCRIPT_DIR, slug_dir, source_url or "Manual entry")
    except Exception as e:
        safe_log(f"Manual import post-processing warning: {e}")

    return slug, slug_dir, len(saved_images)


def index():
    return send_from_directory(_runtime_web_dir(), "index.html")


def analysis_page(slug):
    _ = slug
    return send_from_directory(_runtime_web_dir(), "analysis.html")


def technical_analysis_page(slug):
    _ = slug
    return send_from_directory(_runtime_web_dir(), "technical-analysis.html")


def _admin_token():
    if has_app_context():
        return str(current_app.config.get("ADMIN_DASHBOARD_TOKEN", "") or "")
    return str(SERVER_CONFIG.admin_dashboard_token or "")


def _admin_authenticated():
    secret = str(current_app.config.get("SECRET_KEY", "") or "") if has_app_context() else SERVER_CONFIG.flask_secret_key
    secure_config = bool(_admin_token()) and bool(secret) and secret != "dev-demo-secret-change-me"
    return secure_config and session.get("admin_dashboard_authenticated") is True


def _admin_api_guard():
    secret = str(current_app.config.get("SECRET_KEY", "") or "") if has_app_context() else SERVER_CONFIG.flask_secret_key
    if not _admin_token() or not secret or secret == "dev-demo-secret-change-me":
        return jsonify({"error": "Administrator dashboard is not configured."}), 503
    if not _admin_authenticated():
        return jsonify({"error": "Administrator authentication required."}), 401
    return None


def admin_login():
    error = ""
    if request.method == "POST":
        configured = _admin_token()
        secret = str(current_app.config.get("SECRET_KEY", "") or "")
        submitted = str(request.form.get("token") or "")
        if configured and secret and secret != "dev-demo-secret-change-me" and hmac.compare_digest(submitted, configured):
            session.clear()
            session["admin_dashboard_authenticated"] = True
            return redirect("/token-dashboard.html")
        error = "Invalid administrator token."
    elif _admin_authenticated():
        return redirect("/token-dashboard.html")

    return Response(
        "<!doctype html><html><head><meta charset='utf-8'><title>Admin login</title>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<style>body{font:16px system-ui;max-width:420px;margin:10vh auto;padding:24px}"
        "input,button{box-sizing:border-box;width:100%;padding:12px;margin-top:12px}"
        ".error{color:#b42318}</style></head><body><h1>Calibration admin</h1>"
        f"<p class='error'>{html.escape(error)}</p>"
        "<form method='post'><label>Administrator token<input name='token' type='password' "
        "required autocomplete='current-password'></label><button type='submit'>Sign in</button>"
        "</form></body></html>",
        mimetype="text/html",
        headers={"Cache-Control": "no-store"},
    )


def admin_logout():
    session.clear()
    return redirect("/admin/login")


def token_dashboard():
    if not _admin_authenticated():
        return redirect("/admin/login")
    response = send_from_directory(_runtime_web_dir(), "token-dashboard.html")
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


def api_token_usage():
    denied = _admin_api_guard()
    if denied:
        return denied
    try:
        limit = int(request.args.get("limit", "50"))
    except ValueError:
        limit = 50
    return jsonify(default_tracker.get_stats(recent_limit=limit))


def api_demo_current_progress():
    denied = _admin_api_guard()
    if denied:
        return denied
    return jsonify(_runtime_state().progress.snapshot())


def api_listings():
    return jsonify(get_listings())


def api_demo_listings():
    return jsonify(get_listings(require_analysis=True, image_route_prefix="/api/demo/listings"))


def api_admin_listings():
    denied = _admin_api_guard()
    if denied:
        return denied
    return jsonify(get_listings(image_route_prefix="/api/admin/listings"))


def api_listing_detail(slug):
    try:
        result = _build_listing_detail_payload(slug)
        slug_dir = _listing_dir_or_raise(slug)
    except FileNotFoundError:
        return jsonify({"error": "Listing not found"}), 404

    repository = _job_repository()
    raw_data_path = repository.artifact_path(slug, "raw_data.json")
    vin_decoded_path = repository.artifact_path(slug, "vin_decoded.json")

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


def api_demo_listing_presentation(slug):
    try:
        payload = _build_listing_detail_payload(
            slug,
            image_route_prefix="/api/demo/listings",
            require_analysis=True,
        )
        presentation = build_presentation_payload(
            _job_repository(),
            slug,
            parsed=payload["parsed"],
            images=payload["images"],
            report_markdown=payload.get("analysis_content", ""),
        )
    except FileNotFoundError:
        return jsonify({"error": "Saved analysis not found"}), 404
    return jsonify(presentation)


def api_listing_artifacts(slug):
    denied = _admin_api_guard()
    if denied:
        return denied
    try:
        return jsonify({"slug": slug, "artifacts": _list_listing_artifacts(slug)})
    except FileNotFoundError:
        return jsonify({"error": "Listing not found"}), 404


def api_demo_listing_artifacts(slug):
    denied = _admin_api_guard()
    if denied:
        return denied
    try:
        return jsonify({"slug": slug, "artifacts": _list_listing_artifacts(slug, route_prefix="/api/demo/listings")})
    except FileNotFoundError:
        return jsonify({"error": "Saved analysis not found"}), 404


def api_admin_listing_artifacts(slug):
    denied = _admin_api_guard()
    if denied:
        return denied
    try:
        return jsonify({
            "slug": slug,
            "artifacts": _list_listing_artifacts(
                slug,
                route_prefix="/api/admin/listings",
            ),
        })
    except FileNotFoundError:
        return jsonify({"error": "Analysis attempt not found"}), 404


def api_listing_artifact(slug, filename):
    denied = _admin_api_guard()
    if denied:
        return denied
    try:
        return _send_listing_artifact_file(slug, filename)
    except FileNotFoundError:
        return jsonify({"error": "Listing not found"}), 404


def api_admin_listing_artifact(slug, filename):
    denied = _admin_api_guard()
    if denied:
        return denied
    try:
        return _send_listing_artifact_file(slug, filename)
    except FileNotFoundError:
        return jsonify({"error": "Analysis attempt not found"}), 404


def api_demo_listing_artifact(slug, filename):
    denied = _admin_api_guard()
    if denied:
        return denied
    try:
        return _send_listing_artifact_file(slug, filename)
    except FileNotFoundError:
        return jsonify({"error": "Saved analysis not found"}), 404


def api_update_listing_detail(slug):
    """Update editable listing fields and rewrite car_info.md."""
    repository = _job_repository()
    try:
        slug_dir = str(repository.job_dir(slug, require=True))
    except FileNotFoundError:
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

    car_info_path = repository.artifact_path(slug, "car_info.md")
    existing_raw = repository.read_json(slug, "raw_data.json", default={}) or {}

    existing_parsed = {}
    if os.path.exists(car_info_path):
        try:
            with open(car_info_path, "r", encoding="utf-8") as f:
                existing_parsed = parse_car_info_md(f.read())
        except IOError:
            existing_parsed = {}

    images_dir = repository.images_dir(slug)
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

    repository.write_text(slug, "car_info.md", _format_manual_car_info_md(raw))
    repository.write_json(slug, "raw_data.json", raw)

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


def api_listing_image(slug, filename):
    try:
        return _send_listing_image_file(slug, filename)
    except FileNotFoundError:
        return jsonify({"error": "Not found"}), 404


def api_demo_listing_image(slug, filename):
    try:
        return _send_listing_image_file(slug, filename)
    except FileNotFoundError:
        return jsonify({"error": "Not found"}), 404


def api_listing_analysis_images(slug):
    slug_dir = str(_job_repository().job_dir(slug))
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


def api_listing_analysis_image(slug, filename):
    repository = _job_repository()
    try:
        image_path = repository.analysis_image_path(slug, filename)
    except ValueError:
        return jsonify({"error": "Invalid filename"}), 400

    analysis_dir = repository.analysis_images_dir(slug)
    if not os.path.isdir(analysis_dir):
        return jsonify({"error": "Not found"}), 404
    if not image_path.is_file():
        return jsonify({"error": "Not found"}), 404
    return send_from_directory(str(analysis_dir), filename)


def api_listing_analysis(slug):
    slug_dir = str(_job_repository().job_dir(slug))
    
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
    normalized = normalize_analysis_markdown(text, _read_car_info_text(slug_dir))
    research_path = os.path.join(slug_dir, "grok_research.json")
    try:
        with open(research_path, "r", encoding="utf-8") as research_file:
            research_data = json.load(research_file)
    except (OSError, TypeError, ValueError):
        return normalized
    return add_verified_comparable_links(
        normalized,
        research_data.get("market_comparables", []) if isinstance(research_data, dict) else [],
    )


def api_listing_analysis_result(slug):
    repository = _job_repository()
    slug_dir = str(repository.job_dir(slug))
    result_path = repository.artifact_path(slug, "analysis_result.md")
    kb_autosave_path = repository.artifact_path(slug, "kb_autosave.json")
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


def api_listing_analysis_result_raw(slug):
    denied = _admin_api_guard()
    if denied:
        return denied
    raw_path = _job_repository().artifact_path(slug, "analysis_result_raw.md")
    if not os.path.exists(raw_path):
        return jsonify({"error": "Raw result not found"}), 404

    with open(raw_path, "r", encoding="utf-8") as f:
        content = f.read()

    return jsonify({
        "content": content,
    })


def api_demo_listing_analysis_result_raw(slug):
    denied = _admin_api_guard()
    if denied:
        return denied
    raw_path = _job_repository().artifact_path(slug, "analysis_result_raw.md")
    if not os.path.exists(raw_path):
        return jsonify({"error": "Raw result not found"}), 404

    with open(raw_path, "r", encoding="utf-8") as f:
        content = f.read()

    return jsonify({
        "content": content,
    })


def api_admin_calibration_bundle(slug):
    denied = _admin_api_guard()
    if denied:
        return denied
    try:
        job_dir = _job_repository().job_dir(slug, require=True)
        if not (job_dir / "analysis_result.md").is_file():
            raise FileNotFoundError(slug)
        archive_path = create_calibration_bundle(job_dir, job_dir.name)
    except FileNotFoundError:
        return jsonify({"error": "Completed analysis not found."}), 404

    archive_size = archive_path.stat().st_size

    def stream_archive():
        try:
            with archive_path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    yield chunk
        finally:
            try:
                archive_path.unlink(missing_ok=True)
            except OSError as exc:
                safe_log(f"Calibration archive cleanup warning: {exc}")

    return Response(
        stream_with_context(stream_archive()),
        mimetype="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="calibration-{job_dir.name}.zip"',
            "Content-Length": str(archive_size),
            "Cache-Control": "no-store",
        },
    )


def api_admin_debugging_bundle(slug):
    denied = _admin_api_guard()
    if denied:
        return denied
    try:
        job_dir = _job_repository().job_dir(slug, require=True)
        if not any(
            (job_dir / filename).is_file()
            for filename in (
                "analysis_diagnostics.json",
                "text_research_provider_attempts.json",
                "component_identity.json",
                "car_info.md",
                "raw_data.json",
            )
        ):
            raise FileNotFoundError(slug)
        archive_path = create_debugging_bundle(job_dir, job_dir.name)
    except FileNotFoundError:
        return jsonify({"error": "Analysis attempt not found."}), 404

    archive_size = archive_path.stat().st_size

    def stream_archive():
        try:
            with archive_path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    yield chunk
        finally:
            try:
                archive_path.unlink(missing_ok=True)
            except OSError as exc:
                safe_log(f"Debugging archive cleanup warning: {exc}")

    return Response(
        stream_with_context(stream_archive()),
        mimetype="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="debugging-{job_dir.name}.zip"',
            "Content-Length": str(archive_size),
            "Cache-Control": "no-store",
        },
    )


def api_listing_analysis_export(slug):
    repository = _job_repository()
    slug_dir = str(repository.job_dir(slug))
    result_path = repository.artifact_path(slug, "analysis_result.md")
    if not os.path.exists(result_path):
        return jsonify({"error": "Result not found"}), 404

    with open(result_path, "r", encoding="utf-8") as f:
        stripped_content = _public_analysis_markdown(_strip_kb_section(f.read()), slug_dir)

    embedded_content = _embed_collage_images(stripped_content, slug)

    return jsonify({
        "title": slug.replace("-", " ").title(),
        "content": embedded_content,
    })


def api_kb():
    return jsonify(get_kb_structure())


def api_kb_file(category, filename):
    # Security: prevent path traversal
    if ".." in filename or "/" in filename or "\\" in filename:
        return jsonify({"error": "Invalid filename"}), 400

    filepath = os.path.join(_runtime_kb_dir(), category, filename)
    if not os.path.exists(filepath):
        return jsonify({"error": "File not found"}), 404

    with open(filepath, "r", encoding="utf-8") as f:
        content = json.load(f)

    return jsonify(content)


_is_supported_image = image_service.is_supported_image
_select_representative_indices = image_service.select_representative_indices
_average_hash = image_service.average_hash
_hash_distance = image_service.hash_distance
_optimize_image_for_llm = image_service.optimize_image_for_llm
_create_llm_collage = image_service.create_llm_collage
_overview_grid_dimensions = image_service.overview_grid_dimensions
_create_llm_overview_sheet = image_service.create_llm_overview_sheet
_chunk_items = image_service.chunk_items


def prepare_llm_images(slug_dir):
    return image_service.prepare_llm_images(slug_dir, log=safe_log)




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
            output_dir = str(_job_repository().job_dir(slug))
        except Exception:
            pass

        process = None
        try:
            env = os.environ.copy()
            env["SCRAPPER_AUTA_DIR"] = _runtime_auta_dir()
            env.setdefault(
                "DEMO_MAX_SCRAPED_IMAGES",
                str(_runtime_config("DEMO_MAX_SCRAPED_IMAGES", DEMO_MAX_SCRAPED_IMAGES)),
            )
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
        "demo": str(_runtime_config("DEMO_PROMPT_FILE", DEMO_PROMPT_FILE)),
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
        matched = find_matching_kb_files(_runtime_kb_dir(), car_info)
        if matched:
            kb_section = "\n\n## 💾 KNOWLEDGE BASE (cached component data):\n"
            for category, filepath in matched:
                with open(filepath, "r", encoding="utf-8") as f:
                    kb_section += f"\n### [{category.upper()}] {os.path.basename(filepath)}:\n```json\n{f.read()}\n```\n"
    except Exception:
        pass  # KB matching is optional
    if _runtime_config("DEMO_MODE", DEMO_MODE) and _runtime_config("DEMO_SKIP_KB", DEMO_SKIP_KB):
        kb_section = ""

    try:
        image_data_list, image_meta = prepare_llm_images(slug_dir)
    except ImportError:
        image_data_list = []
        image_meta = {
            "coverage_mode": "none",
            "original_count": 0,
            "unique_count": 0,
            "duplicate_count": 0,
            "selected_originals": [],
            "optimized_files": [],
            "collage_groups": [],
            "collage_count": 0,
            "selected_count": 0,
            "attachment_count": 0,
            "attachment_limit": AI_MAX_VISION_ATTACHMENTS,
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
        f"- Unique photos after perceptual deduplication: "
        f"{image_meta.get('unique_count', image_meta['original_count'])}; "
        f"duplicates skipped: {image_meta.get('duplicate_count', 0)}\n"
        f"- Vision attachments: {image_meta.get('attachment_count', len(image_data_list))}/"
        f"{image_meta.get('attachment_limit', AI_MAX_VISION_ATTACHMENTS)}\n"
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

    if image_meta.get("selected_count", len(image_data_list)) < image_meta.get(
        "unique_count", image_meta["original_count"]
    ):
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
        "Use the localized report schema for OUTPUT_LANGUAGE (`sk`, `cs`, or `en`) exactly as defined in the system prompt. "
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
    analysis_dir = str(_job_repository().analysis_images_dir(slug))
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


def healthz():
    return jsonify({"status": "ok", "demo_mode": bool(_runtime_config("DEMO_MODE", DEMO_MODE))})


RESEARCH_V2_GROUNDED_MAX_CHARS = 12_000


def _research_heading_key(value):
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(
        char for char in normalized if not unicodedata.combining(char)
    ).casefold()


def _clip_research_section(value, max_chars):
    """Clip at a Markdown line boundary so partial claims are never supplied."""
    kept = []
    used = 0
    for line in str(value or "").splitlines():
        addition = len(line) + (1 if kept else 0)
        if used + addition > max_chars:
            break
        kept.append(line)
        used += addition
    return "\n".join(kept).strip()


def _compact_grounded_research_for_v2(value, max_chars=RESEARCH_V2_GROUNDED_MAX_CHARS):
    """Keep claim-bearing grounded sections while dropping duplicated source prose."""
    text = str(value or "").strip()
    if not text or len(text) <= max_chars:
        return text

    matches = list(re.finditer(r"(?m)^###\s+(.+?)\s*$", text))
    if not matches:
        return _clip_research_section(text, max_chars)

    sections = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        heading = match.group(1).strip()
        key = _research_heading_key(heading)
        section_text = text[match.start():end].strip()
        sections.append((index, key, section_text))

    # Component identity and VIN are supplied as backend-owned structured data.
    # The long source bibliography and citation list are replaced by the exact
    # backend source registry, so retaining them here only wastes input tokens.
    excluded_markers = (
        "identifikacia komponentov", "component identification",
        "vin /", "vin history", "vin / history",
        "citacie", "citations",
    )
    candidates = [
        section for section in sections
        if not any(marker in section[1] for marker in excluded_markers)
        and section[1] not in {"zdroje", "sources"}
    ]
    if not candidates:
        return _clip_research_section(text, max_chars)

    compact = "\n\n".join(section[2] for section in candidates)
    if len(compact) <= max_chars:
        return compact

    def priority(key):
        groups = (
            (6, ("najdolezitejs", "key finding", "most important")),
            (5, ("motor", "engine", "prevodov", "pohon", "transmission", "gearbox", "drivetrain")),
            (4, ("zvolav", "recall", "kampan", "naklad", "cost")),
            (3, ("generac", "karoser", "podvoz", "generation", "body", "chassis")),
        )
        for rank, markers in groups:
            if any(marker in key for marker in markers):
                return rank
        return 1

    # Share the budget across the strongest sections so one verbose engine
    # section cannot crowd out transmission, recall, cost, or chassis evidence.
    ranked = sorted(candidates, key=lambda item: (-priority(item[1]), item[0]))[:6]
    separators = 2 * max(0, len(ranked) - 1)
    per_section = max(900, (max_chars - separators) // max(1, len(ranked)))
    clipped = [
        (index, _clip_research_section(section_text, per_section))
        for index, _key, section_text in ranked
    ]
    result = "\n\n".join(text for _index, text in sorted(clipped) if text)
    return _clip_research_section(result, max_chars)


def _build_text_research_context(
    car_info_text,
    output_language="sk",
    web_research_text="",
    component_identity=None,
    *,
    research_v2=False,
    listing_context=None,
    vin_light_decode=None,
    verified_source_registry=None,
):
    if research_v2:
        compact_identity = (
            {
                key: value
                for key, value in component_identity.items()
                if key != "sources"
            }
            if isinstance(component_identity, dict)
            else {}
        )
        payload = {
            "listing": listing_context if isinstance(listing_context, dict) else {},
            # Grounded research already carries the usable source registry.
            # Identity discovery sources are redundant here and used to add
            # roughly a thousand tokens without supporting Research V2 claims.
            "component_identity": compact_identity,
            "vin_light_check": vin_light_decode if isinstance(vin_light_decode, dict) else {},
            "grounded_research": _compact_grounded_research_for_v2(web_research_text),
            "verified_source_registry": (
                dict(verified_source_registry)
                if isinstance(verified_source_registry, dict)
                else {}
            ),
            "output_language": _demo_output_language(output_language),
        }
        return (
            "Normalize only the research-owned evidence in this backend context. "
            "Do not reproduce backend-owned fields.\n\n"
            + _compact_json_for_prompt(payload)
        )
    web_section = ""
    if web_research_text:
        web_section = f"\n\n## Provided web research results\n{web_research_text}"
    identity_section = ""
    if isinstance(component_identity, dict):
        identity_section = (
            "\n\n## Backend grounded component identity\n"
            + _compact_json_for_prompt(component_identity)
            + "\nPreserve each resolution/confidence exactly; do not upgrade PROBABLE, "
            "AMBIGUOUS, or UNKNOWN to confirmed."
        )

    return f"""## Listing data
{car_info_text}
{identity_section}
{web_section}

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
    return {"_parse_error": True, "raw_preview": _clip_text(value, 1200)}




def _split_trailing_url_punctuation(value):
    url = str(value or "")
    trailing = ""
    while url and url[-1] in ".,;:!?":
        trailing = url[-1] + trailing
        url = url[:-1]
    while url.endswith(")") and url.count(")") > url.count("("):
        trailing = ")" + trailing
        url = url[:-1]
    return url, trailing


def _linkify_plain_urls_html(text):
    value = str(text or "")
    output = []
    index = 0
    for match in re.finditer(r"https?://[^\s<]+", value):
        output.append(html.escape(value[index : match.start()]))
        url, trailing = _split_trailing_url_punctuation(match.group(0))
        if _is_verified_public_url(url):
            safe_url = html.escape(url, quote=True)
            output.append(f'<a href="{safe_url}" target="_blank" rel="noopener noreferrer">{html.escape(url)}</a>')
        else:
            output.append(html.escape(url))
        output.append(html.escape(trailing))
        index = match.end()
    output.append(html.escape(value[index:]))
    return "".join(output)


def _inline_markdown_html(text):
    value = str(text or "")
    output = []
    index = 0
    for label, url, start, end in _iter_markdown_links(value):
        output.append(_linkify_plain_urls_html(value[index:start]))
        if _is_verified_public_url(url):
            safe_url = html.escape(url, quote=True)
            output.append(
                f'<a href="{safe_url}" target="_blank" rel="noopener noreferrer">{html.escape(label)}</a>'
            )
        else:
            output.append(html.escape(label))
        index = end
    output.append(_linkify_plain_urls_html(value[index:]))
    rendered = "".join(output)
    rendered = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", rendered)
    rendered = re.sub(r"`([^`]+)`", r"<code>\1</code>", rendered)
    return rendered


def _render_markdown_artifact_body(markdown_text):
    blocks = []
    list_open = False
    paragraph = []

    def flush_paragraph():
        nonlocal paragraph
        if paragraph:
            blocks.append(f"<p>{_inline_markdown_html(' '.join(paragraph))}</p>")
            paragraph = []

    def close_list():
        nonlocal list_open
        if list_open:
            blocks.append("</ul>")
            list_open = False

    for raw_line in str(markdown_text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            close_list()
            continue

        heading = re.match(r"^(#{1,4})\s+(.+)$", stripped)
        if heading:
            flush_paragraph()
            close_list()
            level = len(heading.group(1))
            blocks.append(f"<h{level}>{_inline_markdown_html(heading.group(2))}</h{level}>")
            continue

        bullet = re.match(r"^[-*]\s+(.+)$", stripped)
        if bullet:
            flush_paragraph()
            if not list_open:
                blocks.append("<ul>")
                list_open = True
            blocks.append(f"<li>{_inline_markdown_html(bullet.group(1))}</li>")
            continue

        close_list()
        paragraph.append(stripped)

    flush_paragraph()
    close_list()
    return "\n".join(blocks)


def _render_markdown_artifact_preview(filename, content):
    title = html.escape(filename)
    body = _render_markdown_artifact_body(content)
    return f"""<!doctype html>
<html lang="sk">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{title}</title>
    <style>
        body {{ margin: 0; background: #f8fafc; color: #111827; font-family: Arial, sans-serif; line-height: 1.6; }}
        main {{ max-width: 980px; margin: 28px auto; padding: 28px 34px; background: #fff; border: 1px solid #dbe3ef; }}
        h1, h2, h3, h4 {{ line-height: 1.25; }}
        h1 {{ font-size: 26px; }}
        h2 {{ margin-top: 28px; padding-bottom: 6px; border-bottom: 1px solid #fed7aa; }}
        a {{ color: #c2410c; overflow-wrap: anywhere; }}
        code {{ padding: 1px 4px; background: #f1f5f9; border-radius: 4px; }}
        .toolbar {{ margin-bottom: 18px; color: #64748b; font-size: 13px; }}
        .toolbar a {{ font-weight: 700; }}
    </style>
</head>
<body>
    <main>
        <div class="toolbar">Markdown preview · <a href="?raw=1">raw text</a></div>
        {body}
    </main>
</body>
</html>"""


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


def _mileage_km_value(value):
    digits = re.sub(r"\D", "", str(value or ""))
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def _fold_listing_text(value):
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in normalized if not unicodedata.combining(char)).lower()


def _explicit_gross_price_eur(value):
    text = str(value or "").replace("\u00a0", " ")
    matches = re.findall(
        r"(\d{1,3}(?:[\s.]\d{3})+|\d+)\s*(?:€|eur)\s*(?:s\s*dph|vratane\s*dph|brutto)",
        _fold_listing_text(text),
        re.IGNORECASE,
    )
    amounts = [int(re.sub(r"\D", "", match)) for match in matches]
    return max(amounts) if amounts else None


def _description_capture(description, patterns):
    text = str(description or "").replace("\u00a0", " ")
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip(" :-\t\r\n")
    return ""


def _description_listing_facts(description, title=""):
    text = str(description or "")
    folded_text = _fold_listing_text(text)
    abbreviated_mileage = re.search(
        r"(?:najazd(?:ene|enych)?(?:\s*km)?[^\d]{0,20})?(\d{2,3})\s*tis(?:\.|ic)?\s*\.?\s*km\b",
        folded_text,
        re.IGNORECASE,
    )
    if abbreviated_mileage:
        mileage_km = int(abbreviated_mileage.group(1)) * 1000
        mileage = f"{mileage_km} km"
    else:
        mileage = _description_capture(
            text,
            (
                r"(?:najazden(?:e|é|ých)?(?:\s*km)?|najazd|mileage)\s*:?\s*(?:len\s*)?([\d\s.]+)(?:\s*km)?",
                r"\b(\d{2,3}(?:[\s.]\d{3})+)\s*km\b",
                r"\b(\d{4,6})\s*km\b",
            ),
        )
        mileage_km = _mileage_km_value(mileage)
    if mileage_km:
        mileage = f"{mileage_km} km"

    year = _description_capture(
        text,
        (
            r"(?:mesiac\s*/\s*rok|rok(?:\s*v[ýy]roby)?|year)\s*:?\s*(?:\d{1,2}\s*/\s*)?((?:19|20)\d{2})",
            r"\b(?:0?[1-9]|1[0-2])\s*/\s*((?:19|20)\d{2})\b",
            r"\b((?:19|20)\d{2})\b",
        ),
    )
    engine = _description_capture(
        text,
        (
            r"(?:typ\s+motora|\bmotor\b|\bengine\b)\s*:?\s*([^\r\n,;/•]{2,80})",
            r"\b(\d(?:[.,]\d)\s*(?:l|lit(?:er|re|ra|rov)?))\b",
        ),
    )
    fuel = _description_capture(
        text,
        (r"(?:palivo|fuel)\s*:?\s*([^\r\n,;]{2,40})",),
    )
    transmission = _description_capture(
        text,
        (
            r"(?:prevodovka|převodovka|transmission|gearbox)\s*:?\s*([^\r\n,;]{2,80})",
            r"\b((?:automatick(?:á|a|ou)|automat|automatic|manuáln(?:a|ou)|manualn(?:a|i|ou)|manual|CVT|DCT|DSG)(?:\s+(?:prevodovk|převodovk)(?:a|ou))?)\b",
        ),
    )
    folded_transmission_text = _fold_listing_text(text)
    manual_marker = re.search(
        r"\bmanualn(?:a|i|ou)\s+prevodovk(?:a|ou)\b|\bmanual\s+\d+\s*(?:rychlost|stupn|speed)",
        folded_transmission_text,
        re.IGNORECASE,
    )
    manual_gears = re.search(
        r"\b([4-9])\s*(?:rychlostnich|rychlostni|rychlosti|stupnov|stupnova|speed)",
        folded_transmission_text,
        re.IGNORECASE,
    )
    if manual_marker:
        transmission = (
            f"Manuálna {manual_gears.group(1)}-st."
            if manual_gears else "Manuálna"
        )
    transmission = re.sub(
        r"\([^)]*\)",
        lambda match: "" if "mame aj" in _fold_listing_text(match.group(0)) else match.group(0),
        transmission,
    ).strip()
    power = _description_capture(
        text,
        (
            r"(?:v[ýy]kon|power)\s*:?\s*(\d{2,4}\s*kW)",
            r"\b(\d{2,4}\s*kW)\b",
        ),
    )
    if not power:
        ps_match = re.search(r"\b(\d{2,3})\s*(PS|HP)\b", text, re.IGNORECASE)
        if ps_match:
            power_value = int(ps_match.group(1))
            factor = 0.73549875 if ps_match.group(2).lower() == "ps" else 0.745699872
            power = (
                f"{round(power_value * factor)} kW "
                f"({power_value} {ps_match.group(2).upper()})"
            )
    cleaned_text = re.sub(
        r"\([^)]*\)",
        lambda match: "" if "mame aj" in _fold_listing_text(match.group(0)) else match.group(0),
        text,
    )
    combined = _fold_listing_text(f"{title}\n{cleaned_text}")
    explicit_drive = re.search(
        r"(?:s\s+pohonom|pohon(?:om)?|drive)\s*:?\s*(fwd|4x4|4wd|awd|quattro|allrad|rwd)",
        combined,
        re.IGNORECASE,
    )
    drive_token = explicit_drive.group(1).lower() if explicit_drive else ""
    drive = (
        "Predný"
        if drive_token == "fwd"
        else "Zadný"
        if drive_token == "rwd"
        else "4x4"
        if drive_token or re.search(r"\b(?:4x4|4wd|awd|quattro|allrad|stvorkol(?:ka|kou|ky))\b", combined)
        else "Predný"
        if re.search(r"\b(?:fwd|predny\s+(?:pohon|nahon)|predokolka)\b", combined)
        else ""
    )
    return {
        "mileage": mileage,
        "mileage_km": mileage_km,
        "year": year,
        "engine": engine,
        "fuel": fuel,
        "transmission": transmission,
        "power": power,
        "drive": drive,
    }


def _listing_context_object(car_info_text, description_chars=FINAL_LISTING_DESCRIPTION_CHARS):
    parsed = parse_car_info_md(car_info_text)
    specs = parsed.get("specs") or {}
    description_facts = _description_listing_facts(
        parsed.get("description"), parsed.get("title")
    )
    specification_mileage = (
        specs.get("Mileage")
        or specs.get("Nájazd")
        or specs.get("Najazd")
        or ""
    )
    specification_mileage_km = _mileage_km_value(specification_mileage)
    description_mileage_km = description_facts.get("mileage_km")
    mileage = specification_mileage or description_facts["mileage"] or ""
    if (
        isinstance(description_mileage_km, int)
        and description_mileage_km >= 10000
        and isinstance(specification_mileage_km, int)
        and specification_mileage_km < 1000
    ):
        mileage = description_facts["mileage"]
    transmission = (
        specs.get("Transmission")
        or specs.get("Prevodovka")
        or description_facts["transmission"]
        or ""
    )
    # Structured scraping can retain only the words following "prevodovka"
    # (for example "6 stupnova"). Prefer the explicit family recovered from
    # the complete seller description when the structured value has no family.
    transmission_has_kind = bool(re.search(
        r"\b(?:manual|automat|dsg|dct|cvt|edc|s-tronic|tiptronic)\w*\b",
        _fold_listing_text(transmission),
        re.IGNORECASE,
    ))
    if description_facts["transmission"] and not transmission_has_kind:
        transmission = description_facts["transmission"]
    transmission = re.sub(
        r"\([^)]*\)",
        lambda match: "" if "mame aj" in _fold_listing_text(match.group(0)) else match.group(0),
        str(transmission),
    ).strip()
    fuel = specs.get("Fuel") or specs.get("Palivo") or description_facts["fuel"] or ""
    color = specs.get("Color") or specs.get("Farba") or ""
    gross_price_eur = _explicit_gross_price_eur(parsed.get("description"))
    return {
        "title": parsed.get("title"),
        "price": parsed.get("price"),
        "asking_price_gross_eur": gross_price_eur,
        "currency": parsed.get("currency"),
        "vin": parsed.get("vin"),
        "mileage": mileage,
        "mileage_km": _mileage_km_value(mileage),
        "year": specs.get("Year") or specs.get("Rok") or description_facts["year"] or "",
        "engine": specs.get("Engine") or specs.get("Motor") or description_facts["engine"] or "",
        "power": specs.get("Engine Power") or specs.get("Výkon") or description_facts["power"] or "",
        "fuel": fuel,
        "color": color,
        "transmission": transmission,
        "drive": description_facts["drive"] or specs.get("Drivetrain") or specs.get("Pohon") or "",
        "source_url": parsed.get("source_url"),
        "scraped_at": parsed.get("scraped_at"),
        "photos_count": parsed.get("photos_count"),
        "location": parsed.get("location"),
        "specs": _limit_mapping(specs, max_items=28, max_chars=180),
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




FORBIDDEN_REPORT_CLAIMS = report_validation.FORBIDDEN_REPORT_CLAIMS
INTERNAL_REPORT_LABELS = report_validation.INTERNAL_REPORT_LABELS
REQUIRED_REPORT_SECTION_KEYS = report_validation.REQUIRED_REPORT_SECTION_KEYS
REPORT_HEADING_EMOJIS = report_validation.REPORT_HEADING_EMOJIS
UNVERIFIED_URL_HOSTS = report_validation.UNVERIFIED_URL_HOSTS
_schema_required_fields = report_validation.schema_required_fields
_soft_validate_json_contract = report_validation.soft_validate_json_contract
_normalize_claim_text = report_validation.normalize_claim_text
_normalize_report_structure_text = report_validation.normalize_report_structure_text
_normalize_heading_key = report_validation.normalize_heading_key
_is_verified_public_url = report_validation.is_verified_public_url
_iter_markdown_links = report_validation.iter_markdown_links
_markdown_links = report_validation.markdown_links
_report_section_keys = report_validation.report_section_keys
_soft_validate_final_report = report_validation.soft_validate_final_report
_ensure_end_analysis_marker = report_validation.ensure_end_analysis_marker


def _write_validation_warnings(slug_dir, warnings):
    return report_validation.write_validation_warnings(
        slug_dir,
        warnings,
        log=safe_log,
    )


def _vision_observation_bullet(item):
    if not isinstance(item, dict):
        text = str(item or "").strip()
        return f"- {text}" if text else ""
    label = str(item.get("photo_label") or "").strip()
    observation = str(item.get("observation") or item.get("red_flag") or "").strip()
    relevance = str(
        item.get("buyer_relevance")
        or item.get("why_it_matters")
        or item.get("notes")
        or ""
    ).strip()
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


def _is_useful_photo_limitation(value):
    """Keep only specific image-quality limits that matter to a buyer check."""
    normalized = _normalize_claim_text(value)
    generic_fragments = (
        "mierne tmav",
        "slightly dark",
        "vybrane uhly",
        "selected angles",
        "limited angles",
        "obmedzene uhly",
        "kompletne posudenie vozidla",
        "complete assessment of the vehicle",
        "fully assess the vehicle",
        "fotografie nepokryvaju vsetky",
        "photos do not cover all",
        "nie je mozne kompletne posudit",
        "cannot be completely assessed",
    )
    return bool(normalized) and not any(fragment in normalized for fragment in generic_fragments)


def _photo_analysis_lines_from_vision(vision_result_json, output_language="sk"):
    data = _safe_model_json(vision_result_json)
    language = _demo_output_language(output_language)
    if str(data.get("analysis_status") or "").strip().lower() == "unavailable":
        return [
            _localized(language,
                sk="- Fotografie boli v inzeráte poskytnuté, ale automatická vizuálna analýza nebola spoľahlivo dokončená. Nejde o chýbajúce fotografie ani o negatívne zistenie o stave auta; zábery treba vyhodnotiť manuálne alebo analýzu zopakovať.",
                cs="- Fotografie byly v inzerátu poskytnuty, ale automatická vizuální analýza nebyla spolehlivě dokončena. Nejde o chybějící fotografie ani o negativní zjištění o stavu auta; snímky je třeba vyhodnotit ručně nebo analýzu zopakovat.",
                en="- Listing photos were provided, but automatic visual analysis did not complete reliably. This is neither missing-photo evidence nor a negative finding about the car; review the images manually or retry the analysis.",
            )
        ]
    if not data.get("photos_provided"):
        return [
            _localized(language,
                sk="- Fotografie neboli poskytnuté alebo neboli spoľahlivo analyzovateľné.",
                cs="- Fotografie nebyly poskytnuty nebo je nebylo možné spolehlivě analyzovat.",
                en="- Photos were not provided or could not be analyzed reliably.",
            )
        ]

    seen = set()

    def unique_bullets(items):
        bullets = []
        for item in items or []:
            bullet = _vision_observation_bullet(item)
            if isinstance(item, dict):
                key = _normalize_claim_text(
                    " ".join(
                        str(value or "")
                        for value in (
                            item.get("photo_label"),
                            item.get("observation") or item.get("red_flag"),
                        )
                    )
                )
            else:
                key = _normalize_claim_text(bullet)
            if bullet and key not in seen:
                seen.add(key)
                bullets.append(bullet)
        return bullets

    supported = [item for item in data.get("supported_observations") or [] if isinstance(item, dict)]
    exterior_types = {"body", "paint", "corrosion", "wheels", "tires"}
    interior_types = {"interior", "dashboard", "odometer", "documents", "equipment"}
    exterior_supported = [item for item in supported if str(item.get("type") or "").lower() in exterior_types]
    interior_supported = [item for item in supported if str(item.get("type") or "").lower() in interior_types]

    exterior = unique_bullets((data.get("exterior_observations") or []) + exterior_supported)
    interior_items = list(data.get("interior_observations") or []) + interior_supported
    interior_items.extend(
        item
        for item in data.get("dashboard_or_warning_lights") or []
        if _high_confidence_dashboard_note(item)
    )
    interior = unique_bullets(interior_items)
    red_flags = unique_bullets(data.get("visible_red_flags") or [])

    lines = [_localized(language, sk="### Exteriér", cs="### Exteriér", en="### Exterior")]
    if exterior:
        lines.extend(exterior)
    else:
        lines.append(
            _localized(language,
                sk="- Exteriér nie je na poskytnutých fotografiách dostatočne detailný na spoľahlivé hodnotenie.",
                cs="- Exteriér není na poskytnutých fotografiích dostatečně detailní pro spolehlivé hodnocení.",
                en="- The exterior is not shown in enough detail for a reliable assessment.",
            )
        )

    lines.extend(["", _localized(language, sk="### Interiér", cs="### Interiér", en="### Interior")])
    if interior:
        lines.extend(interior)
    else:
        lines.append(
            _localized(language,
                sk="- Interiér nie je na poskytnutých fotografiách dostatočne detailný na spoľahlivé hodnotenie.",
                cs="- Interiér není na poskytnutých fotografiích dostatečně detailní pro spolehlivé hodnocení.",
                en="- The interior is not shown in enough detail for a reliable assessment.",
            )
        )

    lines.extend(
        [
            "",
            _localized(language, sk="### Červené vlajky a limity fotografií", cs="### Varovné signály a omezení fotografií", en="### Red Flags and Photo Limitations"),
        ]
    )
    if red_flags:
        lines.extend(red_flags)
    else:
        lines.append(
            _localized(language,
                sk="- Na analyzovaných fotografiách neboli označené zjavné vážne vizuálne poškodenia. Fotografie však nevylučujú skryté chyby, staršie opravy ani koróziu mimo záberu.",
                cs="- Na analyzovaných fotografiích nebyla označena zjevná vážná vizuální poškození. Fotografie však nevylučují skryté vady, starší opravy ani korozi mimo záběr.",
                en="- No obvious serious visual damage was flagged in the analyzed photos. Photos cannot exclude hidden defects, earlier repairs, or corrosion outside the frame.",
            )
        )

    view_coverage = data.get("view_coverage") if isinstance(data.get("view_coverage"), dict) else {}
    missing_views = []
    for key, label_sk, label_en in (
        ("engine_bay", "motorový priestor", "engine bay"),
        ("underbody", "podvozok", "underbody"),
    ):
        if str(view_coverage.get(key) or "").strip().lower() == "missing":
            missing_views.append(_localized(language, sk=label_sk, cs={"motorový priestor": "motorový prostor", "podvozok": "podvozek"}.get(label_sk, label_sk), en=label_en))
    if missing_views:
        if language == "sk":
            lines.append(
                "- **Chýbajúce pohľady:** "
                + ", ".join(missing_views)
                + " nie sú na fotkách viditeľné, preto ich stav nemožno spoľahlivo posúdiť."
            )
        elif language == "cs":
            lines.append(
                "- **Chybějící pohledy:** "
                + ", ".join(missing_views)
                + " nejsou na fotografiích viditelné, proto jejich stav nelze spolehlivě posoudit."
            )
        else:
            lines.append(
                "- **Missing views:** "
                + ", ".join(missing_views)
                + " are not visible in the photos, so their condition cannot be assessed reliably."
            )

    for limitation in (data.get("photo_limitations") or [])[:4]:
        text = str(limitation or "").strip()
        if not _is_useful_photo_limitation(text):
            continue
        if any(_normalize_claim_text(text) in _normalize_claim_text(line) for line in lines):
            continue
        label = _localized(language, sk="Obmedzenie", cs="Omezení", en="Limitation")
        lines.append(f"- **{label}:** {text}")

    return lines


def _replace_photo_analysis_section(report_text, vision_result_json, output_language="sk"):
    body_lines = _photo_analysis_lines_from_vision(vision_result_json, output_language)
    if not body_lines:
        return report_text
    heading = _localized(output_language, sk="## 📸 Analýza fotografií", cs="## 📸 Analýza fotografií", en="## 📸 Photo Analysis")
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


def _quick_summary_scorecard_markdown(risk_score_json, output_language="sk"):
    data = _safe_model_json(risk_score_json)
    card = data.get("buyer_scorecard") if isinstance(data.get("buyer_scorecard"), dict) else {}
    scores = card.get("scores") if isinstance(card.get("scores"), dict) else {}
    required = (
        "listing_transparency",
        "market_position",
        "engine_profile",
        "transmission_profile",
        "visual_condition",
        "service_readiness",
    )
    if any(key not in scores or (scores[key] is not None and not isinstance(scores[key], (int, float))) for key in required):
        return ""
    language = _demo_output_language(output_language)
    if language == "en":
        labels = (
            ("Listing transparency", "listing_transparency"),
            ("Price vs. market", "market_position"),
            ("Engine profile", "engine_profile"),
            ("Transmission and drivetrain", "transmission_profile"),
            ("Visual condition", "visual_condition"),
            ("Service readiness", "service_readiness"),
        )
        confidence_labels = {"HIGH": "High", "MEDIUM": "Medium", "LOW": "Low"}
        unavailable_label = "Insufficient data"
        title, area, score_label = "### Analysis score", "Area", "Score"
        overall_label, confidence_label = "Overall score", "Analysis reliability"
        note = "100 means a more favorable profile. Areas without sufficient evidence are not scored or included in the weighted average. This is a screening aid, not a technical inspection."
    elif language == "cs":
        labels = (
            ("Transparentnost inzerátu", "listing_transparency"),
            ("Cena vůči trhu", "market_position"),
            ("Profil motoru", "engine_profile"),
            ("Převodovka a pohon", "transmission_profile"),
            ("Vizuální stav", "visual_condition"),
            ("Servisní připravenost", "service_readiness"),
        )
        confidence_labels = {"HIGH": "Vysoká", "MEDIUM": "Střední", "LOW": "Nízká"}
        unavailable_label = "Nedostatek údajů"
        title, area, score_label = "### Skóre analýzy", "Oblast", "Skóre"
        overall_label, confidence_label = "Celkové skóre", "Spolehlivost analýzy"
        note = "100 znamená příznivější profil. Oblasti bez dostatečných podkladů se nebodují ani nezapočítávají do váženého průměru. Jde o screening, ne technickou prohlídku."
    else:
        labels = (
            ("Transparentnosť inzerátu", "listing_transparency"),
            ("Cena voči trhu", "market_position"),
            ("Profil motora", "engine_profile"),
            ("Prevodovka a pohon", "transmission_profile"),
            ("Vizuálny stav", "visual_condition"),
            ("Servisná pripravenosť", "service_readiness"),
        )
        confidence_labels = {"HIGH": "Vysoká", "MEDIUM": "Stredná", "LOW": "Nízka"}
        unavailable_label = "Nedostatok údajov"
        title, area, score_label = "### Skóre analýzy", "Oblasť", "Skóre"
        overall_label, confidence_label = "Celkové skóre", "Spoľahlivosť analýzy"
        note = "100 znamená priaznivejší profil. Oblasti bez dostatočných podkladov sa nebodujú ani nezapočítajú do váženého priemeru. Ide o skríning, nie technickú prehliadku."
    rows = [title, "", f"| {area} | {score_label} |", "|---|---:|"]
    rows.extend(
        f"| {label} | {int(round(scores[key]))}/100 |"
        if scores[key] is not None
        else f"| {label} | **{unavailable_label}** |"
        for label, key in labels
    )
    overall = int(round(float(card.get("overall_score") or 0)))
    confidence = confidence_labels.get(str(card.get("confidence") or "MEDIUM").upper(), confidence_labels["MEDIUM"])
    rows.extend(
        (
            f"| **{overall_label}** | **{overall}/100** |",
            f"| **{confidence_label}** | **{confidence}** |",
            "",
            f"> {note}",
        )
    )
    return "\n".join(rows)


def _replace_quick_summary_scorecard(report_text, risk_score_json, output_language="sk"):
    """Append the deterministic scorecard to the quick-summary section."""
    scorecard = _quick_summary_scorecard_markdown(risk_score_json, output_language)
    if not scorecard:
        return report_text
    lines = str(report_text or "").splitlines()
    start = None
    end = len(lines)
    for index, line in enumerate(lines):
        if not re.match(r"^\s*##\s+", line):
            continue
        key = _normalize_heading_key(re.sub(r"^\s*##\s+", "", line))
        if start is None and ("rychle zhrnutie" in key or "quick summary" in key):
            start = index
            continue
        if start is not None:
            end = index
            break
    if start is None:
        return report_text
    # Final synthesis is instructed not to create this block, but remove one
    # if a provider emitted it anyway so the backend remains the sole source.
    section = lines[start + 1 : end]
    cleaned = []
    skipping = False
    for line in section:
        if re.match(r"^\s*###\s+", line):
            key = _normalize_heading_key(re.sub(r"^\s*###\s+", "", line))
            skipping = "skore analyzy" in key or "analysis score" in key
            if skipping:
                continue
        if skipping:
            continue
        cleaned.append(line)
    replacement = lines[: start + 1] + cleaned
    while replacement and not replacement[-1].strip():
        replacement.pop()
    replacement.extend(("", scorecard, ""))
    replacement.extend(lines[end:])
    return "\n".join(replacement).rstrip() + "\n"


def _move_pros_cons_after_quick_summary(report_text):
    """Move the existing pros/cons sections directly below quick summary."""
    lines = str(report_text or "").splitlines()
    starts = [
        index
        for index, line in enumerate(lines)
        if re.match(r"^\s*##\s+", line)
    ]
    if not starts:
        return report_text
    preamble = lines[: starts[0]]
    sections = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        key = _normalize_heading_key(re.sub(r"^\s*##\s+", "", lines[start]))
        sections.append({"key": key, "lines": lines[start:end]})

    quick = next(
        (section for section in sections if "rychle zhrnutie" in section["key"] or "quick summary" in section["key"]),
        None,
    )
    pros = next(
        (section for section in sections if section["key"] == "klady" or section["key"] == "pros"),
        None,
    )
    cons = next(
        (
            section
            for section in sections
            if ("zapory" in section["key"] and "rizik" in section["key"])
            or section["key"] in {"cons", "cons risks", "cons risk"}
        ),
        None,
    )
    if quick is None or pros is None or cons is None:
        return report_text

    reordered = []
    for section in sections:
        if section is quick:
            reordered.extend((quick, pros, cons))
        elif section is pros or section is cons:
            continue
        else:
            reordered.append(section)
    output = preamble[:]
    for section in reordered:
        output.extend(section["lines"])
    return "\n".join(output).rstrip() + "\n"


def _compact_text_research_for_final(text_research_json_text):
    data = _safe_model_json(text_research_json_text)
    component_identity = _compact_value(data.get("component_identity"))
    if isinstance(component_identity, dict):
        # Identity labels and resolution belong in final synthesis, but its
        # discovery source registry must not bypass Research V2 evidence policy.
        component_identity.pop("sources", None)
    all_comparables = data.get("market_comparables")
    if not isinstance(all_comparables, list):
        all_comparables = []
    verified_comparables = []
    public_candidates = sorted(
        (
            item
            for item in all_comparables
            if isinstance(item, dict) and is_customer_facing_market_comparable(item)
        ),
        key=customer_link_priority,
        reverse=True,
    )
    for item in public_candidates[:5]:
        if item.get("verified_url") is not True:
            continue
        sanitized = _sanitize_source_item(item)
        if isinstance(sanitized, dict) and sanitized.get("verified_url") is True:
            verified_comparables.append(sanitized)

    market_assessment = _compact_value(data.get("market_assessment"))
    original_market = market_assessment if isinstance(market_assessment, dict) else {}
    legacy_verified_background = any(
        isinstance(item, dict)
        and item.get("verified_url") is True
        and _is_verified_public_url(str(item.get("source_url") or ""))
        for item in all_comparables
    )
    benchmark_available = (
        original_market.get("benchmark_available") is True
        and int(original_market.get("benchmark_comparable_count") or 0) >= 3
    ) or (
        "benchmark_available" not in original_market and legacy_verified_background
    )
    if not benchmark_available:
        original = market_assessment if isinstance(market_assessment, dict) else {}
        has_backend_search_summary = isinstance(
            data.get("market_search_summary"), dict
        )
        market_assessment = {
            "available": bool(original.get("available"))
            if has_backend_search_summary
            else False,
            "advertised_price_eur": original.get("advertised_price_eur"),
            "observed_market_low_eur": None,
            "observed_market_high_eur": None,
            "observed_market_average_eur": None,
            "comparable_count": original.get("comparable_count", 0)
            if has_backend_search_summary
            else 0,
            "public_comparable_count": len(verified_comparables),
            "benchmark_comparable_count": 0,
            "benchmark_available": False,
            "summary": original.get("summary")
            or "Automatic search could not assemble a verified sample.",
            "limitations": "Current market comparison requires manual verification.",
            "negotiation_anchor_eur": None,
            "negotiation_reason": "",
            "price_view": "requires_manual_verification",
        }
    elif isinstance(market_assessment, dict):
        # Aggregate market figures are based on all unique relevant ads,
        # including foreign background evidence. Only the concrete records
        # below are restricted to customer-facing SK/CZ marketplaces.
        market_assessment["public_comparable_count"] = len(verified_comparables)
        if not verified_comparables:
            market_assessment["public_comparable_note"] = (
                "No supported Slovak or Czech detail-page ad is available for public linking; "
                "do not list or link individual foreign/background offers."
            )
    return {
        "research_status": data.get("research_status", "completed"),
        "technical_research_available": data.get("research_status") == "completed",
        "evidence_summary": _compact_value(data.get("evidence_summary")),
        "component_identity": component_identity,
        "listing_facts": _compact_value(data.get("listing_facts")),
        "seller_claims": _limited_list(data.get("seller_claims"), 8, prefer_concerns=True),
        "missing_or_uncertain_data": _limited_list(data.get("missing_or_uncertain_data"), 6, prefer_concerns=True),
        "data_conflicts": _limited_list(data.get("data_conflicts"), 6, prefer_concerns=True),
        "consistency_checks": _limited_list(data.get("consistency_checks"), 6, prefer_concerns=True),
        "vin_check": _compact_value(data.get("vin_check")),
        "safety_and_recall": _compact_value(data.get("safety_and_recall")),
        "knowledge_base_findings": [],
        "web_research_findings": [
            _sanitize_source_item(item)
            for item in _limited_list(data.get("web_research_findings"), 8, prefer_concerns=True)
        ],
        "technical_risks": [
            _sanitize_source_item(item)
            for item in _limited_list(data.get("technical_risks"), 8, prefer_concerns=True)
        ],
        "market_assessment": market_assessment,
        "market_comparables": verified_comparables,
        "expected_costs": _limited_list(data.get("expected_costs"), 10, prefer_concerns=True),
        # Source-backed technical_risks already carry the actionable evidence.
        # Free-form risk flags have no source_ids and must not reintroduce
        # filtered intervals, costs, or mileage-based defect assumptions.
        "text_research_risk_flags": [],
        # The final report does not print citations or a source registry. Keep
        # the complete registry in grok_research.json for audit/calibration,
        # but do not spend final-synthesis context on it.
        "sources_used": [],
        "parse_error": data.get("_parse_error", False),
        "raw_preview": data.get("_raw_preview"),
    }


def _final_payload_size(payload):
    return len(_compact_json_for_prompt(payload))


def _trim_final_context_payload(payload, max_chars=FINAL_CONTEXT_MAX_CHARS):
    """Shrink optional evidence lists while retaining a valid JSON payload."""
    list_paths = [
        ("text_research", "web_research_findings", 4),
        ("text_research", "technical_risks", 5),
        ("text_research", "expected_costs", 4),
        ("text_research", "seller_claims", 3),
        ("text_research", "missing_or_uncertain_data", 3),
        ("text_research", "data_conflicts", 2),
        ("text_research", "consistency_checks", 3),
        ("text_research", "market_comparables", 3),
        ("text_research", "text_research_risk_flags", 3),
        ("vision", "exterior_observations", 4),
        ("vision", "interior_observations", 3),
        ("vision", "supported_observations", 3),
        ("vision", "missing_views", 3),
        ("vision", "visible_red_flags", 3),
        ("backend_risk_score", "vehicle_specific_findings", 4),
        ("backend_risk_score", "model_level_inspection_points", 4),
        ("backend_risk_score", "missing_information", 3),
        ("backend_risk_score", "buyer_actions", 4),
    ]
    while _final_payload_size(payload) > max_chars:
        candidates = []
        for section, key, minimum in list_paths:
            values = payload.get(section, {}).get(key)
            if isinstance(values, list) and len(values) > minimum:
                candidates.append((len(_compact_json_for_prompt(values)), section, key))
        if not candidates:
            break
        _size, section, key = max(candidates)
        payload[section][key].pop()

    # If the structured lists are already at their quality floor, shorten only
    # low-priority explanatory prose; never cut listing facts or verdict data.
    if _final_payload_size(payload) > max_chars:
        for section, key in (
            ("text_research", "evidence_summary"),
            ("text_research", "market_assessment"),
            ("vision", "visual_verdict"),
        ):
            value = payload.get(section, {}).get(key)
            if isinstance(value, str):
                payload[section][key] = _clip_text(value, 280)
    return payload


def _compact_vision_for_final(vision_result_json):
    data = _safe_model_json(vision_result_json)
    useful_photo_limitations = [
        item
        for item in data.get("photo_limitations") or []
        if _is_useful_photo_limitation(item)
    ]
    return {
        "photos_provided": data.get("photos_provided"),
        "photo_coverage": _compact_value(data.get("photo_coverage")),
        "odometer": _compact_value(data.get("odometer")),
        "view_coverage": _compact_value(data.get("view_coverage")),
        "supported_observations": _balanced_vision_list(data.get("supported_observations"), 10),
        "missing_views": _limited_list(data.get("missing_views"), 8, 160),
        "photo_limitations": _limited_list(useful_photo_limitations, 5, 220),
        "exterior_observations": _balanced_vision_list(data.get("exterior_observations"), 8),
        "interior_observations": _balanced_vision_list(data.get("interior_observations"), 6),
        "dashboard_or_warning_lights": _balanced_vision_list(data.get("dashboard_or_warning_lights"), 4),
        "visible_red_flags": _limited_list(data.get("visible_red_flags"), 6, prefer_concerns=True),
        "mileage_wear_consistency": _compact_value(data.get("mileage_wear_consistency")),
        "visual_verdict": _clip_text(data.get("visual_verdict"), 220),
        "visible_vin": _clip_text(data.get("visible_vin"), 40),
        "parse_error": data.get("_parse_error", False),
        "raw_preview": data.get("_raw_preview"),
    }


def _compact_risk_score_for_final(risk_score_json):
    data = _safe_model_json(risk_score_json)
    if data.get("schema_version") == 2:
        return {
            "allowed_final_verdict": data.get("allowed_final_verdict"),
            "decision_status": data.get("decision_status"),
            "vehicle_specific_findings": _limited_list(data.get("vehicle_specific_findings"), 10, 260, prefer_concerns=True),
            "model_level_inspection_points": _limited_list(data.get("model_level_inspection_points"), 8, 220, prefer_concerns=True),
            "missing_information": _limited_list(data.get("missing_information"), 8, 160),
            "buyer_actions": _limited_list(data.get("buyer_actions"), 8, 220, prefer_concerns=True),
        }
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
    text_research_json_text,
    vision_result_json,
    risk_score_json,
    web_research_text,
    image_meta=None,
    vin_light_decode=None,
):
    text_research_data = _safe_model_json(text_research_json_text)
    research_unavailable = text_research_data.get("research_status") in {"unavailable", "limited"}
    has_structured_web_findings = bool(text_research_data.get("web_research_findings"))
    compact_payload = {
        "output_language": _demo_output_language(output_language),
        "listing": _listing_context_object(car_info_text),
        "text_research": _compact_text_research_for_final(text_research_json_text),
        "vision": _compact_vision_for_final(vision_result_json),
        "image_payload": _compact_value(image_meta or {}),
        "vin_light_check": _compact_value(vin_light_decode or {}),
        "backend_risk_score": _compact_risk_score_for_final(risk_score_json),
        # Structured web findings already carry the useful conclusions. The
        # raw grounded excerpt is retained only when parsing failed or yielded
        # no findings, avoiding duplicate source prose in final synthesis.
        "web_research": (
            {"verified_source_lines": [], "unverified_source_notes": [], "evidence_excerpt": ""}
            if has_structured_web_findings or research_unavailable
            else _web_research_context(web_research_text, max_chars=1400)
        ),
    }
    compact_payload = _trim_final_context_payload(compact_payload)
    return (
        "Use only this compact structured context. "
        "Do not infer missing details from omitted raw text. "
        + (
            "Technical research is unavailable: do not invent model-specific risks, component codes, "
            "service intervals, recall findings, or repair costs; report the limitation instead. "
            if research_unavailable
            else ""
        )
        + "If image_payload.full_gallery_included is true, do not describe a view as missing from the listing "
        "unless vision.view_coverage says that view is absent from the full-gallery overview.\n\n"
        + _compact_json_for_prompt(compact_payload)
    )


def _no_photos_vision_result(message="Fotografie neboli poskytnute."):
    return json.dumps(
        {
            "source_role": "vision",
            "analysis_status": "not_provided",
            "photos_provided": False,
            "photo_coverage": {
                "coverage_mode": "none",
                "original_count": 0,
                "analyzed_count": 0,
                "full_gallery_overview": False,
                "notes": [message],
            },
            "odometer": {
                "visible": False,
                "reading_km": None,
                "photo_label": "",
                "confidence": None,
                "notes": message,
            },
            "view_coverage": {
                "exterior": "unknown",
                "interior": "unknown",
                "dashboard": "unknown",
                "engine_bay": "unknown",
                "tires": "unknown",
                "underbody": "unknown",
            },
            "supported_observations": [],
            "missing_views": [],
            "photo_limitations": [message],
            "exterior_observations": [],
            "interior_observations": [],
            "dashboard_or_warning_lights": [],
            "visible_red_flags": [],
            "mileage_wear_consistency": {
                "assessment": "cannot_assess",
                "explanation": message,
                "confidence": "Nízka",
            },
            "visual_verdict": "Nedostatocne fotografie",
            "visible_vin": "",
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


def _stream_text_model(
    provider,
    api_key,
    system_prompt,
    user_content,
    listing_slug=None,
    *,
    phase=None,
    max_output_tokens=None,
    temperature=None,
):
    if provider == "grok":
        yield from analyze_with_grok(
            api_key,
            system_prompt,
            user_content,
            listing_slug=listing_slug,
            phase=phase,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
        )
        return
    if provider == "openrouter":
        yield from analyze_with_openrouter(
            api_key,
            system_prompt,
            user_content,
            listing_slug=listing_slug,
            phase=phase,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
        )
        return

    yield from _call_gemini(
        api_key,
        system_prompt,
        user_content,
        image_data_list=None,
        listing_slug=listing_slug,
        phase=phase,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
    )


def _model_display_name(provider):
    if provider == "grok":
        return "Grok"
    if provider == "openrouter":
        return "OpenRouter"
    return "Gemini"


def _inject_photo_vin_into_pipeline(slug_dir, car_info_text, text_research_json_text, vision_result_json, car_info_path):
    """
    If the vision model found a visible_vin in photos but the listing text has no VIN,
    inject it into the pipeline by:
    1. Updating text_research_json_text (in-memory variable — caller must handle assignment)
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
        atomic_write_text(car_info_path, updated_car_info)
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


def _pipeline_save_kb_blocks(blocks):
    """Keep demo analysis stateless while retaining private-mode KB support."""
    if _runtime_config("DEMO_MODE", DEMO_MODE) and _runtime_config(
        "DEMO_SKIP_KB",
        DEMO_SKIP_KB,
    ):
        return []
    return _save_kb_blocks(blocks)


def _pipeline_calculate_risk_score(
    text_research, vision, listing_text=None, *, output_language="sk"
):
    from risk_scorer import calculate_hotfixed_risk_score

    configured = (
        current_app.config.get("RISK_SCORER_V2_ACTIVE", False)
        if has_app_context()
        else SERVER_CONFIG.risk_scorer_v2_active
    )
    active = configured is True or str(configured).strip().lower() in {"1", "true", "yes", "on"}
    if not active:
        return calculate_hotfixed_risk_score(
            text_research,
            vision,
            listing_text,
            output_language=output_language,
        )
    try:
        from risk_scorer_v2 import calculate_risk_score_v2

        return calculate_risk_score_v2(
            text_research,
            vision,
            listing_text,
            output_language=output_language,
        )
    except Exception as exc:
        safe_log(f"Risk scorer v2 failed; using safe yellow fallback: {exc}")
        from risk_scorer_v2 import safe_yellow_fallback

        return safe_yellow_fallback(str(exc), output_language=output_language)


def _analysis_pipeline_dependencies():
    """Compose the analysis service without exposing Flask state inside it."""
    return AnalysisPipelineDependencies(
        repository=_job_repository(),
        prompt_dir=Path(SCRIPT_DIR) / "prompts",
        build_final_synthesis_context=_build_final_synthesis_context,
        build_text_research_context=_build_text_research_context,
        compact_json_for_prompt=_compact_json_for_prompt,
        output_language=_demo_output_language,
        inject_photo_vin=_inject_photo_vin_into_pipeline,
        listing_context_text=_listing_context_text,
        model_display_name=_model_display_name,
        no_photos_vision_result=_no_photos_vision_result,
        normalize_report_headings=_normalize_report_headings,
        public_analysis_markdown=_public_analysis_markdown,
        replace_photo_analysis_section=_replace_photo_analysis_section,
        replace_quick_summary_scorecard=_replace_quick_summary_scorecard,
        move_pros_cons_after_quick_summary=_move_pros_cons_after_quick_summary,
        save_kb_blocks=_pipeline_save_kb_blocks,
        safe_model_json=_safe_model_json,
        strip_kb_section=_strip_kb_section,
        calculate_risk_score=_pipeline_calculate_risk_score,
        prepare_images=prepare_llm_images,
        stream_text_model=_stream_text_model,
        count_input_tokens=count_gemini_tokens,
        log=safe_log,
    )


def _multi_model_analysis_events(slug, grok_key, gemini_keys, output_language="sk", openrouter_key=""):
    """Compatibility facade for callers of the legacy orchestration helper."""
    yield from multi_model_analysis_events(
        slug,
        grok_key,
        gemini_keys,
        output_language,
        openrouter_key=openrouter_key,
        dependencies=_analysis_pipeline_dependencies(),
    )

def _demo_analysis_events(slug, output_language="sk"):
    _job_repository().write_json(
        slug,
        "analysis_metadata.json",
        {
            "schema_version": 1,
            "output_language": _demo_output_language(output_language),
        },
    )
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
        yield from multi_model_analysis_events(
            slug,
            grok_key,
            keys,
            output_language,
            openrouter_key=openrouter_key,
            dependencies=_analysis_pipeline_dependencies(),
        )
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
    runtime_state = _runtime_state()
    if not runtime_state.jobs.acquire(blocking=False):
        return jsonify({"error": "Another demo analysis is already running. Try again in a moment."}), 429

    def generate():
        try:
            _set_current_progress(status="Starting analysis...", line="Starting analysis...", done=False, reset=True)
            _cleanup_old_demo_jobs()
            for event_text in generator_factory():
                _track_demo_sse_progress(event_text)
                yield event_text
        finally:
            runtime_state.jobs.release()

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
        return jsonify({"error": "Automatic mobile.de scraping is not available. Use manual listing mode.", "unsupported": True}), 400
    if not _is_supported_scraper_url(url):
        return jsonify({"error": "This marketplace is not supported for automatic scraping. Use manual listing mode.", "unsupported": True}), 400

    def events():
        main_py = os.path.join(SCRIPT_DIR, "main.py")
        from main import derive_slug
        slug = derive_slug(url)
        env = os.environ.copy()
        env["SCRAPPER_AUTA_DIR"] = _runtime_auta_dir()
        env.setdefault(
            "DEMO_MAX_SCRAPED_IMAGES",
            str(_runtime_config("DEMO_MAX_SCRAPED_IMAGES", DEMO_MAX_SCRAPED_IMAGES)),
        )
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
        repository = _job_repository()
        if not repository.artifact_path(slug, "car_info.md").exists():
            yield f"data: {json.dumps({'error': 'Scraper finished but did not create listing data.'})}\n\n"
            yield "data: [DONE]\n\n"
            return
        # Validate that scraped content looks like a car listing before spending tokens
        slug_dir = str(repository.job_dir(slug))
        car_info_path = repository.artifact_path(slug, "car_info.md")
        car_info_text_validation = _read_text_file(car_info_path)
        is_car, reject_reason = _is_car_listing(car_info_text_validation)
        if not is_car:
            yield f"data: {json.dumps({'error': reject_reason})}\n\n"
            yield "data: [DONE]\n\n"
            return
        yield f"data: {json.dumps({'status': 'Listing ready. Starting AI analysis...', 'slug': slug})}\n\n"
        yield from _demo_analysis_events(slug, output_language)

    return _stream_with_demo_limits(events)


def api_demo_analyze_manual():
    access_error = _check_demo_access()
    if access_error:
        return access_error
    rate_error = _check_demo_rate_limit()
    if rate_error:
        return rate_error

    # Parse the multipart body before acquiring a job slot. Flask may raise 413
    # here, which must not leave the concurrency gate acquired.
    form = request.form
    uploads = request.files.getlist("images")
    output_language = _demo_output_language(form.get("output_language"))

    runtime_state = _runtime_state()
    if not runtime_state.jobs.acquire(blocking=False):
        return jsonify({"error": "Another demo analysis is already running. Try again in a moment."}), 429

    try:
        _cleanup_old_demo_jobs()
        slug, _slug_dir, photos_count = _create_manual_listing_from_form(
            form,
            uploads,
        )
    except ValueError as exc:
        runtime_state.jobs.release()
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        runtime_state.jobs.release()
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
            runtime_state.jobs.release()

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def api_analyze(slug):
    """
    Run AI analysis on a listing using the separated pipeline:
      1. Text + research, Gemini by default; Grok/OpenRouter only if configured
      2. Gemini vision
      3. Backend deterministic scoring
      4. Final synthesis, Gemini by default with the final-synthesis model chain

    Optional JSON body: {"grok_api_key": "...", "openrouter_api_key": "...", "gemini_api_key": "..."}
    Returns SSE stream of progress and final report.
    """
    data = request.get_json(silent=True) or {}
    demo_gemini_keys = _demo_api_keys()
    grok_key = (data.get("grok_api_key") or _demo_grok_api_key()).strip()
    openrouter_key = (data.get("openrouter_api_key") or _demo_openrouter_api_key()).strip()
    provided_gemini_key = (data.get("gemini_api_key") or "").strip()
    gemini_keys = provider_retry.normalize_gemini_key_entries(
        ([provided_gemini_key] if provided_gemini_key else []) + demo_gemini_keys
    )

    if not gemini_keys:
        return jsonify({"error": "Chýba Gemini API kľúč (GEMINI_API_KEY)."}), 400
    repository = _job_repository()
    slug_dir = str(repository.job_dir(slug))
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

    repository = _job_repository()
    slug_dir = str(repository.job_dir(slug))
    if not os.path.isdir(slug_dir):
        return jsonify({"error": "Inzerát nenájdený"}), 404

    repository.write_text(slug, "analysis_result.md", content)

    # Check for KB save blocks
    kb_blocks = extract_kb_save_blocks(content)

    return jsonify({
        "status": "ok",
        "message": "✅ Výsledok z ChatGPT bol uložený ako analysis_result.md!",
        "has_kb_blocks": len(kb_blocks) > 0
    })


def api_open_folder(slug):
    """
    Open the listing's images folder in Windows Explorer.
    """
    repository = _job_repository()
    slug_dir = str(repository.job_dir(slug))
    if not os.path.isdir(slug_dir):
        return jsonify({"error": "Inzerát nenájdený"}), 404

    images_dir = str(repository.images_dir(slug))
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


def api_save_kb(slug):
    """
    Parse analysis_result.md for [SAVE AS knowledge_base/...] blocks
    and save them to the knowledge base.
    """
    repository = _job_repository()
    slug_dir = str(repository.job_dir(slug))
    result_path = repository.artifact_path(slug, "analysis_result.md")

    if not os.path.exists(result_path):
        return jsonify({"error": "Najprv spusti analýzu."}), 400

    with open(result_path, "r", encoding="utf-8") as f:
        text = f.read()

    blocks = extract_kb_save_blocks(text)
    if not blocks:
        return jsonify({"error": "Žiadne [SAVE AS] bloky nenájdené v analýze.", "saved": []}), 200

    saved = _save_kb_blocks(blocks)
    if saved:
        repository.write_json(
            slug,
            "kb_autosave.json",
            {
                "saved_at": datetime.now().isoformat(timespec="seconds"),
                "saved": saved,
            },
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

        cat_dir = os.path.join(_runtime_kb_dir(), category)
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
        for _ in analyze_with_llm(api_key, system_prompt, user_content):
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
    index_path = os.path.join(_runtime_kb_dir(), "index.json")
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

app.register_blueprint(create_public_blueprint(globals()))
app.register_blueprint(create_private_blueprint(globals()))
app.before_request(_demo_route_gate)


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
