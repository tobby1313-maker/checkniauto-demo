"""Offline import, labelling validation, and evaluation for risk scorer v2."""

from __future__ import annotations

import argparse
import json
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


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


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
    if not isinstance(schema_version, int) or schema_version not in {1, 2}:
        errors.append("label_schema_version must be 1 or 2")
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
        rows.append({
            "case_id": case_dir.name,
            "split": label["dataset_split"],
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
        "confusion_matrix": [
            {"expected": expected, "predicted": predicted, "count": count}
            for (expected, predicted), count in sorted(confusion.items())
        ],
        "cases": rows,
        "validation_errors": validation_errors,
    }


def markdown_report(result: dict[str, Any]) -> str:
    metrics = result["metrics"]
    percent = lambda value: "n/a" if value is None else f"{value * 100:.1f}%"
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
        "## Cases",
        "",
        "| Case | Expected | V2 | Exact | V1 verdict |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in result["cases"]:
        lines.append(f"| {row['case_id']} | {row['expected_status']} | {row['predicted_status']} | {'yes' if row['exact'] else 'no'} | {row['v1_verdict']} |")
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
