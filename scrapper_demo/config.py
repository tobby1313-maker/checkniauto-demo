"""Typed server configuration for the public demo Flask application."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


TRUE_VALUES = {"1", "true", "yes", "on"}


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in TRUE_VALUES


def _as_int(
    values: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int | None = None,
) -> int:
    raw_value = values.get(name, str(default))
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer, got {raw_value!r}.") from exc
    if minimum is not None:
        parsed = max(minimum, parsed)
    return parsed


@dataclass(frozen=True, slots=True)
class DemoServerConfig:
    """Validated configuration owned by the Flask/server layer."""

    script_dir: str
    data_dir: str
    auta_dir: str
    knowledge_base_dir: str
    web_dir: str
    demo_mode: bool
    demo_prompt_file: str
    demo_rate_limit_per_ip: str
    demo_max_concurrent_jobs: int
    demo_job_ttl_minutes: int
    demo_max_manual_images: int
    demo_max_scraped_images: int
    demo_skip_kb: bool
    demo_analysis_profile: str
    demo_max_upload_mb: int
    flask_secret_key: str
    admin_dashboard_token: str
    risk_scorer_v2_active: bool
    gemini_primary_api_key: str
    gemini_backup_api_key: str
    gemini_second_backup_api_key: str
    grok_api_key: str
    openrouter_api_key: str

    @classmethod
    def from_env(
        cls,
        script_dir: str | os.PathLike[str],
        environ: Mapping[str, str] | None = None,
    ) -> "DemoServerConfig":
        values = os.environ if environ is None else environ
        resolved_script_dir = str(Path(script_dir).resolve())
        data_dir = values.get("SCRAPPER_DATA_DIR") or os.path.join(
            tempfile.gettempdir(), "scrapper-demo"
        )
        auta_dir = values.get("SCRAPPER_AUTA_DIR") or os.path.join(data_dir, "Auta")

        return cls(
            script_dir=resolved_script_dir,
            data_dir=data_dir,
            auta_dir=auta_dir,
            knowledge_base_dir=os.path.join(resolved_script_dir, "knowledge_base"),
            web_dir=os.path.join(resolved_script_dir, "web"),
            demo_mode=_as_bool(values.get("DEMO_MODE"), True),
            demo_prompt_file=values.get("DEMO_PROMPT_FILE", "analyze_prompt_v4_koyeb.txt"),
            demo_rate_limit_per_ip=values.get("DEMO_RATE_LIMIT_PER_IP", "3/day"),
            demo_max_concurrent_jobs=_as_int(
                values, "DEMO_MAX_CONCURRENT_JOBS", 1, minimum=1
            ),
            demo_job_ttl_minutes=_as_int(
                values, "DEMO_JOB_TTL_MINUTES", 60, minimum=5
            ),
            demo_max_manual_images=_as_int(
                values, "DEMO_MAX_MANUAL_IMAGES", 12, minimum=0
            ),
            demo_max_scraped_images=_as_int(
                values, "DEMO_MAX_SCRAPED_IMAGES", 0, minimum=0
            ),
            demo_skip_kb=_as_bool(values.get("DEMO_SKIP_KB"), True),
            demo_analysis_profile=(
                values.get("DEMO_ANALYSIS_PROFILE", "quality_optimized").strip().lower()
                if values.get("DEMO_ANALYSIS_PROFILE", "quality_optimized").strip().lower()
                in {"legacy", "quality_optimized", "cost_optimized"}
                else "quality_optimized"
            ),
            demo_max_upload_mb=_as_int(
                values, "DEMO_MAX_UPLOAD_MB", 24, minimum=1
            ),
            flask_secret_key=values.get(
                "FLASK_SECRET_KEY", "dev-demo-secret-change-me"
            ),
            admin_dashboard_token=values.get("ADMIN_DASHBOARD_TOKEN", "").strip(),
            risk_scorer_v2_active=_as_bool(
                values.get("RISK_SCORER_V2_ACTIVE"), False
            ),
            gemini_primary_api_key=values.get("GEMINI_PRIMARY_API_KEY", "").strip(),
            gemini_backup_api_key=values.get("GEMINI_BACKUP_API_KEY", "").strip(),
            gemini_second_backup_api_key=values.get(
                "GEMINI_SECOND_BACKUP_API_KEY", ""
            ).strip(),
            grok_api_key=values.get("GROK_API_KEY", "").strip(),
            openrouter_api_key=values.get("OPENROUTER_API_KEY", "").strip(),
        )

    @property
    def max_upload_bytes(self) -> int:
        return self.demo_max_upload_mb * 1024 * 1024

    def as_flask_mapping(self) -> dict[str, object]:
        return {
            "SECRET_KEY": self.flask_secret_key,
            "MAX_CONTENT_LENGTH": self.max_upload_bytes,
            "DEMO_MODE": self.demo_mode,
            "DEMO_PROMPT_FILE": self.demo_prompt_file,
            "DEMO_RATE_LIMIT_PER_IP": self.demo_rate_limit_per_ip,
            "DEMO_MAX_CONCURRENT_JOBS": self.demo_max_concurrent_jobs,
            "DEMO_JOB_TTL_MINUTES": self.demo_job_ttl_minutes,
            "DEMO_MAX_MANUAL_IMAGES": self.demo_max_manual_images,
            "DEMO_MAX_SCRAPED_IMAGES": self.demo_max_scraped_images,
            "DEMO_SKIP_KB": self.demo_skip_kb,
            "DEMO_ANALYSIS_PROFILE": self.demo_analysis_profile,
            "DEMO_MAX_UPLOAD_MB": self.demo_max_upload_mb,
            "SCRAPPER_DATA_DIR": self.data_dir,
            "SCRAPPER_AUTA_DIR": self.auta_dir,
            "SCRAPPER_KB_DIR": self.knowledge_base_dir,
            "SCRAPPER_WEB_DIR": self.web_dir,
            "ADMIN_DASHBOARD_TOKEN": self.admin_dashboard_token,
            "RISK_SCORER_V2_ACTIVE": self.risk_scorer_v2_active,
            "GEMINI_PRIMARY_API_KEY": self.gemini_primary_api_key,
            "GEMINI_BACKUP_API_KEY": self.gemini_backup_api_key,
            "GEMINI_SECOND_BACKUP_API_KEY": self.gemini_second_backup_api_key,
            "GROK_API_KEY": self.grok_api_key,
            "OPENROUTER_API_KEY": self.openrouter_api_key,
        }
