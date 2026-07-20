"""Deterministic marketplace search for customer-visible SK/CZ comparables.

This module deliberately does not use a language model. It reads bounded
public result pages from the supported SK/CZ marketplaces, keeps the exact
detail links present in those pages, and turns visible result-card fields into
the existing market-comparable contract. A result-page link is evidence for
discovery, not proof of the vehicle's condition or of a completed transaction
price.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any, Callable
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from scrapper_demo.market_comparables import (
    strict_background_market_precheck,
    strict_local_market_precheck,
)


_MAKE_ALIASES: tuple[tuple[str, str], ...] = (
    ("alfa romeo", "Alfa Romeo"),
    ("land rover", "Land Rover"),
    ("mercedes-benz", "Mercedes-Benz"),
    ("mercedes benz", "Mercedes-Benz"),
    ("vw", "Volkswagen"),
    ("volkswagen", "Volkswagen"),
    ("skoda", "Skoda"),
    ("audi", "Audi"),
    ("bmw", "BMW"),
    ("toyota", "Toyota"),
    ("suzuki", "Suzuki"),
    ("hyundai", "Hyundai"),
    ("kia", "Kia"),
    ("jeep", "Jeep"),
    ("ford", "Ford"),
    ("opel", "Opel"),
    ("peugeot", "Peugeot"),
    ("renault", "Renault"),
    ("citroen", "Citroen"),
    ("seat", "Seat"),
    ("cupra", "Cupra"),
    ("mazda", "Mazda"),
    ("honda", "Honda"),
    ("nissan", "Nissan"),
    ("volvo", "Volvo"),
    ("dacia", "Dacia"),
    ("fiat", "Fiat"),
    ("mitsubishi", "Mitsubishi"),
    ("subaru", "Subaru"),
    ("lexus", "Lexus"),
    ("porsche", "Porsche"),
    ("tesla", "Tesla"),
)

_NON_VEHICLE_TITLE_MARKERS = (
    "disky",
    "pneumatik",
    "kolesa",
    "alu ",
    "alu kola",
    "nahradne diel",
    "nahradni dil",
    "rozpredam",
    "rozpredam na diely",
    "autodiel",
    "prevodovka na",
    "motor na",
    "naraznik",
    "kapota",
    "svetlo na",
    "spinac svetiel",
    "klima trubk",
    "pantograf",
    "predna maska",
    "grill w",
    "kupim",
)


def _fold(value: Any) -> str:
    text = str(value or "")
    if "Ã" in text:
        try:
            text = text.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    normalized = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _compact(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _fold(value))


def _looks_like_non_vehicle_listing(value: Any) -> bool:
    folded = _fold(value)
    return any(marker in folded for marker in _NON_VEHICLE_TITLE_MARKERS) or bool(
        re.search(r"\b\d[x×]\d{2,3}\s+r\d{2}\b", folded)
    )


def _model_matches(model: Any, value: Any) -> bool:
    model_folded = _fold(model).strip()
    text = _fold(value)
    if not model_folded:
        return True
    # Audi performance derivatives are different models for price comparison:
    # Q3 must not silently accept SQ3/RS Q3/RSQ3 result cards.
    if re.fullmatch(r"q\d", model_folded):
        if re.search(rf"\b(?:rs|s)\s*-?\s*{re.escape(model_folded)}\b", text):
            return False
        if re.search(rf"\b(?:rs|s){re.escape(model_folded)}\b", text):
            return False
    model_pattern = re.escape(model_folded).replace(r"\-", r"[- ]?")
    if re.search(rf"(?<![a-z0-9]){model_pattern}(?![a-z0-9])", text):
        return True
    return _compact(model_folded) in _compact(text)


def _canonical_url(value: Any) -> str:
    text = str(value or "").strip()
    try:
        parsed = urlparse(text)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    return parsed._replace(query="", fragment="").geturl().rstrip("/")


def _bazos_ad_id(value: Any) -> str:
    match = re.search(r"/inzerat/(\d+)(?:/|$)", _canonical_url(value))
    return match.group(1) if match else ""


def derive_bazos_identity(listing: dict[str, Any]) -> dict[str, str]:
    """Extract a deliberately narrow make/model search identity from a title."""
    title = " ".join(str(listing.get("title") or "").split())
    folded_title = _fold(title)
    for alias, canonical_make in _MAKE_ALIASES:
        match = re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", folded_title)
        if not match:
            continue
        # Positions are stable because NFKD removes combining characters but
        # keeps the base characters. Some sellers put drivetrain or gearbox
        # before the actual model (for example "VOLVO 4x4 XC40"). Skip only
        # well-known non-model modifiers, then keep the first plausible token.
        remainder = title[match.end() :].lstrip(" -,:/")
        multiword_models = {
            ("Suzuki", "grand vitara"): "Grand Vitara",
            ("Land Rover", "range rover"): "Range Rover",
            ("Toyota", "land cruiser"): "Land Cruiser",
        }
        folded_remainder = _fold(remainder)
        for (make_name, phrase), canonical_model in multiword_models.items():
            if canonical_make == make_name and re.match(
                rf"{re.escape(phrase)}(?![a-z0-9])",
                folded_remainder,
            ):
                return {
                    "make": canonical_make,
                    "model": canonical_model,
                    "query": f"{canonical_make} {canonical_model}",
                }
        model = ""
        non_model_prefixes = {
            "4x4", "4wd", "awd", "fwd", "rwd", "automat", "automatic",
            "manual", "manualna", "manualni",
        }
        for model_match in re.finditer(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", remainder):
            candidate = model_match.group(0).strip("-")
            if _fold(candidate) in non_model_prefixes:
                continue
            model = candidate
            break
        if not model:
            return {}
        if not model or re.fullmatch(r"(?:19|20)\d{2}", model):
            return {}
        return {
            "make": canonical_make,
            "model": model,
            "query": f"{canonical_make} {model}",
        }
    # Some private ads use only the model as the title (for example "Rav4")
    # while naming the make in the opening description. Accept that fallback
    # only when the same title model immediately follows a known make; this
    # avoids treating unrelated dealer inventory mentions as the target car.
    model_match = re.match(r"\s*([A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)", title)
    model_hint = model_match.group(1) if model_match else ""
    if model_hint and not re.fullmatch(r"(?:19|20)\d{2}", model_hint):
        folded_model = _fold(model_hint)
        folded_description = _fold(listing.get("description_excerpt") or "")[:500]
        for alias, canonical_make in _MAKE_ALIASES:
            if re.search(
                rf"(?<![a-z0-9]){re.escape(alias)}\s+{re.escape(folded_model)}(?![a-z0-9])",
                folded_description,
            ):
                return {
                    "make": canonical_make,
                    "model": model_hint,
                    "query": f"{canonical_make} {model_hint}",
                }
    return {}


def bazos_search_url(country: str, query: str) -> str:
    country_code = country.lower()
    if country_code not in {"sk", "cz"}:
        raise ValueError("Bazos country must be SK or CZ")
    slug = re.sub(r"[^a-z0-9]+", "-", _fold(query)).strip("-")
    return f"https://auto.bazos.{country_code}/inzeraty/{slug}/"


def _first_year(value: str) -> int | None:
    current_year = date.today().year + 1
    for raw in re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", value):
        year = int(raw)
        if 1980 <= year <= current_year:
            return year
    return None


def _first_mileage(value: str) -> int | None:
    folded = _fold(value).replace("\u00a0", " ")
    masked = re.search(r"(?<!\d)(\d{2,3})\s*x{3}\s*(?:km|kilometrov?)\b", folded)
    if masked:
        return int(masked.group(1)) * 1000
    abbreviated = re.search(r"(\d{2,3})\s*tis(?:\.|ic)?\s*(?:km)?", folded)
    if abbreviated:
        return int(abbreviated.group(1)) * 1000
    for raw in re.findall(r"(?<!\d)(\d{1,3}(?:[ .]\d{3})+|\d{4,6})\s*km\b", folded):
        mileage = int(re.sub(r"\D", "", raw))
        if 1_000 <= mileage <= 1_500_000:
            return mileage
    return None


def _price(value: str, country: str) -> tuple[int | None, str, str]:
    text = " ".join(value.replace("\u00a0", " ").split())
    raw = re.search(r"(\d[\d .]*)", text)
    if not raw:
        return None, "", text
    amount = int(re.sub(r"\D", "", raw.group(1)))
    folded = _fold(text)
    currency = "EUR" if "€" in text or "eur" in folded else "CZK" if country == "CZ" else ""
    display = f"{amount:,} {currency}".replace(",", " ") if currency else text
    return amount, currency, display


def _engine(value: str) -> str:
    folded = _fold(value)
    match = re.search(
        r"\b(\d[.,]\d\s*(?:e[- ]?skyactiv(?:e)?\s*d\s*\d{0,3}|tfsi|tsi|tdi|t-gdi|tgdi|gdi|crdi|dci|hdi|bluehdi|ecoboost|vvt|hybrid|phev|d[345]|b[3456]|t[34568]|l(?:it)?))(?!\s*/\s*100)\b",
        folded,
        re.IGNORECASE,
    )
    if match:
        normalized = " ".join(match.group(1).upper().replace(",", ".").split())
        normalized = normalized.replace("SKYACTIVE", "SKYACTIV")
        return re.sub(r"\s+LIT$", " L", normalized)
    # Some search result cards omit the litre suffix but place power directly
    # after displacement: "2.0 160 PS" or "2.0 118 kW".
    displacement = re.search(
        r"\b(\d[.,]\d)(?=\s+\d{2,3}\s*(?:kw|ps|hp)\b)",
        folded,
        re.IGNORECASE,
    )
    return f"{displacement.group(1).replace(',', '.')} L" if displacement else ""


def _power(value: str) -> str:
    folded = _fold(value)
    match = re.search(r"\b(\d{2,3})\s*kw\b", folded, re.IGNORECASE)
    if match:
        return f"{int(match.group(1))} KW"
    horsepower = re.search(r"\b(\d{2,3})\s*(ps|hp)\b", folded, re.IGNORECASE)
    if not horsepower:
        return ""
    factor = 0.73549875 if horsepower.group(2).lower() == "ps" else 0.745699872
    return f"{round(int(horsepower.group(1)) * factor)} KW"


def _fuel_family(value: str) -> str:
    folded = _fold(value)
    if re.search(r"\b(?:hybrid|phev|mhev)\b", folded):
        return "HYBRID"
    if re.search(r"\b(?:elektro|electric|bev|ev)\b", folded):
        return "ELECTRIC"
    if re.search(r"\b(?:diesel|nafta|tdi|tid|bitdi|crdi|dci|hdi|bluehdi)\b", folded):
        return "DIESEL"
    if re.search(r"\b(?:benzin|benzene|gasoline|petrol|tsi|tfsi|ecoboost|gdi)\b", folded):
        return "PETROL"
    return ""


def _transmission(value: str) -> str:
    folded = _fold(value)
    if re.search(r"\ba\s*/\s*t\b", folded):
        return "AUTOMATIC"
    if re.search(r"\bm\s*/\s*t\b", folded):
        return "MANUAL"
    if re.search(r"(?:\b\d{2,3}\s*kw[\s,;/]{1,12}|[,;/]\s*)m[4-9]\b", folded):
        return "MANUAL"
    if re.search(r"(?:\b\d{2,3}\s*kw[\s,;/]{1,12}|[,;/]\s*)a[4-9]\b", folded):
        return "AUTOMATIC"
    if re.search(r"\b(?:dsg|s[ -]?tronic)\b", folded):
        return "DSG"
    if re.search(r"\b(?:dct|edc)\b", folded):
        return "DCT"
    if re.search(r"\b(?:cvt|tiptronic)\b", folded):
        return re.search(r"\b(?:cvt|tiptronic)\b", folded).group(0).upper()  # type: ignore[union-attr]
    if re.search(r"\b(?:automat|automatic|automatik)\w*\b", folded):
        return "AUTOMATIC"
    if re.search(r"\b(?:manual|manualn)\w*\b", folded):
        return "MANUAL"
    return ""


def _drivetrain(value: str) -> str:
    folded = _fold(value)
    four_wheel = bool(re.search(r"\b(?:4x4|4wd|awd|quattro|allrad|4motion)\b", folded))
    front_wheel = bool(re.search(r"\b(?:fwd|predny(?: (?:pohon|nahon))?|predokol)\w*\b", folded))
    rear_wheel = bool(re.search(r"\b(?:rwd|zadny(?: (?:pohon|nahon))?|zadokol)\w*\b", folded))
    if sum((four_wheel, front_wheel, rear_wheel)) > 1:
        return ""
    if four_wheel:
        return "4X4"
    if front_wheel:
        return "FWD"
    if rear_wheel:
        return "RWD"
    return ""


def _portal_price(value: str) -> tuple[int | None, str, str]:
    """Extract a currency-marked asking price from a result-card text."""
    text = " ".join(str(value or "").replace("\u00a0", " ").split())
    matches = list(
        re.finditer(
            r"(?<!\d)(\d[\d .]{2,})\s*(€|eur|kč|czk|kc)",
            text,
            re.IGNORECASE,
        )
    )
    if not matches:
        return None, "", text
    match = matches[0]
    amount = int(re.sub(r"\D", "", match.group(1)))
    marker = _fold(match.group(2))
    currency = "EUR" if marker in {"€", "eur"} else "CZK"
    return amount, currency, f"{amount:,} {currency}".replace(",", " ")


def _portal_detail_url(portal: str, value: Any, base_url: str) -> str:
    url = _canonical_url(urljoin(base_url, str(value or "")))
    if not url:
        return ""
    parsed = urlparse(url)
    path = parsed.path.lower()
    if portal == "autobazar_sk":
        return url if re.match(r"^/(?:\d+|inzerat/\d+)(?:/|$)", parsed.path) else ""
    if portal == "sauto_cz":
        return url if path.startswith("/osobni/detail/") else ""
    if portal == "autobazar_eu":
        return url if "/detail/" in path or re.search(r"id\d{5,}", path) else ""
    return ""


def _portal_card_text(anchor: Any) -> str:
    """Return the smallest useful listing-card text around a detail link."""
    node = anchor
    for _ in range(6):
        if node is None:
            break
        text = " ".join(node.get_text(" ", strip=True).split())
        if (
            len(text) >= 30
            and re.search(r"(?:\u20ac|eur|k\u010d|czk|kc)", text, re.IGNORECASE)
            and (_first_year(text) is not None or _first_mileage(text) is not None)
        ):
            return text[:3000]
        node = getattr(node, "parent", None)
    return " ".join(anchor.get_text(" ", strip=True).split())[:3000]


def _portal_candidate_id(portal: str, url: str, index: int) -> str:
    numbers = re.findall(r"\d{5,}", url)
    suffix = numbers[-1] if numbers else str(index + 1)
    return f"{portal.upper()}-{suffix}"


def parse_local_portal_search_page(
    html: str,
    *,
    portal: str,
    search_url: str,
    identity: dict[str, str],
    listing: dict[str, Any],
    max_candidates: int = 30,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Parse verified direct links from an Autobazar or Sauto result page."""
    soup = BeautifulSoup(html or "", "html.parser")
    candidates: list[dict[str, Any]] = []
    counters = {
        "result_card_count": 0,
        "non_vehicle_filtered_count": 0,
        "model_mismatch_count": 0,
        "self_listing_filtered_count": 0,
        "parsed_candidate_count": 0,
    }
    model_compact = _compact(identity.get("model"))
    analyzed_url = _canonical_url(listing.get("source_url"))
    seen_urls: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        source_url = _portal_detail_url(portal, anchor.get("href"), search_url)
        if not source_url or source_url in seen_urls:
            continue
        seen_urls.add(source_url)
        counters["result_card_count"] += 1
        title = " ".join(
            str(
                anchor.get_text(" ", strip=True)
                or anchor.get("title")
                or anchor.get("aria-label")
                or ""
            ).split()
        )
        card_text = _portal_card_text(anchor)
        combined = f"{title} {card_text}".strip()
        if _looks_like_non_vehicle_listing(combined):
            counters["non_vehicle_filtered_count"] += 1
            continue
        if model_compact and not _model_matches(identity.get("model"), combined):
            counters["model_mismatch_count"] += 1
            continue
        if analyzed_url and source_url == analyzed_url:
            counters["self_listing_filtered_count"] += 1
            continue

        price_amount, currency, price_display = _portal_price(combined)
        year = _first_year(combined)
        mileage = _first_mileage(combined)
        engine = _engine(combined)
        power = _power(combined)
        transmission = _transmission(combined)
        drive = _drivetrain(combined)
        similarity_tier, material_difference = _similarity(listing, combined)
        source_country = {
            "autobazar_sk": "SK",
            "sauto_cz": "CZ",
        }.get(portal, "SK" if currency == "EUR" else "CZ" if currency == "CZK" else "")
        candidates.append(
            {
                "candidate_id": _portal_candidate_id(portal, source_url, len(candidates)),
                "description": title or combined[:180],
                "listing_title": title or combined[:180],
                "year": year,
                "mileage_km": mileage,
                "engine": engine,
                "power": power,
                "transmission": transmission,
                "drivetrain": drive,
                "price_eur": price_amount if currency == "EUR" else None,
                "price_display": price_display,
                "price_basis": "gross_asking",
                "source_country": source_country,
                "similarity_tier": similarity_tier,
                "material_difference": material_difference,
                "location": "",
                "search_pass": "sk_cz",
                "search_language": "sk" if source_country == "SK" else "cs",
                "market_scope": "PUBLIC_SK_CZ",
                "source_portal": portal,
                "source_url": source_url,
                "claimed_source_url": source_url,
                "evidence_url": search_url,
                "verified_url": True,
                "url_verification_status": "VERIFIED_DETAIL",
                "background_evidence_verified": False,
                "display_in_report": True,
                "data_provenance": "DIRECT_PORTAL_SEARCH",
            }
        )
        if len(candidates) >= max_candidates:
            break
    counters["parsed_candidate_count"] = len(candidates)
    return candidates, counters


