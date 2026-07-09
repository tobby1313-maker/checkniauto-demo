"""Deterministic backend risk scoring for used-car analyses.

Models provide evidence. This module decides the score and allowed verdict.
It intentionally uses simple, inspectable rules rather than model reasoning.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any


VERDICTS = [
    "🟢 DOBRÁ KÚPA",
    "🟡 PRIJATEĽNÁ KÚPA",
    "🟠 ZVÁŽIŤ",
    "🔴 RIZIKOVÁ KÚPA",
    "⛔ EXTRÉMNE RIZIKO",
]

VERDICT_RANK = {verdict: index for index, verdict in enumerate(VERDICTS)}


def parse_model_json(value: Any) -> dict[str, Any]:
    """Parse raw model JSON, including common markdown fenced output."""
    if isinstance(value, dict):
        return value
    if value is None:
        return {}

    text = str(value).strip()
    if not text:
        return {}

    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S | re.I)
    if fence_match:
        text = fence_match.group(1).strip()
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {
            "_parse_error": True,
            "_raw_preview": text[:500],
        }
    return parsed if isinstance(parsed, dict) else {}


def calculate_risk_score(
    text_research: Any,
    vision: Any,
    listing_text: str | None = None,
) -> dict[str, Any]:
    research = parse_model_json(text_research)
    vision_data = parse_model_json(vision)
    listing_text = listing_text or ""

    applied_rules: list[dict[str, Any]] = []
    overrides: list[dict[str, str]] = []
    missing_flags: set[str] = set()
    priority_checks: list[str] = []

    facts = _as_dict(research.get("listing_facts"))
    vin_check = _as_dict(research.get("vin_check"))
    market = _as_dict(research.get("market_assessment"))

    vin_present = _truthy(vin_check.get("vin_present")) or bool(_clean(facts.get("vin")))
    vin_format = _clean(vin_check.get("format_check")).lower()
    vin_invalid = vin_present and vin_format == "problem"
    vin_missing_or_unverified = (not vin_present) or vin_format in {"skipped", "unknown", ""}
    vin_problem = vin_invalid or vin_missing_or_unverified
    if vin_invalid:
        missing_flags.add("VIN")
        _add_rule(applied_rules, "vin_invalid_or_conflicting", 2, "VIN je uvedene, ale format alebo udaje posobia problematicky.")
        priority_checks.append("Nechat predajcu vysvetlit VIN a overit ho mimo inzeratu pred rezervaciou auta.")
    elif vin_missing_or_unverified:
        missing_flags.add("VIN")
        _add_rule(applied_rules, "vin_missing_request_before_viewing", 0, "VIN nie je v inzerate; vyziadat ho od predajcu pred obhliadkou.")
        priority_checks.append("Poziadat predajcu o VIN pred obhliadkou a overit ho mimo inzeratu.")

    all_concern_checks = [
        item for item in _as_list(research.get("consistency_checks"))
        if _clean(_as_dict(item).get("result")).lower() == "concern"
    ]
    plate_only_concern_checks = [
        item for item in all_concern_checks
        if _is_registration_plate_only_concern(_as_dict(item))
    ]
    concern_checks = [
        item for item in all_concern_checks
        if item not in plate_only_concern_checks
    ]
    if plate_only_concern_checks:
        missing_flags.add("registration_plate")
        _add_rule(
            applied_rules,
            "registration_plate_needs_verification",
            0,
            "SPZ/ECV alebo registracny udaj treba overit, sam o sebe vsak nemusi znamenat problem auta.",
        )
        priority_checks.append("Overit SPZ/ECV s dokladmi a predajcom pri komunikacii alebo obhliadke.")
    if concern_checks:
        _add_rule(applied_rules, "obvious_listing_conflict", 2, "Textova analyza nasla rozpor v udajoch inzeratu.")
        priority_checks.append("Nechat predajcu vysvetlit rozpory v udajoch pred rezervaciou auta.")

    year = _extract_int(facts.get("year")) or _extract_year(listing_text)
    mileage = _extract_int(facts.get("mileage")) or _extract_mileage(listing_text)
    high_age_or_mileage = _is_old_or_high_mileage(year, mileage)

    service_history = _clean(facts.get("service_history"))
    service_missing = _is_missing(service_history)
    if service_missing:
        missing_flags.add("service_history")
    if service_missing and high_age_or_mileage:
        _add_rule(
            applied_rules,
            "unclear_service_history_for_old_or_high_mileage_car",
            1,
            "Pri starsom aute alebo vyssom najazde chyba jasna servisna historia.",
        )
        priority_checks.append("Vyziadat servisnu historiu, faktury a intervaly vymen.")

    photos_provided = _truthy(vision_data.get("photos_provided"))
    visual_verdict = _clean(vision_data.get("visual_verdict")).lower()
    photo_limitations = _as_list(vision_data.get("photo_limitations"))
    material_photo_limitations = [
        limitation
        for limitation in photo_limitations
        if not _is_benign_photo_limitation(limitation)
    ]
    weak_photos = (
        not photos_provided
        or bool(material_photo_limitations)
        or "nedost" in visual_verdict
        or "insufficient" in visual_verdict
    )
    if weak_photos:
        missing_flags.add("photos")
        _add_rule(applied_rules, "missing_or_weak_photos", 1, "Fotografie chybaju alebo maju obmedzenu vypovednu hodnotu.")
        priority_checks.append("Doplnit fotky karoserie, interieru, pneu, podvozku a pristrojovky.")

    minor_damage = _has_visual_severity(vision_data, {"minor", "medium"})
    serious_damage = _has_visual_severity(vision_data, {"serious"}) or bool(_as_list(vision_data.get("visible_red_flags")))
    if minor_damage:
        _add_rule(applied_rules, "visible_minor_damage", 1, "Fotografie ukazuju mensie alebo stredne viditelne nedostatky.")
    if serious_damage:
        _add_rule(applied_rules, "visible_serious_damage_or_red_flags", 2, "Vizuálna analyza oznacila vazne viditelne rizika alebo red flags.")
        priority_checks.append("Pred kupou urobit fyzicku kontrolu karoserie a diagnostiku.")

    kb_findings = [_as_dict(item) for item in _as_list(research.get("knowledge_base_findings"))]
    expensive_findings = [item for item in kb_findings if _mentions_expensive_risk(item)]
    high_conf_expensive = [item for item in expensive_findings if _is_high_confidence(item.get("confidence"))]
    if high_conf_expensive:
        _add_rule(applied_rules, "high_confidence_expensive_known_risk", 2, "Knowledge base obsahuje drahe zname riziko s vysokou istotou.")
    elif expensive_findings:
        _add_rule(applied_rules, "relevant_expensive_known_risk", 1, "Knowledge base obsahuje relevantne potencialne drahe riziko.")

    price_view = _clean(market.get("price_view")).lower()
    suspicious_price = price_view in {
        "rather_expensive",
        "rather_cheap",
        "suspicious",
        "requires_manual_verification",
    }
    if suspicious_price:
        risk_so_far = _sum_points(applied_rules)
        points = 2 if risk_so_far > 0 and price_view == "rather_cheap" else 1
        _add_rule(
            applied_rules,
            "price_suspicious_and_other_risks_exist" if points == 2 else "price_suspiciously_low_or_high",
            points,
            "Cenove porovnanie je nejasne alebo vyzaduje manualne overenie.",
        )
        missing_flags.add("market_comparison")

    if high_age_or_mileage:
        _add_rule(applied_rules, "high_age_or_mileage_expected_service", 1, "Vek alebo najazd auta zvysuje pravdepodobnost blizsich servisnych nakladov.")

    origin_missing = _is_missing(facts.get("origin_or_country"))
    seller_missing = _is_missing(facts.get("seller"))
    if origin_missing or seller_missing:
        if origin_missing:
            missing_flags.add("origin")
        if seller_missing:
            missing_flags.add("seller")
        _add_rule(applied_rules, "unclear_origin_or_seller_missing_key_info", 1, "Povod alebo typ predajcu nie je jasne uvedeny.")

    if _has_good_documentation(vin_problem, service_missing, weak_photos, serious_damage, origin_missing):
        _add_rule(applied_rules, "good_documentation_clear_origin_vin_good_photos", -1, "VIN, servis, povod a fotografie vyzeraju dostatocne transparentne.")

    if _has_excellent_profile(vin_problem, service_missing, weak_photos, serious_damage, minor_damage, mileage):
        _add_rule(applied_rules, "excellent_documentation_and_low_risk_profile", -2, "Profil auta vyzera nizkorizikovo a dobre zdokumentovane.")

    score = max(0, _sum_points(applied_rules))
    verdict = _verdict_for_score(score)

    if vin_invalid and service_missing:
        verdict = _cap_verdict(verdict, "🟠 ZVÁŽIŤ")
        _add_override(overrides, "invalid VIN + service history missing", "Final verdict cannot be better than 🟠 ZVÁŽIŤ.")
    if vin_invalid and weak_photos:
        verdict = _cap_verdict(verdict, "🟠 ZVÁŽIŤ")
        _add_override(overrides, "invalid VIN + weak photos", "Final verdict cannot be better than 🟠 ZVÁŽIŤ.")
    if serious_damage:
        verdict = _cap_verdict(verdict, "🟠 ZVÁŽIŤ")
        _add_override(overrides, "serious visible red flags", "Final verdict cannot be better than 🟠 ZVÁŽIŤ.")
    if concern_checks:
        verdict = _cap_verdict(verdict, "🔴 RIZIKOVÁ KÚPA")
        _add_override(overrides, "major listing contradiction", "Final verdict cannot be better than 🔴 RIZIKOVÁ KÚPA.")
    if price_view == "rather_cheap" and vin_invalid:
        verdict = _cap_verdict(verdict, "🔴 RIZIKOVÁ KÚPA")
        _add_override(overrides, "suspiciously low price + invalid VIN", "Final verdict cannot be better than 🔴 RIZIKOVÁ KÚPA.")
    if not photos_provided:
        verdict = _cap_verdict(verdict, "🟡 PRIJATEĽNÁ KÚPA")
        _add_override(overrides, "no photos", "Final verdict cannot be 🟢 DOBRÁ KÚPA.")

    if research.get("_parse_error"):
        missing_flags.add("text_research_json")
        priority_checks.append("Textova analyza nevratila validny JSON; vysledok brat konzervativne.")
    if vision_data.get("_parse_error"):
        missing_flags.add("vision_json")
        priority_checks.append("Vizuálna analyza nevratila validny JSON; fotografie overit manualne.")

    if not priority_checks:
        priority_checks.append("Overit VIN, servisnu historiu a stav auta pri fyzickej obhliadke.")

    return {
        "risk_score": score,
        "allowed_final_verdict": verdict,
        "applied_rules": applied_rules,
        "override_rules_applied": overrides,
        "missing_data_flags": sorted(missing_flags),
        "buyer_priority_checks": _dedupe(priority_checks),
    }


def _add_rule(rules: list[dict[str, Any]], rule: str, points: int, reason: str) -> None:
    rules.append({"rule": rule, "points": points, "reason": reason})


def _add_override(overrides: list[dict[str, str]], rule: str, effect: str) -> None:
    if not any(item.get("rule") == rule for item in overrides):
        overrides.append({"rule": rule, "effect": effect})


def _sum_points(rules: list[dict[str, Any]]) -> int:
    return sum(int(item.get("points") or 0) for item in rules)


def _verdict_for_score(score: int) -> str:
    if score <= 1:
        return "🟢 DOBRÁ KÚPA"
    if score <= 3:
        return "🟡 PRIJATEĽNÁ KÚPA"
    if score <= 6:
        return "🟠 ZVÁŽIŤ"
    if score <= 9:
        return "🔴 RIZIKOVÁ KÚPA"
    return "⛔ EXTRÉMNE RIZIKO"


def _cap_verdict(current: str, cap: str) -> str:
    return cap if VERDICT_RANK[current] < VERDICT_RANK[cap] else current


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _clean(value).lower() in {"true", "yes", "ano", "1", "present"}


def _is_missing(value: Any) -> bool:
    text = _clean(value).lower()
    return text in {"", "null", "none", "neuvedene", "neuvedené", "unknown", "nezname", "neznáme", "n/a"}


def _extract_int(value: Any) -> int | None:
    text = _clean(value).replace("\u00a0", " ")
    match = re.search(r"\d[\d\s.]*", text)
    if not match:
        return None
    try:
        return int(re.sub(r"[^\d]", "", match.group(0)))
    except ValueError:
        return None


def _extract_year(text: str) -> int | None:
    years = [int(match.group(0)) for match in re.finditer(r"\b(?:19|20)\d{2}\b", text or "")]
    current_year = datetime.now().year
    valid = [year for year in years if 1980 <= year <= current_year + 1]
    return valid[0] if valid else None


def _extract_mileage(text: str) -> int | None:
    match = re.search(r"(\d[\d\s.]{3,})\s*(?:km|kilomet)", text or "", re.I)
    if not match:
        return None
    try:
        return int(re.sub(r"[^\d]", "", match.group(1)))
    except ValueError:
        return None


def _is_old_or_high_mileage(year: int | None, mileage: int | None) -> bool:
    current_year = datetime.now().year
    return bool((year and current_year - year >= 8) or (mileage and mileage >= 150000))


def _has_visual_severity(vision: dict[str, Any], severities: set[str]) -> bool:
    sections = (
        "exterior_observations",
        "interior_observations",
        "dashboard_or_warning_lights",
    )
    for section in sections:
        for item in _as_list(vision.get(section)):
            severity = _clean(_as_dict(item).get("severity")).lower()
            if severity in severities:
                return True
    return False


def _mentions_expensive_risk(item: dict[str, Any]) -> bool:
    text = " ".join(_clean(item.get(key)).lower() for key in ("risk", "notes", "component"))
    keywords = (
        "drah",
        "expensive",
        "prevodov",
        "automat",
        "hybrid",
        "battery",
        "turbo",
        "dpf",
        "rozvod",
        "engine",
        "motor",
    )
    return any(keyword in text for keyword in keywords)


def _is_high_confidence(value: Any) -> bool:
    text = _clean(value).lower()
    return text in {"vysoka", "vysoká", "high"}


def _is_registration_plate_only_concern(item: dict[str, Any]) -> bool:
    text = " ".join(
        _clean(item.get(key)).lower()
        for key in ("check", "explanation", "item", "why_it_matters", "risk")
    )
    plate_keywords = (
        "spz",
        "ecv",
        "ečv",
        "evidencne cislo",
        "evidenčné číslo",
        "registration plate",
        "license plate",
        "number plate",
    )
    if not any(keyword in text for keyword in plate_keywords):
        return False

    identity_keywords = (
        "vin",
        "najazd",
        "nájazd",
        "mileage",
        "odometer",
        "rok",
        "year",
        "motor",
        "engine",
        "model",
        "cena",
        "price",
        "povod",
        "pôvod",
        "origin",
    )
    return not any(keyword in text for keyword in identity_keywords)


def _is_benign_photo_limitation(value: Any) -> bool:
    text = _clean(value).lower()
    if not text:
        return True

    hard_missing_phrases = (
        "missing from listing",
        "absent from listing",
        "absent from gallery",
        "no photos",
        "unusable",
        "nedost",
        "chybaju fotografie",
        "chybaju v inzerate",
        "nie su fotografie",
        "nie su v inzerate",
        "low resolution",
        "blur",
        "blurry",
        "dark",
        "cropped",
        "underbody",
        "podvoz",
        "slabom svetle",
        "tmav",
        "rozmaz",
        "nekval",
    )
    if any(phrase in text for phrase in hard_missing_phrases):
        return False

    benign_phrases = (
        "not assessable in detail",
        "visible only in overview",
        "visible_overview_only",
        "overview",
        "contact sheet",
        "thumbnail",
        "sample",
        "representative",
        "detail sample",
        "analyzed sample",
        "vzork",
        "prehlad",
        "nahlad",
    )
    return any(phrase in text for phrase in benign_phrases)


def _has_good_documentation(
    vin_problem: bool,
    service_missing: bool,
    weak_photos: bool,
    serious_damage: bool,
    origin_missing: bool,
) -> bool:
    return not any((vin_problem, service_missing, weak_photos, serious_damage, origin_missing))


def _has_excellent_profile(
    vin_problem: bool,
    service_missing: bool,
    weak_photos: bool,
    serious_damage: bool,
    minor_damage: bool,
    mileage: int | None,
) -> bool:
    return (
        not vin_problem
        and not service_missing
        and not weak_photos
        and not serious_damage
        and not minor_damage
        and (mileage is None or mileage < 80000)
    )


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
