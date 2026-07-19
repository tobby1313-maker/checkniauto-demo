"""Soft JSON and buyer-report validation with warning persistence."""

from __future__ import annotations

import json
import os
import re
import unicodedata
import urllib.parse
from datetime import datetime
from pathlib import Path

from scrapper_demo.storage import atomic_write_json


SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"


def _load_schema(schema_name):
    schema_path = SCHEMA_DIR / schema_name
    try:
        with open(schema_path, "r", encoding="utf-8") as schema_file:
            schema = json.load(schema_file)
    except (OSError, json.JSONDecodeError) as exc:
        return {}, f"Could not read schema {schema_name}: {exc}"
    return schema if isinstance(schema, dict) else {}, None


def _schema_required_fields(schema_name):
    schema, warning = _load_schema(schema_name)
    if warning:
        return [], warning
    required = schema.get("required") if isinstance(schema, dict) else None
    return list(required or []), None


def _schema_type_matches(value, expected):
    types = expected if isinstance(expected, list) else [expected]
    mapping = {
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
        "null": lambda item: item is None,
    }
    return any(type_name in mapping and mapping[type_name](value) for type_name in types)


def _nested_schema_errors(value, schema, *, root_schema, path="$", limit=30):
    """Validate the JSON-schema subset used by project artifact contracts."""
    errors = []

    def add(message):
        if len(errors) < limit:
            errors.append(f"{path}: {message}")

    if not isinstance(schema, dict):
        return errors
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/"):
        resolved = root_schema
        for token in reference[2:].split("/"):
            resolved = resolved.get(token.replace("~1", "/").replace("~0", "~"), {}) if isinstance(resolved, dict) else {}
        return _nested_schema_errors(value, resolved, root_schema=root_schema, path=path, limit=limit)
    for branch in schema.get("allOf") if isinstance(schema.get("allOf"), list) else []:
        errors.extend(_nested_schema_errors(value, branch, root_schema=root_schema, path=path, limit=max(0, limit - len(errors))))
        if len(errors) >= limit:
            return errors[:limit]

    expected_type = schema.get("type")
    if expected_type is not None and not _schema_type_matches(value, expected_type):
        add(f"expected type {expected_type!r}")
        return errors
    if "enum" in schema and value not in schema.get("enum", []):
        add(f"value {value!r} is not in enum")
    pattern = schema.get("pattern")
    if isinstance(pattern, str) and isinstance(value, str) and re.search(pattern, value) is None:
        add(f"value does not match pattern {pattern!r}")
    if isinstance(value, dict):
        required = schema.get("required") if isinstance(schema.get("required"), list) else []
        for field in required:
            if field not in value and path != "$":
                add(f"missing required field {field!r}")
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        for field, child_schema in properties.items():
            if field not in value:
                continue
            child_path = f"{path}.{field}"
            errors.extend(_nested_schema_errors(value[field], child_schema, root_schema=root_schema, path=child_path, limit=max(0, limit - len(errors))))
            if len(errors) >= limit:
                break
    elif isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            errors.extend(_nested_schema_errors(item, schema["items"], root_schema=root_schema, path=f"{path}[{index}]", limit=max(0, limit - len(errors))))
            if len(errors) >= limit:
                break
    return errors[:limit]


def _soft_validate_json_contract(artifact_name, value, schema_name):
    """Return non-blocking warnings for model/backend JSON artifacts."""
    from risk_scorer import parse_model_json

    warnings = []
    parsed = parse_model_json(value)
    if not parsed:
        warnings.append(
            {
                "artifact": artifact_name,
                "type": "json_parse",
                "message": f"{artifact_name} did not contain a JSON object.",
            }
        )
        return warnings

    if parsed.get("_parse_error"):
        warnings.append(
            {
                "artifact": artifact_name,
                "type": "json_parse",
                "message": f"{artifact_name} could not be parsed as clean JSON.",
            }
        )

    schema, schema_warning = _load_schema(schema_name)
    if schema_warning:
        warnings.append(
            {
                "artifact": artifact_name,
                "type": "schema_load",
                "message": schema_warning,
            }
        )
        return warnings

    required = list(schema.get("required") or [])
    missing = [field for field in required if field not in parsed]
    if missing:
        warnings.append(
            {
                "artifact": artifact_name,
                "type": "schema_required",
                "message": f"{artifact_name} is missing required fields: {', '.join(missing)}.",
                "fields": missing,
            }
        )

    nested_errors = _nested_schema_errors(
        parsed,
        schema,
        root_schema=schema,
    )
    if nested_errors:
        warnings.append(
            {
                "artifact": artifact_name,
                "type": "schema_contract",
                "message": f"{artifact_name} contains nested schema violations.",
                "errors": nested_errors,
            }
        )

    return warnings


