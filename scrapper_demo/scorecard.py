"""Deterministic buyer-facing scorecard derived from structured evidence."""

from __future__ import annotations

import json
import unicodedata
from typing import Any


MISSING_VALUES = {"", "unknown", "neuvedene", "nezname", "none", "null"}
STATUS_CEILINGS = {
    "WORTH_INSPECTING": 90,
    "INSPECT_WITH_RESERVATIONS": 75,
    "RESOLVE_BEFORE_PROCEEDING": 55,
    "HIGH_RISK": 35,
    "DO_NOT_PROCEED": 15,
}


def _parse(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or ""))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFKD", _text(value))
    return "".join(ch for ch in text if not unicodedata.combining(ch)).lower()


def _missing(value: Any) -> bool:
    return _fold(value) in MISSING_VALUES


def _clamp(value: float) -> int:
    return max(0, min(100, int(round(value))))


def _transparency_score(research: dict[str, Any], vision: dict[str, Any]) -> int:
    facts = _dict(research.get("listing_facts"))
    vin = _dict(research.get("vin_check"))
    score = 100
    vin_present = bool(_text(facts.get("vin"))) or vin.get("vin_present") is True
    if not vin_present:
        score -= 22
    elif _fold(vin.get("format_check")) == "problem":
        score -= 35
    if _missing(facts.get("service_history")):
        score -= 20
    if _missing(facts.get("mileage")) and facts.get("advertised_mileage_km") in (None, ""):
        score -= 15
    if _missing(facts.get("origin_or_country")):
        score -= 5
    if _missing(facts.get("seller")):
        score -= 5
    for conflict in _list(research.get("data_conflicts")):
        importance = _fold(_dict(conflict).get("importance"))
        score -= 15 if importance == "high" else 8 if importance == "medium" else 3
    if vision.get("photos_provided") is not True:
        score -= 15
    if research.get("_parse_error") or vision.get("_parse_error"):
        score -= 35
    return _clamp(score)


def _market_score(research: dict[str, Any]) -> int | None:
    market = _dict(research.get("market_assessment"))
    comparables = [
        item for item in _list(research.get("market_comparables"))
        if isinstance(item, dict) and item.get("verified_url") is True and _text(item.get("source_url"))
    ]
    if market.get("available") is not True or not comparables:
        return None
    price_view = _fold(market.get("price_view"))
    base = {
        "fair": 82,
        "rather_cheap": 68,
        "rather_expensive": 45,
        "unclear": 50,
        "requires_manual_verification": 42,
    }.get(price_view, 58)
    base += min(8, max(0, len(comparables) - 1) * 3)
    return _clamp(base)


def _risk_deduction(item: dict[str, Any]) -> int:
    level = _fold(item.get("risk_level") or item.get("severity"))
    deduction = {"high": 25, "critical": 32, "medium": 15, "low": 7}.get(level, 10)
    category = _fold(item.get("evidence_category"))
    if category in {"confirmed", "visual_indication", "listing_claim"} and _text(item.get("specific_vehicle_evidence")):
        deduction += 6
    return deduction


def _component_score(research: dict[str, Any], keywords: tuple[str, ...]) -> int | None:
    matched: list[dict[str, Any]] = []
    for raw in _list(research.get("technical_risks")):
        item = _dict(raw)
        haystack = _fold(f"{item.get('component', '')} {item.get('issue', '')}")
        if any(keyword in haystack for keyword in keywords):
            matched.append(item)
    if not matched:
        return None
    score = 88
    score -= min(58, sum(_risk_deduction(item) for item in matched))
    if _missing(_dict(research.get("listing_facts")).get("service_history")):
        score -= 8
    return _clamp(score)


def _visual_score(vision: dict[str, Any]) -> int | None:
    if vision.get("photos_provided") is not True:
        return None
    score = 82
    reassuring = 0
    for section in ("exterior_observations", "interior_observations", "dashboard_or_warning_lights"):
        for raw in _list(vision.get(section)):
            item = _dict(raw)
            assessment = _fold(item.get("assessment"))
            severity = _fold(item.get("severity"))
            if assessment == "reassuring":
                reassuring += 1
            if assessment == "concern" or severity in {"minor", "medium", "serious"}:
                score -= {"serious": 28, "medium": 14, "minor": 5}.get(severity, 8)
    score += min(8, reassuring * 2)
    score -= min(30, len(_list(vision.get("visible_red_flags"))) * 15)
    coverage = _dict(vision.get("view_coverage"))
    score -= sum(4 for key in ("exterior", "interior", "dashboard", "tires") if _fold(coverage.get(key)) == "missing")
    return _clamp(score)


