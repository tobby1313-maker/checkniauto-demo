"""Deterministic filtering and public selection of comparable market ads."""

from __future__ import annotations

import re
import unicodedata
from typing import Any
from urllib.parse import urlparse, urlunparse


_TOKEN_STOPWORDS = {
    "auto",
    "car",
    "vozidlo",
    "predaj",
    "ponuka",
    "serv",
    "servis",
    "kniha",
    "manual",
    "manualna",
    "benzin",
    "diesel",
    "eur",
    "czk",
    "pln",
    "kw",
    "km",
}

_PUBLIC_MARKETPLACE_HOSTS = {
    "autobazar.eu",
    "autobazar.sk",
    "bazos.sk",
    "bazos.cz",
}

_COUNTRY_ALIASES = {
    "sk": "SK",
    "svk": "SK",
    "slovakia": "SK",
    "slovensko": "SK",
    "slovenska republika": "SK",
    "cz": "CZ",
    "cze": "CZ",
    "czechia": "CZ",
    "czech republic": "CZ",
    "cesko": "CZ",
    "ceska republika": "CZ",
}

_URL_TOKEN_STOPWORDS = {
    "auto",
    "detail",
    "en",
    "id",
    "inzerat",
    "offer",
    "offers",
    "osobni",
    "sk",
    "www",
}


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in text if not unicodedata.combining(char)).lower()


def _number(value: Any) -> int | None:
    if isinstance(value, (int, float)):
        return int(round(value))
    matches = re.findall(r"(?<!\d)(?:\d{1,3}(?:[ \u00a0]\d{3})+|\d+)(?!\d)", str(value or ""))
    if not matches:
        return None
    values = [int(re.sub(r"[ \u00a0]", "", item)) for item in matches]
    return max(values)


def _year(item: dict[str, Any]) -> int | None:
    explicit = _number(item.get("year"))
    if explicit is not None and 1980 <= explicit <= 2100:
        return explicit
    match = re.search(r"\b((?:19|20)\d{2})\b", str(item.get("description") or ""))
    return int(match.group(1)) if match else None


def _vin(item: dict[str, Any]) -> str:
    candidate = re.sub(r"[^A-HJ-NPR-Z0-9]", "", str(item.get("vin") or "").upper())
    return candidate if len(candidate) == 17 else ""


def _canonical_url(value: Any) -> str:
    try:
        parsed = urlparse(str(value or "").strip())
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", "", ""))


def _url_host(value: Any) -> str:
    try:
        return (urlparse(str(value or "").strip()).hostname or "").lower()
    except ValueError:
        return ""


def _host_matches(host: str, expected: str) -> bool:
    return host == expected or host.endswith(f".{expected}")


def _marketplace_host(value: Any) -> str:
    host = _url_host(value)
    for expected in (
        *_PUBLIC_MARKETPLACE_HOSTS,
        "sauto.cz",
        "tipcars.com",
        "tipcars.sk",
        "mobile.de",
        "autoscout24.com",
    ):
        if _host_matches(host, expected):
            return expected
    return host


def _looks_like_direct_ad_url(value: Any) -> bool:
    try:
        parsed = urlparse(str(value or "").strip())
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    path = parsed.path.lower()
    if not path.strip("/") or any(
        marker in path
        for marker in ("/inzeraty/", "/search", "/category", "/katalog", "/filter", "/vysledky")
    ):
        return False
    host = _marketplace_host(value)
    if host in {"bazos.sk", "bazos.cz"}:
        return bool(re.search(r"/inzerat/\d+", path))
    if host == "autobazar.eu":
        return "/detail/" in path or bool(re.search(r"-id\d+\.html$", path))
    if host == "autobazar.sk":
        return "/detail/" in path or bool(re.match(r"^/\d+/[^/]+/?$", path))
    return any(
        marker in path
        for marker in ("/detail/", "/inzerat/", "/offer/", "/offers/", "/auto-inserat/")
    )


def _url_identity_tokens(value: Any) -> set[str]:
    try:
        parsed = urlparse(str(value or "").strip())
    except ValueError:
        return set()
    return {
        token
        for token in re.findall(r"[a-z0-9]{2,}", _fold(parsed.path))
        if token not in _URL_TOKEN_STOPWORDS and not token.isdigit()
    }


def _url_identity_similarity(left: Any, right: Any) -> float:
    left_tokens, right_tokens = _url_identity_tokens(left), _url_identity_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _citation_market_urls(web_research_text: str) -> list[str]:
    """Extract direct ad URLs only from grounding's authoritative citations."""
    urls: list[str] = []
    in_citations = False
    for line in str(web_research_text or "").splitlines():
        heading = re.match(r"^\s*(#{2,4})\s+(.+?)\s*$", line)
        if heading:
            title = _fold(heading.group(2))
            in_citations = "citacie z google search" in title or "google search citations" in title
            continue
        if not in_citations:
            continue
        for match in re.finditer(r"\[[^\]\n]+\]\((https?://[^\s)]+(?:\([^\s)]*\)[^\s)]*)*)\)", line, re.IGNORECASE):
            url = _canonical_url(match.group(1))
            if url and _looks_like_direct_ad_url(url) and url not in urls:
                urls.append(url)
    return urls