def _similarity(
    listing: dict[str, Any], candidate_text: str
) -> tuple[str, str]:
    fallback_listing_text = " ".join(
        str(listing.get(key) or "") for key in ("title", "description_excerpt")
    )
    differences: list[str] = []
    missing: list[str] = []
    matches = 0
    expected_count = 0
    matched_labels: set[str] = set()
    expected_labels: set[str] = set()
    for label, extractor, structured_key in (
        ("engine", _engine, "engine"),
        ("fuel", _fuel_family, "fuel"),
        ("power", _power, "power"),
        ("transmission", _transmission, "transmission"),
        ("drive", _drivetrain, "drive"),
    ):
        expected = extractor(str(listing.get(structured_key) or "")) or extractor(
            fallback_listing_text
        )
        observed = extractor(candidate_text)
        if not expected:
            continue
        expected_count += 1
        expected_labels.add(label)
        if not observed:
            missing.append(f"{label} not visible")
        elif (
            label == "power"
            and abs(
                int(re.search(r"\d+", expected).group(0))
                - int(re.search(r"\d+", observed).group(0))
            ) <= 2
        ):
            matches += 1
            matched_labels.add(label)
        elif _compact(expected) == _compact(observed):
            matches += 1
            matched_labels.add(label)
        else:
            differences.append(f"different {label}")
    internal_combustion = _fuel_family(
        str(listing.get("fuel") or "") + " " + fallback_listing_text
    ) in {"DIESEL", "PETROL", "HYBRID"}
    required_labels = {"transmission"}
    if internal_combustion:
        required_labels.add("engine")
    if (
        expected_count >= 2
        and matches == expected_count
        and required_labels <= expected_labels
        and required_labels <= matched_labels
    ):
        return "A", "same visible engine/power/transmission/drivetrain attributes"
    if differences:
        return "B", ", ".join(differences)
    if missing:
        return "B", ", ".join(missing)
    return "B", "same make/model; configuration not fully visible in result card"


