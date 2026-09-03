from __future__ import annotations

import ipaddress
import os
import socket
import urllib.parse
from dataclasses import dataclass
from pathlib import Path


SUPPORTED_HOSTS = {
    "autobazar.eu",
    "www.autobazar.eu",
    "autobazar.sk",
    "www.autobazar.sk",
    "auto.bazos.sk",
    "www.bazos.sk",
    "bazos.sk",
    "auto.bazos.cz",
    "www.bazos.cz",
    "bazos.cz",
}

TERMINAL_JOB_STATES = {"completed", "failed"}


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def normalize_language(value: str | None) -> str:
    value = (value or "sk").strip().lower()
    return value if value in {"sk", "cs", "en"} else "sk"


def canonical_host(host: str) -> str:
    host = (host or "").strip().lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def validate_listing_url(value: str) -> str:
    """Validate and normalize a public listing URL from the supported allowlist."""
    value = (value or "").strip()
    if not value or len(value) > 2048:
        raise ValueError("Zadaj platný odkaz na inzerát.")

    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("Odkaz musí používať http alebo https.")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Odkaz neobsahuje platnú verejnú doménu.")

    host = parsed.hostname.lower().rstrip(".")
    if host not in SUPPORTED_HOSTS:
        raise ValueError(
            "Tento portál zatiaľ nepodporujeme. Použi Autobazar.eu, Autobazar.sk, Bazoš.sk alebo Bazoš.cz."
        )

    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        address = None
    if address and (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved):
        raise ValueError("Súkromné alebo lokálne adresy nie sú povolené.")

    clean_path = parsed.path or "/"
    return urllib.parse.urlunsplit(
        (parsed.scheme.lower(), parsed.netloc.lower(), clean_path, parsed.query, "")
    )


def resolve_public_host(host: str) -> bool:
    """Best-effort DNS guard used immediately before an outbound generic fetch."""
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        return False
    for item in addresses:
        raw = item[4][0]
        try:
            address = ipaddress.ip_address(raw)
        except ValueError:
            return False
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
            return False
    return bool(addresses)


@dataclass(frozen=True)
class Settings:
    project_root: Path
    data_dir: Path
    web_dir: Path
    max_workers: int
    scraper_timeout_seconds: int
    gemini_timeout_seconds: int
    max_scraped_images: int
    max_ai_images: int
    max_upload_mb: int
    job_ttl_hours: int
    research_cache_hours: int
    rate_limit_per_day: int
    gemini_model: str
    gemini_fallback_models: tuple[str, ...]
    billing_mode: str
    access_token: str
    debug: bool

    @classmethod
    def from_env(cls) -> "Settings":
        project_root = Path(__file__).resolve().parent.parent
        data_dir = Path(os.environ.get("CHECKNI_DATA_DIR", "/tmp/checkni-auto-v2")).expanduser()
        fallback_models = tuple(
            model.strip()
            for model in os.environ.get(
                "CHECKNI_GEMINI_FALLBACK_MODELS", "gemini-2.5-flash-lite"
            ).split(",")
            if model.strip()
        )
        billing_mode = os.environ.get("CHECKNI_BILLING_MODE", "demo").strip().lower()
        if billing_mode not in {"demo", "access-token"}:
            billing_mode = "demo"

        settings = cls(
            project_root=project_root,
            data_dir=data_dir,
            web_dir=project_root / "web_v2_product",
            max_workers=_int_env("CHECKNI_MAX_WORKERS", 2, 1, 8),
            scraper_timeout_seconds=_int_env("CHECKNI_SCRAPE_TIMEOUT_SECONDS", 90, 30, 240),
            gemini_timeout_seconds=_int_env("CHECKNI_GEMINI_TIMEOUT_SECONDS", 65, 20, 180),
            max_scraped_images=_int_env("CHECKNI_MAX_SCRAPED_IMAGES", 14, 4, 30),
            max_ai_images=_int_env("CHECKNI_MAX_AI_IMAGES", 10, 2, 16),
            max_upload_mb=_int_env("CHECKNI_MAX_UPLOAD_MB", 24, 4, 80),
            job_ttl_hours=_int_env("CHECKNI_JOB_TTL_HOURS", 48, 1, 720),
            research_cache_hours=_int_env("CHECKNI_RESEARCH_CACHE_HOURS", 168, 1, 2160),
            rate_limit_per_day=_int_env("CHECKNI_RATE_LIMIT_PER_DAY", 8, 1, 10000),
            gemini_model=os.environ.get("CHECKNI_GEMINI_MODEL", "gemini-2.5-flash").strip(),
            gemini_fallback_models=fallback_models,
            billing_mode=billing_mode,
            access_token=os.environ.get("CHECKNI_ACCESS_TOKEN", "").strip(),
            debug=_bool_env("CHECKNI_DEBUG", False),
        )
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        return settings

    @property
    def gemini_keys(self) -> tuple[str, ...]:
        values = (
            os.environ.get("GEMINI_PRIMARY_API_KEY", ""),
            os.environ.get("GEMINI_BACKUP_API_KEY", ""),
            os.environ.get("GEMINI_API_KEY", ""),
        )
        unique: list[str] = []
        for value in values:
            value = value.strip()
            if value and value not in unique:
                unique.append(value)
        return tuple(unique)
