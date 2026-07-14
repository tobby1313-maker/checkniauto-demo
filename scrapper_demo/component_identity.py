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
    result["notes"] = result["notes"][:6]
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
