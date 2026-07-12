"""Gate-based, offline-calibrated candidate scorer for used-car listings."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


POLICY_PATH = Path(__file__).with_name("risk_policy_v2.json")
STATUS_RANK = {
    "WORTH_INSPECTING": 0,
    "INSPECT_WITH_RESERVATIONS": 1,
    "RESOLVE_BEFORE_PROCEEDING": 2,
    "HIGH_RISK": 3,
    "DO_NOT_PROCEED": 4,
}


def load_policy(path: str | Path | None = None) -> dict[str, Any]:
    selected = Path(path) if path else POLICY_PATH
    return json.loads(selected.read_text(encoding="utf-8"))


def _parse(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    text = str(value or "").strip()
    if not text:
        return {}
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"_parse_error": True}
    return parsed if isinstance(parsed, dict) else {"_parse_error": True}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _missing(value: Any) -> bool:
    return _text(value).lower() in {
        "", "none", "null", "unknown", "n/a", "neuvedene", "neuvedené", "nezname", "neznáme"
    }


def _confidence(value: Any) -> str:
    normalized = _text(value).lower()
    if normalized in {"high", "vysoka", "vysoká"}:
        return "HIGH"
    if normalized in {"medium", "stredna", "stredná"}:
        return "MEDIUM"
    return "LOW"


def _year(value: Any, listing_text: str) -> int | None:
    match = re.search(r"\b(?:19|20)\d{2}\b", _text(value))
    if not match:
        match = re.search(r"\b(?:19|20)\d{2}\b", listing_text or "")
    if not match:
        return None
    parsed = int(match.group(0))
    return parsed if 1980 <= parsed <= datetime.now().year + 1 else None


def _age_bucket(year: int | None) -> str:
    if year is None:
        return "unknown"
    age = max(0, datetime.now().year - year)
    if age <= 3:
        return "0_3"
    if age <= 9:
        return "4_9"
    return "10_plus"


def _truthy(value: Any) -> bool:
    return value is True or _text(value).lower() in {"true", "yes", "ano", "1", "present"}


def _material_photo_limitation(value: Any) -> bool:
    text = _text(value).lower()
    if not text:
        return False
    hard = ("missing", "absent", "no photos", "unusable", "low resolution", "blur", "dark", "cropped", "underbody", "podvoz", "rozmaz", "nekval")
    if any(term in text for term in hard):
        return True
    benign = ("overview", "contact sheet", "thumbnail", "sample", "representative", "visible_overview_only", "not assessable in detail")
    return not any(term in text for term in benign)


def _add_unique(items: list[dict[str, Any]], item: dict[str, Any], keys: tuple[str, ...]) -> None:
    identity = tuple(_text(item.get(key)).lower() for key in keys)
    if any(tuple(_text(existing.get(key)).lower() for key in keys) == identity for existing in items):
        return
    items.append(item)


def calculate_risk_score_v2(
    text_research: Any,
    vision: Any,
    listing_text: str | None = None,
    *,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy = policy or load_policy()
    research = _parse(text_research)
    vision_data = _parse(vision)
    listing_text = listing_text or ""
    facts = _dict(research.get("listing_facts"))
    vin_check = _dict(research.get("vin_check"))
    year = _year(facts.get("year"), listing_text)
    age_bucket = _age_bucket(year)

    findings: list[dict[str, Any]] = []
    normal_wear: list[dict[str, Any]] = []
    inspection_points: list[dict[str, Any]] = []
    missing_information: list[dict[str, Any]] = []
    buyer_actions: list[str] = []
    triggers: list[dict[str, Any]] = []

    vin_present = _truthy(vin_check.get("vin_present")) or not _missing(facts.get("vin"))
    vin_format = _text(vin_check.get("format_check")).lower()
    if not vin_present:
        missing_information.append({"code": "VIN_MISSING", "material": True})
        buyer_actions.append("Vyžiadať VIN od predajcu a následne ho preveriť cez Cebia, CarVertical alebo oficiálnu službu.")
    elif vin_format == "problem":
        triggers.append({"family": "identity", "level": "ORANGE", "code": "VIN_INVALID"})
        findings.append({"code": "VIN_INVALID", "family": "identity", "buyer_impact": "identity_legal", "severity": "medium", "confidence": "HIGH"})
        buyer_actions.append("Pred ďalším krokom vysvetliť alebo opraviť problematický VIN.")
    else:
        buyer_actions.append("Preveriť uvedený VIN cez Cebia, CarVertical, register zvolávacích akcií alebo inú oficiálnu službu.")

    if _missing(facts.get("service_history")):
        missing_information.append({"code": "SERVICE_HISTORY_MISSING", "material": True})
        buyer_actions.append("Vyžiadať servisnú históriu a faktúry.")

    photos_provided = _truthy(vision_data.get("photos_provided"))
    limitations = [
        item for item in _list(vision_data.get("photo_limitations"))
        if _material_photo_limitation(item)
    ]
    if not photos_provided:
        missing_information.append({"code": "NO_USABLE_PHOTOS", "material": True})
        buyer_actions.append("Vyžiadať použiteľné fotografie vozidla pred rozhodnutím o obhliadke.")
    elif limitations:
        missing_information.append({"code": "PARTIAL_PHOTO_COVERAGE", "material": True})

    visual_sections = (
        ("exterior_observations", "cosmetic"),
        ("interior_observations", "cosmetic"),
        ("dashboard_or_warning_lights", "safety"),
        ("visible_red_flags", "safety"),
    )
    cosmetic_modifier = 0
    for section, default_impact in visual_sections:
        for raw in _list(vision_data.get(section)):
            item = _dict(raw)
            assessment = _text(item.get("assessment")).lower()
            observation = _text(item.get("observation") or item.get("red_flag"))
            reference = _text(item.get("photo_label"))
            if assessment in {"reassuring", "neutral"}:
                _add_unique(normal_wear, {"section": section, "observation": observation, "reference": reference, "assessment": assessment}, ("section", "observation", "reference"))
                continue
            if assessment != "concern":
                if observation:
                    _add_unique(inspection_points, {"family": section, "reason": observation, "reference": reference}, ("family", "reason", "reference"))
                continue

            severity = _text(item.get("severity")).lower()
            confidence = _confidence(item.get("confidence"))
            impact = _text(item.get("buyer_impact")).lower() or default_impact
            age_context = _text(item.get("age_context")).lower() or "unknown"
            if severity not in {"minor", "medium", "serious"} or confidence == "LOW" or not reference:
                _add_unique(inspection_points, {"family": section, "reason": observation, "reference": reference}, ("family", "reason", "reference"))
                continue
            finding = {"code": f"VISUAL_{impact.upper()}", "family": section, "buyer_impact": impact, "severity": severity, "confidence": confidence, "reference": reference, "evidence": observation, "age_context": age_context}
            _add_unique(findings, finding, ("family", "evidence", "reference"))

            if impact == "cosmetic":
                cosmetic_modifier += int(policy["age_cosmetic_modifiers"][age_bucket].get(severity, 0))
                if severity == "medium" and (age_bucket == "0_3" or age_context == "worse_than_expected"):
                    triggers.append({"family": section, "level": "YELLOW", "code": "UNUSUAL_COSMETIC_WEAR"})
                elif severity == "serious":
                    triggers.append({"family": section, "level": "YELLOW", "code": "SERIOUS_COSMETIC_DAMAGE"})
                else:
                    normal_wear.append(finding)
            elif severity == "minor":
                triggers.append({"family": section, "level": "YELLOW", "code": finding["code"]})
            else:
                triggers.append({"family": section, "level": "ORANGE", "code": finding["code"]})

    for conflict in _list(research.get("data_conflicts")):
        item = _dict(conflict)
        if not _text(item.get("source_a")) or not _text(item.get("source_b")):
            continue
        importance = _text(item.get("importance")).upper()
        issue = _text(item.get("issue"))
        if "vin" in issue.lower() and importance == "HIGH":
            triggers.append({"family": "identity", "level": "RED", "code": "VIN_CONFLICT"})
        elif importance == "HIGH":
            triggers.append({"family": "listing_conflict", "level": "ORANGE", "code": "SOURCED_CONFLICT"})
        elif importance == "MEDIUM":
            triggers.append({"family": "listing_conflict", "level": "YELLOW", "code": "SOURCED_CONFLICT"})

    for risk in _list(research.get("technical_risks")) + _list(research.get("knowledge_base_findings")):
        item = _dict(risk)
        category = _text(item.get("evidence_category")).upper()
        issue = _text(item.get("issue") or item.get("risk"))
        component = _text(item.get("component"))
        specific = _text(item.get("specific_vehicle_evidence"))
        if category not in {"CONFIRMED", "LISTING_CLAIM", "VISUAL_INDICATION"} or not specific:
            _add_unique(inspection_points, {"family": "model_level", "component": component, "reason": issue}, ("family", "component", "reason"))
            continue
        level = _text(item.get("risk_level")).upper()
        confidence = _confidence(item.get("confidence"))
        findings.append({"code": "SPECIFIC_TECHNICAL_CONCERN", "family": "technical", "buyer_impact": "mechanical", "severity": level.lower(), "confidence": confidence, "evidence": specific})
        if level == "HIGH" and confidence == "HIGH":
            triggers.append({"family": "technical", "level": "ORANGE", "code": "SPECIFIC_TECHNICAL_CONCERN"})
        else:
            triggers.append({"family": "technical", "level": "YELLOW", "code": "SPECIFIC_TECHNICAL_CONCERN"})

    hard_stops = [item for item in _list(research.get("confirmed_hard_stops")) if _dict(item).get("authoritative") is True]
    if hard_stops:
        triggers.append({"family": "authoritative", "level": "EXTREME", "code": "CONFIRMED_HARD_STOP"})

    material_gaps = sum(1 for item in missing_information if item.get("material"))
    parse_failed = bool(research.get("_parse_error") or vision_data.get("_parse_error"))
    if parse_failed or not photos_provided or material_gaps >= 2:
        evidence_quality = "LOW"
    elif material_gaps == 1:
        evidence_quality = "MEDIUM"
    else:
        evidence_quality = "HIGH"

    highest = "WORTH_INSPECTING"
    level_to_status = {"YELLOW": "INSPECT_WITH_RESERVATIONS", "ORANGE": "RESOLVE_BEFORE_PROCEEDING", "RED": "HIGH_RISK", "EXTREME": "DO_NOT_PROCEED"}
    for trigger in triggers:
        candidate = level_to_status[trigger["level"]]
        if STATUS_RANK[candidate] > STATUS_RANK[highest]:
            highest = candidate
    orange_families = {item["family"] for item in triggers if item["level"] == "ORANGE"}
    if len(orange_families) >= 2 and STATUS_RANK[highest] < STATUS_RANK["HIGH_RISK"]:
        highest = "HIGH_RISK"
        triggers.append({"family": "combined", "level": "RED", "code": "MULTIPLE_MATERIAL_BLOCKERS"})
    if evidence_quality == "LOW" and highest == "WORTH_INSPECTING":
        highest = "INSPECT_WITH_RESERVATIONS"
        triggers.append({"family": "evidence", "level": "YELLOW", "code": "LOW_EVIDENCE_CAP"})

    band = policy["bands"][highest]
    additional = max(0, len({(item["family"], item["code"]) for item in triggers}) - 1)
    additional_deduction = min(policy["additional_trigger_cap"], additional * policy["additional_trigger_deduction"])
    cosmetic_deduction = min(policy["cosmetic_modifier_cap"], cosmetic_modifier)
    evidence_deduction = policy["evidence_quality_deduction"][evidence_quality]
    score = max(band["floor"], band["ceiling"] - cosmetic_deduction - additional_deduction - evidence_deduction)

    return {
        "schema_version": 2,
        "policy_version": policy["policy_version"],
        "calibration_status": policy["calibration_status"],
        "decision_status": highest,
        "allowed_final_verdict": band["verdict"],
        "screening_score": score,
        "evidence_quality": evidence_quality,
        "vehicle_specific_findings": findings,
        "normal_wear_observations": normal_wear,
        "model_level_inspection_points": inspection_points,
        "missing_information": missing_information,
        "buyer_actions": list(dict.fromkeys(buyer_actions)),
        "gate_triggers": triggers,
        "score_breakdown": {
            "band_ceiling": band["ceiling"],
            "cosmetic_deduction": cosmetic_deduction,
            "additional_trigger_deduction": additional_deduction,
            "evidence_quality_deduction": evidence_deduction,
            "final_score": score,
        },
    }


def safe_yellow_fallback(reason: str = "v2 scorer failure") -> dict[str, Any]:
    return {
        "schema_version": 2,
        "policy_version": 2,
        "calibration_status": "UNCALIBRATED",
        "decision_status": "INSPECT_WITH_RESERVATIONS",
        "allowed_final_verdict": "🟡 PRIJATEĽNÁ KÚPA",
        "screening_score": 75,
        "evidence_quality": "LOW",
        "vehicle_specific_findings": [],
        "normal_wear_observations": [],
        "model_level_inspection_points": [],
        "missing_information": [{"code": "SCORER_FAILURE", "material": True}],
        "buyer_actions": [],
        "gate_triggers": [{"family": "system", "level": "YELLOW", "code": "SCORER_FAILURE"}],
        "score_breakdown": {"reason": reason, "final_score": 75},
    }
