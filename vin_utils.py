#!/usr/bin/env python3
"""
VIN Validation & Basic Decoding Utility

Provides:
  - validate_vin(vin)        — Full ISO 3779 validation + WMI/year decoding
  - extract_vin_from_text()  — Scan free text for valid VINs
  - vin_to_markdown()        — Format validation result as markdown

This module has ZERO external dependencies — only Python standard library.
"""
import re
from vin_data import (
    MODEL_YEAR_MAP,
    MODEL_YEAR_MAP_LEGACY,
    PLANT_MAP,
    REGION_MAP,
    WMI_MAP,
    WMI_REGION_MAP,
)

VIN_REGEX = re.compile(r'\b[A-HJ-NPR-Z0-9]{17}\b')
MISSING_VIN_SENTINELS = {"", "N/A", "NA", "NONE", "NULL", "UNKNOWN", "NEUVEDENE", "NEUVEDENÉ"}


def validate_vin(vin: str) -> dict:
    """
    Validate a VIN per ISO 3779 and decode basic info.

    Returns dict with keys:
      vin, valid, validation_message, wmi, manufacturer, vds, vis,
      model_year_hint, region, plant_hint, check_digit_valid
    """
    cleaned_input = str(vin or "").strip().upper()
    if cleaned_input in MISSING_VIN_SENTINELS:
        cleaned_input = ""
    result = {
        "vin": cleaned_input, "valid": False, "validation_message": "",
        "wmi": "", "manufacturer": "", "vds": "", "vis": "",
        "model_year_hint": None, "model_year_code": "", "model_year_candidates": [],
        "region": "", "plant_hint": "",
        "check_digit_valid": False,
        "check_digit_policy": "unknown",
        "check_digit_severity": "error",
    }

    if not cleaned_input:
        result["validation_message"] = "No VIN provided."
        return result

    cleaned = cleaned_input

    if len(cleaned) != 17:
        result["validation_message"] = f"Invalid length: {len(cleaned)} chars (expected 17)."
        return result

    illegal = set('IOQ')
    if illegal & set(cleaned):
        result["validation_message"] = "Invalid: contains illegal characters (I, O, Q)."
        return result

    if not cleaned.isalnum():
        result["validation_message"] = "Invalid: contains non-alphanumeric characters."
        return result

    check_digit_valid = _verify_check_digit(cleaned)
    wmi = cleaned[0:3]
    vds = cleaned[3:9]
    vis = cleaned[9:17]
    manufacturer = WMI_MAP.get(wmi, "Unknown/Unmapped")

    region = WMI_REGION_MAP.get(wmi, REGION_MAP.get(wmi[0], "Unknown"))
    # Volkswagen WVG Touareg VINs encode the assembly plant at position 11;
    # older generic mappings in this utility use position 4 as a fallback.
    plant_key = wmi + (cleaned[10] if wmi == "WVG" else cleaned[3])
    plant_hint = PLANT_MAP.get(plant_key, "")
    check_digit_policy = _check_digit_policy(region)
    check_digit_severity = _check_digit_severity(check_digit_valid, check_digit_policy)

    year_char = cleaned[9]
    year_candidates = []
    if year_char in MODEL_YEAR_MAP_LEGACY:
        year_candidates.append(MODEL_YEAR_MAP_LEGACY[year_char])
    if year_char in MODEL_YEAR_MAP:
        year_candidates.append(MODEL_YEAR_MAP[year_char])
    year_hint = None
    if check_digit_policy == "mandatory_na":
        if year_char in MODEL_YEAR_MAP:
            year_hint = MODEL_YEAR_MAP[year_char]
        elif year_char in MODEL_YEAR_MAP_LEGACY:
            year_hint = MODEL_YEAR_MAP_LEGACY[year_char]

    result.update({
        "vin": cleaned, "valid": True,
        "wmi": wmi, "manufacturer": manufacturer,
        "vds": vds, "vis": vis,
        "model_year_hint": year_hint,
        "model_year_code": year_char,
        "model_year_candidates": year_candidates,
        "region": region, "plant_hint": plant_hint,
        "check_digit_valid": check_digit_valid,
        "check_digit_policy": check_digit_policy,
        "check_digit_severity": check_digit_severity,
    })

    if check_digit_valid:
        result["validation_message"] = (
            f"VIN parsed successfully. Valid checksum. Manufacturer: {manufacturer}. Region: {region}."
        )
    else:
        if check_digit_policy == "mandatory_na":
            result["validation_message"] = (
                "VIN parsed successfully, but the check digit does not match standard ISO 3779 validation. "
                "For North American VINs this mismatch is more likely to indicate an invalid VIN."
            )
        else:
            result["validation_message"] = (
                "VIN parsed successfully. The North American check digit does not match, but European/rest-of-world "
                "VINs often do not use that convention; this alone should not be treated as a risk."
            )

    return result