def parse_bazos_search_page(
    html: str,
    *,
    country: str,
    search_url: str,
    identity: dict[str, str],
    listing: dict[str, Any],
    max_candidates: int = 30,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Parse visible Bazos result cards without inventing or repairing links."""
    soup = BeautifulSoup(html or "", "html.parser")
    cards = soup.select("div.inzeraty.inzeratyflex") or soup.select("div.inzeraty")
    candidates: list[dict[str, Any]] = []
    counters = {
        "result_card_count": len(cards),
        "non_vehicle_filtered_count": 0,
        "model_mismatch_count": 0,
        "self_listing_filtered_count": 0,
        "parsed_candidate_count": 0,
    }
    model_compact = _compact(identity.get("model"))
    analyzed_url = _canonical_url(listing.get("source_url"))
    analyzed_ad_id = _bazos_ad_id(analyzed_url)
    seen_urls: set[str] = set()
    for card in cards:
        title_link = card.select_one("h2.nadpis a[href]")
        if title_link is None:
            continue
        title = " ".join(title_link.get_text(" ", strip=True).split())
        folded_title = _fold(title)
        if _looks_like_non_vehicle_listing(folded_title):
            counters["non_vehicle_filtered_count"] += 1
            continue
        description_node = card.select_one("div.popis")
        description = (
            " ".join(description_node.get_text(" ", strip=True).split())
            if description_node is not None
            else ""
        )
        if model_compact and not _model_matches(identity.get("model"), f"{title} {description[:240]}"):
            counters["model_mismatch_count"] += 1
            continue
        source_url = _canonical_url(urljoin(search_url, str(title_link.get("href") or "")))
        if not source_url or source_url in seen_urls:
            continue
        if analyzed_url and (
            source_url == analyzed_url
            or (analyzed_ad_id and _bazos_ad_id(source_url) == analyzed_ad_id)
        ):
            counters["self_listing_filtered_count"] += 1
            continue
        seen_urls.add(source_url)
        price_node = card.select_one("div.inzeratycena")
        price_text = price_node.get_text(" ", strip=True) if price_node is not None else ""
        amount, currency, price_display = _price(price_text, country.upper())
        combined = f"{title} {description}"
        year = _first_year(combined)
        mileage = _first_mileage(combined)
        engine = _engine(combined)
        power = _power(combined)
        transmission = _transmission(combined)
        drive = _drivetrain(combined)
        similarity_tier, material_difference = _similarity(listing, combined)
        ad_id_match = re.search(r"/inzerat/(\d+)", source_url)
        candidate_id = (
            f"BAZOS-{country.upper()}-{ad_id_match.group(1)}"
            if ad_id_match
            else f"BAZOS-{country.upper()}-{len(candidates) + 1}"
        )
        location_node = card.select_one("div.inzeratylok")
        candidate: dict[str, Any] = {
            "candidate_id": candidate_id,
            "description": title,
            "listing_title": title,
            "year": year,
            "mileage_km": mileage,
            "engine": engine,
            "power": power,
            "transmission": transmission,
            "drivetrain": drive,
            "price_eur": amount if currency == "EUR" else None,
            "price_display": price_display,
            "price_basis": "gross_asking",
            "source_country": country.upper(),
            "similarity_tier": similarity_tier,
            "material_difference": material_difference,
            "location": " ".join(location_node.get_text(" ", strip=True).split()) if location_node is not None else "",
            "search_pass": "sk_cz",
            "search_language": "sk" if country.upper() == "SK" else "cs",
            "market_scope": "PUBLIC_SK_CZ",
            "source_url": source_url,
            "claimed_source_url": source_url,
            "evidence_url": search_url,
            "verified_url": True,
            "url_verification_status": "VERIFIED_DETAIL",
            "background_evidence_verified": False,
            "display_in_report": True,
            "data_provenance": "DIRECT_PORTAL_SEARCH",
        }
        candidates.append(candidate)
        if len(candidates) >= max_candidates:
            break
    counters["parsed_candidate_count"] = len(candidates)
    return candidates, counters


def _fetch_html(url: str, timeout: float) -> str:
    response = requests.get(
        url,
        timeout=timeout,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (compatible; CarAnalysisMarketCheck/1.0; "
                "+https://auto.bazos.sk/)"
            )
        },
    )
    response.raise_for_status()
    if not response.encoding or response.encoding.lower() == "iso-8859-1":
        response.encoding = response.apparent_encoding or "utf-8"
    return response.text


def _fetch_mobile_de_html(url: str, timeout: float) -> str:
    """Fetch a Mobile.de result page with a short cookie/bootstrap request.

    Mobile.de commonly applies access controls to plain one-shot HTTP clients.
    A session with normal browser navigation headers gives the portal a chance
    to establish its cookies before the actual search request.  This remains
    an HTTP-only fallback; it does not pretend that an access-denied response
    contains market data.
    """
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
            ),
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Referer": "https://www.google.com/",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }
    )
    try:
        session.get(
            "https://suchen.mobile.de/",
            timeout=min(max(float(timeout), 1.0), 4.0),
            allow_redirects=True,
        )
    except requests.RequestException:
        # The result request below is the authoritative attempt.  A failed
        # bootstrap must not hide its status or response diagnostics.
        pass

    response = session.get(url, timeout=timeout, allow_redirects=True)
    response.raise_for_status()
    if not response.encoding or response.encoding.lower() == "iso-8859-1":
        response.encoding = response.apparent_encoding or "utf-8"
    return response.text


def _request_error_details(exc: Exception) -> dict[str, Any]:
    """Return compact, non-sensitive request diagnostics for debug artifacts."""
    details: dict[str, Any] = {"error_type": type(exc).__name__}
    response = getattr(exc, "response", None)
    if response is not None:
        status_code = getattr(response, "status_code", None)
        if status_code is not None:
            details["http_status"] = int(status_code)
        response_url = str(getattr(response, "url", "") or "")
        if response_url:
            details["final_url"] = response_url
        try:
            preview = BeautifulSoup(
                str(getattr(response, "text", "") or ""),
                "html.parser",
            ).get_text(" ", strip=True)
        except Exception:
            preview = ""
        if preview:
            details["response_preview"] = preview[:240]
    if "http_status" not in details:
        message = str(exc).strip()
        if message:
            details["error_message"] = message[:240]
    return details


def search_bazos_sk_cz(
    listing: dict[str, Any],
    *,
    timeout: float = 8.0,
    fetch_html: Callable[[str, float], str] | None = None,
) -> list[dict[str, Any]]:
    """Run two bounded direct searches and return one auditable SK/CZ pass."""
    identity = derive_bazos_identity(listing)
    if not identity:
        return [
            {
                "pass_id": "sk_cz",
                "portal": "Bazos SK/CZ",
                "language": "sk/cs",
                "market_scope": "PUBLIC_SK_CZ",
                "search_method": "DIRECT_PORTAL_HTML",
                "status": "ERROR",
                "error_type": "SEARCH_IDENTITY_UNAVAILABLE",
                "citation_count": 0,
                "candidate_count": 0,
                "verified_detail_count": 0,
                "verified_background_count": 0,
                "url_unverified_count": 0,
                "candidates": [],
                "source_attempts": [],
            }
        ]

    loader = fetch_html or _fetch_html
    all_candidates: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    successful_fetches = 0
    total_cards = 0
    for country in ("SK", "CZ"):
        search_url = bazos_search_url(country, identity["query"])
        try:
            html = loader(search_url, timeout)
            candidates, counters = parse_bazos_search_page(
                html,
                country=country,
                search_url=search_url,
                identity=identity,
                listing=listing,
            )
            successful_fetches += 1
            total_cards += counters["result_card_count"]
            all_candidates.extend(candidates)
            attempts.append(
                {
                    "country": country,
                    "search_url": search_url,
                    "status": "SUCCESS",
                    **counters,
                }
            )
        except Exception as exc:  # one country must not suppress the other
            attempts.append(
                {
                    "country": country,
                    "search_url": search_url,
                    "status": "ERROR",
                    "error_type": type(exc).__name__,
                    "result_card_count": 0,
                    "parsed_candidate_count": 0,
                }
            )

    unique_candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in all_candidates:
        source_url = str(candidate.get("source_url") or "")
        if source_url and source_url not in seen:
            seen.add(source_url)
            unique_candidates.append(candidate)
    status = (
        "FOUND"
        if unique_candidates
        else "SEARCH_RESULTS_FOUND_NOT_STRUCTURED"
        if total_cards
        else "NOTHING_FOUND"
        if successful_fetches
        else "ERROR"
    )
    return [
        {
            "pass_id": "sk_cz",
            "portal": "Bazos SK/CZ",
            "language": "sk/cs",
            "market_scope": "PUBLIC_SK_CZ",
            "search_method": "DIRECT_PORTAL_HTML",
            "search_query": identity["query"],
            "status": status,
            "error_type": "" if successful_fetches else "DIRECT_SEARCH_UNAVAILABLE",
            "citation_count": total_cards,
            "candidate_count": len(unique_candidates),
            "verified_detail_count": len(unique_candidates),
            "verified_background_count": 0,
            "url_unverified_count": 0,
            "candidates": unique_candidates,
            "source_attempts": attempts,
        }
    ]


def search_local_marketplaces(
    listing: dict[str, Any],
    *,
    timeout: float = 8.0,
    fetch_html: Callable[[str, float], str] | None = None,
) -> list[dict[str, Any]]:
    """Search the bounded set of customer-facing SK/CZ marketplaces.

    Bazos keeps its existing two-country parser. The other portals are parsed
    from their public model-result pages and only exact detail links found in
    those pages become candidates. The wrapper deliberately returns one
    ``sk_cz`` pass so the existing artifact schema and benchmark contract stay
    stable while ``source_portal`` and per-attempt records preserve provenance.
    """
    identity = derive_bazos_identity(listing)
    if not identity:
        return [
            {
                "pass_id": "sk_cz",
                "portal": "SK/CZ local marketplaces",
                "language": "sk/cs",
                "market_scope": "PUBLIC_SK_CZ",
                "search_method": "DIRECT_PORTAL_HTML",
                "status": "ERROR",
                "error_type": "SEARCH_IDENTITY_UNAVAILABLE",
                "citation_count": 0,
                "candidate_count": 0,
                "verified_detail_count": 0,
                "verified_background_count": 0,
                "url_unverified_count": 0,
                "candidates": [],
                "source_attempts": [],
            }
        ]

    loader = fetch_html or _fetch_html
    bazos_pass = search_bazos_sk_cz(
        listing,
        timeout=timeout,
        fetch_html=loader,
    )[0]
    attempts: list[dict[str, Any]] = []
    for attempt in bazos_pass.get("source_attempts") or []:
        if isinstance(attempt, dict):
            country = str(attempt.get("country") or "").upper()
            attempts.append(
                {
                    "portal": "bazos_sk" if country == "SK" else "bazos_cz",
                    **attempt,
                }
            )

    slug = re.sub(
        r"[^a-z0-9]+",
        "-",
        _fold(f"{identity['make']} {identity['model']}"),
    ).strip("-")
    make_slug = re.sub(r"[^a-z0-9]+", "-", _fold(identity["make"])).strip("-")
    model_slug = re.sub(r"[^a-z0-9]+", "-", _fold(identity["model"])).strip("-")
    portal_specs = (
        (
            "autobazar_sk",
            f"https://{slug}.autobazar.sk/?v=1",
        ),
        (
            "autobazar_eu",
            f"https://www.autobazar.eu/cs/vysledky/suv-terenne-vozidla/{make_slug}/{model_slug}/",
        ),
        (
            "autobazar_eu",
            f"https://www.autobazar.eu/cs/vysledky/osobne-vozidla/{make_slug}/{model_slug}/",
        ),
        (
            "sauto_cz",
            f"https://www.sauto.cz/inzerce/osobni/{make_slug}/{model_slug}",
        ),
    )
    all_candidates: list[dict[str, Any]] = list(bazos_pass.get("candidates") or [])
    total_cards = int(bazos_pass.get("citation_count") or 0)
    successful_fetches = sum(
        1
        for attempt in attempts
        if attempt.get("status") == "SUCCESS"
    )
    for portal, search_url in portal_specs:
        try:
            html = loader(search_url, timeout)
            candidates, counters = parse_local_portal_search_page(
                html,
                portal=portal,
                search_url=search_url,
                identity=identity,
                listing=listing,
            )
            successful_fetches += 1
            total_cards += counters["result_card_count"]
            all_candidates.extend(candidates)
            attempts.append(
                {
                    "portal": portal,
                    "search_url": search_url,
                    "status": "SUCCESS",
                    **counters,
                }
            )
        except Exception as exc:  # one portal must not suppress the others
            attempts.append(
                {
                    "portal": portal,
                    "search_url": search_url,
                    "status": "ERROR",
                    "error_type": type(exc).__name__,
                    "result_card_count": 0,
                    "parsed_candidate_count": 0,
                }
            )

    unique_candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in all_candidates:
        source_url = str(candidate.get("source_url") or "")
        if source_url and source_url not in seen:
            seen.add(source_url)
            unique_candidates.append(candidate)
    status = (
        "FOUND"
        if unique_candidates
        else "SEARCH_RESULTS_FOUND_NOT_STRUCTURED"
        if total_cards
        else "NOTHING_FOUND"
        if successful_fetches
        else "ERROR"
    )
    return [
        {
            "pass_id": "sk_cz",
            "portal": "SK/CZ local marketplaces",
            "language": "sk/cs",
            "market_scope": "PUBLIC_SK_CZ",
            "search_method": "DIRECT_PORTAL_HTML",
            "search_query": identity["query"],
            "status": status,
            "error_type": "" if successful_fetches else "DIRECT_SEARCH_UNAVAILABLE",
            "citation_count": total_cards,
            "candidate_count": len(unique_candidates),
            "verified_detail_count": len(unique_candidates),
            "verified_background_count": 0,
            "url_unverified_count": 0,
            "candidates": unique_candidates,
            "source_attempts": attempts,
        }
    ]


def _mobile_detail_url(value: Any, base_url: str) -> str:
    """Keep only exact Mobile.de detail URLs found in a result card."""
    raw_url = urljoin(base_url, str(value or "").strip())
    try:
        parsed = urlparse(raw_url)
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower()
    if not (host == "mobile.de" or host.endswith(".mobile.de")):
        return ""
    path = parsed.path.lower()
    if path.startswith("/fahrzeuge/details.html"):
        listing_ids = parse_qs(parsed.query).get("id") or []
        if any(re.fullmatch(r"\d+", value) for value in listing_ids):
            return parsed._replace(fragment="").geturl()
    if "/auto-inserat/" in path:
        return _canonical_url(raw_url)
    return ""


def _mobile_search_url(listing: dict[str, Any], identity: dict[str, str]) -> str:
    listing_text = " ".join(
        str(listing.get(key) or "")
        for key in ("title", "description_excerpt", "engine")
    )
    engine = _engine(listing_text)
    engine_slug = re.sub(r"[^a-z0-9]+", "-", _fold(engine)).strip("-")
    query_slug = re.sub(
        r"[^a-z0-9]+",
        "-",
        _fold(f"{identity['make']} {identity['model']} {engine_slug}"),
    ).strip("-")
    return f"https://suchen.mobile.de/auto/{query_slug}.html"


def parse_mobile_de_search_page(
    html: str,
    *,
    search_url: str,
    identity: dict[str, str],
    listing: dict[str, Any],
    max_candidates: int = 30,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Parse exact Mobile.de detail links for the hidden EU benchmark."""
    soup = BeautifulSoup(html or "", "html.parser")
    candidates: list[dict[str, Any]] = []
    counters = {
        "result_card_count": 0,
        "non_vehicle_filtered_count": 0,
        "model_mismatch_count": 0,
        "self_listing_filtered_count": 0,
        "parsed_candidate_count": 0,
    }
    model_compact = _compact(identity.get("model"))
    seen_urls: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        source_url = _mobile_detail_url(anchor.get("href"), search_url)
        if not source_url or source_url in seen_urls:
            continue
        seen_urls.add(source_url)
        counters["result_card_count"] += 1
        image = anchor.find("img")
        title = " ".join(
            str(
                anchor.get_text(" ", strip=True)
                or anchor.get("title")
                or anchor.get("aria-label")
                or (image.get("alt") if image is not None else "")
                or ""
            ).split()
        )
        card_text = _portal_card_text(anchor)
        combined = f"{title} {card_text}".strip()
        if _looks_like_non_vehicle_listing(combined):
            counters["non_vehicle_filtered_count"] += 1
            continue
        if model_compact and not _model_matches(identity.get("model"), combined):
            counters["model_mismatch_count"] += 1
            continue

        price_amount, currency, price_display = _portal_price(combined)
        year = _first_year(combined)
        mileage = _first_mileage(combined)
        engine = _engine(combined)
        power = _power(combined)
        transmission = _transmission(combined)
        drive = _drivetrain(combined)
        similarity_tier, material_difference = _similarity(listing, combined)
        candidates.append(
            {
                "candidate_id": _portal_candidate_id("mobile_de", source_url, len(candidates)),
                "description": title or combined[:180],
                "listing_title": title or combined[:180],
                "year": year,
                "mileage_km": mileage,
                "engine": engine,
                "power": power,
                "transmission": transmission,
                "drivetrain": drive,
                "price_eur": price_amount if currency == "EUR" else None,
                "price_display": price_display,
                "price_basis": "gross_asking",
                "source_country": "DE",
                "similarity_tier": similarity_tier,
                "material_difference": material_difference,
                "location": "",
                "search_pass": "mobile_de",
                "search_language": "de",
                "market_scope": "BACKGROUND_EU",
                "source_portal": "mobile_de",
                "source_url": source_url,
                "claimed_source_url": source_url,
                "evidence_url": search_url,
                "verified_url": True,
                "url_verification_status": "VERIFIED_DETAIL",
                "background_evidence_verified": False,
                "display_in_report": False,
                "data_provenance": "DIRECT_PORTAL_SEARCH",
            }
        )
        if len(candidates) >= max_candidates:
            break
    counters["parsed_candidate_count"] = len(candidates)
    return candidates, counters


def search_mobile_de(
    listing: dict[str, Any],
    *,
    timeout: float = 8.0,
    fetch_html: Callable[[str, float], str] | None = None,
) -> list[dict[str, Any]]:
    """Run one bounded Mobile.de background-only search."""
    identity = derive_bazos_identity(listing)
    if not identity:
        return [
            {
                "pass_id": "mobile_de",
                "portal": "Mobile.de",
                "language": "de",
                "market_scope": "BACKGROUND_EU",
                "search_method": "DIRECT_PORTAL_HTML",
                "status": "ERROR",
                "error_type": "SEARCH_IDENTITY_UNAVAILABLE",
                "citation_count": 0,
                "candidate_count": 0,
                "verified_detail_count": 0,
                "verified_background_count": 0,
                "url_unverified_count": 0,
                "candidates": [],
                "source_attempts": [],
            }
        ]

    search_url = _mobile_search_url(listing, identity)
    loader = fetch_html or _fetch_mobile_de_html
    try:
        html = loader(search_url, timeout)
        candidates, counters = parse_mobile_de_search_page(
            html,
            search_url=search_url,
            identity=identity,
            listing=listing,
        )
        status = (
            "FOUND"
            if candidates
            else "SEARCH_RESULTS_FOUND_NOT_STRUCTURED"
            if counters["result_card_count"]
            else "NOTHING_FOUND"
        )
        attempt = {
            "portal": "mobile_de",
            "search_url": search_url,
            "status": "SUCCESS",
            **counters,
        }
        return [
            {
                "pass_id": "mobile_de",
                "portal": "Mobile.de",
                "language": "de",
                "market_scope": "BACKGROUND_EU",
                "search_method": "DIRECT_PORTAL_HTML",
                "search_query": identity["query"],
                "status": status,
                "error_type": "",
                "citation_count": counters["result_card_count"],
                "candidate_count": len(candidates),
                "verified_detail_count": len(candidates),
                "verified_background_count": 0,
                "url_unverified_count": 0,
                "candidates": candidates,
                "source_attempts": [attempt],
            }
        ]
    except Exception as exc:
        error_details = _request_error_details(exc)
        return [
            {
                "pass_id": "mobile_de",
                "portal": "Mobile.de",
                "language": "de",
                "market_scope": "BACKGROUND_EU",
                "search_method": "DIRECT_PORTAL_HTML",
                "search_query": identity["query"],
                "status": "ERROR",
                **error_details,
                "citation_count": 0,
                "candidate_count": 0,
                "verified_detail_count": 0,
                "verified_background_count": 0,
                "url_unverified_count": 0,
                "candidates": [],
                "source_attempts": [
                    {
                        "portal": "mobile_de",
                        "search_url": search_url,
                        "status": "ERROR",
                        **error_details,
                        "result_card_count": 0,
                        "parsed_candidate_count": 0,
                    }
                ],
            }
        ]


def search_all_marketplaces(
    listing: dict[str, Any],
    *,
    timeout: float = 8.0,
    fetch_html: Callable[[str, float], str] | None = None,
) -> list[dict[str, Any]]:
    """Search local portals first and use Mobile.de only for a thin sample."""
    local_passes = search_local_marketplaces(
        listing,
        timeout=timeout,
        fetch_html=fetch_html,
    )
    local_pass = local_passes[0] if local_passes else {}
    precheck = strict_local_market_precheck(
        list(local_pass.get("candidates") or []),
        listing,
    )
    local_pass["strict_local_precheck"] = precheck
    local_pass["strict_eligible_count"] = precheck["eligible_count"]
    if precheck["sufficient"]:
        return local_passes + [{
            "pass_id": "mobile_de",
            "portal": "Mobile.de",
            "language": "de",
            "market_scope": "BACKGROUND_EU",
            "search_method": "SKIPPED_LOCAL_SAMPLE_SUFFICIENT",
            "search_query": "",
            "status": "SKIPPED",
            "skip_reason": "strict_local_sample_sufficient",
            "strict_local_eligible_count": precheck["eligible_count"],
            "citation_count": 0,
            "candidate_count": 0,
            "verified_detail_count": 0,
            "verified_background_count": 0,
            "url_unverified_count": 0,
            "candidates": [],
            "source_attempts": [],
        }]
    mobile_passes = search_mobile_de(
        listing,
        timeout=timeout,
        fetch_html=fetch_html,
    )
    for mobile_pass in mobile_passes:
        background_precheck = strict_background_market_precheck(
            list(mobile_pass.get("candidates") or []),
            listing,
        )
        mobile_pass["strict_background_precheck"] = background_precheck
        mobile_pass["strict_eligible_count"] = background_precheck["eligible_count"]
        mobile_pass["skip_reason"] = ""
        mobile_pass["strict_local_eligible_count"] = precheck["eligible_count"]
    return local_passes + mobile_passes
