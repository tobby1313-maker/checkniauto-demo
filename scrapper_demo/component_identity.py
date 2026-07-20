"""Normalize the short, grounded component-identification pass."""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any
from urllib.parse import urlparse


RESOLUTIONS = {"VERIFIED", "PROBABLE", "AMBIGUOUS", "UNKNOWN"}
CONFIDENCES = {"HIGH", "MEDIUM", "LOW"}
VERIFICATION_BASES = {
    "VIN_RECORD",
    "VEHICLE_DOCUMENT",
    "PHYSICAL_LABEL",
    "SPECIFICATION_MATCH",
    "MULTIPLE_CANDIDATES",
    "INSUFFICIENT",
}
DIRECT_VERIFICATION_BASES = {"VIN_RECORD", "VEHICLE_DOCUMENT", "PHYSICAL_LABEL"}
SOURCE_TYPES = {
    "OFFICIAL",
    "REGULATORY",
    "OEM_CATALOG",
    "TECHNICAL_PUBLICATION",
    "PARTS_CATALOG",
    "REPAIR_SOURCE",
    "OWNER_REPORT",
    "OTHER",
}


def _text(value: Any, limit: int = 240) -> str:
    normalized = " ".join(str(value or "").split()).strip()
    return normalized[:limit]


def _enum(value: Any, allowed: set[str], default: str) -> str:
    normalized = _text(value, 40).upper()
    return normalized if normalized in allowed else default


def _string_list(value: Any, limit: int = 6) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = _text(item)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _public_url(value: Any) -> str:
    url = _text(value, 800)
    try:
        parsed = urlparse(url)
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not host:
        return ""
    if host == "vertexaisearch.cloud.google.com" or host.endswith(
        ".vertexaisearch.cloud.google.com"
    ):
        return ""
    return url


def parse_first_json_object(value: Any) -> dict[str, Any]:
    """Decode the first JSON object while allowing trailing grounding citations."""
    if isinstance(value, dict):
        return value
    text = str(value or "").lstrip()
    start = text.find("{")
    if start < 0:
        return {}
    try:
        parsed, _end = json.JSONDecoder().raw_decode(text[start:])
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _fold_tokens(value: Any) -> set[str]:
    normalized = unicodedata.normalize("NFKD", _text(value, 500)).casefold()
    folded = "".join(char for char in normalized if not unicodedata.combining(char))
    return {
        token
        for token in re.findall(r"[a-z0-9]+", folded)
        if len(token) >= 2 and token not in {"the", "and", "for", "www", "com"}
    }


def _grounding_citations(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, str):
        return []
    heading = re.search(
        r"(?im)^#{2,4}\s+(?:Citacie z Google Search|Google Search citations)\s*$",
        value,
    )
    if not heading:
        return []
    result: list[dict[str, str]] = []
    for title, url in re.findall(
        r"\[([^\]\n]+)\]\((https?://[^\s)]+(?:\([^\s)]*\)[^\s)]*)*)\)",
        value[heading.end() :],
        re.IGNORECASE,
    ):
        public_url = _public_url(url)
        if public_url and not any(item["source_url"] == public_url for item in result):
            result.append(
                {"source_name": _text(title, 160), "source_url": public_url}
            )
        if len(result) >= 24:
            break
    return result


def _citation_url_for_source(
    source_name: str,
    citations: list[dict[str, str]],
    used_urls: set[str],
) -> str:
    source_tokens = _fold_tokens(source_name)
    if not source_tokens:
        return ""
    best_url = ""
    best_score = 0.0
    for citation in citations:
        url = citation["source_url"]
        if url in used_urls:
            continue
        citation_tokens = _fold_tokens(citation["source_name"])
        if not citation_tokens:
            continue
        overlap = len(source_tokens & citation_tokens)
        score = overlap / max(1, len(source_tokens | citation_tokens))
        if source_tokens <= citation_tokens or citation_tokens <= source_tokens:
            score = max(score, 0.8)
        if overlap >= 2 and score > best_score:
            best_score = score
            best_url = url
    if best_score >= 0.45:
        used_urls.add(best_url)
        return best_url
    return ""


