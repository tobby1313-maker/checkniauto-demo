"""Deterministic filtering and public selection of comparable market ads."""

from __future__ import annotations

import json
import re
import statistics
import time
import unicodedata
from datetime import date, timedelta
from typing import Any, TypedDict
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

MARKET_SEARCH_PASS_SPECS: dict[str, dict[str, Any]] = {
    "sk_cz": {
        "label": "SK/CZ",
        "language": "sk/cs",
        "market_scope": "PUBLIC_SK_CZ",
        "hosts": (
            "bazos.sk",
            "bazos.cz",
            "autobazar.eu",
            "autobazar.sk",
            "sauto.cz",
            "tipcars.com",
        ),
    },
    "mobile_de": {
        "label": "Mobile.de",
        "language": "de",
        "market_scope": "BACKGROUND_EU",
        "hosts": ("mobile.de",),
    },
    "otomoto_pl": {
        "label": "Otomoto",
        "language": "pl",
        "market_scope": "BACKGROUND_EU",
        "hosts": ("otomoto.pl",),
    },
    "autoscout": {
        "label": "AutoScout24",
        "language": "market-local",
        "market_scope": "BACKGROUND_EU",
        "hosts": (
            "autoscout24.at",
            "autoscout24.be",
            "autoscout24.com",
            "autoscout24.de",
            "autoscout24.fr",
            "autoscout24.it",
        ),
    },
}

class _ToleranceStage(TypedDict):
    name: str
    year_delta: int
    mileage_floor: int
    mileage_ratio: float
    weight: float


_TOLERANCE_STAGES: tuple[_ToleranceStage, ...] = (
    {
        "name": "STRICT",
        "year_delta": 3,
        "mileage_floor": 40000,
        "mileage_ratio": 0.35,
        "weight": 1.0,
    },
    {
        "name": "EXPANDED_YEAR",
        "year_delta": 5,
        "mileage_floor": 40000,
        "mileage_ratio": 0.35,
        "weight": 0.75,
    },
    {
        "name": "EXPANDED_MILEAGE",
        "year_delta": 5,
        "mileage_floor": 80000,
        "mileage_ratio": 0.60,
        "weight": 0.5,
    },
)

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


def _citation_market_urls(
    web_research_text: str,
    *,
    direct_only: bool = True,
) -> list[str]:
    """Extract marketplace URLs only from grounding's authoritative citations."""
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
            if (
                url
                and _marketplace_host(url)
                and (not direct_only or _looks_like_direct_ad_url(url))
                and url not in urls
            ):
                urls.append(url)
    return urls


