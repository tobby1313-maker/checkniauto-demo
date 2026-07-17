"""Offline import, labelling validation, and evaluation for risk scorer v2."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from risk_scorer import calculate_hotfixed_risk_score
from risk_scorer_v2 import calculate_risk_score_v2
from scrapper_demo.calibration import safe_extract_bundle, validate_extracted_case
from scrapper_demo.verdicts import STATUS_RANK, status_for_label


CONFIDENCE_VALUES = {"LOW", "MEDIUM", "HIGH"}
SPLIT_VALUES = {"tuning", "holdout"}
IDENTITY_FIELDS = ("generation", "engine_code", "transmission_code", "drivetrain")
REPORT_REVIEW_FIELDS = (
    "slovak_language_rating",
    "report_completeness",
    "unsupported_claim_count",
    "market_link_violation_count",
)


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _optional_json(path: Path) -> dict[str, Any]:
    try:
        return _json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            return {}
        if text.startswith("```json") and text.endswith("```"):
            try:
                value = json.loads(text[7:-3].strip())
            except json.JSONDecodeError:
                return {}
            return value if isinstance(value, dict) else {}
        return {}


def iter_cases(dataset: Path) -> Iterable[Path]:
    if (dataset / "manifest.json").is_file():
        yield dataset
        return
    for path in sorted(dataset.iterdir() if dataset.is_dir() else []):
        if path.is_dir() and (path / "manifest.json").is_file():
            yield path


def validate_label(case_dir: Path) -> list[str]:
    errors: list[str] = []
    try:
        manifest = validate_extracted_case(case_dir)
        label = _json(case_dir / "expert_label.json")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [str(exc)]
    if label.get("case_id") != manifest.get("case_id"):
        errors.append("case_id does not match manifest")
    expected_status = label.get("expected_status")
    legacy_status = status_for_label(str(label.get("expected_verdict") or ""))
    if expected_status not in STATUS_RANK and legacy_status is None:
        errors.append("expected_status must be one of the five stable screening statuses")
    if not isinstance(label.get("proceed_to_inspection"), bool):
        errors.append("proceed_to_inspection must be true or false")
    if str(label.get("reviewer_confidence") or "").upper() not in CONFIDENCE_VALUES:
        errors.append("reviewer_confidence must be LOW, MEDIUM, or HIGH")
    if label.get("dataset_split") not in SPLIT_VALUES:
        errors.append("dataset_split must be tuning or holdout")
    if not str(label.get("reviewer_role") or "").strip():
        errors.append("reviewer_role is required")
    if not isinstance(label.get("material_findings"), list):
        errors.append("material_findings must be an array")
    schema_version = label.get("label_schema_version", 1)
    if not isinstance(schema_version, int) or schema_version not in {1, 2, 3}:
        errors.append("label_schema_version must be 1, 2, or 3")
    if schema_version >= 2:
        identity = label.get("expected_component_identity")
        if not isinstance(identity, dict):
            errors.append("expected_component_identity must be an object")
        else:
            for field in (*IDENTITY_FIELDS, "verification_source"):
                if not isinstance(identity.get(field, ""), str):
                    errors.append(f"expected_component_identity.{field} must be text")
            identity_confidence = str(identity.get("identity_confidence") or "").upper()
            if identity_confidence and identity_confidence not in CONFIDENCE_VALUES:
                errors.append(
                    "expected_component_identity.identity_confidence must be blank, LOW, MEDIUM, or HIGH"
                )
    if schema_version >= 3:
        if not isinstance(label.get("comparison_group", ""), str):
            errors.append("comparison_group must be text")
        review = label.get("post_unblinding_report_review")
        if not isinstance(review, dict):
            errors.append("post_unblinding_report_review must be an object")
        else:
            language_rating = review.get("slovak_language_rating")
            if language_rating is not None and (
                not isinstance(language_rating, (int, float))
                or isinstance(language_rating, bool)
                or not 1 <= float(language_rating) <= 5
            ):
                errors.append(
                    "post_unblinding_report_review.slovak_language_rating must be null or 1-5"
                )
            if review.get("report_completeness") is not None and not isinstance(
                review.get("report_completeness"), bool
            ):
                errors.append(
                    "post_unblinding_report_review.report_completeness must be null, true, or false"
                )
            for field in ("unsupported_claim_count", "market_link_violation_count"):
                value = review.get(field)
                if value is not None and (
                    not isinstance(value, int) or isinstance(value, bool) or value < 0
                ):
                    errors.append(
                        f"post_unblinding_report_review.{field} must be null or a non-negative integer"
                    )
            if not isinstance(review.get("notes", ""), str):
                errors.append("post_unblinding_report_review.notes must be text")
    return errors


def _case_inputs(case_dir: Path) -> tuple[str, str, str]:
    research = (case_dir / "grok_research.json").read_text(encoding="utf-8")
    vision = (case_dir / "gemini_vision.json").read_text(encoding="utf-8")
    listing = (case_dir / "car_info.md").read_text(encoding="utf-8")
    return research, vision, listing


def _normalized_identity_value(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _identity_comparison(case_dir: Path, label: dict[str, Any]) -> dict[str, Any]:
    expected = label.get("expected_component_identity")
    if not isinstance(expected, dict):
        expected = {}
    try:
        predicted = _json(case_dir / "component_identity.json")
    except (OSError, ValueError, json.JSONDecodeError):
        predicted = {}

    mappings = {
        "generation": ("generation", "name"),
        "engine_code": ("engine", "code"),
        "transmission_code": ("transmission", "code"),
        "drivetrain": ("drivetrain", "type"),
    }
    comparisons: dict[str, bool | None] = {}
    false_verified = False
    for field, (component_name, value_key) in mappings.items():
        expected_value = _normalized_identity_value(expected.get(field))
        component = predicted.get(component_name)
        if not isinstance(component, dict):
            component = {}
        predicted_value = _normalized_identity_value(component.get(value_key))
        comparisons[field] = (
            predicted_value == expected_value if expected_value else None
        )
        if (
            expected_value
            and predicted_value != expected_value
            and str(component.get("resolution") or "").upper() == "VERIFIED"
        ):
            false_verified = True
    reviewed = any(_normalized_identity_value(expected.get(field)) for field in IDENTITY_FIELDS)
    return {
        "reviewed": reviewed,
        "comparisons": comparisons,
        "false_verified": false_verified,
        "predicted_status": str(predicted.get("identification_status") or "UNKNOWN"),
    }


def _distribution(values: Iterable[int | float]) -> dict[str, float | int | None]:
    cleaned = sorted(
        float(value)
        for value in values
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    )
    if not cleaned:
        return {"median": None, "p90": None, "max": None}
    middle = len(cleaned) // 2
    median = (
        cleaned[middle]
        if len(cleaned) % 2
        else (cleaned[middle - 1] + cleaned[middle]) / 2
    )
    p90 = cleaned[max(0, math.ceil(len(cleaned) * 0.9) - 1)]

    def tidy(value: float) -> float | int:
        return int(value) if value.is_integer() else round(value, 6)

    return {"median": tidy(median), "p90": tidy(p90), "max": tidy(cleaned[-1])}


def _warning_metrics(case_dir: Path, diagnostics: dict[str, Any]) -> dict[str, Any]:
    warning_artifact = _optional_json(case_dir / "validation_warnings.json")
    warnings = warning_artifact.get("warnings")
    if not isinstance(warnings, list):
        warnings = []
    warning_types = Counter(
        str(item.get("type") or "unknown")
        for item in warnings
        if isinstance(item, dict)
    )
    if not warning_types:
        diagnostics_validation = diagnostics.get("validation")
        if isinstance(diagnostics_validation, dict):
            for warning_type in diagnostics_validation.get("warning_types") or []:
                warning_types[str(warning_type or "unknown")] += 1
    return {
        "warning_count": sum(warning_types.values()),
        "warning_types": dict(sorted(warning_types.items())),
        "unsupported_claim_count": warning_types.get("forbidden_claim", 0),
        "market_link_violation_count": (
            warning_types.get("public_link_outside_price_section", 0)
            + warning_types.get("unverified_public_link", 0)
        ),
        "missing_required_sections": warning_types.get("missing_required_sections", 0),
    }


def _operational_metrics(
    case_dir: Path,
    diagnostics: dict[str, Any],
    research_data: dict[str, Any],
) -> dict[str, Any]:
    usage = _optional_json(case_dir / "ai_usage_summary.json")
    phases = diagnostics.get("phases")
    if not isinstance(phases, dict):
        phases = {}
    text_phase = phases.get("text_research")
    vision_phase = phases.get("vision")
    final_phase = phases.get("final_synthesis")
    risk_phase = phases.get("risk_scoring")
    text_phase = text_phase if isinstance(text_phase, dict) else {}
    vision_phase = vision_phase if isinstance(vision_phase, dict) else {}
    final_phase = final_phase if isinstance(final_phase, dict) else {}
    risk_phase = risk_phase if isinstance(risk_phase, dict) else {}
    warning_metrics = _warning_metrics(case_dir, diagnostics)

    usage_by_phase = usage.get("usage_by_phase")
    if not isinstance(usage_by_phase, dict):
        usage_by_phase = {}
    token_fields = (
        "input_tokens", "visible_output_tokens", "thinking_tokens",
        "cached_input_tokens", "total_tokens",
    )
    tokens = {
        field: sum(
            int(bucket.get(field) or 0)
            for bucket in usage_by_phase.values()
            if isinstance(bucket, dict)
        )
        for field in token_fields
    }
    provider_reported_total = sum(
        int(actual.get("total") or 0)
        for bucket in usage_by_phase.values()
        if isinstance(bucket, dict)
        and isinstance((actual := bucket.get("actual_usage")), dict)
    )

    section_counts = {
        field: len(research_data.get(field) or [])
        if isinstance(research_data.get(field), list)
        else 0
        for field in ("web_research_findings", "technical_risks", "expected_costs")
    }
    delivery_gate = risk_phase.get("research_delivery_gate")
    if isinstance(delivery_gate, dict) and isinstance(delivery_gate.get("section_counts"), dict):
        section_counts.update({
            field: int(delivery_gate["section_counts"].get(field) or 0)
            for field in section_counts
        })
    text_schema_marker = text_phase.get("provider_schema_valid")
    text_schema_valid = (
        text_schema_marker is True
        if isinstance(text_schema_marker, bool)
        else str(text_phase.get("status") or "").lower() == "completed"
    )
    vision_schema_valid = (
        str(vision_phase.get("status") or vision_phase.get("analysis_status") or "").lower()
        == "completed"
        and vision_phase.get("parse_error") is not True
    )
    report_completed = str(final_phase.get("status") or "").lower() == "completed"
    report_complete = report_completed and warning_metrics["missing_required_sections"] == 0
    models = diagnostics.get("models")
    if not isinstance(models, dict):
        models = {}
    usage_by_model = usage.get("usage_by_model")
    if not isinstance(usage_by_model, dict):
        usage_by_model = {}
    usage_by_model_source = "provider_telemetry"
    if not usage_by_model and usage_by_phase:
        usage_by_model_source = "configured_phase_mapping"
        model_phase_aliases = {
            "grounding": "reliability_grounding",
            "market_grounding_mobile_de": "reliability_grounding",
            "text_recovery": "text_research",
            "vision_recovery": "vision",
        }
        derived: dict[str, dict[str, Any]] = {}
        for phase_name, phase_usage in usage_by_phase.items():
            if not isinstance(phase_usage, dict):
                continue
            model_key = model_phase_aliases.get(str(phase_name), str(phase_name))
            model_name = str(models.get(model_key) or "unknown")
            bucket = derived.setdefault(model_name, {"calls": 0, "estimated_cost": 0.0})
            bucket["calls"] += int(phase_usage.get("calls") or 0)
            bucket["estimated_cost"] += float(phase_usage.get("estimated_cost") or 0.0)
        usage_by_model = {
            model_name: {
                "calls": values["calls"],
                "estimated_cost": round(values["estimated_cost"], 6),
            }
            for model_name, values in derived.items()
        }
    delivery = diagnostics.get("delivery")
    if not isinstance(delivery, dict):
        delivery = {}

    return {
        "telemetry_available": bool(usage),
        "diagnostics_available": bool(diagnostics),
        "analysis_profile": str(diagnostics.get("analysis_profile") or "unknown"),
        "delivery_status": str(
            delivery.get("status") or ("COMPLETED" if report_completed else "UNKNOWN")
        ),
        "call_count": int(usage.get("call_count") or 0),
        "successful_calls": int(usage.get("successful_calls") or 0),
        "failed_calls": int(usage.get("failed_calls") or 0),
        "retry_count": int(usage.get("retry_count") or 0),
        "recovery_count": int(usage.get("recovery_count") or 0),
        "grounding_call_count": int(usage.get("grounding_call_count") or 0),
        "duration_ms": int(usage.get("duration_ms") or 0),
        "estimated_cost": float(usage.get("estimated_cost") or 0.0),
        "cost_currency": str(usage.get("cost_currency") or "EUR"),
        "tokens": {
            **tokens,
            "provider_reported_total_tokens": provider_reported_total,
            "provider_total_coverage": (
                (usage.get("actual_usage_coverage") or {}).get("total")
                if isinstance(usage.get("actual_usage_coverage"), dict)
                else None
            ),
        },
        "usage_by_model": usage_by_model,
        "usage_by_model_source": usage_by_model_source,
        "configured_models": models,
        "schema_validity": {
            "text_research": text_schema_valid,
            "vision": vision_schema_valid,
            "all": text_schema_valid and vision_schema_valid,
        },
        "research_section_counts": section_counts,
        "research_complete": all(section_counts.values()),
        "report_complete": report_complete,
        **warning_metrics,
    }


def evaluate_dataset(dataset: Path, *, split: str | None = None) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    validation_errors: dict[str, list[str]] = {}
    for case_dir in iter_cases(dataset):
        errors = validate_label(case_dir)
        if errors:
            validation_errors[case_dir.name] = errors
            continue
        label = _json(case_dir / "expert_label.json")
        manifest = _json(case_dir / "manifest.json")
        if split and label.get("dataset_split") != split:
            continue
        research, vision, listing = _case_inputs(case_dir)
        research_data = _optional_json(case_dir / "grok_research.json")
        diagnostics = _optional_json(case_dir / "analysis_diagnostics.json")
        operational = _operational_metrics(case_dir, diagnostics, research_data)
        v2 = calculate_risk_score_v2(research, vision, listing)
        v1 = calculate_hotfixed_risk_score(research, vision, listing)
        expected = str(label.get("expected_status") or "")
        if expected not in STATUS_RANK:
            expected = status_for_label(str(label.get("expected_verdict") or "")) or ""
        predicted = v2["decision_status"]
        expected_rank = STATUS_RANK[expected]
        predicted_rank = STATUS_RANK[predicted]
        expert_proceed = bool(label["proceed_to_inspection"])
        predicted_proceed = predicted_rank <= STATUS_RANK["INSPECT_WITH_RESERVATIONS"]
        try:
            manifest_year = manifest.get("vehicle_year")
            if not isinstance(manifest_year, (str, int, float)):
                raise ValueError("vehicle year unavailable")
            age = max(0, datetime.now().year - int(manifest_year))
            age_group = "0-3" if age <= 3 else "4-9" if age <= 9 else "10+"
        except (TypeError, ValueError):
            age_group = "unknown"
        expected_families = {
            str(item.get("concern_family") or "").strip()
            for item in label.get("material_findings", [])
            if isinstance(item, dict) and str(item.get("concern_family") or "").strip()
        }
        predicted_families = {str(item.get("family") or "") for item in v2["gate_triggers"] if item.get("family") not in {"evidence", "combined"}}
        identity = _identity_comparison(case_dir, label)
        report_review = label.get("post_unblinding_report_review")
        if not isinstance(report_review, dict):
            report_review = {}
        rows.append({
            "case_id": case_dir.name,
            "split": label["dataset_split"],
            "comparison_group": str(label.get("comparison_group") or ""),
            "expected_status": expected,
            "predicted_status": predicted,
            "v1_verdict": v1["allowed_final_verdict"],
            "v2_verdict": v2["allowed_final_verdict"],
            "exact": expected == predicted,
            "adjacent": abs(expected_rank - predicted_rank) <= 1,
            "expert_proceed": expert_proceed,
            "predicted_proceed": predicted_proceed,
            "evidence_quality": v2["evidence_quality"],
            "age_group": age_group,
            "expected_families": sorted(expected_families),
            "predicted_families": sorted(predicted_families),
            "normal_wear_only": any(
                isinstance(item, dict) and any(str(value or "").strip() for value in item.values())
                for item in label.get("normal_wear_observations", [])
            ) and not expected_families,
            "missing_vin": any(item.get("code") == "VIN_MISSING" for item in v2["missing_information"]),
            "gate_triggers": v2["gate_triggers"],
            "component_identity_reviewed": identity["reviewed"],
            "component_identity_matches": identity["comparisons"],
            "component_identity_false_verified": identity["false_verified"],
            "component_identity_status": identity["predicted_status"],
            "operational": operational,
            "post_unblinding_report_review": {
                field: report_review.get(field)
                for field in REPORT_REVIEW_FIELDS
            },
        })

    total = len(rows)
    exact = sum(row["exact"] for row in rows)
    adjacent = sum(row["adjacent"] for row in rows)
    proceed = sum(row["expert_proceed"] == row["predicted_proceed"] for row in rows)
    false_green = sum(row["predicted_status"] == "WORTH_INSPECTING" and STATUS_RANK[row["expected_status"]] >= 3 for row in rows)
    false_red = sum(STATUS_RANK[row["predicted_status"]] >= 3 and row["expected_status"] == "WORTH_INSPECTING" for row in rows)
    confusion = Counter((row["expected_status"], row["predicted_status"]) for row in rows)
    ages: dict[str, dict[str, Any]] = {}
    for age_group in ("0-3", "4-9", "10+", "unknown"):
        selected = [row for row in rows if row["age_group"] == age_group]
        if selected:
            ages[age_group] = {
                "case_count": len(selected),
                "exact_agreement": sum(row["exact"] for row in selected) / len(selected),
                "proceed_agreement": sum(row["expert_proceed"] == row["predicted_proceed"] for row in selected) / len(selected),
            }
    missed_families = Counter(
        family for row in rows for family in set(row["expected_families"]) - set(row["predicted_families"])
    )
    unexpected_families = Counter(
        family for row in rows for family in set(row["predicted_families"]) - set(row["expected_families"])
    )
    identity_rows = [row for row in rows if row["component_identity_reviewed"]]
    identity_metrics: dict[str, Any] = {
        "reviewed_case_count": len(identity_rows),
        "false_verified_count": sum(
            row["component_identity_false_verified"] for row in identity_rows
        ),
    }
    for field in IDENTITY_FIELDS:
        compared = [
            row["component_identity_matches"][field]
            for row in identity_rows
            if row["component_identity_matches"][field] is not None
        ]
        identity_metrics[f"{field}_exact_agreement"] = (
            sum(compared) / len(compared) if compared else None
        )
    operational_rows = [
        row["operational"] for row in rows
        if row["operational"].get("telemetry_available")
    ]
    diagnostic_rows = [
        row["operational"] for row in rows
        if row["operational"].get("diagnostics_available")
    ]
    operational_summary = {
        "telemetry_case_count": len(operational_rows),
        "diagnostics_case_count": len(diagnostic_rows),
        "call_count": _distribution(item["call_count"] for item in operational_rows),
        "retry_count": _distribution(item["retry_count"] for item in operational_rows),
        "recovery_count": _distribution(item["recovery_count"] for item in operational_rows),
        "grounding_call_count": _distribution(
            item["grounding_call_count"] for item in operational_rows
        ),
        "duration_ms": _distribution(item["duration_ms"] for item in operational_rows),
        "estimated_cost": _distribution(item["estimated_cost"] for item in operational_rows),
        "input_tokens": _distribution(
            item["tokens"]["input_tokens"] for item in operational_rows
        ),
        "visible_output_tokens": _distribution(
            item["tokens"]["visible_output_tokens"] for item in operational_rows
        ),
        "total_tokens": _distribution(
            item["tokens"]["total_tokens"] for item in operational_rows
        ),
        "provider_reported_total_tokens": _distribution(
            item["tokens"]["provider_reported_total_tokens"]
            for item in operational_rows
        ),
        "provider_total_coverage": _distribution(
            item["tokens"]["provider_total_coverage"]
            for item in operational_rows
            if item["tokens"]["provider_total_coverage"] is not None
        ),
        "schema_valid_case_count": sum(
            item["schema_validity"]["all"] for item in diagnostic_rows
        ),
        "research_complete_case_count": sum(
            item["research_complete"] for item in diagnostic_rows
        ),
        "report_complete_case_count": sum(
            item["report_complete"] for item in diagnostic_rows
        ),
        "unsupported_claim_count": sum(
            item["unsupported_claim_count"] for item in diagnostic_rows
        ),
        "market_link_violation_count": sum(
            item["market_link_violation_count"] for item in diagnostic_rows
        ),
    }
    model_costs: Counter[str] = Counter()
    model_calls: Counter[str] = Counter()
    for item in operational_rows:
        for model_name, model_usage in item.get("usage_by_model", {}).items():
            if not isinstance(model_usage, dict):
                continue
            model_costs[str(model_name)] += float(model_usage.get("estimated_cost") or 0.0)
            model_calls[str(model_name)] += int(model_usage.get("calls") or 0)
    operational_summary["by_model"] = {
        model: {
            "calls": model_calls[model],
            "estimated_cost": round(cost, 6),
        }
        for model, cost in sorted(model_costs.items())
    }

    profiles: dict[str, dict[str, Any]] = {}
    for profile in sorted({item["analysis_profile"] for item in operational_rows}):
        selected = [item for item in operational_rows if item["analysis_profile"] == profile]
        profiles[profile] = {
            "case_count": len(selected),
            "estimated_cost": _distribution(item["estimated_cost"] for item in selected),
            "call_count": _distribution(item["call_count"] for item in selected),
            "duration_ms": _distribution(item["duration_ms"] for item in selected),
            "total_tokens": _distribution(item["tokens"]["total_tokens"] for item in selected),
        }

    comparisons: list[dict[str, Any]] = []
    comparison_groups = sorted({row["comparison_group"] for row in rows if row["comparison_group"]})
    for group in comparison_groups:
        grouped = [row for row in rows if row["comparison_group"] == group]
        legacy = next(
            (row for row in grouped if row["operational"]["analysis_profile"] == "legacy"),
            None,
        )
        optimized_rows = [
            row for row in grouped
            if row["operational"]["analysis_profile"]
            in {"quality_optimized", "cost_optimized"}
        ]
        if not legacy:
            continue
        for optimized in optimized_rows:
            legacy_cost = float(legacy["operational"]["estimated_cost"] or 0.0)
            optimized_cost = float(optimized["operational"]["estimated_cost"] or 0.0)
            comparisons.append({
                "comparison_group": group,
                "legacy_case_id": legacy["case_id"],
                "optimized_case_id": optimized["case_id"],
                "optimized_profile": optimized["operational"]["analysis_profile"],
                "legacy_cost": legacy_cost,
                "optimized_cost": optimized_cost,
                "cost_reduction_percent": (
                    round((legacy_cost - optimized_cost) / legacy_cost * 100, 3)
                    if legacy_cost > 0
                    else None
                ),
                "legacy_status": legacy["predicted_status"],
                "optimized_status": optimized["predicted_status"],
            })
    language_ratings = [
        float(value)
        for row in rows
        if isinstance(
            (value := row["post_unblinding_report_review"].get("slovak_language_rating")),
            (int, float),
        )
        and not isinstance(value, bool)
    ]
    completed_diagnostic_rows = [
        item for item in diagnostic_rows
        if item["delivery_status"] == "COMPLETED"
    ]
    quality_gates = {
        "minimum_20_cases": total >= 20,
        "all_completed_reports_have_nonempty_research_sections": (
            bool(completed_diagnostic_rows)
            and all(item["research_complete"] for item in completed_diagnostic_rows)
        ),
        "all_schema_valid": (
            bool(diagnostic_rows)
            and all(item["schema_validity"]["all"] for item in diagnostic_rows)
        ),
        "no_unsupported_high_impact_claims": (
            operational_summary["unsupported_claim_count"] == 0
            if diagnostic_rows
            else None
        ),
        "no_market_link_violations": (
            operational_summary["market_link_violation_count"] == 0
            if diagnostic_rows
            else None
        ),
        "all_reports_complete": (
            bool(completed_diagnostic_rows)
            and all(item["report_complete"] for item in completed_diagnostic_rows)
        ),
        "cost_reduction_at_least_35_percent": (
            _distribution(
                item["cost_reduction_percent"]
                for item in comparisons
                if item["cost_reduction_percent"] is not None
            )["median"]
            >= 35
            if any(item["cost_reduction_percent"] is not None for item in comparisons)
            else None
        ),
    }
    return {
        "case_count": total,
        "split": split or "all",
        "metrics": {
            "exact_agreement": exact / total if total else None,
            "adjacent_agreement": adjacent / total if total else None,
            "proceed_agreement": proceed / total if total else None,
            "false_green_count": false_green,
            "false_red_or_extreme_count": false_red,
            "low_evidence_cap_count": sum(any(t.get("code") == "LOW_EVIDENCE_CAP" for t in row["gate_triggers"]) for row in rows),
            "missing_vin_case_count": sum(row["missing_vin"] for row in rows),
            "normal_wear_escalation_count": sum(row["normal_wear_only"] and row["predicted_status"] != "WORTH_INSPECTING" for row in rows),
            "v1_v2_difference_count": sum(row["v1_verdict"] != row["v2_verdict"] for row in rows),
        },
        "by_vehicle_age": ages,
        "family_errors": {
            "missed": dict(sorted(missed_families.items())),
            "unexpected": dict(sorted(unexpected_families.items())),
        },
        "component_identity": identity_metrics,
        "operational": operational_summary,
        "by_profile": profiles,
        "profile_comparisons": comparisons,
        "post_unblinding_report_review": {
            "reviewed_case_count": len(language_ratings),
            "slovak_language_rating": _distribution(language_ratings),
        },
        "quality_gates": quality_gates,
        "confusion_matrix": [
            {"expected": expected, "predicted": predicted, "count": count}
            for (expected, predicted), count in sorted(confusion.items())
        ],
        "cases": rows,
        "validation_errors": validation_errors,
    }


def markdown_report(result: dict[str, Any]) -> str:
    metrics = result["metrics"]
    operational = result["operational"]
    percent = lambda value: "n/a" if value is None else f"{value * 100:.1f}%"
    metric_range = lambda value, suffix="": (
        "n/a"
        if value.get("median") is None
        else f"{value['median']}{suffix} / {value['p90']}{suffix} / {value['max']}{suffix}"
    )
    lines = [
        "# Risk Scorer v2 Offline Evaluation",
        "",
        f"- Cases: {result['case_count']}",
        f"- Split: {result['split']}",
        f"- Exact verdict agreement: {percent(metrics['exact_agreement'])}",
        f"- Adjacent verdict agreement: {percent(metrics['adjacent_agreement'])}",
        f"- Proceed/no-proceed agreement: {percent(metrics['proceed_agreement'])}",
        f"- False green: {metrics['false_green_count']}",
        f"- False red/extreme: {metrics['false_red_or_extreme_count']}",
        f"- Component identity reviewed: {result['component_identity']['reviewed_case_count']}",
        f"- False VERIFIED component identities: {result['component_identity']['false_verified_count']}",
        "",
        "## Operational metrics",
        "",
        f"- Cases with usage telemetry: {operational['telemetry_case_count']}",
        f"- Calls median / p90 / max: {metric_range(operational['call_count'])}",
        f"- Retries median / p90 / max: {metric_range(operational['retry_count'])}",
        f"- Recoveries median / p90 / max: {metric_range(operational['recovery_count'])}",
        f"- Duration median / p90 / max: {metric_range(operational['duration_ms'], ' ms')}",
        f"- Estimated cost median / p90 / max: {metric_range(operational['estimated_cost'], ' EUR')}",
        f"- Total tokens median / p90 / max: {metric_range(operational['total_tokens'])}",
        f"- Provider-reported total tokens median / p90 / max: {metric_range(operational['provider_reported_total_tokens'])}",
        f"- Provider total-usage coverage median / p90 / max: {metric_range(operational['provider_total_coverage'])}",
        f"- Schema-valid cases: {operational['schema_valid_case_count']}/{operational['diagnostics_case_count']}",
        f"- Complete research sections: {operational['research_complete_case_count']}/{operational['diagnostics_case_count']}",
        f"- Complete reports: {operational['report_complete_case_count']}/{operational['diagnostics_case_count']}",
        f"- Unsupported high-impact claims: {operational['unsupported_claim_count']}",
        f"- Market-link violations: {operational['market_link_violation_count']}",
        "",
        "## Quality gates",
        "",
    ]
    for name, passed in result["quality_gates"].items():
        status = "not evaluated" if passed is None else "PASS" if passed else "FAIL"
        lines.append(f"- {name}: {status}")
    lines.extend([
        "",
        "## Cases",
        "",
        "| Case | Profile | Expected | V2 | Exact | Calls | Retries | Cost EUR | Schema | Research | Report |",
        "| --- | --- | --- | --- | --- | ---: | ---: | ---: | --- | --- | --- |",
    ])
    for row in result["cases"]:
        operation = row["operational"]
        lines.append(
            f"| {row['case_id']} | {operation['analysis_profile']} | {row['expected_status']} | "
            f"{row['predicted_status']} | {'yes' if row['exact'] else 'no'} | "
            f"{operation['call_count']} | {operation['retry_count']} | "
            f"{operation['estimated_cost']:.6f} | "
            f"{'yes' if operation['schema_validity']['all'] else 'no'} | "
            f"{'yes' if operation['research_complete'] else 'no'} | "
            f"{'yes' if operation['report_complete'] else 'no'} |"
        )
    return "\n".join(lines) + "\n"


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    import_parser = sub.add_parser("import")
    import_parser.add_argument("bundle", type=Path)
    import_parser.add_argument("dataset", type=Path)
    validate_parser = sub.add_parser("validate-labels")
    validate_parser.add_argument("dataset", type=Path)
    for name in ("evaluate", "compare", "report"):
        child = sub.add_parser(name)
        child.add_argument("dataset", type=Path)
        child.add_argument("--split", choices=sorted(SPLIT_VALUES))
        child.add_argument("--json-output", type=Path)
        child.add_argument("--markdown-output", type=Path)
    args = parser.parse_args()

    if args.command == "import":
        args.dataset.mkdir(parents=True, exist_ok=True)
        case = safe_extract_bundle(args.bundle, args.dataset)
        print(case)
        return 0
    if args.command == "validate-labels":
        failures = {case.name: errors for case in iter_cases(args.dataset) if (errors := validate_label(case))}
        print(json.dumps(failures, indent=2, ensure_ascii=False))
        return 1 if failures else 0

    result = evaluate_dataset(args.dataset, split=args.split)
    rendered_json = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    rendered_markdown = markdown_report(result)
    if args.json_output:
        args.json_output.write_text(rendered_json, encoding="utf-8")
    if args.markdown_output:
        args.markdown_output.write_text(rendered_markdown, encoding="utf-8")
    print(rendered_markdown if args.command == "report" else rendered_json)
    return 1 if result["validation_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(_main())