def _generic_transmission_code(value: Any) -> bool:
    compact = re.sub(r"[^A-Z0-9]", "", _text(value).upper())
    return bool(
        compact in {"AT", "AUTO", "AUTOMATIC", "DCT", "DSG", "CVT", "EDCT"}
        or re.fullmatch(r"\d{1,2}(?:SPEED|ST|GEAR)?(?:AT|DCT|DSG|CVT)", compact)
    )


def _generic_drivetrain_code(value: Any) -> bool:
    compact = re.sub(r"[^A-Z0-9]", "", _text(value).upper())
    return compact in {"AWD", "4WD", "4X4", "HTRAC", "QUATTRO", "XDRIVE"}


def _component(value: Any, *, value_key: str = "name") -> dict[str, Any]:
    item = value if isinstance(value, dict) else {}
    result: dict[str, Any] = {
        value_key: _text(item.get(value_key)),
        "code": _text(item.get("code"), 120),
        "family": _text(item.get("family"), 160),
        "resolution": _enum(item.get("resolution"), RESOLUTIONS, "UNKNOWN"),
        "confidence": _enum(item.get("confidence"), CONFIDENCES, "LOW"),
        "verification_basis": _enum(
            item.get("verification_basis"), VERIFICATION_BASES, "INSUFFICIENT"
        ),
        "evidence_refs": _string_list(item.get("evidence_refs")),
    }
    if not any(result[key] for key in (value_key, "code", "family")):
        result["resolution"] = "UNKNOWN"
        result["confidence"] = "LOW"
    return result


def unknown_component_identity(reason: str = "") -> dict[str, Any]:
    result = {
        "schema_version": 1,
        "identification_status": "UNKNOWN",
        "generation": _component({}, value_key="name"),
        "engine": _component({}, value_key="marketing_name"),
        "transmission": _component({}, value_key="marketing_name"),
        "drivetrain": _component({}, value_key="type"),
        "candidate_variants": [],
        "sources": [],
        "notes": [],
    }
    if reason:
        result["notes"] = [_text(reason)]
    return result


