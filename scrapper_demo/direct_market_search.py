"""Deterministic marketplace search for customer-visible SK/CZ comparables.

This module deliberately does not use a language model.  It reads the public
Bazos result pages, keeps the exact detail links present in those pages, and
turns the visible result-card fields into the existing market-comparable
contract.  A result-page link is evidence for discovery, not proof of the
vehicle's condition or of a completed transaction price.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


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
        # keeps the base characters.  The first following token is the model;
        # this avoids polluting a query with year, engine, trim, or mileage.
        remainder = title[match.end() :].lstrip(" -,:/")
        model_match = re.match(r"([A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)", remainder)
        if not model_match:
            return {}
        model = model_match.group(1).strip("-")
        if not model or re.fullmatch(r"(?:19|20)\d{2}", model):
            return {}
        return {
            "make": canonical_make,
            "model": model,
            "query": f"{canonical_make} {model}",
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
    match = re.search(
        r"\b(\d[.,]\d\s*(?:tsi|tdi|t-gdi|tgdi|gdi|crdi|dci|hdi|bluehdi|ecoboost|vvt|hybrid|phev|l))\b",
        _fold(value),
        re.IGNORECASE,
    )
    return " ".join(match.group(1).upper().replace(",", ".").split()) if match else ""


def _transmission(value: str) -> str:
    folded = _fold(value)
    if re.search(r"\b(?:dsg|dct|cvt|edc|s-tronic|tiptronic)\b", folded):
        return re.search(r"\b(?:dsg|dct|cvt|edc|s-tronic|tiptronic)\b", folded).group(0).upper()  # type: ignore[union-attr]
    if re.search(r"\b(?:automat|automatic|automatik)\w*\b", folded):
        return "AUTOMATIC"
    if re.search(r"\b(?:manual|manualn)\w*\b", folded):
        return "MANUAL"
    return ""


def _drivetrain(value: str) -> str:
    folded = _fold(value)
    four_wheel = bool(re.search(r"\b(?:4x4|4wd|awd|quattro|allrad)\b", folded))
    front_wheel = bool(re.search(r"\b(?:fwd|predny pohon|predokol)\w*\b", folded))
    rear_wheel = bool(re.search(r"\b(?:rwd|zadny pohon|zadokol)\w*\b", folded))
    if sum((four_wheel, front_wheel, rear_wheel)) > 1:
        return ""
    if four_wheel:
        return "4X4"
    if front_wheel:
        return "FWD"
    if rear_wheel:
        return "RWD"
    return ""


def _similarity(
    listing: dict[str, Any], candidate_text: str
) -> tuple[str, str]:
    fallback_listing_text = " ".join(
        str(listing.get(key) or "") for key in ("title", "description_excerpt")
    )
    differences: list[str] = []
    matches = 0
    compared = 0
    for label, extractor, structured_key in (
        ("engine", _engine, "engine"),
        ("transmission", _transmission, "transmission"),
        ("drive", _drivetrain, "drive"),
    ):
        expected = extractor(str(listing.get(structured_key) or "")) or extractor(
            fallback_listing_text
        )
        observed = extractor(candidate_text)
        if expected and observed:
            compared += 1
            if _compact(expected) == _compact(observed):
                matches += 1
            else:
                differences.append(f"different {label}")
    if compared >= 2 and matches == compared:
        return "A", "same visible engine/transmission/drivetrain attributes"
    if differences:
        return "B", ", ".join(differences)
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
        if any(marker in folded_title for marker in _NON_VEHICLE_TITLE_MARKERS):
            counters["non_vehicle_filtered_count"] += 1
            continue
        description_node = card.select_one("div.popis")
        description = (
            " ".join(description_node.get_text(" ", strip=True).split())
            if description_node is not None
            else ""
        )
        if model_compact and model_compact not in _compact(f"{title} {description[:240]}"):
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