def _normalize_claim_text(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    return re.sub(r"\s+", " ", text)


def _normalize_report_structure_text(value):
    return "\n".join(_normalize_claim_text(line) for line in str(value or "").splitlines())


def _normalize_heading_key(value):
    normalized = _normalize_claim_text(value)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


FORBIDDEN_REPORT_CLAIMS = (
    ("verified_online_vin", r"\bvin\b.{0,50}\b(overeny|verified|confirmed)\b.{0,50}\b(online|internet)"),
    ("confirmed_no_accident", r"\b(nebolo|not)\b.{0,30}\b(havarovane|accident)"),
    ("confirmed_accident", r"\b(bolo|was)\b.{0,30}\b(havarovane|accident)"),
    ("guaranteed_buy", r"\b(garantovana kupa|guaranteed buy|bez rizika|without risk)\b"),
    ("definite_odometer_claim", r"\b(kilometre|odometer|mileage)\b.{0,40}\b(urcite|definitely|sto[cč]ene|rolled back|prave|genuine)\b"),
)

INTERNAL_REPORT_LABELS = (
    ("Dôkaz", "dokaz"),
    ("Istota", "istota"),
    ("Evidence", "evidence"),
    ("Confidence", "confidence"),
)

REQUIRED_REPORT_SECTION_KEYS = (
    "rychle zhrnutie",
    "data z inzeratu",
    "vin a transparentnost",
    "webove overenie",
    "cena a vyjednavanie",
    "ocakavane naklady na najblizsich 30 000 km",
    "analyza fotografii",
    "klady",
    "zapory rizika",
    "otazky pre predajcu a kontrola pri obhliadke",
    "zaverecne odporucanie",
)

REPORT_HEADING_EMOJIS = {
    "rychle zhrnutie": "## 📋 Rýchle zhrnutie",
    "data z inzeratu": "## 🧾 Dáta z inzerátu",
    "vin a transparentnost": "## 🔍 VIN a transparentnosť",
    "webove overenie": "## 🌐 Webové overenie",
    "technicke rizika modelu a komponentov": "## 🔧 Technické riziká modelu a komponentov",
    "cena a vyjednavanie": "## 💰 Cena a vyjednávanie",
    "ocakavane naklady na najblizsich 30 000 km": "## 🛠️ Očakávané náklady na najbližších 30 000 km",
    "analyza fotografii": "## 📸 Analýza fotografií",
    "klady": "## ✅ Klady",
    "zapory rizika": "## ❌ Zápory / riziká",
    "otazky pre predajcu a kontrola pri obhliadke": "## ❓ Otázky pre predajcu a kontrola pri obhliadke",
    "zaverecne odporucanie": "## 🏁 Záverečné odporúčanie",
    "quick summary": "## 📋 Quick Summary",
    "listing data": "## 🧾 Listing Data",
    "vin and transparency": "## 🔍 VIN and Transparency",
    "web verification": "## 🌐 Web Verification",
    "technical risks": "## 🔧 Technical Risks",
    "price and negotiation": "## 💰 Price and Negotiation",
    "expected costs over the next 30 000 km": "## 🛠️ Expected Costs Over the Next 30,000 km",
    "expected costs over the next 30 000 miles": "## 🛠️ Expected Costs Over the Next 30,000 Miles",
    "photo analysis": "## 📸 Photo Analysis",
    "pros": "## ✅ Pros",
    "cons risks": "## ❌ Cons / Risks",
    "questions for the seller and inspection checklist": "## ❓ Questions for the Seller and Inspection Checklist",
    "final recommendation": "## 🏁 Final Recommendation",
}


UNVERIFIED_URL_HOSTS = (
    "vertexaisearch.cloud.google.com",
    "example.com",
    "example.org",
    "example.net",
)

def _is_verified_public_url(url):
    text = str(url or "").strip()
    if not text:
        return False
    try:
        parsed = urllib.parse.urlparse(text)
    except Exception:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    host = parsed.netloc.lower()
    return not any(host == blocked or host.endswith(f".{blocked}") for blocked in UNVERIFIED_URL_HOSTS)


def _iter_markdown_links(text):
    value = str(text or "")
    index = 0
    while True:
        label_start = value.find("[", index)
        if label_start < 0:
            break
        label_end = value.find("](", label_start + 1)
        if label_end < 0:
            break
        url_start = label_end + 2
        if not value.startswith(("http://", "https://"), url_start):
            index = label_end + 2
            continue

        depth = 0
        url_end = url_start
        while url_end < len(value):
            char = value[url_end]
            if char.isspace():
                break
            if char == "(":
                depth += 1
            elif char == ")":
                if depth == 0:
                    break
                depth -= 1
            url_end += 1

        if url_end >= len(value) or value[url_end] != ")":
            index = url_start
            continue

        label = value[label_start + 1 : label_end]
        url = value[url_start:url_end]
        if label and url:
            yield label, url, label_start, url_end + 1
        index = url_end + 1


def _markdown_links(text):
    return [(label, url) for label, url, _start, _end in _iter_markdown_links(text)]

def _report_section_keys(report_text):
    keys = set()
    for line in str(report_text or "").splitlines():
        heading_match = re.match(r"^\s*##\s+(.+?)\s*$", line)
        bare_heading_match = None
        if not heading_match:
            bare_heading_match = re.match(r"^\s*([📋🧾🔍🌐🔧💰🛠️📸✅❌❓🏁].+?)\s*$", line)
        if heading_match or bare_heading_match:
            heading_text = heading_match.group(1) if heading_match else bare_heading_match.group(1)
            keys.add(_normalize_heading_key(heading_text))
    return keys

def _contains_public_internal_label(normalized_text, normalized_label):
    table_label = rf"(^|\n)\s*\|[^\n]*\b{re.escape(normalized_label)}\b[^\n]*\|"
    bold_label = rf"(^|\n)\s*(?:[-*]\s*)?\*\*\s*{re.escape(normalized_label)}\s*:\s*\*\*"
    heading_label = rf"(^|\n)\s*#{1,6}\s+{re.escape(normalized_label)}\b"
    return any(
        re.search(pattern, normalized_text)
        for pattern in (table_label, bold_label, heading_label)
    )


def _soft_validate_final_report(report_text, backend_verdict):
    """Return non-blocking warnings for the generated buyer report."""
    warnings = []
    text = str(report_text or "")
    if backend_verdict and str(backend_verdict) not in text:
        warnings.append(
            {
                "artifact": "analysis_result.md",
                "type": "verdict_lock",
                "message": "Final report does not contain the backend allowed verdict.",
                "expected_verdict": str(backend_verdict),
            }
        )

    if "<!-- END_ANALYSIS -->" not in text:
        warnings.append(
            {
                "artifact": "analysis_result.md",
                "type": "missing_end_marker",
                "message": "Final report is missing <!-- END_ANALYSIS -->.",
            }
        )

    section_keys = _report_section_keys(text)
    missing_sections = [key for key in REQUIRED_REPORT_SECTION_KEYS if key not in section_keys]
    if missing_sections:
        warnings.append(
            {
                "artifact": "analysis_result.md",
                "type": "missing_required_sections",
                "message": "Final report is missing required sections: " + ", ".join(missing_sections) + ".",
                "sections": missing_sections,
            }
        )

    normalized = _normalize_claim_text(text)
    for claim_id, pattern in FORBIDDEN_REPORT_CLAIMS:
        if re.search(pattern, normalized):
            warnings.append(
                {
                    "artifact": "analysis_result.md",
                    "type": "forbidden_claim",
                    "claim": claim_id,
                    "message": f"Final report may contain an unsupported high-confidence claim: {claim_id}.",
                }
            )

    normalized_structure = _normalize_report_structure_text(text)
    for label, normalized_label in INTERNAL_REPORT_LABELS:
        if _contains_public_internal_label(normalized_structure, normalized_label):
            warnings.append(
                {
                    "artifact": "analysis_result.md",
                    "type": "internal_label",
                    "label": label,
                    "message": f"Final public report contains internal label: {label}.",
                }
            )

    for label, url, start, _end in _iter_markdown_links(text):
        if not _is_verified_public_url(url):
            warnings.append(
                {
                    "artifact": "analysis_result.md",
                    "type": "unverified_public_link",
                    "label": label,
                    "url": url,
                    "message": "Final public report contains an unverified or placeholder Markdown link.",
                }
            )
        preceding = text[:start]
        headings = list(re.finditer(r"(?m)^\s*##\s+(.+?)\s*$", preceding))
        heading = headings[-1].group(1) if headings else ""
        normalized_heading = _normalize_heading_key(heading)
        in_price_section = (
            ("cena" in normalized_heading and "vyjed" in normalized_heading)
            or ("price" in normalized_heading and "negoti" in normalized_heading)
        )
        if not in_price_section:
            warnings.append(
                {
                    "artifact": "analysis_result.md",
                    "type": "public_link_outside_price_section",
                    "label": label,
                    "url": url,
                    "message": "Final public report contains a hyperlink outside the verified comparable-ad section.",
                }
            )

    return warnings


def _ensure_end_analysis_marker(report_text):
    text = str(report_text or "").rstrip()
    has_marker = "<!-- END_ANALYSIS -->" in text
    if not has_marker:
        text = text + "\n\n<!-- END_ANALYSIS -->"
    return text


def _write_validation_warnings(slug_dir, warnings, *, log=None):
    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "warnings": list(warnings or []),
    }
    path = os.path.join(slug_dir, "validation_warnings.json")
    atomic_write_json(path, payload)
    for warning in payload["warnings"]:
        if log:
            log(f"Validation warning: {warning.get('message', warning)}")
    return path

schema_required_fields = _schema_required_fields
soft_validate_json_contract = _soft_validate_json_contract
normalize_claim_text = _normalize_claim_text
normalize_report_structure_text = _normalize_report_structure_text
normalize_heading_key = _normalize_heading_key
is_verified_public_url = _is_verified_public_url
iter_markdown_links = _iter_markdown_links
markdown_links = _markdown_links
report_section_keys = _report_section_keys
soft_validate_final_report = _soft_validate_final_report
ensure_end_analysis_marker = _ensure_end_analysis_marker
write_validation_warnings = _write_validation_warnings