def normalize_component_identity(value: Any) -> dict[str, Any]:
    """Return the stable component-identity contract from grounded model output."""
    data = parse_first_json_object(value)
    if not data:
        return unknown_component_identity("Component-identification output was not valid JSON.")

    citations = _grounding_citations(value)
    sources: list[dict[str, Any]] = []
    used_citation_urls: set[str] = set()
    for raw in data.get("sources", []) if isinstance(data.get("sources"), list) else []:
        if not isinstance(raw, dict):
            continue
        source_url = _public_url(raw.get("source_url"))
        if not source_url:
            source_url = _citation_url_for_source(
                _text(raw.get("source_name"), 160),
                citations,
                used_citation_urls,
            )
        source = {
            "source_id": _text(raw.get("source_id"), 80),
            "source_name": _text(raw.get("source_name"), 160),
            "source_url": source_url,
            "source_type": _enum(raw.get("source_type"), SOURCE_TYPES, "OTHER"),
            "used_for": _text(raw.get("used_for"), 200),
        }
        if source["source_name"] or source["source_url"]:
            sources.append(source)
        if len(sources) >= 30:
            break

    candidates: list[dict[str, Any]] = []
    raw_candidates = data.get("candidate_variants")
    if isinstance(raw_candidates, list):
        for raw in raw_candidates:
            if not isinstance(raw, dict):
                continue
            candidate = {
                "engine_code": _text(raw.get("engine_code"), 120),
                "transmission_code": _text(raw.get("transmission_code"), 120),
                "reason": _text(raw.get("reason"), 220),
            }
            if any(candidate.values()):
                candidates.append(candidate)
            if len(candidates) >= 5:
                break

    result: dict[str, Any] = {
        "schema_version": 1,
        "identification_status": _enum(
            data.get("identification_status"), RESOLUTIONS, "UNKNOWN"
        ),
        "generation": _component(data.get("generation"), value_key="name"),
        "engine": _component(data.get("engine"), value_key="marketing_name"),
        "transmission": _component(
            data.get("transmission"), value_key="marketing_name"
        ),
        "drivetrain": _component(data.get("drivetrain"), value_key="type"),
        "candidate_variants": candidates,
        "sources": sources,
        "notes": _string_list(data.get("notes"), limit=6),
    }

    if _generic_transmission_code(result["transmission"].get("code")):
        generic_code = result["transmission"]["code"]
        result["transmission"]["code"] = ""
        result["notes"].append(
            f"Transmission label {generic_code} is not an exact manufacturing code."
        )
    if _generic_drivetrain_code(result["drivetrain"].get("code")):
        generic_code = result["drivetrain"]["code"]
        result["drivetrain"]["code"] = ""
        result["notes"].append(
            f"Drivetrain label {generic_code} is not an exact component code."
        )

    for component_name in ("generation", "engine", "transmission", "drivetrain"):
        component = result[component_name]
        if (
            component["resolution"] == "VERIFIED"
            and component["verification_basis"] not in DIRECT_VERIFICATION_BASES
        ):
            component["resolution"] = "PROBABLE"
            if component["confidence"] == "HIGH":
                component["confidence"] = "MEDIUM"
            result["notes"].append(
                f"{component_name} downgraded to PROBABLE: no direct vehicle-specific verification basis."
            )

    referenced_ids = list(
        dict.fromkeys(
            reference
            for component_name in ("generation", "engine", "transmission", "drivetrain")
            for reference in result[component_name]["evidence_refs"]
        )
    )
    source_by_id = {
        source["source_id"]: source for source in sources if source.get("source_id")
    }
    selected_sources = [
        source_by_id[reference]
        for reference in referenced_ids
        if reference in source_by_id
    ]
    for source in sources:
        if source not in selected_sources and len(selected_sources) < 24:
            selected_sources.append(source)
    selected_urls = {
        source["source_url"] for source in selected_sources if source.get("source_url")
    }
    citation_index = 1
    for citation in citations:
        if citation["source_url"] in selected_urls or len(selected_sources) >= 24:
            continue
        selected_sources.append(
            {
                "source_id": f"grounding_{citation_index}",
                "source_name": citation["source_name"],
                "source_url": citation["source_url"],
                "source_type": "OTHER",
                "used_for": "Grounding citation",
            }
        )
        selected_urls.add(citation["source_url"])
        citation_index += 1
    result["sources"] = selected_sources
    preserved_ids = {
        source["source_id"] for source in selected_sources if source.get("source_id")
    }
    for component_name in ("generation", "engine", "transmission", "drivetrain"):
        result[component_name]["evidence_refs"] = [
            reference
            for reference in result[component_name]["evidence_refs"]
            if reference in preserved_ids
        ]

    # Do this only after dangling source references have been removed. A precise
    # manufacturing code without a real component-level source is an unverified
    # candidate, not the selected identity used by reliability research.
    for component_name, candidate_key in (
        ("engine", "engine_code"),
        ("transmission", "transmission_code"),
    ):
        component = result[component_name]
        exact_code = _text(component.get("code"), 120)
        if (
            not exact_code
            or component.get("verification_basis") in DIRECT_VERIFICATION_BASES
            or component.get("evidence_refs")
        ):
            continue
        candidate = {
            "engine_code": exact_code if candidate_key == "engine_code" else "",
            "transmission_code": exact_code if candidate_key == "transmission_code" else "",
            "reason": f"Unreferenced {component_name} code; verify against the exact drivetrain/application.",
        }
        if not any(
            item.get(candidate_key) == exact_code
            for item in result["candidate_variants"]
        ):
            result["candidate_variants"].append(candidate)
        component["code"] = ""
        if not any(
            _text(component.get(key))
            for key in ("name", "marketing_name", "family", "type")
        ):
            component["resolution"] = "AMBIGUOUS"
            component["confidence"] = "LOW"
        elif component["confidence"] == "HIGH":
            component["confidence"] = "MEDIUM"
        result["notes"].append(
            f"{component_name} code {exact_code} moved to candidates: no valid evidence_refs support the exact application."
        )

    substantive = (result["generation"], result["engine"], result["transmission"])
    resolutions = [component["resolution"] for component in substantive]
    if all(resolution == "UNKNOWN" for resolution in resolutions):
        result["identification_status"] = "UNKNOWN"
    elif "AMBIGUOUS" in resolutions or "UNKNOWN" in resolutions:
        result["identification_status"] = "AMBIGUOUS"
    elif all(resolution == "VERIFIED" for resolution in resolutions):
        result["identification_status"] = "VERIFIED"
    else:
        result["identification_status"] = "PROBABLE"
    result["candidate_variants"] = result["candidate_variants"][:5]
    result["notes"] = result["notes"][:6]
    return result


