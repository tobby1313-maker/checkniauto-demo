from __future__ import annotations

import json
import statistics
from typing import Any
from urllib.parse import urlparse

from v2_ai_client import call_generate_content_json, call_interaction_json
from v2_config import (
    TEXT_FALLBACK_MODELS,
    TEXT_MODEL,
    VISION_FALLBACK_MODELS,
    VISION_MODEL,
    _model_candidates,
    _unique,
)
from v2_normalize import _number
from v2_schemas import FINAL_SCHEMA, PHOTO_SCHEMA, RESEARCH_SCHEMA


def analyze_photos(
    listing: dict[str, Any],
    images: list[dict[str, Any]],
    language: str,
) -> dict[str, Any]:
    if not images:
        return {
            "available": False,
            "images_reviewed": 0,
            "summary": "Fotografie neboli dostupné na analýzu.",
            "findings": [],
            "positive_signals": [],
            "coverage_gaps": ["Nie je možné vizuálne preveriť karosériu, interiér ani pneumatiky."],
            "limitations": ["Bez fotografií nemožno posúdiť viditeľné poškodenia ani opotrebenie."],
        }

    language_name = "češtine" if language == "cs" else "slovenčine"
    image_map = "\n".join(
        f"- {item['label']} = pôvodný súbor {item['original_name']}" for item in images
    )
    prompt = f"""
Si opatrný automobilový vizuálny inšpektor. Odpovedaj v {language_name}.
Posúď iba to, čo je na priložených fotografiách reálne viditeľné. Nevyhlasuj haváriu,
koróziu, stočený nájazd ani technickú poruchu ako fakt, ak fotografia poskytuje iba náznak.
Odlišuj pozorovanie od interpretácie a pri neistote zníž confidence.

Vozidlo: {listing.get('title')}
Rok: {listing.get('year') or 'neuvedený'}
Najazdené km: {listing.get('mileage_km') or 'neuvedené'}

Mapovanie fotografií:
{image_map}

Skontroluj najmä rozdiely odtieňov laku, medzery panelov, poškodenia, hrdzu,
pneumatiky a disky, svetlá, opotrebenie volantu/sedadiel/pedálov, kontrolky,
vlhkosť, motorový priestor a chýbajúce zábery. Uveď najviac 8 významných zistení.
Odhad nákladov daj iba pri rozumne identifikovateľnej veci; inak použi 0.
""".strip()
    result = call_generate_content_json(
        prompt,
        PHOTO_SCHEMA,
        images,
        _model_candidates(VISION_MODEL, VISION_FALLBACK_MODELS),
    )
    result["available"] = True
    result["images_reviewed"] = len(images)
    result["findings"] = _list_of_dicts(result.get("findings"))[:8]
    return result


def research_vehicle(listing: dict[str, Any], language: str) -> dict[str, Any]:
    language_name = "češtine" if language == "cs" else "slovenčine"
    public_context = {
        key: value
        for key, value in listing.items()
        if key not in {"raw_listing", "data_quality"}
    }
    prompt = f"""
Si web research modul pre kupujúceho ojazdeného auta na Slovensku alebo v Česku.
Odpovedaj v {language_name}. Použi Google Search cielene a skromne.

Úlohy:
1. Identifikuj čo najpresnejšie generáciu, motor a prevodovku iba z dostupných údajov.
2. Nájdi modelovo špecifické technické riziká. Každé webové tvrdenie musí mať URL.
3. Pokús sa nájsť aktuálne porovnateľné inzeráty v SK/CZ/EÚ s podobným rokom,
   motorom, prevodovkou a nájazdom. Nevymýšľaj porovnateľné autá ani ceny.
4. Ak je VIN uvedené, vyhľadaj iba verejné zmienky. Nenahrádzaj platený VIN report.
5. Ak zdroje nestačia, nastav status limited alebo unavailable a hodnoty na 0.
6. Uprednostni dôveryhodné servisné, výrobné a inzertné zdroje. Blog bez dôkazov nestačí.
7. Vráť najviac 6 technických rizík a najviac 5 porovnateľných ponúk.

Dáta inzerátu:
{json.dumps(public_context, ensure_ascii=False, indent=2)}
""".strip()
    result, citations = call_interaction_json(
        prompt,
        RESEARCH_SCHEMA,
        _model_candidates(TEXT_MODEL, TEXT_FALLBACK_MODELS),
        use_search=True,
    )
    citation_map = {
        citation["url"]: citation["title"]
        for citation in citations
        if _safe_http_url(citation.get("url"))
    }
    declared_support = {
        str(item.get("url") or ""): str(item.get("supports") or "")
        for item in _list_of_dicts(result.get("sources"))
    }
    result["sources"] = [
        {
            "title": title or url,
            "url": url,
            "supports": declared_support.get(url) or "Zdroj použitý vo webovom overení.",
        }
        for url, title in list(citation_map.items())[:12]
    ]

    verified_risks = []
    for risk in _list_of_dicts(result.get("known_risks"))[:6]:
        risk["source_urls"] = [
            url for url in _list_of_strings(risk.get("source_urls")) if url in citation_map
        ]
        if not risk["source_urls"]:
            risk["confidence"] = "low"
        verified_risks.append(risk)
    result["known_risks"] = verified_risks

    market = result.get("market") if isinstance(result.get("market"), dict) else {}
    listing_currency = str(listing.get("price", {}).get("currency") or "EUR")
    verified_comparables = []
    for comparable in _list_of_dicts(market.get("comparables")):
        url = str(comparable.get("url") or "")
        price = _clamp_amount(comparable.get("price"))
        currency = str(comparable.get("currency") or listing_currency).upper()
        if url not in citation_map or price <= 0 or currency != listing_currency:
            continue
        comparable["price"] = price
        comparable["currency"] = currency
        comparable["year"] = _clamp_amount(comparable.get("year"), 1950, 2100)
        comparable["mileage_km"] = _clamp_amount(comparable.get("mileage_km"), 0, 2_000_000)
        verified_comparables.append(comparable)
        if len(verified_comparables) >= 5:
            break

    prices = [item["price"] for item in verified_comparables]
    listing_price = _clamp_amount(listing.get("price", {}).get("amount"))
    market["comparables"] = verified_comparables
    market["comparable_count"] = len(verified_comparables)
    market["currency"] = listing_currency
    if len(prices) >= 3:
        median = int(statistics.median(prices))
        market.update(
            {
                "status": "supported",
                "range_min": min(prices),
                "range_max": max(prices),
                "median": median,
                "recommended_max": median,
                "position": (
                    "unknown"
                    if listing_price <= 0
                    else "low"
                    if listing_price < median * 0.9
                    else "high"
                    if listing_price > median * 1.1
                    else "fair"
                ),
            }
        )
    elif prices:
        market.update(
            {
                "status": "limited",
                "range_min": min(prices),
                "range_max": max(prices),
                "median": int(statistics.median(prices)),
                "recommended_max": 0,
                "position": "unknown",
            }
        )
    else:
        market.update(
            {
                "status": "unavailable",
                "range_min": 0,
                "range_max": 0,
                "median": 0,
                "recommended_max": 0,
                "comparable_count": 0,
                "comparables": [],
                "position": "unknown",
            }
        )
    result["market"] = market
    if citation_map and (verified_risks or verified_comparables):
        result["status"] = "supported" if len(prices) >= 3 else "limited"
    else:
        result["status"] = "unavailable"
    return result


