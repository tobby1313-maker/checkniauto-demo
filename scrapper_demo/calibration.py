"""Build and validate portable offline-calibration case bundles."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CALIBRATION_FILES = (
    "raw_data.json",
    "car_info.md",
    "vin_decoded.json",
    "listing_facts.json",
    "component_identity.json",
    "reliability_research.md",
    "market_research.md",
    "web_research.md",
    "grok_research.json",
    "gemini_vision.json",
    "validation_warnings.json",
)

LABEL_TEMPLATE: dict[str, Any] = {
    "label_schema_version": 2,
    "case_id": "",
    "expected_component_identity": {
        "generation": "",
        "engine_code": "",
        "transmission_code": "",
        "drivetrain": "",
        "identity_confidence": "",
        "verification_source": ""
    },
    "expected_status": "",
    "proceed_to_inspection": None,
    "reviewer_confidence": "",
    "reviewer_role": "",
    "dataset_split": "tuning",
    "material_findings": [
        {
            "concern_family": "",
            "buyer_impact": "",
            "severity": "",
            "expected_for_age_or_mileage": None,
            "evidence_reference": "",
            "rationale": ""
        }
    ],
    "normal_wear_observations": [{"evidence_reference": "", "notes": ""}],
    "unresolved_information": [{"item": "", "should_worsen_vehicle": False, "notes": ""}],
    "later_outcome": None,
    "notes": "",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_file(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        # Model JSON artifacts may be wrapped in a markdown json fence.
        stripped = text.strip()
        if stripped.startswith("```json") and stripped.endswith("```"):
            try:
                value = json.loads(stripped[7:-3].strip())
            except json.JSONDecodeError:
                return {}
        else:
            return {}
    return value if isinstance(value, dict) else {}


def _car_info_metadata(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}

    metadata: dict[str, Any] = {}
    for key, pattern in (
        ("source_url", r"(?m)^\*\*Source:\*\*\s*(https?://\S+)"),
        ("vehicle_year", r"(?m)^-\s*\*\*Year:\*\*\s*(\d{4})"),
        ("vehicle_mileage", r"(?m)^-\s*\*\*Mileage:\*\*\s*([\d\s]+)\s*km"),
    ):
        match = re.search(pattern, text)
        if match:
            value = match.group(1).strip()
            metadata[key] = int(value.replace(" ", "")) if key == "vehicle_mileage" else value
    return metadata


def _manifest_metadata(job_dir: Path, slug: str) -> dict[str, Any]:
    raw = _json_file(job_dir / "raw_data.json")
    deterministic = _json_file(job_dir / "listing_facts.json")
    research = _json_file(job_dir / "grok_research.json")
    car_info = _car_info_metadata(job_dir / "car_info.md")
    facts = research.get("listing_facts")
    if not isinstance(facts, dict):
        facts = {}
    return {
        "bundle_schema_version": 1,
        "case_id": slug,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_url": raw.get("source_url") or raw.get("url") or car_info.get("source_url") or "",
        "vehicle_year": facts.get("year") or deterministic.get("year") or raw.get("year") or raw.get("yearValue") or car_info.get("vehicle_year"),
        "vehicle_mileage": facts.get("mileage") or deterministic.get("mileage") or raw.get("mileage") or car_info.get("vehicle_mileage"),
        "pipeline_version": "demo-v2-component-identity-1",
        "component_identity_schema_version": 1,
        "vision_schema_version": 2,
        "risk_policy_version": 2,
    }


def create_calibration_bundle(job_dir: Path, slug: str) -> Path:
    """Create a temporary ZIP containing evidence but no generated verdict."""
    job_dir = job_dir.resolve()
    if not job_dir.is_dir():
        raise FileNotFoundError(slug)

    temp = tempfile.NamedTemporaryFile(
        prefix=f"calibration-{slug}-", suffix=".zip", delete=False
    )
    archive_path = Path(temp.name)
    temp.close()

    entries: list[tuple[Path, str]] = []
    for filename in CALIBRATION_FILES:
        path = job_dir / filename
        if path.is_file():
            entries.append((path, filename))

    for dirname, archive_dirname in (
        ("images", "images"),
        (".analysis_images", "analysis_images"),
    ):
        base = job_dir / dirname
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file():
                entries.append((path, f"{archive_dirname}/{path.relative_to(base).as_posix()}"))

    manifest = _manifest_metadata(job_dir, slug)
    manifest["files"] = {
        archive_name: {"sha256": _sha256(path), "size": path.stat().st_size}
        for path, archive_name in entries
    }
    label = dict(LABEL_TEMPLATE)
    label["case_id"] = slug

    try:
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path, archive_name in entries:
                archive.write(path, archive_name)
            archive.writestr(
                "manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
            )
            archive.writestr(
                "expert_label.json", json.dumps(label, indent=2, ensure_ascii=False) + "\n"
            )
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise
    return archive_path


def safe_extract_bundle(archive_path: Path, destination: Path) -> Path:
    """Validate archive paths and extract one case into destination/case_id."""
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        if "manifest.json" not in names:
            raise ValueError("Calibration bundle has no manifest.json.")
        manifest = json.loads(archive.read("manifest.json"))
        case_id = str(manifest.get("case_id") or "").strip()
        if not case_id or any(char in case_id for char in "/\\.."):
            raise ValueError("Calibration bundle has an unsafe case id.")
        case_dir = (destination.resolve() / case_id).resolve()
        if destination.resolve() not in case_dir.parents:
            raise ValueError("Unsafe calibration destination.")
        for member in archive.infolist():
            target = (case_dir / member.filename).resolve()
            if case_dir != target and case_dir not in target.parents:
                raise ValueError(f"Unsafe archive member: {member.filename}")
        case_dir.mkdir(parents=True, exist_ok=True)
        archive.extractall(case_dir)

    validate_extracted_case(case_dir)
    return case_dir


def validate_extracted_case(case_dir: Path) -> dict[str, Any]:
    manifest = json.loads((case_dir / "manifest.json").read_text(encoding="utf-8"))
    for relative, expected in manifest.get("files", {}).items():
        path = (case_dir / relative).resolve()
        if case_dir.resolve() not in path.parents or not path.is_file():
            raise ValueError(f"Missing calibration file: {relative}")
        if _sha256(path) != expected.get("sha256"):
            raise ValueError(f"Checksum mismatch: {relative}")
    return manifest