def reconcile_market_comparable_urls(
    research: dict[str, Any],
    web_research_text: str,
) -> dict[str, Any]:
    """Use only direct ad URLs backed by the current grounding citations.

    Grounded narrative occasionally repeats an expired marketplace URL while
    its annotation/citation contains the current canonical detail URL. Match
    those by marketplace and stable slug tokens. If no citation supports an
    advertised URL, it must not become a customer link or market datapoint.
    """
    raw_items = research.get("market_comparables")
    if not isinstance(raw_items, list):
        return research
    citations = _citation_market_urls(web_research_text)
    citation_set = set(citations)
    reconciled: list[dict[str, Any]] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        item = dict(raw_item)
        original_url = _canonical_url(item.get("source_url"))
        replacement = original_url if original_url in citation_set else ""
        if not replacement and original_url:
            same_market = [
                url
                for url in citations
                if _marketplace_host(url) == _marketplace_host(original_url)
            ]
            ranked = sorted(
                (
                    (_url_identity_similarity(original_url, candidate), candidate)
                    for candidate in same_market
                ),
                reverse=True,
            )
            if ranked and ranked[0][0] >= 0.55:
                if len(ranked) == 1 or ranked[0][0] > ranked[1][0]:
                    replacement = ranked[0][1]
        if replacement:
            item["source_url"] = replacement
            item["verified_url"] = True
        else:
            item["source_url"] = ""
            item["verified_url"] = False
            item["display_in_report"] = False
        reconciled.append(item)
    research["market_comparables"] = reconciled
    return research


def _normalized_country(value: Any) -> str:
    return _COUNTRY_ALIASES.get(_fold(value).strip(), "")


def _market_country(item: dict[str, Any]) -> str:
    """Return SK/CZ when the ad's public market can be established safely."""
    explicit_value = str(item.get("source_country") or "").strip()
    if explicit_value:
        explicit = _normalized_country(explicit_value)
        if explicit:
            return explicit
        # Preserve a supplied foreign country code so an Autobazar.EU offer
        # from another national market cannot be inferred as Slovak by price.
        if re.fullmatch(r"[A-Za-z]{2,3}", explicit_value):
            return explicit_value.upper()
        return _fold(explicit_value).strip().upper()
    host = _url_host(item.get("source_url"))
    if _host_matches(host, "bazos.sk") or _host_matches(host, "autobazar.sk"):
        return "SK"
    if _host_matches(host, "bazos.cz"):
        return "CZ"
    if _host_matches(host, "autobazar.eu"):
        # Autobazar.EU contains several national markets. Older model output
        # has no source_country, so use the advertisement currency as a narrow
        # backwards-compatible hint instead of exposing arbitrary EU offers.
        display = _fold(item.get("price_display"))
        if "czk" in display or re.search(r"\bkc\b", display):
            return "CZ"
        if "eur" in display or item.get("price_eur") not in (None, ""):
            return "SK"
    return ""


def customer_link_priority(item: dict[str, Any]) -> int:
    """Rank customer-facing links: supported Slovak ads, then Czech ads."""
    host = _url_host(item.get("source_url"))
    if not any(_host_matches(host, expected) for expected in _PUBLIC_MARKETPLACE_HOSTS):
        return 0
    country = _market_country(item)
    if country == "SK":
        return 2
    if country == "CZ":
        return 1
    return 0


def is_customer_facing_market_comparable(item: Any) -> bool:
    """Return whether an ad may be linked as a concrete public comparison."""
    return (
        isinstance(item, dict)
        and item.get("verified_url") is True
        and bool(_canonical_url(item.get("source_url")))
        and customer_link_priority(item) > 0
    )


def _tokens(item: dict[str, Any]) -> set[str]:
    text = _fold(
        " ".join(
            str(item.get(key) or "")
            for key in ("description", "engine", "trim", "transmission", "drivetrain")
        )
    )
    return {
        token
        for token in re.findall(r"[a-z0-9]{2,}", text)
        if token not in _TOKEN_STOPWORDS and not re.fullmatch(r"\d{4,6}", token)
    }


def _token_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_tokens, right_tokens = _tokens(left), _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _price(item: dict[str, Any]) -> tuple[str, int] | None:
    eur = _number(item.get("price_eur"))
    if eur is not None:
        return "EUR", eur
    display = _fold(item.get("price_display"))
    amount = _number(display)
    if amount is None:
        return None
    currency = next(
        (code for marker, code in (("czk", "CZK"), ("kc", "CZK"), ("pln", "PLN"), ("huf", "HUF"), ("eur", "EUR")) if marker in display),
        "",
    )
    return (currency, amount) if currency else None


def _prices_close(left: dict[str, Any], right: dict[str, Any], *, exact: bool = False) -> bool:
    left_price, right_price = _price(left), _price(right)
    if left_price is None or right_price is None or left_price[0] != right_price[0]:
        return False
    difference = abs(left_price[1] - right_price[1])
    tolerance = max(50, max(left_price[1], right_price[1]) * (0.005 if exact else 0.05))
    return difference <= tolerance