def _transmission_kind(value: Any) -> str:
    if isinstance(value, dict):
        text = " ".join(
            _text(value.get(key), 160)
            for key in ("marketing_name", "name", "family", "code", "transmission")
        )
    else:
        text = _text(value, 500)
    folded = unicodedata.normalize("NFKD", text).casefold()
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    if re.search(r"\bmanual(?:n[aiou]?)?\b", folded):
        return "MANUAL"
    if re.search(
        r"\bautomat|\bautomatik|\bautomatic|\b(?:dsg|dct|cvt|stronic|tiptronic)\b|"
        r"\bs[ -]?tronic\b|\b(?:zf\s*)?8hp\d*\b|\bga8hp",
        folded,
    ):
        return "AUTOMATIC"
    return ""


def _fold_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _exact_code_has_application_support(
    identity: dict[str, Any],
    component_name: str,
    listing: dict[str, Any],
) -> bool:
    component = identity.get(component_name)
    if not isinstance(component, dict):
        return True
    code = _text(component.get("code"), 120)
    if not code or component.get("verification_basis") in DIRECT_VERIFICATION_BASES:
        return True
    refs = set(_string_list(component.get("evidence_refs"), limit=12))
    sources = [
        source for source in identity.get("sources", [])
        if isinstance(source, dict) and source.get("source_id") in refs
    ]
    code_compact = re.sub(r"[^a-z0-9]", "", _fold_text(code))
    listing_text = " ".join(
        str(listing.get(key) or "")
        for key in ("title", "engine", "power", "transmission", "drive", "year")
    )
    listing_folded = _fold_text(listing_text)
    title_tokens = _fold_tokens(listing.get("title"))
    model_tokens = title_tokens - {
        "audi", "bmw", "skoda", "volkswagen", "vw", "seat", "honda",
        "hyundai", "toyota", "ford", "nissan", "kia", "mazda", "mercedes",
        "benz", "nabizim", "ponukam", "predam", "coupe", "combi", "kombi",
        "suv", "diesel", "benzin", "automat", "manual", "dsg", "tdi", "tsi",
        "line", "look", "matrix", "pano", "navi", "tour",
    }
    engine_match = re.search(
        r"\b(\d[.,]\d\s*(?:tfsi|tsi|tdi|t-gdi|tgdi|gdi|crdi|dci|hdi|bluehdi|ecoboost|vvt))\b",
        listing_folded,
    )
    engine_compact = (
        re.sub(r"[^a-z0-9]", "", engine_match.group(1)) if engine_match else ""
    )
    power_match = re.search(r"\b(\d{2,3})\s*kw\b", listing_folded)
    year_match = re.search(r"\b((?:19|20)\d{2})\b", listing_folded)
    listing_transmission_kind = _transmission_kind(listing.get("transmission"))
    listing_has_dsg = "dsg" in listing_folded
    listing_drive = _fold_text(listing.get("drive"))

    for source in sources:
        source_text = _fold_text(
            f"{source.get('source_name', '')} {source.get('source_url', '')}"
        )
        source_compact = re.sub(r"[^a-z0-9]", "", source_text)
        if not code_compact or code_compact not in source_compact:
            continue
        score = 0
        source_tokens = _fold_tokens(source_text)
        if model_tokens and model_tokens & source_tokens:
            score += 1
        if engine_compact and engine_compact in source_compact:
            score += 1
        if power_match and re.search(rf"\b{power_match.group(1)}\s*kw\b", source_text):
            score += 1
        if listing_has_dsg and "dsg" in source_text:
            score += 1
        elif listing_transmission_kind == "MANUAL" and "manual" in source_text:
            score += 1
        elif listing_transmission_kind == "AUTOMATIC" and re.search(
            r"\bautomat|\bautomatic|\bautomatik", source_text
        ):
            score += 1
        if listing_drive:
            if any(term in listing_drive for term in ("4x4", "awd", "quattro")):
                if re.search(r"\b(?:4x4|awd|quattro|4wd|xdrive)\b", source_text):
                    score += 1
            elif any(term in listing_drive for term in ("predn", "fwd")):
                if re.search(r"\b(?:fwd|front[ -]wheel|predny)\b", source_text):
                    score += 1
        if year_match and year_match.group(1) in source_text:
            score += 1
        if score >= 2:
            return True
    return False


