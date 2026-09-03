from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from v2_config import (
    MAX_LISTING_CHARS,
    PipelineError,
    _clean_markdown_label,
    _clean_markdown_value,
    normalized_host,
)


def parse_markdown_pairs(text: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("|") and line.count("|") >= 2:
            cells = [_clean_markdown_value(cell) for cell in line.strip("|").split("|")]
            if len(cells) >= 2 and cells[0] and cells[1] and not set(cells[0]) <= {"-", ":"}:
                key = _clean_markdown_label(cells[0])
                if key not in {"parameter", "položka", "hodnota", "value"}:
                    pairs.setdefault(key, cells[1])
            continue

        match = re.match(
            r"^\s*(?:[-+]\s*)?\*{0,2}([^:*|]{2,60}?)\*{0,2}\s*:\s*\*{0,2}(.+?)\*{0,2}\s*$",
            line,
        )
        if match:
            key = _clean_markdown_label(match.group(1))
            value = _clean_markdown_value(match.group(2))
            if key and value:
                pairs.setdefault(key, value)
    return pairs


def _pick(pairs: dict[str, str], aliases: Iterable[str]) -> str:
    normalized_aliases = [_clean_markdown_label(alias) for alias in aliases]
    for alias in normalized_aliases:
        if alias in pairs and pairs[alias]:
            return pairs[alias]
    for key, value in pairs.items():
        if any(alias in key or key in alias for alias in normalized_aliases):
            return value
    return ""


def _number(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    text = str(value or "")
    match = re.search(r"\d[\d\s\u00a0.,]*", text)
    if not match:
        return 0
    digits = re.sub(r"\D", "", match.group(0))
    try:
        return int(digits)
    except ValueError:
        return 0


def _year(value: str, fallback_text: str) -> int:
    for source in (value, fallback_text):
        for match in re.finditer(r"\b(19\d{2}|20\d{2})\b", source or ""):
            result = int(match.group(1))
            current_year = datetime.now().year + 1
            if 1950 <= result <= current_year:
                return result
    return 0


def _first_regex(patterns: Iterable[str], text: str) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            return _clean_markdown_value(match.group(1))
    return ""


def _title_from_markdown(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return _clean_markdown_value(line[2:])
    return "Neznáme vozidlo"


def _load_raw_listing(listing_dir: Path) -> dict[str, Any]:
    raw_path = listing_dir / "raw_data.json"
    if not raw_path.exists():
        return {}
    try:
        value = json.loads(raw_path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _raw_parameter(raw: dict[str, Any], aliases: Iterable[str]) -> Any:
    alias_keys = {_clean_markdown_label(alias) for alias in aliases}
    containers = [raw]
    for name in ("parameters", "specs", "specifications"):
        value = raw.get(name)
        if isinstance(value, dict):
            containers.append(value)
    for container in containers:
        for key, value in container.items():
            normalized = _clean_markdown_label(str(key))
            if normalized in alias_keys or any(alias in normalized or normalized in alias for alias in alias_keys):
                if value not in (None, "", 0, [], {}):
                    return value
    return ""


def normalize_listing(listing_dir: Path, source_url: str = "") -> dict[str, Any]:
    car_info_path = listing_dir / "car_info.md"
    if not car_info_path.exists():
        raise PipelineError("Chýba car_info.md s údajmi o aute.")

    text = car_info_path.read_text(encoding="utf-8", errors="replace")
    pairs = parse_markdown_pairs(text)
    raw = _load_raw_listing(listing_dir)
    title = str(raw.get("title") or _title_from_markdown(text))

    source = source_url or str(raw.get("url") or _pick(pairs, ["source", "url", "zdroj", "source url"]))
    raw_price = raw.get("priceCurrent") or raw.get("price") or raw.get("listPrice") or 0
    price_text = str(raw_price or _pick(pairs, ["current price", "price", "cena"]))
    raw_currency = str(raw.get("currency") or raw.get("priceCurrency") or "")
    source_host = normalized_host(source) if source else ""
    currency = (
        "CZK"
        if source_host.endswith("bazos.cz")
        or re.search(r"\b(?:czk|kč)\b", f"{raw_currency} {price_text}", re.I)
        else "EUR"
    )
    if not price_text:
        price_text = _first_regex(
            [r"(?:cena|price)\s*[:\-]?\s*([\d\s.,]+\s*(?:€|eur|kč|czk))"], text
        )
        if re.search(r"\b(?:czk|kč)\b", price_text, re.I):
            currency = "CZK"

    mileage_text = str(_raw_parameter(raw, ["mileage", "mileage_km", "najazdené km", "najazd", "kilometre", "stav tachometra"]) or _pick(
        pairs,
        ["mileage", "najazdené km", "najazd", "kilometre", "stav tachometra"],
    ))
    if not mileage_text:
        mileage_text = _first_regex([r"\b([\d\s.]{3,})\s*km\b"], text)

    year_text = str(_raw_parameter(raw, ["yearValue", "year", "rok", "rok výroby", "v prevádzke od", "model year"]) or _pick(
        pairs,
        ["year", "rok", "rok výroby", "v prevádzke od", "model year"],
    ))
    vin = str(_raw_parameter(raw, ["vin", "vin number", "vin číslo"]) or _pick(pairs, ["vin", "vin number", "vin číslo"]))
    vin_match = re.search(r"\b[A-HJ-NPR-Z0-9]{17}\b", vin or text, re.I)
    vin = vin_match.group(0).upper() if vin_match else ""

    engine = str(_raw_parameter(raw, ["engineCapacity", "engine_capacity_cc", "engine", "motor", "engine capacity", "objem motora", "zdvihový objem"]) or _pick(
        pairs,
        ["engine", "motor", "engine capacity", "objem motora", "zdvihový objem"],
    ))
    power = str(_raw_parameter(raw, ["enginePower", "engine_power_kw", "engine power", "výkon", "power"]) or _pick(pairs, ["engine power", "výkon", "power"]))
    fuel = str(_raw_parameter(raw, ["fuelValue", "fuel", "palivo"]) or _pick(pairs, ["fuel", "palivo"]))
    transmission = str(_raw_parameter(raw, ["gearboxValue", "transmission", "prevodovka", "gearbox"]) or _pick(pairs, ["transmission", "prevodovka", "gearbox"]))
    drivetrain = str(_raw_parameter(raw, ["driveValue", "drivetrain", "pohon", "drive"]) or _pick(pairs, ["drivetrain", "pohon", "drive"]))
    seller_raw = raw.get("seller") or raw.get("user") or {}
    if isinstance(seller_raw, dict):
        seller_raw = seller_raw.get("name") or seller_raw.get("displayName") or seller_raw.get("Meno") or ""
    seller = str(seller_raw or _pick(pairs, ["name", "meno", "seller", "predajca"]))
    location_raw = raw.get("location") or ""
    if isinstance(location_raw, dict):
        location_raw = location_raw.get("name") or location_raw.get("city") or ""
    location = str(location_raw or _pick(pairs, ["location", "lokalita", "city", "mesto"]))

    lower = text.lower()
    if not fuel:
        if re.search(r"\b(hybrid|phev|mhev)\b", lower):
            fuel = "Hybrid"
        elif re.search(r"\b(elektr|\bev\b)", lower):
            fuel = "Elektrické"
        elif re.search(r"\b(diesel|nafta|tdi|dci|hdi|crdi)\b", lower):
            fuel = "Diesel"
        elif re.search(r"\b(benz[ií]n|tsi|tfsi|tce|mpi)\b", lower):
            fuel = "Benzín"

    if not transmission:
        if re.search(r"\b(dsg|s[ -]?tronic|tiptronic|cvt|e-cvt|automat)\b", lower):
            transmission = "Automatická"
        elif re.search(r"\b(manu[aá]l)\b", lower):
            transmission = "Manuálna"

    if not drivetrain:
        if re.search(r"\b(4x4|awd|4wd|quattro|xdrive|4matic)\b", lower):
            drivetrain = "4x4 / AWD"

    description = str(raw.get("description") or raw.get("poznamka") or "")
    section_match = re.search(
        r"##\s+(?:Seller Note \(Poznamka\)|Pozn[aá]mka|Description)\s*\n+(.*?)(?=\n##\s+|\Z)",
        text,
        re.I | re.S,
    )
    if section_match and not description:
        description = section_match.group(1).strip()

    service_claimed = bool(
        re.search(
            r"\b(servisn[aá]\s+(?:hist[oó]ria|kniha|kni[zž]ka)|service history|fakt[uú]r|servisovan[éeý]|dealer service)\b",
            lower,
        )
    )

    images_dir = listing_dir / "images"
    photo_files = [p for p in images_dir.iterdir() if p.is_file()] if images_dir.exists() else []
    photos_count = max(
        len(photo_files),
        _number(raw.get("photos_count")),
        _number(_pick(pairs, ["downloaded", "photos", "fotografie", "počet fotiek"])),
    )

    normalized = {
        "title": title,
        "source_url": source,
        "source_host": source_host,
        "price": {"amount": _number(price_text), "currency": currency},
        "year": _year(year_text, f"{title}\n{text[:4000]}"),
        "mileage_km": _number(mileage_text),
        "engine": engine,
        "power_kw": _number(power),
        "fuel": fuel,
        "transmission": transmission,
        "drivetrain": drivetrain,
        "vin": vin,
        "seller": seller,
        "location": location,
        "description": description[:8_000],
        "service_history_claimed": service_claimed,
        "photos_count": photos_count,
        "raw_listing": text[:MAX_LISTING_CHARS],
    }
    normalized["data_quality"] = calculate_data_quality(normalized)
    return normalized


def calculate_data_quality(listing: dict[str, Any]) -> dict[str, Any]:
    checks: list[tuple[str, bool, int, bool]] = [
        ("názov a model", bool(listing.get("title") and listing.get("title") != "Neznáme vozidlo"), 10, True),
        ("cena", bool(listing.get("price", {}).get("amount")), 10, True),
        ("rok výroby", bool(listing.get("year")), 10, True),
        ("najazdené kilometre", bool(listing.get("mileage_km")), 10, True),
        ("motor", bool(listing.get("engine")), 8, True),
        ("palivo", bool(listing.get("fuel")), 5, False),
        ("prevodovka", bool(listing.get("transmission")), 7, False),
        ("pohon", bool(listing.get("drivetrain")), 4, False),
        ("VIN", bool(listing.get("vin")), 14, True),
        ("servisná história", bool(listing.get("service_history_claimed")), 10, True),
        ("popis vozidla", len(listing.get("description", "")) >= 80, 6, False),
        ("fotografie", int(listing.get("photos_count", 0)) >= 5, 6, True),
    ]
    score = sum(weight for _name, present, weight, _critical in checks if present)
    missing = [name for name, present, _weight, _critical in checks if not present]
    missing_critical = [
        name for name, present, _weight, critical in checks if critical and not present
    ]
    present = [name for name, is_present, _weight, _critical in checks if is_present]
    return {
        "score": max(0, min(100, score)),
        "missing": missing,
        "missing_critical": missing_critical,
        "present": present,
    }
