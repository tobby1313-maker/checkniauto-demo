from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

ROOT_DIR = Path(__file__).resolve().parent
INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
GENERATE_CONTENT_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
SUPPORTED_HOSTS = ("autobazar.eu", "autobazar.sk", "bazos.sk", "bazos.cz")

TEXT_MODEL = os.environ.get("CHECKNI_TEXT_MODEL", "gemini-3.8-flash")
TEXT_FALLBACK_MODELS = tuple(
    item.strip()
    for item in os.environ.get(
        "CHECKNI_TEXT_FALLBACK_MODELS",
        "gemini-3.7-flash,gemini-3.5-flash-lite,gemini-2.5-flash",
    ).split(",")
    if item.strip()
)
VISION_MODEL = os.environ.get("CHECKNI_VISION_MODEL", "gemini-2.5-flash")
VISION_FALLBACK_MODELS = tuple(
    item.strip()
    for item in os.environ.get(
        "CHECKNI_VISION_FALLBACK_MODELS",
        "gemini-2.5-flash-lite",
    ).split(",")
    if item.strip()
)
REQUEST_TIMEOUT_SECONDS = max(20, int(os.environ.get("CHECKNI_AI_TIMEOUT_SECONDS", "90")))
SCRAPE_TIMEOUT_SECONDS = max(30, int(os.environ.get("CHECKNI_SCRAPE_TIMEOUT_SECONDS", "90")))
MAX_LISTING_CHARS = max(8_000, int(os.environ.get("CHECKNI_MAX_LISTING_CHARS", "30000")))
MAX_VISION_IMAGES = max(1, min(16, int(os.environ.get("CHECKNI_MAX_VISION_IMAGES", "10"))))
IMAGE_MAX_SIDE = max(640, min(1600, int(os.environ.get("CHECKNI_IMAGE_MAX_SIDE", "1024"))))
IMAGE_QUALITY = max(45, min(90, int(os.environ.get("CHECKNI_IMAGE_QUALITY", "65"))))

ProgressCallback = Callable[[str, int, str, dict[str, Any] | None], None]


class PipelineError(RuntimeError):
    """Expected user-facing analysis pipeline failure."""


class ProviderError(PipelineError):
    """Gemini provider failure after retries."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _emit(
    callback: ProgressCallback | None,
    stage: str,
    progress: int,
    message: str,
    payload: dict[str, Any] | None = None,
) -> None:
    if callback:
        callback(stage, max(0, min(100, int(progress))), message, payload)


def _unique(items: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = (item or "").strip()
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _model_candidates(primary: str, fallbacks: Iterable[str]) -> list[str]:
    return _unique([primary, *fallbacks])


def api_keys() -> list[str]:
    return _unique(
        [
            os.environ.get("GEMINI_PRIMARY_API_KEY", ""),
            os.environ.get("GEMINI_BACKUP_API_KEY", ""),
            os.environ.get("GEMINI_API_KEY", ""),
        ]
    )


def normalize_language(value: str | None) -> str:
    return "cs" if (value or "").lower() in {"cs", "cz"} else "sk"


def normalized_host(url: str) -> str:
    parsed = urllib.parse.urlparse(url.strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def is_supported_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url.strip())
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    host = normalized_host(url)
    return any(host == allowed or host.endswith(f".{allowed}") for allowed in SUPPORTED_HOSTS)


def derive_slug(url: str) -> str:
    try:
        from main import derive_slug as legacy_derive_slug

        slug = legacy_derive_slug(url)
        if slug:
            return slug
    except Exception:
        pass
    parsed = urllib.parse.urlparse(url)
    tail = Path(parsed.path.rstrip("/")).name or "listing"
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", tail).strip("-").lower()
    return cleaned[:80] or hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


def scrape_listing(
    url: str,
    job_dir: Path,
    callback: ProgressCallback | None = None,
) -> Path:
    if not is_supported_url(url):
        raise PipelineError(
            "Tento portál zatiaľ nepodporujeme automaticky. Použite manuálny režim."
        )

    listing_root = job_dir / "listings"
    listing_root.mkdir(parents=True, exist_ok=True)
    slug = derive_slug(url)
    expected_dir = listing_root / slug
    host = normalized_host(url)
    scraper_entry = ROOT_DIR / (
        "Bazos_v2.py" if host.endswith(("bazos.sk", "bazos.cz")) else "main.py"
    )
    if not scraper_entry.exists():
        raise PipelineError(f"Chýba scraper {scraper_entry.name}.")

    env = os.environ.copy()
    env["SCRAPPER_AUTA_DIR"] = str(listing_root)
    env["DEMO_MAX_SCRAPED_IMAGES"] = str(MAX_VISION_IMAGES + 2)
    env.setdefault("DEMO_IMAGE_REQUEST_TIMEOUT", "8")

    _emit(callback, "scraping", 12, "Načítavam údaje a fotografie z inzerátu.")
    try:
        completed = subprocess.run(
            [sys.executable, str(scraper_entry), url],
            cwd=str(ROOT_DIR),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=SCRAPE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise PipelineError(
            "Načítanie inzerátu prekročilo časový limit. Skúste manuálny režim."
        ) from exc

    combined_log = "\n".join(
        part.strip() for part in (completed.stdout, completed.stderr) if part and part.strip()
    )
    (job_dir / "scraper.log").write_text(combined_log[-20_000:], encoding="utf-8")

    if completed.returncode != 0:
        useful = _last_useful_line(combined_log)
        suffix = f" Detail: {useful}" if useful else ""
        raise PipelineError(f"Inzerát sa nepodarilo načítať.{suffix}")

    if not (expected_dir / "car_info.md").exists():
        candidates = list(listing_root.glob("*/car_info.md"))
        if len(candidates) == 1:
            expected_dir = candidates[0].parent
        else:
            raise PipelineError("Scraper nedodal použiteľné údaje z inzerátu.")

    _emit(callback, "scraping", 28, "Inzerát je načítaný.")
    return expected_dir


def _last_useful_line(text: str) -> str:
    for line in reversed(text.splitlines()):
        cleaned = line.strip()
        if cleaned and not cleaned.lower().startswith(("traceback", "file \"")):
            return cleaned[:240]
    return ""


def _clean_markdown_label(value: str) -> str:
    value = re.sub(r"[*_`#]", "", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" -|:\t").lower()


def _clean_markdown_value(value: str) -> str:
    value = re.sub(r"[*_`]", "", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" |\t")
