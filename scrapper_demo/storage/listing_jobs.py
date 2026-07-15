"""Safe, compatibility-preserving filesystem repository for listing jobs."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any


PUBLIC_ARTIFACTS = (
    "raw_data.json",
    "car_info.md",
    "analysis_request.md",
    "vin_decoded.json",
    "listing_facts.json",
    "component_identity_research.md",
    "component_identity.json",
    "reliability_research.md",
    "market_research.md",
    "market_research_sk_cz.md",
    "market_research_mobile_de.md",
    "market_research_otomoto_pl.md",
    "market_research_autoscout.md",
    "market_search_results.json",
    "market_benchmark.json",
    "web_research.md",
    "grok_research.json",
    "gemini_vision.json",
    "vision_provider_attempts.json",
    "risk_score.json",
    "validation_warnings.json",
    "analysis_diagnostics.json",
    "analysis_result_raw.md",
    "analysis_result.md",
)

INTERNAL_ARTIFACTS = PUBLIC_ARTIFACTS + ("kb_autosave.json",)

ARTIFACT_LABELS = {
    "raw_data.json": "Scraped raw JSON",
    "car_info.md": "Scraped listing markdown",
    "analysis_request.md": "Legacy prompt input",
    "vin_decoded.json": "VIN decoded",
    "listing_facts.json": "Deterministic listing facts",
    "component_identity_research.md": "Raw grounded component-identity research",
    "component_identity.json": "Grounded component identity",
    "reliability_research.md": "Grounded reliability research",
    "market_research.md": "Direct market search diagnostics",
    "market_research_sk_cz.md": "Direct local SK/CZ market search",
    "market_research_mobile_de.md": "Legacy grounded Mobile.de market search",
    "market_research_otomoto_pl.md": "Legacy grounded Otomoto market search",
    "market_research_autoscout.md": "Legacy grounded AutoScout24 market search",
    "market_search_results.json": "Verified market search candidates",
    "market_benchmark.json": "Deterministic background market benchmark",
    "web_research.md": "Grounded web research",
    "grok_research.json": "Text/research JSON",
    "gemini_vision.json": "Vision JSON",
    "vision_provider_attempts.json": "Sanitized vision provider attempts",
    "risk_score.json": "Backend risk score",
    "validation_warnings.json": "Validation warnings",
    "analysis_diagnostics.json": "Per-phase analysis diagnostics",
    "analysis_result_raw.md": "Raw final model output",
    "analysis_result.md": "Public report",
}


def atomic_write_text(path: str | os.PathLike[str], content: str) -> None:
    """Replace a UTF-8 text file atomically within its destination directory."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as temp_file:
            temp_file.write(str(content))
            temp_file.flush()
            os.fsync(temp_file.fileno())
            temp_path = temp_file.name
        os.replace(temp_path, destination)
        temp_path = None
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass


def atomic_write_json(
    path: str | os.PathLike[str],
    value: Any,
    *,
    indent: int = 2,
    ensure_ascii: bool = False,
) -> None:
    content = json.dumps(value, indent=indent, ensure_ascii=ensure_ascii) + "\n"
    atomic_write_text(path, content)