def extract_vin_from_text(text: str) -> str | None:
    """
    Scan free text for potential VINs, validate each, return the first valid one.
    """
    if not text:
        return None

    candidates = VIN_REGEX.findall(text.upper())
    for candidate in candidates:
        if any(c in candidate for c in 'IOQ'):
            continue
        if _verify_check_digit(candidate):
            return candidate

    # Fallback: return first candidate even if check digit fails
    clean = [c for c in candidates if not any(x in c for x in 'IOQ')]
    return clean[0] if clean else None


def vin_to_markdown(vin_info: dict) -> str:
    """Format VIN validation result as a Markdown section."""
    if not vin_info or not vin_info.get("vin"):
        return ""

    valid = vin_info.get("valid", False)
    severity = vin_info.get("check_digit_severity", "error")
    if valid and severity == "warning":
        status_icon = "[WARN]"
    elif valid:
        status_icon = "[OK]"
    else:
        status_icon = "[INVALID]"

    lines = ["## VIN Validation", "",
             "| Field | Value |", "|-------|-------|",
             f"| **VIN** | `{vin_info['vin']}` |",
             f"| **Status** | {status_icon} {vin_info.get('validation_message', '')} |"]

    if vin_info.get("manufacturer"):
        lines.append(f"| **Manufacturer** | {vin_info['manufacturer']} |")
    wmi = vin_info.get("wmi", "")
    region = vin_info.get("region", "")
    wmi_str = f"{wmi} ({region})" if region else wmi
    if wmi:
        lines.append(f"| **WMI (Origin)** | {wmi_str} |")
    if vin_info.get("model_year_hint") is not None:
        lines.append(f"| **Model year (approx)** | {vin_info['model_year_hint']} |")
    if vin_info.get("plant_hint"):
        lines.append(f"| **Assembly plant** | {vin_info['plant_hint']} |")
    if vin_info.get('check_digit_valid'):
        ck = 'Valid'
    elif vin_info.get('check_digit_severity') == 'info':
        ck = 'Not matched / optional outside North America'
    else:
        ck = 'Invalid'
    lines.append(f"| **Check digit (pos 9)** | {ck} |")
    lines.append("")
    return "\n".join(lines)


def _check_digit_policy(region: str) -> str:
    """Return how strongly the ISO/SAE check digit should be interpreted."""
    if region in {"USA", "Canada", "Mexico"}:
        return "mandatory_na"
    return "optional_row"


def _check_digit_severity(check_digit_valid: bool, policy: str) -> str:
    if check_digit_valid:
        return "ok"
    if policy == "mandatory_na":
        return "warning"
    return "info"


def _verify_check_digit(vin: str) -> bool:
    """
    Verify VIN check digit (position 9) per ISO 3779 / SAE J853.
    """
    if len(vin) != 17:
        return False

    transliteration = {
        'A': 1, 'B': 2, 'C': 3, 'D': 4, 'E': 5, 'F': 6, 'G': 7, 'H': 8,
        'J': 1, 'K': 2, 'L': 3, 'M': 4, 'N': 5, 'P': 7, 'R': 9,
        'S': 2, 'T': 3, 'U': 4, 'V': 5, 'W': 6, 'X': 7, 'Y': 8, 'Z': 9,
        '1': 1, '2': 2, '3': 3, '4': 4, '5': 5,
        '6': 6, '7': 7, '8': 8, '9': 9, '0': 0,
    }
    weights = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]

    total = 0
    for i, char in enumerate(vin):
        if char not in transliteration:
            return False
        total += transliteration[char] * weights[i]

    expected_check = total % 11
    check_char = vin[8]

    if expected_check == 10:
        return check_char == 'X'
    return check_char == str(expected_check)
