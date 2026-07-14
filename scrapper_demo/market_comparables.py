"""Deterministic filtering and public selection of comparable market ads."""

from __future__ import annotations

import re
import statistics
import time
import unicodedata
from datetime import date, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree


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
    "sauto.cz",
    "tipcars.com",
}

_BACKGROUND_MARKETPLACE_HOSTS = {
    "autoscout24.at",
    "autoscout24.be",
    "autoscout24.com",
    "autoscout24.de",
    "autoscout24.fr",
    "autoscout24.it",
    "mobile.de",
    "otomoto.pl",
}

ECB_90_DAY_RATES_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml"
_ECB_CACHE_SECONDS = 6 * 60 * 60
_ecb_cache: tuple[float, dict[str, Any]] | None = None

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
    "de": "DE",
    "germany": "DE",
    "deutschland": "DE",
    "nemecko": "DE",
    "pl": "PL",
    "poland": "PL",
    "polsko": "PL",
    "at": "AT",
    "austria": "AT",
    "rakusko": "AT",
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
    host = (parsed.hostname or "").lower()
    query = ""
    if _host_matches(host, "mobile.de") and parsed.path.lower().endswith("/details.html"):
        stable = [(key, item) for key, item in parse_qsl(parsed.query) if key.lower() == "id"]
        query = urlencode(stable[:1])
        if not query:
            return ""
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip("/"),
            "",
            query,
            "",
        )
    )


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
        "tipcars.sk",
        *_BACKGROUND_MARKETPLACE_HOSTS,
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
    if host == "sauto.cz":
        return bool(re.search(r"/osobni/detail/[^/]+/[^/]+/\d+", path))
    if host in {"tipcars.com", "tipcars.sk"}:
        return "/auto-inserat/" in path
    if host == "mobile.de":
        return "/auto-inserat/" in path or "/fahrzeuge/details.html" in path
    if host.startswith("autoscout24."):
        return "/angebote/" in path or "/offres/" in path or "/annunci/" in path
    if host == "otomoto.pl":
        return "/osobowe/oferta/" in path and "-id" in path
    return any(
        marker in path
        for marker in ("/detail/", "/inzerat/", "/offer/", "/offers/", "/auto-inserat/")
    )


def _parse_ecb_reference_rates(payload: bytes) -> dict[str, Any]:
    """Use latest ECB rates, except a 30-calendar-day average for CZK."""
    root = ElementTree.fromstring(payload)
    observations: list[tuple[date, dict[str, float]]] = []
    for element in root.iter():
        raw_date = element.attrib.get("time")
        if not raw_date:
            continue
        try:
            observation_date = date.fromisoformat(raw_date)
        except ValueError:
            continue
        rates: dict[str, float] = {"EUR": 1.0}
        for child in element:
            currency = str(child.attrib.get("currency") or "").upper()
            raw_rate = child.attrib.get("rate")
            if not currency or not raw_rate:
                continue
            try:
                rates[currency] = float(raw_rate)
            except ValueError:
                continue
        if len(rates) > 1:
            observations.append((observation_date, rates))
    if not observations:
        raise ValueError("ECB response contained no usable exchange rates")
    observations.sort(key=lambda item: item[0])
    latest_date, latest_rates = observations[-1]
    rates_per_eur = dict(latest_rates)
    window_start = latest_date - timedelta(days=29)
    czk_observations = [
        rates["CZK"]
        for observation_date, rates in observations
        if observation_date >= window_start and "CZK" in rates
    ]
    rate_details: dict[str, dict[str, Any]] = {}
    if czk_observations:
        rates_per_eur["CZK"] = statistics.fmean(czk_observations)
        rate_details["CZK"] = {
            "method": "ECB_30_CALENDAR_DAY_AVERAGE",
            "window_start": window_start.isoformat(),
            "window_end": latest_date.isoformat(),
            "observations": len(czk_observations),
        }
    return {
        "base_currency": "EUR",
        "rate_date": latest_date.isoformat(),
        "source": "ECB_REFERENCE_RATES",
        "source_url": ECB_90_DAY_RATES_URL,
        "rates_per_eur": rates_per_eur,
        "rate_details": rate_details,
    }