def synthesize_report(
    listing: dict[str, Any],
    photo: dict[str, Any],
    research: dict[str, Any],
    language: str,
) -> dict[str, Any]:
    language_name = "češtine" if language == "cs" else "slovenčine"
    prompt_payload = {
        "listing": {key: value for key, value in listing.items() if key != "raw_listing"},
        "photo_analysis": photo,
        "web_research": research,
    }
    prompt = f"""
Si senior analytik ojazdených áut. Z pripravených modulov vytvor praktický spotrebiteľský
report v {language_name}. Výsledok má kupujúcemu odpovedať, či má zmysel kontaktovať
predajcu alebo cestovať na obhliadku, čo preveriť a aký finančný vankúš potrebuje.

Pravidlá:
- Nevymýšľaj históriu vozidla, nehody, servis, ceny ani URL.
- Chýbajúci údaj je zistenie o transparentnosti, nie dôkaz problému auta.
- Dôležité tvrdenia previaž s listing/photo/web/manual_check evidence_type.
- Z fotografie rozlišuj viditeľné pozorovanie a neistú interpretáciu.
- Ak market.status nie je supported, price_assessment evidence_quality nesmie byť high.
- safety_score: 100 = nízke zistené riziko, 0 = veľmi vysoké riziko. Nezamieňaj ho s istotou.
- confidence musí zohľadniť úplnosť vstupu. Pri chýbajúcom VIN a slabých dátach ju zníž.
- Náklady sú orientačné rozsahy v EUR; pri neistote použi 0 a vysvetli ju.
- Vyber 3 až 8 najhodnotnejších zistení, nie generický zoznam všetkého.
- Otázky pre predajcu musia byť konkrétne a pripravené na skopírovanie.
- Checklist má mať 3 až 5 skupín a krátke vykonateľné položky.

Vstupné moduly:
{json.dumps(prompt_payload, ensure_ascii=False, indent=2)[:90_000]}
""".strip()
    report, _citations = call_interaction_json(
        prompt,
        FINAL_SCHEMA,
        _model_candidates(TEXT_MODEL, TEXT_FALLBACK_MODELS),
        use_search=False,
    )
    return report


def _safe_http_url(value: Any) -> bool:
    try:
        parsed = urlparse(str(value or ""))
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _clamp_amount(value: Any, minimum: int = 0, maximum: int = 100_000_000) -> int:
    return max(minimum, min(maximum, _number(value)))


def unavailable_research(reason: str) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "identified_variant": "",
        "variant_confidence": "low",
        "known_risks": [],
        "market": {
            "status": "unavailable",
            "summary": "Nepodarilo sa získať dostatočné aktuálne trhové podklady.",
            "position": "unknown",
            "currency": "",
            "range_min": 0,
            "range_max": 0,
            "median": 0,
            "recommended_max": 0,
            "comparable_count": 0,
            "comparables": [],
        },
        "vin_public_mentions": {
            "status": "not_checked",
            "summary": "Verejné zmienky o VIN neboli overené.",
            "source_urls": [],
        },
        "sources": [],
        "limitations": [reason],
    }


def unavailable_photo(reason: str, images_reviewed: int = 0) -> dict[str, Any]:
    return {
        "available": False,
        "images_reviewed": images_reviewed,
        "summary": "Fotografická analýza nebola dokončená.",
        "findings": [],
        "positive_signals": [],
        "coverage_gaps": [],
        "limitations": [reason],
    }


def _list_of_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _clamp(value: Any, minimum: int = 0, maximum: int = 100) -> int:
    return max(minimum, min(maximum, _number(value)))