def _service_readiness_score(research: dict[str, Any]) -> int:
    """Estimate how prepared the car appears for near-term ownership service."""
    facts = _dict(research.get("listing_facts"))
    service_history = _fold(facts.get("service_history"))
    if _missing(service_history):
        score = 48
    elif any(term in service_history for term in ("faktur", "doklad", "zaznam")):
        score = 82
    else:
        score = 68
    likely_costs = [
        _dict(item)
        for item in _list(research.get("expected_costs"))
        if _fold(_dict(item).get("cost_type")) in {"initial_service", "diagnostic"}
    ]
    total_high = 0.0
    urgent_count = 0
    for item in likely_costs:
        high = item.get("estimated_cost_eur_high")
        low = item.get("estimated_cost_eur_low")
        estimate = high if isinstance(high, (int, float)) else low
        if isinstance(estimate, (int, float)):
            total_high += max(0.0, float(estimate))
        if _fold(item.get("urgency")) in {"high", "critical"}:
            urgent_count += 1
    if total_high >= 1_500:
        score -= 30
    elif total_high >= 900:
        score -= 22
    elif total_high >= 500:
        score -= 14
    elif total_high >= 250:
        score -= 7
    score -= min(12, urgent_count * 4)
    return _clamp(score)


def _backend_score(risk_score: dict[str, Any]) -> int:
    if isinstance(risk_score.get("screening_score"), (int, float)):
        return _clamp(float(risk_score["screening_score"]))
    status = _text(risk_score.get("decision_status"))
    return {
        "WORTH_INSPECTING": 86,
        "INSPECT_WITH_RESERVATIONS": 70,
        "RESOLVE_BEFORE_PROCEEDING": 50,
        "HIGH_RISK": 30,
        "DO_NOT_PROCEED": 12,
    }.get(status, 55)


def _confidence(
    research: dict[str, Any],
    vision: dict[str, Any],
    risk_score: dict[str, Any],
    scores: dict[str, int | None],
) -> str:
    quality = _fold(risk_score.get("evidence_quality") or _dict(research.get("evidence_summary")).get("overall_confidence"))
    available_count = sum(value is not None for value in scores.values())
    if research.get("_parse_error") or vision.get("_parse_error") or available_count <= 3:
        return "LOW"
    if quality in {"high", "vysoka"} and available_count >= 5:
        return "HIGH"
    if quality in {"low", "nizka"}:
        return "LOW"
    return "MEDIUM"


def build_buyer_scorecard(
    text_research: Any,
    vision: Any,
    risk_score: dict[str, Any],
) -> dict[str, Any]:
    """Build scores where 100 consistently means a more favorable profile."""
    research = _parse(text_research)
    vision_data = _parse(vision)
    transparency = _transparency_score(research, vision_data)
    market = _market_score(research)
    engine = _component_score(research, ("motor", "engine", "rozvod", "turbo", "dpf", "egr"))
    transmission = _component_score(
        research,
        ("prevodov", "automat", "transmission", "gearbox", "spojk", "dsg", "cvt", "pohon", "4x4", "awd", "diferencial"),
    )
    visual = _visual_score(vision_data)
    service = _service_readiness_score(research)
    scores: dict[str, int | None] = {
        "listing_transparency": transparency,
        "market_position": market,
        "engine_profile": engine,
        "transmission_profile": transmission,
        "visual_condition": visual,
        "service_readiness": service,
    }
    weights = {
        "listing_transparency": 0.18,
        "market_position": 0.18,
        "engine_profile": 0.18,
        "transmission_profile": 0.16,
        "visual_condition": 0.15,
        "service_readiness": 0.15,
    }
    available_weight = sum(weights[key] for key, value in scores.items() if value is not None)
    weighted = sum(
        value * weights[key]
        for key, value in scores.items()
        if value is not None
    ) / available_weight
    overall = _clamp(weighted * 0.65 + _backend_score(risk_score) * 0.35)
    ceiling = STATUS_CEILINGS.get(_text(risk_score.get("decision_status")), 75)
    overall = min(overall, ceiling)
    return {
        "schema_version": 1,
        "scale_direction": "higher_is_better",
        "scores": scores,
        "overall_score": overall,
        "confidence": _confidence(research, vision_data, risk_score, scores),
    }