def fetch_ecb_reference_rates(*, timeout: float = 5.0) -> dict[str, Any]:
    """Return ECB rates with a stable 30-calendar-day CZK average.

    This call is best-effort at pipeline level. If it fails, non-EUR ads stay
    in the audit data but do not enter an EUR-denominated benchmark.
    """
    global _ecb_cache
    now = time.monotonic()
    if _ecb_cache and now - _ecb_cache[0] < _ECB_CACHE_SECONDS:
        return dict(_ecb_cache[1])
    request = Request(
        ECB_90_DAY_RATES_URL,
        headers={"User-Agent": "CarWorth/market-benchmark"},
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed ECB URL
        payload = response.read()
    result = _parse_ecb_reference_rates(payload)
    _ecb_cache = (now, result)
    return dict(result)


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
    if _host_matches(host, "sauto.cz") or _host_matches(host, "tipcars.com"):
        return "CZ"
    if _host_matches(host, "otomoto.pl"):
        return "PL"
    if _host_matches(host, "mobile.de") or _host_matches(host, "autoscout24.de"):
        return "DE"
    if _host_matches(host, "autoscout24.at"):
        return "AT"
    if _host_matches(host, "autoscout24.be"):
        return "BE"
    if _host_matches(host, "autoscout24.fr"):
        return "FR"
    if _host_matches(host, "autoscout24.it"):
        return "IT"
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
        (
            code
            for marker, code in (
                ("czk", "CZK"),
                ("kc", "CZK"),
                ("pln", "PLN"),
                ("zl", "PLN"),
                ("huf", "HUF"),
                ("eur", "EUR"),
                ("€", "EUR"),
            )
            if marker in display
        ),
        "",
    )
    return (currency, amount) if currency else None


def _similarity_tier(item: dict[str, Any]) -> str:
    explicit = str(item.get("similarity_tier") or "").strip().upper()
    if explicit in {"A", "B", "C"}:
        return explicit
    return {
        "HIGH": "A",
        "MEDIUM": "B",
        "LOW": "C",
    }.get(str(item.get("relevance") or "").strip().upper(), "C")


def _excluded_price_basis(item: dict[str, Any]) -> str:
    basis = _fold(item.get("price_basis"))
    text = _fold(
        f"{item.get('description', '')} {item.get('material_difference', '')} "
        f"{item.get('price_display', '')}"
    )
    if basis in {"net", "net_price", "auction", "damaged", "export_only"}:
        return basis.upper()
    if any(marker in text for marker in ("netto", "bez dph", "without vat", "excl vat")):
        return "NET_PRICE"
    if any(marker in text for marker in ("havaria", "havarovane", "damaged", "salvage", "aukcia")):
        return "NON_RETAIL_OFFER"
    return ""


def _normalized_eur_price(
    item: dict[str, Any],
    rates_per_eur: dict[str, Any],
) -> tuple[int | None, str, int | None]:
    parsed = _price(item)
    if parsed is None:
        return None, "", None
    currency, amount = parsed
    if currency == "EUR":
        return amount, currency, amount
    raw_rate = rates_per_eur.get(currency)
    if raw_rate is None:
        return None, currency, amount
    try:
        rate = float(raw_rate)
    except (TypeError, ValueError):
        return None, currency, amount
    if rate <= 0:
        return None, currency, amount
    return int(round(amount / rate)), currency, amount


