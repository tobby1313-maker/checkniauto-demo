"""Build and validate portable offline-calibration case bundles."""

from __future__ import annotations

import hashlib
import json
import os
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
    "component_identity_research.md",
    "component_identity.json",
    "reliability_research.md",
    "market_research.md",
    "market_benchmark.json",
    "web_research.md",
    "grok_research.json",
    "gemini_vision.json",
    "validation_warnings.json",
    "analysis_diagnostics.json",
)

DEBUGGING_FILES = (
    "raw_data.json",
    "car_info.md",
    "analysis_request.md",
    "vin_decoded.json",
    "listing_facts.json",
    "component_identity_research.md",
    "component_identity.json",
    "reliability_research.md",
    "market_research.md",
    "market_benchmark.json",
    "web_research.md",
    "grok_research.json",
    "gemini_vision.json",
    "risk_score.json",
    "validation_warnings.json",
    "analysis_diagnostics.json",
    "analysis_result_raw.md",
    "analysis_result.md",
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPRODUCIBILITY_FILES = (
    (PROJECT_ROOT / "risk_policy_v2.json", "reproducibility/risk_policy_v2.json"),
    (PROJECT_ROOT / "schemas" / "risk_score.schema.json", "reproducibility/schemas/risk_score.schema.json"),
    (PROJECT_ROOT / "schemas" / "grok_research.schema.json", "reproducibility/schemas/grok_research.schema.json"),
    (PROJECT_ROOT / "schemas" / "gemini_vision.schema.json", "reproducibility/schemas/gemini_vision.schema.json"),
    (PROJECT_ROOT / "schemas" / "component_identity.schema.json", "reproducibility/schemas/component_identity.schema.json"),
    (PROJECT_ROOT / "schemas" / "market_benchmark.schema.json", "reproducibility/schemas/market_benchmark.schema.json"),
    (PROJECT_ROOT / "schemas" / "analysis_diagnostics.schema.json", "reproducibility/schemas/analysis_diagnostics.schema.json"),
    (PROJECT_ROOT / "prompts" / "grok_text_research_system.md", "reproducibility/prompts/grok_text_research_system.md"),
    (PROJECT_ROOT / "prompts" / "gemini_vision_system.md", "reproducibility/prompts/gemini_vision_system.md"),
    (PROJECT_ROOT / "prompts" / "grok_final_synthesis_system.md", "reproducibility/prompts/grok_final_synthesis_system.md"),
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


def _bytes_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


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


def _manifest_metadata(
    job_dir: Path,
    slug: str,
    *,
    bundle_type: str = "calibration",
) -> dict[str, Any]:
    raw = _json_file(job_dir / "raw_data.json")
    deterministic = _json_file(job_dir / "listing_facts.json")
    research = _json_file(job_dir / "grok_research.json")
    car_info = _car_info_metadata(job_dir / "car_info.md")
    facts = research.get("listing_facts")
    if not isinstance(facts, dict):
        facts = {}
    diagnostics = _json_file(job_dir / "analysis_diagnostics.json")
    return {
        "bundle_schema_version": 2,
        "bundle_type": bundle_type,
        "case_id": slug,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_url": raw.get("source_url") or raw.get("url") or car_info.get("source_url") or "",
        "vehicle_year": facts.get("year") or deterministic.get("year") or raw.get("year") or raw.get("yearValue") or car_info.get("vehicle_year"),
        "vehicle_mileage": facts.get("mileage") or deterministic.get("mileage") or raw.get("mileage") or car_info.get("vehicle_mileage"),
        "pipeline_version": "demo-v2-component-identity-2",
        "component_identity_schema_version": 1,
        "vision_schema_version": 2,
        "risk_policy_version": 2,
        "risk_scorer_v2_active": diagnostics.get("risk_scorer_v2_active", False),
        "build_commit": diagnostics.get("build_commit") or os.environ.get("RENDER_GIT_COMMIT") or os.environ.get("GIT_COMMIT") or "",
        "models": diagnostics.get("models", {}),
        "prompt_and_policy_snapshots_included": True,
    }


def _create_analysis_bundle(
    job_dir: Path,
    slug: str,
    *,
    bundle_type: str,
    artifact_files: tuple[str, ...],
    include_expert_label: bool,
) -> Path:
    job_dir = job_dir.resolve()
    if not job_dir.is_dir():
        raise FileNotFoundError(slug)

    temp = tempfile.NamedTemporaryFile(
        prefix=f"{bundle_type}-{slug}-", suffix=".zip", delete=False
    )
    archive_path = Path(temp.name)
    temp.close()

    entries: list[tuple[Path, str]] = []
    for filename in artifact_files:
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

    for path, archive_name in REPRODUCIBILITY_FILES:
        if path.is_file():
            entries.append((path, archive_name))

    generated_entries: dict[str, bytes] = {}
    if not (job_dir / "validation_warnings.json").is_file():
        generated_entries["validation_warnings.json"] = (
            json.dumps(
                {"created_at": datetime.now(timezone.utc).isoformat(), "warnings": []},
                indent=2,
                ensure_ascii=False,
            )
            + "\n"
        ).encode("utf-8")

    manifest = _manifest_metadata(job_dir, slug, bundle_type=bundle_type)
    manifest["files"] = {
        archive_name: {"sha256": _sha256(path), "size": path.stat().st_size}
        for path, archive_name in entries
    }
    manifest["files"].update(
        {
            archive_name: {"sha256": _bytes_sha256(value), "size": len(value)}
            for archive_name, value in generated_entries.items()
        }
    )
    label = dict(LABEL_TEMPLATE)
    label["case_id"] = slug

    try:
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path, archive_name in entries:
                archive.write(path, archive_name)
            for archive_name, value in generated_entries.items():
                archive.writestr(archive_name, value)
            archive.writestr(
                "manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
            )
            if include_expert_label:
                archive.writestr(
                    "expert_label.json", json.dumps(label, indent=2, ensure_ascii=False) + "\n"
                )
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise
    return archive_path


def create_calibration_bundle(job_dir: Path, slug: str) -> Path:
    """Create a blind evidence ZIP without the generated verdict or report."""
    return _create_analysis_bundle(
        job_dir,
        slug,
        bundle_type="calibration",
        artifact_files=CALIBRATION_FILES,
        include_expert_label=True,
    )


def create_debugging_bundle(job_dir: Path, slug: str) -> Path:
    """Create a complete administrator-only ZIP for end-to-end debugging."""
    return _create_analysis_bundle(
        job_dir,
        slug,
        bundle_type="debugging",
        artifact_files=DEBUGGING_FILES,
        include_expert_label=False,
    )


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