def _move_code_to_candidates(
    identity: dict[str, Any],
    component_name: str,
) -> None:
    component = identity.get(component_name)
    if not isinstance(component, dict):
        return
    code = _text(component.get("code"), 120)
    if not code:
        return
    candidate_key = "engine_code" if component_name == "engine" else "transmission_code"
    candidates = identity.setdefault("candidate_variants", [])
    if isinstance(candidates, list) and not any(
        isinstance(item, dict) and item.get(candidate_key) == code for item in candidates
    ):
        candidates.append({
            "engine_code": code if component_name == "engine" else "",
            "transmission_code": code if component_name == "transmission" else "",
            "reason": (
                f"{component_name.title()} code is plausible for the model family, but the "
                "available source does not uniquely match this listing application."
            ),
        })
    component["code"] = ""
    if component.get("confidence") == "HIGH":
        component["confidence"] = "MEDIUM"
    existing_notes = [
        note for note in _string_list(identity.get("notes"), limit=6)
        if code.casefold() not in note.casefold()
    ]
    identity["notes"] = ([
        f"Exact {component_name} code moved to candidates: application support was not specific enough for this listing."
    ] + existing_notes[:5])[:6]


def _generalize_unverified_code_family(
    identity: dict[str, Any],
    component_name: str,
    listing: dict[str, Any],
) -> None:
    """Do not let an unsupported exact code escape through the family field."""
    component = identity.get(component_name)
    if not isinstance(component, dict) or _text(component.get("code"), 120):
        return
    family = _text(component.get("family"), 160)
    parts = [part.strip() for part in re.split(r"\s*/\s*", family) if part.strip()]
    if not parts or not all(
        re.fullmatch(r"[A-Za-z0-9-]{3,12}", part)
        and re.search(r"[A-Za-z]", part)
        and re.search(r"\d", part)
        for part in parts
    ):
        return

    exact_candidates = [part for part in parts if "x" not in part.casefold()]
    for candidate_code in exact_candidates:
        probe = json.loads(json.dumps(identity))
        probe[component_name]["code"] = candidate_code
        if _exact_code_has_application_support(probe, component_name, listing):
            return

    candidate_key = "engine_code" if component_name == "engine" else "transmission_code"
    candidates = identity.setdefault("candidate_variants", [])
    if isinstance(candidates, list):
        for candidate_code in exact_candidates:
            if any(
                isinstance(item, dict) and item.get(candidate_key) == candidate_code
                for item in candidates
            ):
                continue
            candidates.append({
                "engine_code": candidate_code if component_name == "engine" else "",
                "transmission_code": candidate_code if component_name == "transmission" else "",
                "reason": (
                    f"{component_name.title()} family code is plausible, but the available "
                    "source does not uniquely match this listing application."
                ),
            })
    generic_family = _text(component.get("marketing_name"), 160)
    if component_name == "transmission" and not generic_family:
        generic_family = "Manual" if _transmission_kind(component) == "MANUAL" else "Automatic"
    component["family"] = generic_family
    if component.get("confidence") == "HIGH":
        component["confidence"] = "MEDIUM"
    identity["notes"] = ([
        f"Unverified {component_name} family code {family} moved to candidates/generalized."
    ] + _string_list(identity.get("notes"), limit=5))[:6]