class ListingJobRepository:
    """Own job paths, compatible artifact names, discovery, and cleanup."""

    def __init__(self, root: str | os.PathLike[str], *, create: bool = True):
        self.root = Path(root).resolve()
        if create:
            self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def normalize_slug(value: str | None, fallback: str = "listing") -> str:
        normalized = (value or "").strip().lower()
        normalized = re.sub(r"https?://", "", normalized)
        normalized = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
        return normalized[:80] or fallback

    @staticmethod
    def _validate_filename(filename: str) -> str:
        if (
            not filename
            or filename in {".", ".."}
            or ".." in filename
            or "/" in filename
            or "\\" in filename
            or Path(filename).name != filename
        ):
            raise ValueError("Unsafe job filename.")
        return filename

    @staticmethod
    def _inside(base: Path, candidate: Path) -> bool:
        try:
            return os.path.commonpath([str(base), str(candidate)]) == str(base)
        except ValueError:
            return False

    def job_dir(
        self,
        slug: str,
        *,
        create: bool = False,
        require: bool = False,
    ) -> Path:
        safe_slug = self.normalize_slug(slug)
        path = (self.root / safe_slug).resolve()
        if not self._inside(self.root, path):
            raise ValueError("Unsafe listing path.")
        if create:
            path.mkdir(parents=True, exist_ok=True)
        if require and not path.is_dir():
            raise FileNotFoundError(safe_slug)
        return path

    def artifact_path(
        self,
        slug: str,
        filename: str,
        *,
        public_only: bool = False,
    ) -> Path:
        filename = self._validate_filename(filename)
        allowed = PUBLIC_ARTIFACTS if public_only else INTERNAL_ARTIFACTS
        if filename not in allowed:
            raise ValueError("Unsupported job artifact.")
        return self.job_dir(slug) / filename

    def images_dir(self, slug: str, *, create: bool = False) -> Path:
        path = self.job_dir(slug) / "images"
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def analysis_images_dir(self, slug: str, *, create: bool = False) -> Path:
        path = self.job_dir(slug) / ".analysis_images"
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def image_path(self, slug: str, filename: str) -> Path:
        return self.images_dir(slug) / self._validate_filename(filename)

    def analysis_image_path(self, slug: str, filename: str) -> Path:
        return self.analysis_images_dir(slug) / self._validate_filename(filename)

    def read_text(
        self,
        slug: str,
        filename: str,
        *,
        public_only: bool = False,
        default: str | None = None,
    ) -> str | None:
        path = self.artifact_path(slug, filename, public_only=public_only)
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return default

    def read_json(
        self,
        slug: str,
        filename: str,
        *,
        default: Any = None,
    ) -> Any:
        text = self.read_text(slug, filename)
        if text is None:
            return default
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return default

    def write_text(self, slug: str, filename: str, content: str) -> Path:
        path = self.artifact_path(slug, filename)
        self.job_dir(slug, create=True)
        atomic_write_text(path, content)
        return path

    def write_json(
        self,
        slug: str,
        filename: str,
        value: Any,
        *,
        indent: int = 2,
        ensure_ascii: bool = False,
    ) -> Path:
        path = self.artifact_path(slug, filename)
        self.job_dir(slug, create=True)
        atomic_write_json(path, value, indent=indent, ensure_ascii=ensure_ascii)
        return path

    def available_artifacts(self, slug: str) -> list[Path]:
        return [
            self.artifact_path(slug, filename, public_only=True)
            for filename in PUBLIC_ARTIFACTS
            if self.artifact_path(slug, filename, public_only=True).is_file()
        ]

    def iter_job_directories(
        self,
        *,
        require_artifact: str | None = None,
    ) -> Iterator[tuple[str, Path]]:
        if not self.root.is_dir():
            return
        if require_artifact is not None and require_artifact not in INTERNAL_ARTIFACTS:
            raise ValueError("Unsupported required artifact.")
        for path in sorted(self.root.iterdir(), key=lambda item: item.name, reverse=True):
            if not path.is_dir():
                continue
            if require_artifact and not (path / require_artifact).is_file():
                continue
            yield path.name, path

    def unique_slug(self, base_slug: str, *, timestamp: str | None = None) -> str:
        base = self.normalize_slug(base_slug, "manual-listing")
        if not self.job_dir(base).exists():
            return base
        timestamp = timestamp or time.strftime("%Y%m%d-%H%M%S")
        candidate = f"{base}-{timestamp}"
        suffix = 2
        while self.job_dir(candidate).exists():
            candidate = f"{base}-{timestamp}-{suffix}"
            suffix += 1
        return candidate

    def cleanup_expired(self, ttl_minutes: int, *, now: float | None = None) -> list[str]:
        cutoff = (time.time() if now is None else now) - (int(ttl_minutes) * 60)
        removed: list[str] = []
        for slug, path in list(self.iter_job_directories()):
            try:
                if path.stat().st_mtime < cutoff:
                    shutil.rmtree(path, ignore_errors=True)
                    if not path.exists():
                        removed.append(slug)
            except OSError:
                continue
        return removed