def _same_vehicle(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_vin, right_vin = _vin(left), _vin(right)
    if left_vin and right_vin:
        return left_vin == right_vin

    left_year, right_year = _year(left), _year(right)
    if left_year is None or right_year is None or left_year != right_year:
        return False
    left_mileage = _number(left.get("mileage_km"))
    right_mileage = _number(right.get("mileage_km"))
    if left_mileage is None or right_mileage is None or abs(left_mileage - right_mileage) > 20:
        return False

    seller_left = _fold(left.get("seller_or_location"))
    seller_right = _fold(right.get("seller_or_location"))
    same_seller = bool(seller_left and seller_right and seller_left == seller_right)
    similarity = _token_similarity(left, right)
    if same_seller and similarity >= 0.35:
        return True

    precise_mileage = left_mileage % 1000 != 0 or right_mileage % 1000 != 0
    if precise_mileage:
        return similarity >= 0.55 and _prices_close(left, right)
    return (
        left_mileage == right_mileage
        and similarity >= 0.75
        and _prices_close(left, right, exact=True)
    )


def _listing_identity(listing_text: str) -> dict[str, Any]:
    text = str(listing_text or "")
    source_match = re.search(r"(?im)^\*\*Source:\*\*\s*(https?://\S+)", text)
    price_match = re.search(r"(?im)^-\s*\*\*Price:\*\*\s*([^\n]+)", text)
    mileage_match = re.search(r"(?im)^\|\s*Mileage\s*\|\s*([^|]+)", text)
    year_match = (
        re.search(r"(?im)^\|\s*(?:Year|Rok)\s*\|\s*((?:19|20)\d{2})\b", text)
        or re.search(r"(?i)\b(?:r\.?\s*v\.?|rok(?:\s+vyroby)?)\s*[:.]?\s*(?:\d{1,2}/)?((?:19|20)\d{2})\b", text)
    )
    title_match = re.search(r"(?m)^#\s+(.+)$", text)
    vin_match = re.search(r"\b([A-HJ-NPR-Z0-9]{17})\b", text.upper())
    price_text = price_match.group(1).strip() if price_match else ""
    return {
        "description": title_match.group(1).strip() if title_match else "",
        "year": int(year_match.group(1)) if year_match else None,
        "mileage_km": _number(mileage_match.group(1)) if mileage_match else None,
        "price_eur": _number(price_text) if "eur" in _fold(price_text) else None,
        "price_display": price_text,
        "vin": vin_match.group(1) if vin_match else "",
        "source_url": source_match.group(1).strip() if source_match else "",
    }


def _quality(item: dict[str, Any]) -> tuple[int, int, int, int, int]:
    relevance = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(str(item.get("relevance") or "").upper(), 0)
    return (
        customer_link_priority(item),
        relevance,
        1 if _vin(item) else 0,
        1 if item.get("seller_or_location") else 0,
        sum(item.get(key) not in (None, "") for key in ("mileage_km", "price_eur", "price_display")),
    )


def deduplicate_market_comparables(
    research: dict[str, Any],
    listing_text: str,
) -> dict[str, Any]:
    """Keep one best record per vehicle and exclude the analyzed listing."""
    raw_items = research.get("market_comparables")
    if not isinstance(raw_items, list):
        return research
    candidates = [
        dict(item)
        for item in raw_items
        if isinstance(item, dict)
        and item.get("verified_url") is True
        and _looks_like_direct_ad_url(item.get("source_url"))
    ]
    candidates.sort(key=_quality, reverse=True)
    original = _listing_identity(listing_text)
    original_url = _canonical_url(original.get("source_url"))
    unique: list[dict[str, Any]] = []
    for item in candidates:
        item_url = _canonical_url(item.get("source_url"))
        if item_url and original_url and item_url == original_url:
            continue
        if _same_vehicle(item, original):
            continue
        if any(_same_vehicle(item, existing) for existing in unique):
            continue
        country = _market_country(item)
        if country and not item.get("source_country"):
            item["source_country"] = country
        item["display_in_report"] = is_customer_facing_market_comparable(item)
        unique.append(item)

    unique.sort(key=_quality, reverse=True)

    research["market_comparables"] = unique
    market = research.get("market_assessment")
    if isinstance(market, dict):
        market["comparable_count"] = len(unique)
        market["available"] = bool(unique)
        market["public_comparable_count"] = sum(
            1 for item in unique if is_customer_facing_market_comparable(item)
        )
        eur_prices = [
            int(round(item["price_eur"]))
            for item in unique
            if isinstance(item.get("price_eur"), (int, float))
        ]
        market["eur_priced_comparable_count"] = len(eur_prices)
        market["observed_market_low_eur"] = min(eur_prices) if eur_prices else None
        market["observed_market_high_eur"] = max(eur_prices) if eur_prices else None
        market["observed_market_average_eur"] = (
            int(round(sum(eur_prices) / len(eur_prices))) if eur_prices else None
        )
        if not unique:
            market["price_view"] = "requires_manual_verification"
            market["negotiation_anchor_eur"] = None
    return research