def _parse_grounded_market_json(value: str) -> dict[str, Any]:
    text = str(value or "")
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            parsed, _end = decoder.raw_decode(text[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("candidates"), list):
            return parsed
    return {}


def _url_matches_pass(url: str, pass_id: str) -> bool:
    spec = MARKET_SEARCH_PASS_SPECS.get(pass_id) or {}
    host = _url_host(url)
    return any(_host_matches(host, expected) for expected in spec.get("hosts", ()))


def extract_grounded_market_search_pass(
    grounded_text: str,
    pass_id: str,
) -> dict[str, Any]:
    """Create provenance-locked candidates from one portal-specific search pass.

    A detail URL is verified only by an exact canonical grounding citation. A
    foreign result-card observation may use a cited results page as background
    evidence, but it can never become a customer-facing link.
    """
    spec = MARKET_SEARCH_PASS_SPECS.get(pass_id)
    if not spec:
        raise ValueError(f"Unknown market search pass: {pass_id}")
    parsed = _parse_grounded_market_json(grounded_text)
    raw_candidates = parsed.get("candidates") if isinstance(parsed, dict) else []
    if not isinstance(raw_candidates, list):
        raw_candidates = []
    cited_urls = [
        url
        for url in _citation_market_urls(grounded_text, direct_only=False)
        if _url_matches_pass(url, pass_id)
    ]
    cited_set = set(cited_urls)
    cited_details = {url for url in cited_urls if _looks_like_direct_ad_url(url)}
    scope = str(spec["market_scope"])
    candidates: list[dict[str, Any]] = []
    unverified_count = 0
    for index, raw in enumerate(raw_candidates):
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        claimed_url = _canonical_url(
            item.get("detail_url") or item.get("source_url")
        )
        evidence_url = _canonical_url(item.get("evidence_url"))
        verified_detail = bool(
            claimed_url
            and claimed_url in cited_details
            and _url_matches_pass(claimed_url, pass_id)
        )
        verified_evidence = bool(
            evidence_url
            and evidence_url in cited_set
            and _url_matches_pass(evidence_url, pass_id)
        )
        if not evidence_url and verified_detail:
            evidence_url = claimed_url
            verified_evidence = True

        if verified_detail:
            verification_status = "VERIFIED_DETAIL"
            source_url = claimed_url
        elif claimed_url:
            verification_status = "URL_UNVERIFIED"
            source_url = ""
            unverified_count += 1
        elif verified_evidence and scope == "BACKGROUND_EU":
            verification_status = "VERIFIED_SEARCH_RESULT"
            source_url = ""
        elif verified_evidence:
            verification_status = "URL_UNVERIFIED"
            source_url = ""
            unverified_count += 1
        else:
            verification_status = "URL_UNVERIFIED"
            source_url = ""
            unverified_count += 1

        background_evidence_verified = bool(
            scope == "BACKGROUND_EU"
            and verified_evidence
            and verification_status in {
                "VERIFIED_DETAIL",
                "VERIFIED_SEARCH_RESULT",
                "URL_UNVERIFIED",
            }
        )
        item.update(
            {
                "candidate_id": f"{pass_id}-{index + 1}",
                "search_pass": pass_id,
                "search_language": spec["language"],
                "market_scope": scope,
                "source_url": source_url,
                "claimed_source_url": claimed_url,
                "evidence_url": evidence_url if verified_evidence else "",
                "verified_url": verified_detail,
                "url_verification_status": verification_status,
                "background_evidence_verified": background_evidence_verified,
                "display_in_report": bool(
                    scope == "PUBLIC_SK_CZ" and verified_detail
                ),
                "data_provenance": (
                    "GROUNDED_DETAIL"
                    if verified_detail
                    else "GROUNDED_SEARCH_RESULT"
                    if background_evidence_verified
                    else "UNVERIFIED_MODEL_OUTPUT"
                ),
            }
        )
        candidates.append(item)

    if candidates:
        status = "FOUND_WITH_UNVERIFIED_URLS" if unverified_count else "FOUND"
    elif cited_urls:
        status = "SEARCH_RESULTS_FOUND_NOT_STRUCTURED"
    else:
        status = "NOTHING_FOUND"
    return {
        "pass_id": pass_id,
        "portal": spec["label"],
        "language": spec["language"],
        "market_scope": scope,
        "status": status,
        "citation_count": len(cited_urls),
        "candidate_count": len(candidates),
        "verified_detail_count": sum(
            item.get("url_verification_status") == "VERIFIED_DETAIL"
            for item in candidates
        ),
        "verified_background_count": sum(
            item.get("background_evidence_verified") is True for item in candidates
        ),
        "url_unverified_count": unverified_count,
        "candidates": candidates,
    }


def build_market_search_results(pass_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Combine separate portal passes into one auditable search artifact."""
    passes = [item for item in pass_results if isinstance(item, dict)]
    candidates = [
        candidate
        for item in passes
        for candidate in item.get("candidates", [])
        if isinstance(candidate, dict)
    ]
    return {
        "schema_version": 1,
        "passes": passes,
        "candidates": candidates,
        "summary": {
            "pass_count": len(passes),
            "nothing_found_passes": sum(
                item.get("status") == "NOTHING_FOUND" for item in passes
            ),
            "search_results_found_count": sum(
                int(item.get("citation_count") or 0) for item in passes
            ),
            "candidate_count": len(candidates),
            "url_unverified_count": sum(
                int(item.get("url_unverified_count") or 0) for item in passes
            ),
            "verified_detail_count": sum(
                int(item.get("verified_detail_count") or 0) for item in passes
            ),
            "verified_background_count": sum(
                int(item.get("verified_background_count") or 0) for item in passes
            ),
        },
    }


def reconcile_market_comparable_urls(
    research: dict[str, Any],
    web_research_text: str,
) -> dict[str, Any]:
    """Require an exact canonical grounding citation for every detail URL.

    This is retained for callers using the legacy combined research response.
    It deliberately does not repair, substitute, or fuzzy-match URLs: a model
    URL that differs from the citation is preserved only as an unverified
    claim for diagnostics.
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
        if replacement:
            item["source_url"] = replacement
            item["verified_url"] = True
            item["url_verification_status"] = "VERIFIED_DETAIL"
        else:
            item["claimed_source_url"] = original_url
            item["source_url"] = ""
            item["verified_url"] = False
            item["display_in_report"] = False
            item["url_verification_status"] = "URL_UNVERIFIED"
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


def _build_market_benchmark_v1(
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
        audit_row: dict[str, Any] = {
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
    advertised = (
        _number(listing_facts.get("asking_price_gross_eur"))
        if isinstance(listing_facts, dict)
        else None
    )
    if advertised is None:
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


def _weighted_median(rows: list[dict[str, Any]]) -> int | None:
    weighted = sorted(
        (
            (int(item["normalized_price_eur"]), float(item.get("weight") or 0))
            for item in rows
            if item.get("normalized_price_eur") is not None
            and float(item.get("weight") or 0) > 0
        ),
        key=lambda item: item[0],
    )
    if not weighted:
        return None
    halfway = sum(weight for _price_value, weight in weighted) / 2
    cumulative = 0.0
    for price_value, weight in weighted:
        cumulative += weight
        if cumulative >= halfway:
            return price_value
    return weighted[-1][0]


def _minimum_tolerance_stage(
    listing_year: int,
    listing_mileage: int,
    item_year: int,
    item_mileage: int,
) -> _ToleranceStage | None:
    for stage in _TOLERANCE_STAGES:
        mileage_limit = max(
            int(stage["mileage_floor"]),
            int(listing_mileage * float(stage["mileage_ratio"])),
        )
        if (
            abs(item_year - listing_year) <= int(stage["year_delta"])
            and abs(item_mileage - listing_mileage) <= mileage_limit
        ):
            return stage
    return None


def _market_unavailable_summary(
    search_summary: dict[str, Any],
    diagnostic_counts: dict[str, int],
) -> str:
    if diagnostic_counts.get("url_unverified", 0):
        return "Boli nájdené ponuky, ale nepodarilo sa overiť ich detailné URL."
    if diagnostic_counts.get("year_rejected", 0) or diagnostic_counts.get(
        "mileage_rejected", 0
    ):
        return "Nájdené ponuky boli mimo nastavených tolerancií."
    if (
        int(search_summary.get("search_results_found_count") or 0) > 0
        or int(search_summary.get("candidate_count") or 0) > 0
    ):
        return "Automatickému vyhľadávaniu sa nepodarilo zostaviť overenú vzorku."
    return "Automatické vyhľadávanie nenašlo použiteľné porovnateľné ponuky."


def build_market_benchmark(
    research: dict[str, Any],
    listing_text: str,
    *,
    exchange_rates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a staged, provenance-aware weighted asking-price benchmark."""
    market = research.get("market_assessment")
    if not isinstance(market, dict):
        market = {}
        research["market_assessment"] = market
    items = research.get("market_comparables")
    if not isinstance(items, list):
        items = []
    search_summary = research.get("market_search_summary")
    if not isinstance(search_summary, dict):
        search_summary = {}

    rate_payload = exchange_rates if isinstance(exchange_rates, dict) else {}
    rates = rate_payload.get("rates_per_eur")
    rates = {**rates, "EUR": 1.0} if isinstance(rates, dict) else {"EUR": 1.0}
    rate_details = rate_payload.get("rate_details")
    if not isinstance(rate_details, dict):
        rate_details = {}

    listing_identity = _listing_identity(listing_text)
    listing_facts = research.get("listing_facts")
    if isinstance(listing_facts, dict):
        listing_year = _number(listing_facts.get("year")) or _number(
            listing_facts.get("advertised_year")
        )
        listing_mileage = _number(
            listing_facts.get("advertised_mileage_km")
        ) or _number(listing_facts.get("mileage_km")) or _number(
            listing_facts.get("mileage")
        )
    else:
        listing_year = None
        listing_mileage = None
    listing_year = listing_year or listing_identity.get("year")
    listing_mileage = listing_mileage or listing_identity.get("mileage_km")

    diagnostic_counts = {
        "nothing_found": int(search_summary.get("nothing_found_passes") or 0),
        "url_unverified": 0,
        "year_rejected": 0,
        "mileage_rejected": 0,
        "europe_background_only": 0,
        "full_comparable_accepted": 0,
        "insufficient_sample": 0,
    }
    eligible: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        item = raw
        tier = _similarity_tier(item)
        country = _market_country(item)
        explicit_scope = str(item.get("market_scope") or "").upper()
        scope = (
            explicit_scope
            if explicit_scope in {"PUBLIC_SK_CZ", "BACKGROUND_EU"}
            else "PUBLIC_SK_CZ"
            if is_customer_facing_market_comparable(item)
            else "BACKGROUND_EU"
        )
        public = scope == "PUBLIC_SK_CZ" and is_customer_facing_market_comparable(item)
        item["similarity_tier"] = tier
        item["market_scope"] = scope
        item["display_in_report"] = public
        if country and not item.get("source_country"):
            item["source_country"] = country

        normalized, currency, original_amount = _normalized_eur_price(item, rates)
        item["original_currency"] = currency
        item["original_price"] = original_amount
        item["normalized_price_eur"] = normalized
        rate_detail = rate_details.get(currency)
        if not isinstance(rate_detail, dict):
            rate_detail = {}
        item["normalization_method"] = (
            "ORIGINAL_EUR"
            if normalized is not None and currency == "EUR"
            else str(rate_detail.get("method") or "ECB_REFERENCE_RATE")
            if normalized is not None
            else "UNAVAILABLE"
        )

        audit_row: dict[str, Any] = {
            "candidate_id": str(item.get("candidate_id") or ""),
            "search_pass": str(item.get("search_pass") or ""),
            "source_url": str(item.get("source_url") or ""),
            "evidence_url": str(item.get("evidence_url") or ""),
            "source_country": country,
            "market_scope": scope,
            "similarity_tier": tier,
            "original_price": original_amount,
            "original_currency": currency,
            "normalized_price_eur": normalized,
            "url_verification_status": str(
                item.get("url_verification_status") or ""
            ),
        }
        if item.get("url_verification_status") == "URL_UNVERIFIED":
            diagnostic_counts["url_unverified"] += 1

        exclusion = _excluded_price_basis(item)
        verified_detail = bool(
            item.get("verified_url") is True
            and _looks_like_direct_ad_url(item.get("source_url"))
        )
        verified_background = bool(
            scope == "BACKGROUND_EU"
            and item.get("background_evidence_verified") is True
            and _canonical_url(item.get("evidence_url"))
        )
        if scope == "PUBLIC_SK_CZ" and not verified_detail:
            exclusion = exclusion or "URL_UNVERIFIED"
        elif scope == "BACKGROUND_EU" and not (
            verified_detail or verified_background
        ):
            exclusion = exclusion or "URL_UNVERIFIED"
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
        if normalized is None:
            exclusion = exclusion or "NO_EUR_NORMALIZATION"
        if exclusion:
            audit_row["exclusion_reason"] = exclusion
            rejected.append(audit_row)
            continue

        assert listing_year is not None
        assert listing_mileage is not None
        assert item_year is not None
        assert item_mileage is not None
        stage = _minimum_tolerance_stage(
            listing_year, listing_mileage, item_year, item_mileage
        )
        if stage is None:
            if abs(item_year - listing_year) > _TOLERANCE_STAGES[-1]["year_delta"]:
                audit_row["exclusion_reason"] = "YEAR_OUTSIDE_EXPANDED_BAND"
                diagnostic_counts["year_rejected"] += 1
            else:
                audit_row["exclusion_reason"] = "MILEAGE_OUTSIDE_EXPANDED_BAND"
                diagnostic_counts["mileage_rejected"] += 1
            rejected.append(audit_row)
            continue

        provenance_factor = (
            0.75 if item.get("data_provenance") == "GROUNDED_SEARCH_RESULT" else 1.0
        )
        tier_factor = 0.85 if tier == "B" else 1.0
        audit_row["tolerance_stage"] = stage["name"]
        audit_row["weight"] = round(
            float(stage["weight"]) * provenance_factor * tier_factor, 3
        )
        audit_row["acceptance_status"] = (
            "FULL_COMPARABLE"
            if scope == "PUBLIC_SK_CZ"
            else "EUROPE_BACKGROUND_ONLY"
        )
        eligible.append(audit_row)

    selected_stage: _ToleranceStage | None = None
    selected: list[dict[str, Any]] = []
    benchmark_scope = "EU_MIXED_BACKGROUND"
    stage_names: set[str] = set()
    for stage in _TOLERANCE_STAGES:
        stage_names.add(str(stage["name"]))
        stage_rows = [
            item for item in eligible if item.get("tolerance_stage") in stage_names
        ]
        local_rows = [
            item for item in stage_rows if item.get("market_scope") == "PUBLIC_SK_CZ"
        ]
        if len(local_rows) >= 3:
            selected_stage = stage
            selected = local_rows
            benchmark_scope = "SK_CZ"
            break
        if len(stage_rows) >= 3:
            selected_stage = stage
            selected = stage_rows
            break

    benchmark_available = len(selected) >= 3
    stage_name = str(selected_stage.get("name")) if selected_stage else "NONE"
    if not benchmark_available:
        diagnostic_counts["insufficient_sample"] = len(eligible)
    for item in selected:
        if item.get("acceptance_status") == "FULL_COMPARABLE":
            diagnostic_counts["full_comparable_accepted"] += 1
        else:
            diagnostic_counts["europe_background_only"] += 1

    median_eur = _weighted_median(selected) if benchmark_available else None
    local_median_eur = _weighted_median(
        [item for item in eligible if item.get("market_scope") == "PUBLIC_SK_CZ"]
    )
    foreign_median_eur = _weighted_median(
        [item for item in eligible if item.get("market_scope") == "BACKGROUND_EU"]
    )
    selected_prices = [int(item["normalized_price_eur"]) for item in selected]
    classification_threshold = (
        12
        if benchmark_scope == "SK_CZ" and stage_name == "STRICT"
        else 15
        if stage_name == "STRICT"
        else 18
        if stage_name == "EXPANDED_YEAR"
        else 22
    )
    advertised = (
        _number(listing_facts.get("asking_price_gross_eur"))
        if isinstance(listing_facts, dict)
        else None
    )
    if advertised is None:
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

    tier_a_count = sum(item.get("similarity_tier") == "A" for item in selected)
    confidence = (
        "HIGH"
        if benchmark_available
        and benchmark_scope == "SK_CZ"
        and stage_name == "STRICT"
        and len(selected) >= 5
        and tier_a_count >= 3
        else "MEDIUM"
        if benchmark_available
        else "LOW"
    )
    summary = (
        f"Cenová pozícia vychádza z váženého mediánu {len(selected)} porovnateľných ponúk."
        if benchmark_available
        else _market_unavailable_summary(search_summary, diagnostic_counts)
    )
    market.update(
        {
            "available": bool(items)
            or int(search_summary.get("search_results_found_count") or 0) > 0,
            "advertised_price_eur": advertised,
            "comparable_count": len(items),
            "public_comparable_count": sum(
                1 for item in items if is_customer_facing_market_comparable(item)
            ),
            "eur_priced_comparable_count": sum(
                item.get("normalized_price_eur") is not None
                for item in items
                if isinstance(item, dict)
            ),
            "benchmark_comparable_count": len(selected),
            "benchmark_available": benchmark_available,
            "benchmark_confidence": confidence,
            "benchmark_scope": benchmark_scope,
            "benchmark_tolerance_stage": stage_name,
            "observed_market_low_eur": min(selected_prices) if selected_prices else None,
            "observed_market_high_eur": max(selected_prices) if selected_prices else None,
            "observed_market_average_eur": median_eur,
            "benchmark_median_eur": median_eur,
            "local_market_median_eur": local_median_eur,
            "foreign_background_median_eur": foreign_median_eur,
            "price_delta_percent": delta_percent if benchmark_available else None,
            "price_view": price_view,
            "negotiation_anchor_eur": median_eur
            if price_view == "rather_expensive"
            else None,
            "summary": summary,
            "limitations": "Ide o ponukové, nie realizačné ceny; širšie a výsledkové porovnania majú nižšiu váhu.",
            "negotiation_reason": (
                f"Inzerovaná cena je najmenej o {classification_threshold} % nad váženým mediánom."
                if price_view == "rather_expensive"
                else ""
            ),
        }
    )
    return {
        "schema_version": 2,
        "method": "WEIGHTED_MEDIAN_OF_GROUNDED_TIER_A_B_ASKING_PRICES",
        "minimum_sample_size": 3,
        "available": benchmark_available,
        "confidence": confidence,
        "benchmark_scope": benchmark_scope,
        "tolerance_stage": stage_name,
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
        "accepted_comparables": selected,
        "eligible_comparables": eligible,
        "rejected_comparables": rejected,
        "diagnostic_counts": diagnostic_counts,
        "search_summary": search_summary,
        "limitations": [
            "Asking prices are not completed transaction prices.",
            "No deterministic adjustment is made for equipment, condition, import costs, or warranty.",
            "Tier C, net, auction, damaged, export-only, and non-normalizable offers are excluded.",
            "Tolerance expands only when fewer than three candidates survive: year first, then mileage.",
            "Expanded and search-card observations receive lower weights.",
        ],
    }


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


def _auditable_market_candidate(item: dict[str, Any]) -> bool:
    if item.get("verified_url") is True and _looks_like_direct_ad_url(
        item.get("source_url")
    ):
        return True
    # Only the backend pass extractor may set these provenance fields. Legacy
    # model output without a grounded evidence URL remains excluded.
    if item.get("background_evidence_verified") is True:
        return bool(_canonical_url(item.get("evidence_url")))
    return bool(
        item.get("search_pass")
        and item.get("url_verification_status") == "URL_UNVERIFIED"
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
        and _auditable_market_candidate(item)
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
    accepted_urls = {
        _canonical_url(item.get("source_url"))
        for item in unique
        if _canonical_url(item.get("source_url"))
    }
    accepted_urls.update(
        _canonical_url(item.get("evidence_url"))
        for item in unique
        if _canonical_url(item.get("evidence_url"))
    )
    sources = research.get("sources_used")
    if isinstance(sources, list):
        research["sources_used"] = [
            source
            for source in sources
            if not (
                isinstance(source, dict)
                and (
                    str(source.get("source_type") or "").upper() == "MARKET_COMPARABLE"
                    or "market comparable" in _fold(source.get("used_for"))
                )
                and _canonical_url(source.get("source_url")) not in accepted_urls
            )
        ]
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