def build_market_benchmark(
    research: dict[str, Any],
    listing_text: str,
    *,
    exchange_rates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an auditable EUR benchmark without exposing foreign ad links.

    Only direct, deduplicated A/B retail offers enter the benchmark. Three
    normalized observations are required before the advertised price is
    classified. This keeps a thin or C-tier sample from producing false
    precision while still retaining every verified ad for audit purposes.
    """
    market = research.get("market_assessment")
    if not isinstance(market, dict):
        market = {}
        research["market_assessment"] = market
    items = research.get("market_comparables")
    if not isinstance(items, list):
        items = []

    rate_payload = exchange_rates if isinstance(exchange_rates, dict) else {}
    rates = rate_payload.get("rates_per_eur")
    if not isinstance(rates, dict):
        rates = {"EUR": 1.0}
    else:
        rates = {**rates, "EUR": 1.0}
    rate_details = rate_payload.get("rate_details")
    if not isinstance(rate_details, dict):
        rate_details = {}

    listing_identity = _listing_identity(listing_text)
    listing_facts = research.get("listing_facts")
    if isinstance(listing_facts, dict):
        listing_year = _number(listing_facts.get("year")) or _number(
            listing_facts.get("advertised_year")
        )
        listing_mileage = _number(listing_facts.get("advertised_mileage_km")) or _number(
            listing_facts.get("mileage_km")
        ) or _number(listing_facts.get("mileage"))
    else:
        listing_year = None
        listing_mileage = None
    listing_year = listing_year or listing_identity.get("year")
    listing_mileage = listing_mileage or listing_identity.get("mileage_km")

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        item = raw
        tier = _similarity_tier(item)
        country = _market_country(item)
        public = is_customer_facing_market_comparable(item)
        item["similarity_tier"] = tier
        item["market_scope"] = "PUBLIC_SK_CZ" if public else "BACKGROUND_EU"
        item["display_in_report"] = public
        if country and not item.get("source_country"):
            item["source_country"] = country

        normalized, currency, original_amount = _normalized_eur_price(item, rates)
        item["original_currency"] = currency
        item["original_price"] = original_amount
        item["normalized_price_eur"] = normalized
        currency_rate_detail = rate_details.get(currency)
        if not isinstance(currency_rate_detail, dict):
            currency_rate_detail = {}
        item["normalization_method"] = (
            "ORIGINAL_EUR"
            if normalized is not None and currency == "EUR"
            else str(currency_rate_detail.get("method") or "ECB_REFERENCE_RATE")
            if normalized is not None
            else "UNAVAILABLE"
        )
        exclusion = _excluded_price_basis(item)
        if tier == "C":
            exclusion = exclusion or "SIMILARITY_TIER_C"
        item_year = _year(item)
        item_mileage = _number(item.get("mileage_km"))
        if (
            listing_year is None
            or listing_mileage is None
            or item_year is None
            or item_mileage is None
        ):
            exclusion = exclusion or "MISSING_YEAR_OR_MILEAGE"
        elif abs(item_year - listing_year) > 3:
            exclusion = exclusion or "YEAR_OUTSIDE_BENCHMARK_BAND"
        elif abs(item_mileage - listing_mileage) > max(
            40000, int(listing_mileage * 0.35)
        ):
            exclusion = exclusion or "MILEAGE_OUTSIDE_BENCHMARK_BAND"
        if normalized is None:
            exclusion = exclusion or "NO_EUR_NORMALIZATION"
        audit_row = {
            "source_url": str(item.get("source_url") or ""),
            "source_country": country,
            "market_scope": item["market_scope"],
            "similarity_tier": tier,
            "original_price": original_amount,
            "original_currency": currency,
            "normalized_price_eur": normalized,
        }
        if exclusion:
            audit_row["exclusion_reason"] = exclusion
            rejected.append(audit_row)
        else:
            accepted.append(audit_row)

    all_normalized_prices = [int(item["normalized_price_eur"]) for item in accepted]
    local_prices = [
        int(item["normalized_price_eur"])
        for item in accepted
        if item.get("market_scope") == "PUBLIC_SK_CZ"
    ]
    foreign_prices = [
        int(item["normalized_price_eur"])
        for item in accepted
        if item.get("market_scope") == "BACKGROUND_EU"
    ]
    if len(local_prices) >= 3:
        selected_prices = local_prices
        benchmark_scope = "SK_CZ"
        classification_threshold = 12
    else:
        selected_prices = all_normalized_prices
        benchmark_scope = "EU_MIXED_BACKGROUND"
        classification_threshold = 15
    benchmark_available = len(selected_prices) >= 3
    median_eur = int(round(statistics.median(selected_prices))) if selected_prices else None
    local_median_eur = int(round(statistics.median(local_prices))) if local_prices else None
    foreign_median_eur = int(round(statistics.median(foreign_prices))) if foreign_prices else None
    advertised = _number(market.get("advertised_price_eur"))
    if advertised is None:
        advertised = listing_identity.get("price_eur")
    delta_percent = (
        round(((advertised - median_eur) / median_eur) * 100, 1)
        if advertised is not None and median_eur
        else None
    )
    if not benchmark_available or delta_percent is None:
        price_view = "requires_manual_verification"
    elif delta_percent <= -classification_threshold:
        price_view = "rather_cheap"
    elif delta_percent >= classification_threshold:
        price_view = "rather_expensive"
    else:
        price_view = "fair"

    selected_scope = (
        {"PUBLIC_SK_CZ"}
        if benchmark_scope == "SK_CZ"
        else {"PUBLIC_SK_CZ", "BACKGROUND_EU"}
    )
    selected = [item for item in accepted if item.get("market_scope") in selected_scope]
    tier_a_count = sum(item.get("similarity_tier") == "A" for item in selected)
    confidence = (
        "HIGH"
        if benchmark_available
        and benchmark_scope == "SK_CZ"
        and len(selected_prices) >= 5
        and tier_a_count >= 3
        else "MEDIUM"
        if benchmark_available
        else "LOW"
    )
    market.update(
        {
            "available": bool(items),
            "advertised_price_eur": advertised,
            "comparable_count": len(items),
            "public_comparable_count": sum(
                1 for item in items if is_customer_facing_market_comparable(item)
            ),
            "eur_priced_comparable_count": len(all_normalized_prices),
            "benchmark_comparable_count": len(selected_prices),
            "benchmark_available": benchmark_available,
            "benchmark_confidence": confidence,
            "benchmark_scope": benchmark_scope,
            "observed_market_low_eur": min(selected_prices) if selected_prices else None,
            "observed_market_high_eur": max(selected_prices) if selected_prices else None,
            "observed_market_average_eur": median_eur,
            "benchmark_median_eur": median_eur,
            "local_market_median_eur": local_median_eur,
            "foreign_background_median_eur": foreign_median_eur,
            "price_delta_percent": delta_percent if benchmark_available else None,
            "price_view": price_view,
            "negotiation_anchor_eur": median_eur if price_view == "rather_expensive" else None,
            "summary": (
                f"Cenová pozícia vychádza z mediánu {len(selected_prices)} overených porovnateľných ponúk."
                if benchmark_available
                else "Na spoľahlivé vyhodnotenie ceny nie sú aspoň tri overené A/B ponuky."
            ),
            "limitations": (
                "Ide o ponukové, nie realizačné ceny; výbava, stav, záruka a dovozné náklady nie sú normalizované."
            ),
            "negotiation_reason": (
                f"Inzerovaná cena je najmenej o {classification_threshold} % nad mediánom overenej A/B vzorky."
                if price_view == "rather_expensive"
                else ""
            ),
        }
    )
    benchmark = {
        "schema_version": 1,
        "method": "MEDIAN_OF_VERIFIED_TIER_A_B_ASKING_PRICES",
        "minimum_sample_size": 3,
        "available": benchmark_available,
        "confidence": confidence,
        "benchmark_scope": benchmark_scope,
        "classification_threshold_percent": classification_threshold,
        "advertised_price_eur": advertised,
        "median_eur": median_eur,
        "local_market_median_eur": local_median_eur,
        "foreign_background_median_eur": foreign_median_eur,
        "price_delta_percent": delta_percent if benchmark_available else None,
        "price_view": price_view,
        "exchange_rates": {
            "source": rate_payload.get("source", ""),
            "source_url": rate_payload.get("source_url", ""),
            "rate_date": rate_payload.get("rate_date", ""),
            "rate_details": rate_details,
        },
        "accepted_comparables": accepted,
        "rejected_comparables": rejected,
        "limitations": [
            "Asking prices are not completed transaction prices.",
            "No deterministic adjustment is made for equipment, condition, import costs, or warranty.",
            "Tier C, net, auction, damaged, export-only, and non-normalizable offers are excluded.",
            "Offers outside +/-3 model years or the mileage tolerance are excluded.",
        ],
    }
    return benchmark


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