def reconcile_component_identity_with_listing(
    identity: Any,
    listing_context: Any,
) -> dict[str, Any]:
    """Reconcile exact component claims and transmission type with listing facts."""
    result = json.loads(json.dumps(identity if isinstance(identity, dict) else {}))
    if not result:
        return unknown_component_identity("Component identity was unavailable for reconciliation.")
    listing = listing_context if isinstance(listing_context, dict) else {}
    for component_name in ("engine", "transmission"):
        if not _exact_code_has_application_support(result, component_name, listing):
            _move_code_to_candidates(result, component_name)
        _generalize_unverified_code_family(result, component_name, listing)
    for component_name in ("generation", "engine", "transmission", "drivetrain"):
        component = result.get(component_name)
        if not isinstance(component, dict):
            continue
        if (
            component.get("verification_basis") == "SPECIFICATION_MATCH"
            and not component.get("evidence_refs")
            and component.get("confidence") == "HIGH"
        ):
            component["confidence"] = "MEDIUM"
            result["notes"] = ([
                f"{component_name} confidence reduced to MEDIUM because no source reference was attached to the specification match."
            ] + _string_list(result.get("notes"), limit=5))[:6]

    engine = result.get("engine")
    engine = engine if isinstance(engine, dict) else {}
    marketing_name = _text(engine.get("marketing_name"), 160)
    variant_match = re.search(r"\b([A-Za-z]{2,5})\s+([1-9]\d{2})\b", marketing_name)
    listing_text = _fold_text(" ".join(
        str(listing.get(key) or "")
        for key in ("title", "description_excerpt", "engine", "fuel")
    ))
    if variant_match:
        advertised_variant = _fold_text(variant_match.group(0))
        if advertised_variant not in listing_text:
            listing_engine = _text(listing.get("engine"), 80)
            displacement = re.search(r"\b(\d[.,]\d)\b", listing_engine)
            suffix = re.sub(
                r"^\s*[A-Za-z]{2,5}\s+[1-9]\d{2}\s*",
                "",
                marketing_name,
            ).strip()
            generic_parts = []
            if displacement:
                generic_parts.append(f"{displacement.group(1).replace(',', '.')} L")
            if suffix:
                generic_parts.append(suffix)
            fuel = _text(listing.get("fuel"), 40)
            if fuel:
                generic_parts.append(fuel)
            generic_name = " ".join(generic_parts) or listing_engine or "Engine variant to verify"
            old_family = _text(engine.get("family"), 160)
            engine["marketing_name"] = generic_name
            if not old_family or _fold_text(old_family) == _fold_text(marketing_name):
                engine["family"] = generic_name
            engine["resolution"] = "AMBIGUOUS"
            engine["confidence"] = "LOW"
            result["identification_status"] = "AMBIGUOUS"
            result["notes"] = ([
                f"Specific engine variant {variant_match.group(0)} is not advertised by the seller; generalized until VIN or vehicle documents confirm it."
            ] + _string_list(result.get("notes"), limit=5))[:6]
    selected_engine_text = _fold_text(" ".join(
        str(engine.get(key) or "")
        for key in ("marketing_name", "family")
    ))
    selected_displacement = re.search(r"\b(\d[.,]\d)\b", selected_engine_text)
    advertised_engine_text = _fold_text(" ".join(
        str(listing.get(key) or "")
        for key in ("title", "engine")
    ))
    advertised_displacements = {
        value.replace(",", ".")
        for value in re.findall(r"\b\d[.,]\d\b", advertised_engine_text)
    }
    if (
        selected_displacement
        and advertised_engine_text.strip()
        and selected_displacement.group(1).replace(",", ".") not in advertised_displacements
        and engine.get("verification_basis") not in DIRECT_VERIFICATION_BASES
    ):
        selected_name = _text(engine.get("marketing_name"), 160)
        fuel_family = next(
            (label for label in ("TDI", "TSI", "TFSI", "EcoBoost", "CRDI")
             if label.casefold() in advertised_engine_text.casefold()),
            "",
        )
        generic_name = " ".join(
            part for part in (fuel_family, _text(listing.get("fuel"), 40)) if part
        ) or "Engine variant to verify"
        engine["marketing_name"] = generic_name
        engine["family"] = generic_name
        engine["resolution"] = "AMBIGUOUS"
        engine["confidence"] = "LOW"
        result["identification_status"] = "AMBIGUOUS"
        result["notes"] = ([
            f"Engine displacement from {selected_name or 'the selected variant'} is not advertised by the seller; generalized until VIN or vehicle documents confirm it."
        ] + _string_list(result.get("notes"), limit=5))[:6]
    listing_transmission = _text(listing.get("transmission"), 200)
    listing_kind = _transmission_kind(listing_transmission)
    transmission = result.get("transmission")
    transmission = transmission if isinstance(transmission, dict) else {}
    identity_kind = _transmission_kind(transmission)
    if not listing_kind or identity_kind == listing_kind:
        return result

    direct_identity = (
        transmission.get("resolution") == "VERIFIED"
        and transmission.get("verification_basis") in DIRECT_VERIFICATION_BASES
    )
    if direct_identity:
        result["identification_status"] = "AMBIGUOUS"
        result["notes"] = ([
            "Explicit listing transmission conflicts with directly verified component identity; verify the vehicle documents."
        ] + _string_list(result.get("notes"), limit=5))[:6]
        return result

    gear_match = re.search(r"\b([4-9]|10)\s*(?:[- ]?st\.?|speed|gear)", listing_transmission, re.I)
    if listing_kind == "MANUAL":
        marketing_name = (
            f"{gear_match.group(1)}-speed Manual" if gear_match else "Manual"
        )
        family = "Manual"
    else:
        marketing_name = listing_transmission or "Automatic"
        family = "Automatic"
    result["transmission"] = {
        "marketing_name": marketing_name,
        "code": "",
        "family": family,
        "resolution": "PROBABLE",
        "confidence": "MEDIUM",
        "verification_basis": "SPECIFICATION_MATCH",
        "evidence_refs": [],
    }

    candidates: list[dict[str, str]] = []
    for raw in result.get("candidate_variants", []):
        if not isinstance(raw, dict):
            continue
        engine_code = _text(raw.get("engine_code"), 120)
        if engine_code and not any(
            candidate.get("engine_code") == engine_code for candidate in candidates
        ):
            candidates.append({
                "engine_code": engine_code,
                "transmission_code": "",
                "reason": "Engine candidate retained; incompatible transmission candidate removed using the explicit listing specification.",
            })
    result["candidate_variants"] = candidates[:5]
    result["notes"] = ([
        "Model transmission guess replaced because it conflicted with the explicit listing transmission."
    ] + _string_list(result.get("notes"), limit=5))[:6]

    substantive = (result.get("generation", {}), result.get("engine", {}), result["transmission"])
    resolutions = [str(component.get("resolution") or "UNKNOWN") for component in substantive]
    if all(resolution == "UNKNOWN" for resolution in resolutions):
        result["identification_status"] = "UNKNOWN"
    elif "AMBIGUOUS" in resolutions or "UNKNOWN" in resolutions:
        result["identification_status"] = "AMBIGUOUS"
    elif all(resolution == "VERIFIED" for resolution in resolutions):
        result["identification_status"] = "VERIFIED"
    else:
        result["identification_status"] = "PROBABLE"
    return result


def component_is_identified(identity: Any, component_name: str) -> bool:
    """Return whether a component has a usable family/name/code identification."""
    if not isinstance(identity, dict):
        return False
    component = identity.get(component_name)
    if not isinstance(component, dict):
        return False
    resolution = _enum(component.get("resolution"), RESOLUTIONS, "UNKNOWN")
    if resolution == "UNKNOWN":
        return False
    return any(
        _text(component.get(key))
        for key in ("name", "marketing_name", "code", "family", "type")
    )
