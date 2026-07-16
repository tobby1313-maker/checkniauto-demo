"""Customer-safe presentation model assembled from canonical job artifacts."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from scrapper_demo.storage import ListingJobRepository
from scrapper_demo.verdicts import STATUS_RANK, status_for_label


VERDICT_LABELS = {
    "sk": {
        "WORTH_INSPECTING": "Stojí za obhliadku",
        "INSPECT_WITH_RESERVATIONS": "Najprv preveriť",
        "RESOLVE_BEFORE_PROCEEDING": "Riešiť len s výhradami",
        "HIGH_RISK": "Skôr neriešiť",
        "DO_NOT_PROCEED": "Ruky preč",
    },
    "en": {
        "WORTH_INSPECTING": "Worth checking out",
        "INSPECT_WITH_RESERVATIONS": "Verify first",
        "RESOLVE_BEFORE_PROCEEDING": "Proceed with reservations",
        "HIGH_RISK": "Probably skip",
        "DO_NOT_PROCEED": "Walk away",
    },
}

VERDICT_SUMMARIES = {
    "sk": {
        "WORTH_INSPECTING": "Inzerát má dobrý základ. Pred kúpou stále odporúčame nezávislú kontrolu.",
        "INSPECT_WITH_RESERVATIONS": "Auto môže byť zaujímavé, no pred cestou treba doplniť alebo overiť dôležité údaje.",
        "RESOLVE_BEFORE_PROCEEDING": "Pokračovať má zmysel až po vyriešení významných neistôt a cielenej kontrole.",
        "HIGH_RISK": "Zistené riziká výrazne znižujú zmysel pokračovať bez silných dôkazov a odbornej kontroly.",
        "DO_NOT_PROCEED": "Dostupné dôkazy nepodporujú ďalší záväzok voči tomuto vozidlu.",
    },
    "en": {
        "WORTH_INSPECTING": "The listing has a good foundation. An independent inspection is still recommended.",
        "INSPECT_WITH_RESERVATIONS": "The car may be interesting, but important facts should be verified before travelling.",
        "RESOLVE_BEFORE_PROCEEDING": "Proceed only after the material uncertainties are resolved and checked.",
        "HIGH_RISK": "The identified risks make proceeding difficult to justify without strong evidence and inspection.",
        "DO_NOT_PROCEED": "The available evidence does not support making a further commitment to this vehicle.",
    },
}

VERDICT_TONES = {
    "WORTH_INSPECTING": "good",
    "INSPECT_WITH_RESERVATIONS": "warn",
    "RESOLVE_BEFORE_PROCEEDING": "warn",
    "HIGH_RISK": "risk",
    "DO_NOT_PROCEED": "risk",
}

_PRIVATE_SCORE_SECTION = re.compile(
    r"(?ims)^#{2,4}\s+(?:Sk[oó]re anal[yý]zy|Analysis score)\s*$.*?(?=^#{1,4}\s+|\Z)"
)


def _public_report_markdown(value: str) -> str:
    """Remove the legacy numeric scorecard from otherwise public report prose."""
    cleaned = _PRIVATE_SCORE_SECTION.sub("", _text(value))
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _first_text(*values: Any) -> str:
    for value in values:
        candidate = _text(value)
        if candidate:
            return candidate
    return ""


def _component_label(value: Any) -> str:
    if not isinstance(value, dict):
        return _text(value)
    return _first_text(
        value.get("marketing_name"),
        value.get("name"),
        value.get("type"),
        value.get("family"),
        value.get("code"),
    )


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    cleaned = re.sub(r"[^0-9,.-]", "", _text(value))
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif cleaned.count(",") == 1:
        left, right = cleaned.split(",")
        cleaned = f"{left}.{right}" if len(right) <= 2 else left + right
    elif cleaned.count(".") == 1:
        left, right = cleaned.split(".")
        cleaned = left + right if len(right) == 3 else cleaned
    elif cleaned.count(".") > 1:
        cleaned = cleaned.replace(".", "")
    try:
        number = float(cleaned)
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _clean_string_list(values: Any, *, limit: int = 12) -> list[str]:
    result: list[str] = []
    for value in _list(values):
        candidate = _text(value)
        if candidate and candidate not in result:
            result.append(candidate)
        if len(result) >= limit:
            break
    return result


def _language(metadata: dict[str, Any]) -> str:
    return "en" if _text(metadata.get("output_language")).lower().startswith("en") else "sk"


def _status(risk: dict[str, Any]) -> str:
    candidate = _text(risk.get("decision_status"))
    if candidate in STATUS_RANK:
        return candidate
    legacy = status_for_label(_text(risk.get("allowed_final_verdict")))
    return legacy or "RESOLVE_BEFORE_PROCEEDING"


def _source_name(source_url: str, raw: dict[str, Any]) -> str:
    explicit = _first_text(raw.get("source"), raw.get("portal"))
    if explicit and explicit.lower() != "manual":
        return explicit
    try:
        host = urlparse(source_url).hostname or ""
    except ValueError:
        host = ""
    return host.removeprefix("www.") or ("Manual input" if raw.get("source") == "manual" else "")


def _normalize_observations(values: Any) -> list[str]:
    result: list[str] = []
    for value in _list(values):
        if isinstance(value, dict):
            candidate = _first_text(
                value.get("observation"),
                value.get("red_flag"),
                value.get("finding"),
                value.get("description"),
                value.get("summary"),
            )
        else:
            candidate = _text(value)
        if candidate and candidate not in result:
            result.append(candidate)
    return result


def _finding(
    *,
    tone: str,
    title: Any,
    detail: Any = "",
    action: Any = "",
    category: str = "",
) -> dict[str, str] | None:
    clean_title = _text(title)
    if not clean_title:
        return None
    return {
        "tone": tone,
        "title": clean_title,
        "detail": _text(detail),
        "action": _text(action),
        "category": category,
    }


def _priority_findings(
    research: dict[str, Any],
    vision: dict[str, Any],
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []

    for item in _list(research.get("missing_or_uncertain_data")):
        data = _dict(item)
        severity = _text(data.get("severity")).lower()
        found = _finding(
            tone="risk" if severity == "high" else "warn",
            title=data.get("item"),
            detail=data.get("why_it_matters"),
            category="missing_information",
        )
        if found:
            findings.append(found)

    for item in _list(research.get("data_conflicts")):
        data = _dict(item)
        found = _finding(
            tone="risk" if _text(data.get("importance")).upper() == "HIGH" else "warn",
            title=data.get("issue"),
            detail=data.get("interpretation"),
            category="data_conflict",
        )
        if found:
            findings.append(found)

    for item in _list(research.get("technical_risks")):
        data = _dict(item)
        title = " — ".join(
            value for value in (_text(data.get("component")), _text(data.get("issue"))) if value
        )
        found = _finding(
            tone="risk" if _text(data.get("risk_level")).upper() == "HIGH" else "warn",
            title=title,
            detail=data.get("buyer_impact"),
            action=data.get("verification_action"),
            category="technical_risk",
        )
        if found:
            findings.append(found)

    for observation in _normalize_observations(vision.get("visible_red_flags")):
        found = _finding(
            tone="risk",
            title=observation,
            category="visual_red_flag",
        )
        if found:
            findings.append(found)

    visual_verdict = _text(vision.get("visual_verdict"))
    if visual_verdict:
        found = _finding(
            tone="good",
            title="Visual review",
            detail=visual_verdict,
            category="visual_review",
        )
        if found:
            findings.append(found)

    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in findings:
        key = item["title"].casefold()
        if key not in seen:
            seen.add(key)
            deduped.append(item)
        if len(deduped) >= 6:
            break
    return deduped


def _buyer_actions(risk: dict[str, Any], research: dict[str, Any]) -> list[str]:
    candidates = _clean_string_list(risk.get("buyer_actions"), limit=10)
    if not candidates:
        candidates = _clean_string_list(risk.get("buyer_priority_checks"), limit=10)
    for item in _list(research.get("technical_risks")):
        action = _text(_dict(item).get("verification_action"))
        if action and action not in candidates:
            candidates.append(action)
    for item in _list(research.get("missing_or_uncertain_data")):
        data = _dict(item)
        action = _first_text(data.get("required_action"), data.get("why_it_matters"))
        if action and action not in candidates:
            candidates.append(action)
    return candidates[:8]


def _seller_message(actions: list[str], language: str) -> str:
    selected = [action.rstrip(". ") for action in actions[:4] if action]
    if language == "en":
        if not selected:
            return "Hello, please send the VIN and available service documentation before the viewing. Thank you."
        return "Hello, before the viewing please help me verify: " + "; ".join(selected) + ". Thank you."
    if not selected:
        return "Dobrý deň, pred obhliadkou prosím o VIN a dostupnú servisnú dokumentáciu. Ďakujem."
    return "Dobrý deň, pred obhliadkou si prosím potrebujem overiť: " + "; ".join(selected) + ". Ďakujem."


def _costs(research: dict[str, Any]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    initial_low = 0
    initial_high = 0
    conditional_low = 0
    conditional_high = 0
    initial_available = False
    conditional_available = False
    for raw_item in _list(research.get("expected_costs")):
        item = _dict(raw_item)
        low = _number(item.get("estimated_cost_eur_low"))
        high = _number(item.get("estimated_cost_eur_high"))
        cost_type = _text(item.get("cost_type"))
        normalized = {
            "item": _text(item.get("item")),
            "why": _text(item.get("why")),
            "low_eur": low,
            "high_eur": high,
            "cost_type": cost_type,
            "urgency": _text(item.get("urgency")),
            "basis": _text(item.get("basis")),
        }
        if normalized["item"]:
            items.append(normalized)
        if low is None and high is None:
            continue
        use_low = int(low or 0)
        use_high = int(high if high is not None else use_low)
        if cost_type in {"initial_service", "diagnostic"}:
            initial_available = True
            initial_low += use_low
            initial_high += use_high
        else:
            conditional_available = True
            conditional_low += use_low
            conditional_high += use_high
    return {
        "items": items,
        "initial_service": {
            "available": initial_available,
            "low_eur": initial_low if initial_available else None,
            "high_eur": initial_high if initial_available else None,
        },
        "conditional_repairs": {
            "available": conditional_available,
            "low_eur": conditional_low if conditional_available else None,
            "high_eur": conditional_high if conditional_available else None,
        },
    }


def _technical_risks(research: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw_item in _list(research.get("technical_risks")):
        item = _dict(raw_item)
        component = _text(item.get("component"))
        issue = _text(item.get("issue"))
        if not component and not issue:
            continue
        result.append(
            {
                "component": component,
                "issue": issue,
                "risk_level": _text(item.get("risk_level")),
                "evidence_category": _text(item.get("evidence_category")),
                "buyer_impact": _text(item.get("buyer_impact")),
                "specific_vehicle_evidence": _text(item.get("specific_vehicle_evidence")),
                "verification_action": _text(item.get("verification_action")),
                "low_eur": _number(item.get("estimated_cost_eur_low")),
                "high_eur": _number(item.get("estimated_cost_eur_high")),
                "confidence": _text(item.get("confidence")),
            }
        )
    return result


def _market(benchmark: dict[str, Any], research: dict[str, Any]) -> dict[str, Any]:
    assessment = _dict(research.get("market_assessment"))
    benchmark_comparables = _list(benchmark.get("accepted_comparables"))
    legacy_comparables = _list(research.get("market_comparables"))
    comparables: list[dict[str, Any]] = []
    for raw_item in benchmark_comparables or legacy_comparables:
        item = _dict(raw_item)
        url = _first_text(item.get("detail_url"), item.get("source_url"), item.get("url"))
        display = item.get("display_in_report") is True or item.get("customer_facing") is True
        verified = bool(benchmark_comparables) or item.get("verified_url") is True
        if not display or not verified or not url.startswith(("http://", "https://")):
            continue
        comparables.append(
            {
                "title": _first_text(
                    item.get("title"), item.get("description"), item.get("model"), item.get("portal")
                ),
                "price_eur": _number(item.get("price_eur")),
                "year": _integer(item.get("year")),
                "mileage_km": _integer(item.get("mileage_km")),
                "portal": _text(item.get("portal")),
                "url": url,
            }
        )
    return {
        "available": benchmark.get("available") is True or assessment.get("benchmark_available") is True,
        "confidence": _first_text(benchmark.get("confidence"), assessment.get("benchmark_confidence")),
        "advertised_price_eur": _number(
            benchmark.get("advertised_price_eur") or assessment.get("advertised_price_eur")
        ),
        "median_eur": _number(benchmark.get("median_eur") or assessment.get("benchmark_median_eur")),
        "local_median_eur": _number(
            benchmark.get("local_market_median_eur") or assessment.get("local_market_median_eur")
        ),
        "price_delta_percent": _number(
            benchmark.get("price_delta_percent") or assessment.get("price_delta_percent")
        ),
        "price_view": _first_text(benchmark.get("price_view"), assessment.get("price_view")),
        "scope": _first_text(benchmark.get("benchmark_scope"), assessment.get("benchmark_scope")),
        "comparables": comparables,
        "limitations": _clean_string_list(benchmark.get("limitations"), limit=8)
        or ([_text(assessment.get("limitations"))] if _text(assessment.get("limitations")) else []),
    }


def _sources(research: dict[str, Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for raw_item in _list(research.get("sources_used")):
        item = _dict(raw_item)
        url = _text(item.get("source_url"))
        if item.get("verified_url") is not True or not url.startswith(("http://", "https://")):
            continue
        result.append(
            {
                "name": _first_text(item.get("source_name"), urlparse(url).hostname),
                "type": _text(item.get("source_type")),
                "reliability": _text(item.get("reliability")),
                "used_for": _text(item.get("used_for")),
                "url": url,
            }
        )
    for raw_item in _list(research.get("web_research_findings")):
        item = _dict(raw_item)
        url = _text(item.get("source_url"))
        if item.get("verified_url") is not True or not url.startswith(("http://", "https://")):
            continue
        if any(source["url"] == url for source in result):
            continue
        result.append(
            {
                "name": _first_text(item.get("source_name"), urlparse(url).hostname),
                "type": _text(item.get("source_type")),
                "reliability": _text(item.get("confidence")),
                "used_for": _text(item.get("claim")),
                "url": url,
            }
        )
    return result


def _pros(research: dict[str, Any], vision: dict[str, Any]) -> list[str]:
    evidence = _dict(research.get("evidence_summary"))
    result = _clean_string_list(evidence.get("strongest_evidence"), limit=4)
    for claim in _list(research.get("seller_claims")):
        item = _dict(claim)
        if _text(item.get("verification_status")).lower() in {"verified", "confirmed", "supported"}:
            candidate = _text(item.get("claim"))
            if candidate and candidate not in result:
                result.append(candidate)
    if not result:
        result.extend(_normalize_observations(vision.get("exterior_observations"))[:2])
    return result[:6]


def build_presentation_payload(
    repository: ListingJobRepository,
    slug: str,
    *,
    parsed: dict[str, Any],
    images: list[dict[str, str]],
    report_markdown: str,
) -> dict[str, Any]:
    """Build the stable public view model without exposing internal diagnostics."""
    repository.job_dir(slug, require=True)
    raw = _dict(repository.read_json(slug, "raw_data.json", default={}))
    facts = _dict(repository.read_json(slug, "listing_facts.json", default={}))
    identity = _dict(repository.read_json(slug, "component_identity.json", default={}))
    risk = _dict(repository.read_json(slug, "risk_score.json", default={}))
    research = _dict(repository.read_json(slug, "grok_research.json", default={}))
    vision = _dict(repository.read_json(slug, "gemini_vision.json", default={}))
    benchmark = _dict(repository.read_json(slug, "market_benchmark.json", default={}))
    metadata = _dict(repository.read_json(slug, "analysis_metadata.json", default={}))

    if not identity:
        identity = _dict(research.get("component_identity"))
    if not facts:
        facts = _dict(research.get("listing_facts"))

    language = _language(metadata)
    status = _status(risk)
    specs = _dict(parsed.get("specs"))
    source_url = _first_text(parsed.get("source_url"), raw.get("source_url"), raw.get("url"))
    evidence = _dict(research.get("evidence_summary"))
    buyer_actions = _buyer_actions(risk, research)
    findings = _priority_findings(research, vision)

    listing = {
        "slug": slug,
        "title": _first_text(facts.get("title"), parsed.get("title"), raw.get("title"), slug),
        "price_eur": _number(_first_text(facts.get("price"), parsed.get("price"), raw.get("price"))),
        "year": _integer(_first_text(facts.get("year"), specs.get("Year"), raw.get("year"))),
        "mileage_km": _integer(
            facts.get("advertised_mileage_km")
            or _first_text(facts.get("mileage"), specs.get("Mileage"), raw.get("mileage"))
        ),
        "vin": _first_text(facts.get("vin"), parsed.get("vin"), specs.get("VIN"), raw.get("vin")),
        "engine": _first_text(facts.get("engine"), specs.get("Engine"), raw.get("engine")),
        "fuel": _first_text(facts.get("fuel"), specs.get("Fuel"), raw.get("fuel")),
        "transmission": _first_text(
            facts.get("transmission"), specs.get("Transmission"), raw.get("transmission")
        ),
        "drivetrain": _first_text(_component_label(identity.get("drivetrain")), specs.get("Drivetrain")),
        "location": _first_text(parsed.get("location"), raw.get("location")),
        "source_url": source_url,
        "source_name": _source_name(source_url, raw),
        "scraped_at": _text(parsed.get("scraped_at")),
        "photos_count": len(images),
        "images": images,
    }

    safe_identity = {
        "status": _text(identity.get("identification_status")),
        "make": _first_text(facts.get("make"), raw.get("make")),
        "model": _first_text(facts.get("model"), raw.get("model")),
        "generation": _dict(identity.get("generation")),
        "engine": _dict(identity.get("engine")),
        "transmission": _dict(identity.get("transmission")),
        "drivetrain": _dict(identity.get("drivetrain")),
        "notes": _clean_string_list(identity.get("notes"), limit=8),
        "candidate_variants": [
            {
                "engine_code": _text(_dict(item).get("engine_code")),
                "transmission_code": _text(_dict(item).get("transmission_code")),
                "reason": _text(_dict(item).get("reason")),
            }
            for item in _list(identity.get("candidate_variants"))[:5]
            if any(_text(value) for value in _dict(item).values())
        ],
        "confidence_label": _first_text(
            _dict(identity.get("generation")).get("confidence"),
            identity.get("identification_status"),
        ),
    }

    return {
        "schema_version": 1,
        "language": language,
        "listing": listing,
        "verdict": {
            "status": status,
            "label": VERDICT_LABELS[language][status],
            "summary": VERDICT_SUMMARIES[language][status],
            "tone": VERDICT_TONES[status],
            "evidence_quality": _first_text(risk.get("evidence_quality"), evidence.get("overall_confidence"), "LOW").upper(),
        },
        "priority_findings": findings,
        "buyer_actions": buyer_actions,
        "seller_message": _seller_message(buyer_actions, language),
        "pros": _pros(research, vision),
        "technical_risks": _technical_risks(research),
        "costs": _costs(research),
        "market": _market(benchmark, research),
        "identity": safe_identity,
        "vin": _dict(research.get("vin_check")),
        "safety_and_recall": _dict(research.get("safety_and_recall")),
        "research_findings": [
            {
                "claim": _text(_dict(item).get("claim")),
                "buyer_impact": _text(_dict(item).get("buyer_impact")),
                "confidence": _text(_dict(item).get("confidence")),
                "evidence_category": _text(_dict(item).get("evidence_category")),
            }
            for item in _list(research.get("web_research_findings"))
            if _text(_dict(item).get("claim"))
        ],
        "vision": {
            "photos_provided": vision.get("photos_provided") is True,
            "visual_verdict": _text(vision.get("visual_verdict")),
            "photo_limitations": _clean_string_list(vision.get("photo_limitations"), limit=8),
            "exterior_observations": _normalize_observations(vision.get("exterior_observations")),
            "interior_observations": _normalize_observations(vision.get("interior_observations")),
            "warning_lights": _normalize_observations(vision.get("dashboard_or_warning_lights")),
            "visible_red_flags": _normalize_observations(vision.get("visible_red_flags")),
            "supported_observations": _normalize_observations(vision.get("supported_observations")),
            "missing_views": _clean_string_list(vision.get("missing_views"), limit=8),
            "mileage_wear_consistency": _dict(vision.get("mileage_wear_consistency")),
        },
        "sources": _sources(research),
        "report_markdown": _public_report_markdown(report_markdown),
    }


__all__ = ["build_presentation_payload"]
