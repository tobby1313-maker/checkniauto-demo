"""Google Gemini text, vision, and grounded-search provider adapter."""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable, Iterator
from typing import Any

import requests

from token_tracker import (
    default_tracker,
    estimate_output_tokens,
    estimate_request_tokens,
    estimate_text_tokens,
)

from scrapper_demo.logging import safe_log
from .errors import ApiKeyError, GroundingTransientError, ModelOutputLimitError, RateLimitError


# Google Gemini API URL (model placeholder will be substituted)
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_INTERACTIONS_API_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
GEMINI_FLASH_MODEL = os.environ.get("GEMINI_FLASH_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
GEMINI_ADVANCED_FLASH_MODEL = os.environ.get("GEMINI_ADVANCED_FLASH_MODEL", "gemini-3.5-flash").strip() or "gemini-3.5-flash"
GEMINI_FLASH_LITE_MODEL = os.environ.get("GEMINI_FLASH_LITE_MODEL", "gemini-3.1-flash-lite").strip() or "gemini-3.1-flash-lite"
GEMINI_GROUNDING_MODEL = os.environ.get("GEMINI_GROUNDING_MODEL", GEMINI_FLASH_MODEL).strip() or GEMINI_FLASH_MODEL
GEMINI_TEXT_RESEARCH_MODEL = os.environ.get("GEMINI_TEXT_RESEARCH_MODEL", GEMINI_FLASH_MODEL).strip() or GEMINI_FLASH_MODEL
GEMINI_VISION_MODEL = os.environ.get("GEMINI_VISION_MODEL", GEMINI_FLASH_MODEL).strip() or GEMINI_FLASH_MODEL
GEMINI_FINAL_MODEL = os.environ.get("GEMINI_FINAL_MODEL", GEMINI_ADVANCED_FLASH_MODEL).strip() or GEMINI_ADVANCED_FLASH_MODEL
GEMINI_MODEL = GEMINI_FLASH_MODEL
GEMINI_API_URL = f"{GEMINI_API_BASE}/{GEMINI_MODEL}:streamGenerateContent"
GEMINI_FALLBACK_MODELS = [
    GEMINI_FLASH_MODEL,
    GEMINI_ADVANCED_FLASH_MODEL,
    GEMINI_FLASH_LITE_MODEL,
]
GEMINI_FINAL_FALLBACK_MODELS = [
    GEMINI_FINAL_MODEL,
    GEMINI_FLASH_MODEL,
    GEMINI_FLASH_LITE_MODEL,
]
GEMINI_GROUNDING_FALLBACK_MODELS = [
    GEMINI_FLASH_MODEL,
    GEMINI_ADVANCED_FLASH_MODEL,
    GEMINI_FLASH_LITE_MODEL,
]
LEGACY_GENERATION_SETTINGS = {
    "max_output_tokens": 65536,
    "temperature": 0.7,
}
QUALITY_GENERATION_SETTINGS = {
    # Structured phases need enough headroom to finish valid JSON. These are
    # still far below the legacy 65k cap and are bounded by compact prompts.
    "text_research": {"max_output_tokens": 12000, "temperature": 0.2},
    "vision": {"max_output_tokens": 8000, "temperature": 0.2},
    # This is a safety ceiling, not a target. Gemini counts hidden thinking and
    # visible report tokens together against it.
    "final_synthesis": {"max_output_tokens": 12000, "temperature": 0.5},
}
# Gemini 2.5 Flash enables thinking by default.  The text-research and vision
# phases are contract-bound JSON extraction steps, so hidden reasoning can
# consume the entire generation budget before any JSON is emitted.  Keep the
# final synthesis on the model's normal reasoning path to preserve report
# quality, but disable thinking for these two structured phases.
QUALITY_THINKING_CONFIG = {
    "text_research": {"thinkingBudget": 0},
    "vision": {"thinkingBudget": 0},
    # Gemini 2.5 fallback: keep a small reasoning allowance for report
    # composition. Gemini 3.x is handled with thinkingLevel in the helper.
    "final_synthesis": {"thinkingBudget": 1024},
}
GROUNDING_CONTEXT_MAX_CHARS = 6000
GROUNDING_REDIRECT_HOST = "vertexaisearch.cloud.google.com"
GROUNDING_RESOLVE_TIMEOUT = 6     # seconds per redirect resolution (HEAD request)
GROUNDING_RESOLVE_MAX_REDIRECTS = 5

MARKET_RESPONSE_FORMAT = {
    "type": "text",
    "mime_type": "application/json",
    "schema": {
        "type": "object",
        "properties": {
            "search_pass": {"type": "string"},
            "candidates": {
                "type": "array",
                "maxItems": 6,
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "year": {"type": ["integer", "null"]},
                        "mileage_km": {"type": ["integer", "null"]},
                        "engine": {"type": "string"},
                        "transmission": {"type": "string"},
                        "drivetrain": {"type": "string"},
                        "price_display": {"type": "string"},
                        "price_eur": {"type": ["number", "null"]},
                        "price_basis": {"type": "string"},
                        "source_country": {"type": "string"},
                        "market_scope": {"type": "string"},
                        "similarity_tier": {"type": "string"},
                        "material_difference": {"type": "string"},
                        "detail_url": {"type": "string"},
                        "evidence_url": {"type": "string"},
                        "url_kind": {"type": "string"},
                    },
                    "required": [
                        "description",
                        "year",
                        "mileage_km",
                        "engine",
                        "transmission",
                        "drivetrain",
                        "price_display",
                        "price_eur",
                        "price_basis",
                        "source_country",
                        "market_scope",
                        "similarity_tier",
                        "material_difference",
                        "detail_url",
                        "evidence_url",
                        "url_kind",
                    ],
                },
            },
        },
        "required": ["search_pass", "candidates"],
    },
}


def _emit_provider_diagnostics(
    callback: Callable[[dict[str, Any]], None] | None,
    event: dict[str, Any],
) -> None:
    if not callback:
        return
    try:
        callback(event)
    except Exception:
        # Diagnostics must never change provider behavior.
        pass


def _generation_settings(
    phase: str | None,
    *,
    max_output_tokens: int | None = None,
    temperature: float | None = None,
) -> dict[str, float | int]:
    """Return phase settings while retaining a runtime rollback profile."""
    profile = os.environ.get("DEMO_ANALYSIS_PROFILE", "quality_optimized").strip().lower()
    if profile == "legacy" or not phase:
        settings = dict(LEGACY_GENERATION_SETTINGS)
    else:
        settings = dict(QUALITY_GENERATION_SETTINGS.get(phase, LEGACY_GENERATION_SETTINGS))
    if max_output_tokens is not None:
        settings["max_output_tokens"] = max(256, int(max_output_tokens))
    if temperature is not None:
        settings["temperature"] = max(0.0, min(2.0, float(temperature)))
    return settings


def _thinking_config(phase: str | None, model: str | None = None) -> dict[str, int | str]:
    """Return phase/model-specific Gemini thinking controls."""
    profile = os.environ.get("DEMO_ANALYSIS_PROFILE", "quality_optimized").strip().lower()
    if profile == "legacy" or not phase:
        return {}
    model_name = str(model or "").strip().lower().split("/")[-1]
    if model_name.startswith("gemini-3"):
        # Gemini 3.x uses thinkingLevel; numeric thinkingBudget is only a
        # backwards-compatibility path and may be ignored in practice.
        if phase in {"text_research", "vision"}:
            return {"thinkingLevel": "minimal"}
        if phase == "final_synthesis":
            return {"thinkingLevel": "low"}
    return dict(QUALITY_THINKING_CONFIG.get(phase, {}))


def _is_retryable_gemini_model_error(status_code: int, error_text: str = "") -> bool:
    """Return True when another configured Gemini model should be tried."""
    if status_code == 503:
        return True
    if status_code == 404:
        lowered = (error_text or "").lower()
        return "model" in lowered or "not_found" in lowered or "no longer available" in lowered
    return False


def _is_gemini_rate_limit_error(status_code: int, error_text: str = "") -> bool:
    """Return True when Gemini reports a quota/rate limit that may be model-specific."""
    if status_code != 429:
        return False
    lowered = (error_text or "").lower()
    return "quota" in lowered or "rate limit" in lowered or "resource exhausted" in lowered


def _ordered_unique_models(primary_model: str, fallback_models: list[str]) -> list[str]:
    """Build a stable model chain without duplicate empty entries."""
    candidates = []
    for candidate in [primary_model, *(fallback_models or [])]:
        candidate = str(candidate or "").strip()
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates


def _resolve_annotation_redirects(research_text: str) -> str:
    """
    Post-process Gemini grounding citations that contain Google/Vertex redirect URLs.
    Follows the redirect chain with lightweight HEAD requests to find the real public URL.

    If resolution fails or the resolved URL is still a redirect host, the original
    unverifiable citation is left unchanged (preserving existing 'URL nie je overitelna' behavior).
    """
    if not research_text or GROUNDING_REDIRECT_HOST not in research_text:
        return research_text

    markdown_redirect_pattern = re.compile(
        r"\[([^\]]+)\]\((https://" + re.escape(GROUNDING_REDIRECT_HOST) + r"[^)\s]+)\)"
    )
    raw_redirect_pattern = re.compile(
        r"https://" + re.escape(GROUNDING_REDIRECT_HOST) + r"/[^\s\"'<>\)]+"
    )
    result = str(research_text)

    # A grounding annotation URL can occur both in the model's JSON
    # evidence_url and in the provider-appended Markdown citation. Resolve it
    # once and replace every occurrence so both values stay identical.
    for redirect_url in dict.fromkeys(raw_redirect_pattern.findall(research_text)):
        try:
            resp = requests.head(
                redirect_url,
                allow_redirects=True,
                timeout=GROUNDING_RESOLVE_TIMEOUT,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            if resp.status_code < 400:
                final_url = str(resp.url).rstrip("/")
                # Skip if still a Google/Vertex redirect or empty
                if (
                    not final_url
                    or GROUNDING_REDIRECT_HOST in final_url
                    or "google.com" in final_url.lower()
                    or "googleapis.com" in final_url.lower()
                    or "gstatic.com" in final_url.lower()
                ):
                    continue
                result = result.replace(redirect_url, final_url)
        except Exception:
            # If resolution fails (timeout, connection error, etc.), keep original
            continue
    # Never pass unresolved Google redirect URLs to later model/public stages.
    # Keep the source title for context, but mark the URL as unavailable.
    result = markdown_redirect_pattern.sub(
        lambda match: f"{match.group(1)} (URL citacia nie je overitelna)",
        result,
    )
    # A redirect may remain in a JSON string even when no Markdown citation
    # could be resolved. It is not usable evidence and must not flow further.
    result = raw_redirect_pattern.sub("", result)
    return result

def analyze_with_llm(
    api_key: str,
    system_prompt: str,
    user_content: str,
    image_data_list: list[tuple[str, str, str]] | None = None,
    model: str | None = None,
    listing_slug: str | None = None,
    allow_image_text_fallback: bool = True,
    phase: str | None = None,
    max_output_tokens: int | None = None,
    temperature: float | None = None,
) -> Iterator[str]:
    """
    Send a request to Google Gemini and yield response chunks.
    """
    if not api_key or not api_key.strip():
        raise ApiKeyError("API kluc nie je nastaveny. Pridaj ho v Nastaveniach.")

    yield from _call_gemini(
        api_key,
        system_prompt,
        user_content,
        image_data_list=image_data_list,
        model=model,
        listing_slug=listing_slug,
        allow_image_text_fallback=allow_image_text_fallback,
        phase=phase,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
    )


def _build_grounded_search_prompt(listing_context: str) -> str:
    context = listing_context
    if len(context) > GROUNDING_CONTEXT_MAX_CHARS:
        context = context[:GROUNDING_CONTEXT_MAX_CHARS] + "\n\n[Context truncated for web research pass.]"

    return f"""Si web research modul pre analyzu ojazdeneho auta.

Pouzi Google Search grounding. Toto je jediny zdroj modelovych a komponentovych
znalosti pre stateless verejne demo, preto pokry vsetky relevantne vrstvy vozidla,
nie iba dve alebo tri najznamejsie chyby. Vyhladavaj cielene, hlavne v SK/CZ/EU.

Postup researchu:
1. Pouzi dodany COMPONENT_IDENTITY ako vopred vyhladanu identifikaciu generacie,
   motora, prevodovky a pohonu. Over jeho aplikovatelnost, ale nezvysuj jeho
   resolution/confidence a nevynucuj presny kod, ked zostal PROBABLE, AMBIGUOUS
   alebo UNKNOWN.
2. Samostatne vyhladaj motor: spolahlivost, typicke poruchy, servisne intervaly,
   kilometrove/vekove spustace a prakticke kontroly pred kupou.
3. Samostatne vyhladaj prevodovku a AWD/4x4/hybrid/EV komponenty: kvapaliny,
   zname prejavy, diagnostiku, typicke spustace a nakladne scenare.
4. Samostatne vyhladaj generacne problemy karoserie a podvozka: korozia,
   napravy, loziska, brzdy, elektronika, zatekanie a ine opakujuce sa body.
5. Over zvolavacie a servisne kampane, prednostne cez oficialny web vyrobcu
   alebo regulatorny zdroj. Produkcne obdobie znamena iba kontrolu cez VIN,
   nie potvrdenu otvorenu akciu na konkretnom aute.
6. Ak je model a rok pokryty ADAC Pannenstatistik alebo TÜV Reportom, pouzi ich
   iba ako modelovy/vekovy kontext. ADAC nepovazuj za dokaz stavu motora alebo
   prevodovky a TÜV nepovazuj za dokaz vady konkretneho auta.
7. CarSurvey a specializovane fora pouzi iba na hladanie opakujucich sa
   symptomov presne identifikovanej kombinacie. Jasne ich oznac ako owner report
   alebo anekdoticky zdroj; nikdy nimi nepotvrdzuj vadu konkretneho vozidla.
8. Zisti orientacne SK/CZ/EU ceny servisu a opravy. Rozlis bezny vstupny servis,
   diagnostiku, podmienene opravy a drahy downside; podmienene opravy nikdy
   neprezentuj ako ocakavany sucet.
9. Ak je uvedeny VIN, urob aj lahku samostatnu kontrolu presneho VIN vo vyhladavani
   (hladaj cely retazec v uvodzovkach). Uved iba konkretnu relevantnu verejnu
   zmienku o aukcii, poistnej udalosti, servise alebo inom zazname, ak sa najde.
   Ak sa relevantny verejne indexovany zaznam nenajde, uved to raz neutralne ako
   vysledok tejto lahkej kontroly a odporuc manualne overenie oficialnej historie;
   absenciu vysledku nikdy neprezentuj ako riziko ani ako nejasnu historiu vozidla.

Pravidla:
- Nevymyslaj zdroje ani odkazy.
- Zdroj o inej generacii, inom motore, prevodovke alebo trhu moze byt nanajvys
  background. Nepouzivaj ho na interval, naklad ani typicku poruchu analyzovanej
  kombinacie. Napriklad 2.0 CRDi nepodporuje tvrdenie o 1.6 T-GDi a novsi MHEV
  Tucson nepodporuje starsiu generaciu TL.
- Servisny interval pre kvapaliny preber iba z manualu vyrobcu alebo technickeho
  dokumentu pre rovnaku generaciu, prevodovku, pohon a trh. Vseobecna servisna
  stranka smie vytvorit iba otazku pre servis, nie pevny interval ani ocakavany
  naklad. Nepouzivaj slovo Haldex pre Hyundai AWD, ak to nepotvrdzuje OEM zdroj.
- Zahranicny TSB alebo recall je iba modelovy bod na VIN kontrolu, ak dokument
  obmedzuje platnost podla trhu, vyrobneho zavodu alebo VIN prefixu. Neoznacuj ho
  ako otvorenu kampan konkretneho auta bez VIN vysledku.
- Ak nenajdes spolahlive zdroje, napis "Nenasiel som spolahlivy webovy zdroj".
- Neanalyzuj fotografie, tento pass je iba textovy web research.
- Daj prednost praktickym zisteniam pre kupujuceho.
- Vystup udrz pod 1100 slov, ale nevynechaj podporenu vrstvu motora, prevodovky,
  pohonu ani generacie len kvoli strucnosti.
- URL citacie: pouzi iba odkazy, ktore vyzeraju ako realne existujuce verejne stranky.
- Nepouzivaj presmerovacie URL z Google/Vertex AI ako verejne zdroje. Ak mas iba taky odkaz, uveď nazov zdroja a napis: "URL citacia nie je overitelna."
- Ak nevies dolozit cenu alebo naklad, napis, ze ide iba o orientacny odhad.

Format:

## Webove overenie cez Google Search

### Identifikacia komponentov
- generacia/platforma; motor/kod alebo rodina; prevodovka/kod alebo rodina; pohon; istota a zdroj

### Zdroje
- [nazov](url) - co zdroj potvrdzuje

### Motor
- problem alebo servisny bod - dopad - typicky km/vek/spustac - kontrola - odhad EUR - URL

### Prevodovka a pohon
- problem alebo servisny bod - dopad - typicky km/vek/spustac - kontrola - odhad EUR - URL

### Generacia, karoseria a podvozok
- problem alebo servisny bod - dopad - typicky km/vek/spustac - kontrola - odhad EUR - URL

### Zvolavacie a servisne kampane
- kampan alebo jasne uved, ze spolahlivy zdroj nebol najdeny - potrebna VIN kontrola - URL

### Naklady: pravdepodobne vs. podmienene
- pravdepodobny vstupny servis a diagnostika
- podmienene opravy iba ak kontrola potvrdi problem; nesucitavaj ich do ocakavaneho totalu

### VIN / historia / transparentnost
- lahke dekodovanie z VIN_LIGHT_CHECK; potom presny VIN search a iba konkretne verejne zistenie s URL
- ak sa nic relevantne nenajde: jedna neutralna veta o vysledku tejto kontroly a odporucanie na manualne overenie

### Najdolezitejsie webove zistenia pre finalnu analyzu
- 5 az 8 bodov napriec motorom, prevodovkou/pohonom, generaciou a nakladmi

Kontext inzeratu:

{context}
"""


def _build_grounded_component_identity_prompt(listing_context: str) -> str:
    """Build the short grounded pass that resolves generation and components."""
    context = listing_context
    if len(context) > GROUNDING_CONTEXT_MAX_CHARS:
        context = context[:GROUNDING_CONTEXT_MAX_CHARS] + "\n\n[Context truncated.]"
    return f"""Si identifikacny modul pre analyzu ojazdeneho auta.

Pouzi Google Search a cielene urci generaciu/platformu, motor, prevodovku a
pohon vozidla z inzeratu. Vyhladaj kombinaciu znacky, modelu, roku/mesiaca,
paliva, vykonu v kW, poctu stupnov, typu prevodovky a pohonu. Preferuj:
1. oficialne katalogy/brozury vyrobcu a regulatorne zdroje,
2. OEM alebo doveryhodne katalogy dielov a dielenske prirucky,
3. renomovane technicke publikacie a specialistov.

Pravidla istoty:
- VERIFIED pouzi iba ked presny kod doklada VIN build sheet, dokument/stitok
  konkretneho vozidla, OEM zaznam viazany na VIN alebo rovnocenny priamy dokaz.
- PROBABLE pouzi pri jedinej dobre podporenej kombinacii rok + vykon + palivo +
  prevodovka + pohon, ked chyba priamy dokaz konkretneho kusu.
- AMBIGUOUS pouzi, ked zostavaju aspon dva realne varianty.
- UNKNOWN pouzi, ked zdroje nestacia.
- Marketingovy nazov ako 7DCT, DSG, Steptronic alebo automat nie je automaticky
  vyrobny kod prevodovky. Hodnoty 7DCT, DCT, DSG, automat, HTRAC, AWD a 4x4
  nikdy nevkladaj do pola `code`; patria do marketing_name, family alebo type.
- Ak zdroj uvadza viac kodov, napriklad D7UF1/D7GF1, pole `code` nechaj prazdne
  a oba vloz do candidate_variants. Nevyberaj jeden bez priameho dokazu.
- Presny kod prevodovky musi byt explicitne kompatibilny aj s uvedenym pohonom.
  Zdroj pre 2WD/FWD variant nesmie potvrdit prevodovku pre AWD/4x4 a naopak.
- Kazdy neprazdny presny kod motora alebo prevodovky musi mat v `evidence_refs`
  aspon jeden source_id zdroja, ktory priamo podporuje presnu kombinaciu motora,
  prevodovky a pohonu. Bez takeho zdroja kod presun do candidate_variants a
  pole `code` nechaj prazdne.
- HIGH confidence pre presny kod pouzi iba pri oficialnom/OEM katalogu,
  dielenskej prirucke alebo rovnocennom technickom dokumente s presnou
  kombinaciou. Recenzie, bazarove katalogy a Scribd samy o sebe nestacia.
- VIN prefix alebo modelovy rok sam o sebe nepotvrdzuje presny motor ani prevodovku.
- Nevymyslaj kod. Pri neistote vrat kandidatov a sposob manualneho overenia.
- verification_basis musi byt VIN_RECORD, VEHICLE_DOCUMENT alebo PHYSICAL_LABEL
  iba pri priamom dokaze konkretneho auta. Bez neho pouzi SPECIFICATION_MATCH,
  MULTIPLE_CANDIDATES alebo INSUFFICIENT.
- Nevytvaraj hodnotenie kupy, poruchovost ani cenu. Toto je iba identita.
- Do source_url kopiruj iba realny verejny URL; Google/Vertex redirect vynechaj.

Vrat jeden kratky JSON objekt bez Markdownu. Pocty: sources najviac 6,
candidate_variants najviac 4, notes najviac 4. Pouzi presne tuto strukturu:
{{
  "schema_version": 1,
  "identification_status": "VERIFIED|PROBABLE|AMBIGUOUS|UNKNOWN",
  "generation": {{"name":"", "code":"", "family":"", "resolution":"VERIFIED|PROBABLE|AMBIGUOUS|UNKNOWN", "confidence":"HIGH|MEDIUM|LOW", "verification_basis":"VIN_RECORD|VEHICLE_DOCUMENT|PHYSICAL_LABEL|SPECIFICATION_MATCH|MULTIPLE_CANDIDATES|INSUFFICIENT", "evidence_refs":[]}},
  "engine": {{"marketing_name":"", "code":"", "family":"", "resolution":"VERIFIED|PROBABLE|AMBIGUOUS|UNKNOWN", "confidence":"HIGH|MEDIUM|LOW", "verification_basis":"VIN_RECORD|VEHICLE_DOCUMENT|PHYSICAL_LABEL|SPECIFICATION_MATCH|MULTIPLE_CANDIDATES|INSUFFICIENT", "evidence_refs":[]}},
  "transmission": {{"marketing_name":"", "code":"", "family":"", "resolution":"VERIFIED|PROBABLE|AMBIGUOUS|UNKNOWN", "confidence":"HIGH|MEDIUM|LOW", "verification_basis":"VIN_RECORD|VEHICLE_DOCUMENT|PHYSICAL_LABEL|SPECIFICATION_MATCH|MULTIPLE_CANDIDATES|INSUFFICIENT", "evidence_refs":[]}},
  "drivetrain": {{"type":"", "code":"", "family":"", "resolution":"VERIFIED|PROBABLE|AMBIGUOUS|UNKNOWN", "confidence":"HIGH|MEDIUM|LOW", "verification_basis":"VIN_RECORD|VEHICLE_DOCUMENT|PHYSICAL_LABEL|SPECIFICATION_MATCH|MULTIPLE_CANDIDATES|INSUFFICIENT", "evidence_refs":[]}},
  "candidate_variants": [{{"engine_code":"", "transmission_code":"", "reason":""}}],
  "sources": [{{"source_id":"src_1", "source_name":"", "source_url":"", "source_type":"OFFICIAL|REGULATORY|OEM_CATALOG|TECHNICAL_PUBLICATION|PARTS_CATALOG|REPAIR_SOURCE|OWNER_REPORT|OTHER", "used_for":""}}],
  "notes": []
}}

Kontext inzeratu:

{context}
"""


def _build_grounded_market_prompt(
    listing_context: str,
    market_pass: str = "sk_cz",
) -> str:
    """Build one portal- and language-specific comparable-search prompt."""
    context = listing_context
    if len(context) > GROUNDING_CONTEXT_MAX_CHARS:
        context = context[:GROUNDING_CONTEXT_MAX_CHARS] + "\n\n[Context truncated for market research pass.]"
    config = {
        "sk_cz": (
            "SK_CZ",
            "slovencine a cestine",
            "auto.bazos.sk, autobazar.eu, autobazar.sk, auto.bazos.cz, sauto.cz a tipcars.com",
            "PUBLIC_SK_CZ",
        ),
        "mobile_de": (
            "MOBILE_DE",
            "nemcine s terminmi Benzin, Automatik, Allrad/4x4 a Leistung",
            "iba mobile.de",
            "BACKGROUND_EU",
        ),
        "otomoto_pl": (
            "OTOMOTO_PL",
            "polstine s terminmi benzyna, automatyczna a naped 4x4",
            "iba otomoto.pl",
            "BACKGROUND_EU",
        ),
        "autoscout": (
            "AUTOSCOUT",
            "jazyku relevantneho trhu; zacni DE/AT a potom skus iny blizky trh",
            "iba AutoScout24 na relevantnej narodnej domene",
            "BACKGROUND_EU",
        ),
    }.get(market_pass)
    if config is None:
        raise ValueError(f"Unknown market search pass: {market_pass}")
    pass_label, query_language, portals, scope = config
    return f"""Si market-research modul pre analyzu ojazdeneho auta.

Toto je samostatny search pass {pass_label}. Pouzi Google Search grounding,
hladaj v {query_language} a pouzi {portals}. Ine portaly nevracaj. Povodny
analyzovany inzerat nepouzivaj ako porovnanie.

Z kontextu vytiahni znacku, model, generaciu, motor, vykon, prevodovku, pohon,
rok a najazd. Vykonaj viac roznych site-specific dopytov iba pre tento portal.
Pouzi lokalne aliasy motora, prevodovky a pohonu; po jednom prazdnom dopyte sa
nevzdavaj.

Podobnost:
- A: rovnaka generacia, motor, prevodovka a pohon; rocnik +/-2 roky.
- B: rovnaka generacia, motor a prevodovka; pohon alebo vykon sa moze lisit.
- C: rovnaka generacia, podobny rocnik, palivo a prevodovka.
Najazd moze byt odlisny. Vrat aj polozky mimo povodneho rozsahu; backend ich
deterministicky zaradi do strict/expanded-year/expanded-mileage alebo vyradi.
Najprv vsak hladaj ponuky s najazdom co najblizsim cielovemu vozidlu. Ak je
cielovy najazd vysoky, nezapln vystup iba novymi alebo nizko najazdenymi autami.

Pravidla proveniencie:
- URL nikdy nevytvaraj ani nedoplnaj podla sablony.
- `detail_url` skopiruj iba ak je presne totozny s realnou grounding citaciou
  detailu konkretneho inzeratu.
- `evidence_url` musi byt presna grounding citacia z tohto passu.
- Pre scope {scope} moze byt vysledkova alebo kategoriova stranka iba v
  `evidence_url`, nie ako overeny detail URL.
- Pri zahranicnom passe vrat aj search kartu bez detail URL, ak karta priamo
  obsahuje cenu, rok, najazd a relevantnu konfiguraciu. Pouzi
  `url_kind=RESULTS_PAGE`; backend ju pouzije iba v skrytom EU benchmarku.
- Ak vidis detail URL v texte, ale nie je medzi citaciami, zachovaj ju a nastav
  `url_kind=UNVERIFIED`. Backend ju nezverejni a zaznamena URL ako neoverenu.
- Nevymyslaj cenu, rok, najazd, parametre ani URL. Zachovaj povodnu menu.
- Vrat najviac 6 ponuk. Vyluc salvage, aukcie, export-only a netto-only ceny.

Vrat vzdy jeden kompletny JSON objekt bez uvodu, ospravedlnenia alebo Markdown
komentara, aj keby `candidates` zostalo prazdne:
{{
  "search_pass": "{market_pass}",
  "candidates": [
    {{
      "description": "",
      "year": null,
      "mileage_km": null,
      "engine": "",
      "transmission": "",
      "drivetrain": "",
      "price_display": "",
      "price_eur": null,
      "price_basis": "gross_asking | net | auction | damaged | export_only | unknown",
      "source_country": "",
      "market_scope": "{scope}",
      "similarity_tier": "A | B | C",
      "material_difference": "",
      "detail_url": "",
      "evidence_url": "",
      "url_kind": "DETAIL | RESULTS_PAGE | UNVERIFIED"
    }}
  ]
}}
Google Search citacie musia zostat zachovane poskytovatelom za JSON objektom.

Kontext analyzovaneho vozidla:

{context}
"""


def _extract_interaction_text_and_citations(data: dict[str, Any]) -> str:
    """Extract model text and URL citations from an Interactions API response."""
    text_blocks: list[str] = []
    seen_texts: set[str] = set()
    citations: list[str] = []
    seen_urls: set[str] = set()

    def add_text(value: Any) -> None:
        if not isinstance(value, str) or not value.strip():
            return
        text = value.strip()
        if text in seen_texts:
            return
        seen_texts.add(text)
        text_blocks.append(text)

    def add_annotations(annotations: Any) -> None:
        if not isinstance(annotations, list):
            return
        for annotation in annotations:
            if not isinstance(annotation, dict):
                continue
            url = annotation.get("url")
            if not url:
                continue
            title = _clean_citation_label(
                annotation.get("title")
                or annotation.get("name")
                or annotation.get("source")
                or url
            )
            if GROUNDING_REDIRECT_HOST in url:
                # Keep the redirect temporarily. The resolver runs immediately
                # after extraction and replaces it with the destination URL.
                if url not in seen_urls:
                    seen_urls.add(url)
                    citations.append(f"- [{title}]({url})")
                continue
            if url in seen_urls:
                continue
            seen_urls.add(url)
            citations.append(f"- [{title}]({url})")

    add_text(data.get("output_text") or data.get("outputText") or data.get("text"))

    for step in data.get("steps", []):
        if not isinstance(step, dict):
            continue
        step_type = step.get("type") or step.get("stepType") or ""
        if step_type not in {"model_output", "modelOutput"}:
            continue
        content_blocks = step.get("content") or step.get("contents") or []
        if isinstance(content_blocks, dict):
            content_blocks = [content_blocks]
        for block in content_blocks:
            if isinstance(block, str):
                add_text(block)
                continue
            if not isinstance(block, dict):
                continue
            add_text(block.get("text"))
            add_annotations(block.get("annotations"))

    result = "\n\n".join(text_blocks).strip()
    if citations:
        result = f"{result}\n\n### Citacie z Google Search\n" + "\n".join(citations)
    return result.strip()


def _clean_citation_label(value: str) -> str:
    label = re.sub(r"\s+", " ", str(value or "")).strip()
    if not label or GROUNDING_REDIRECT_HOST in label:
        return "Zdroj z Google Search"
    return label[:120]


def run_grounded_web_research(
    api_key: str,
    listing_context: str,
    model: str | None = None,
    listing_slug: str | None = None,
    research_mode: str = "full",
) -> str:
    """Run a text-only Gemini Interactions API pass with Google Search grounding."""
    if not api_key or not api_key.strip():
        raise ApiKeyError("API kluc nie je nastaveny. Pridaj ho v Nastaveniach.")

    model_to_use = model if model else GEMINI_GROUNDING_MODEL
    model_candidates = _ordered_unique_models(model_to_use, GEMINI_GROUNDING_FALLBACK_MODELS)

    normalized_mode = str(research_mode or "full").strip().lower()
    market_only = normalized_mode == "market" or normalized_mode.startswith("market_")
    identity_only = normalized_mode == "identity"
    if market_only:
        market_pass = (
            "sk_cz"
            if normalized_mode == "market"
            else normalized_mode.removeprefix("market_")
        )
        prompt = _build_grounded_market_prompt(listing_context, market_pass)
    elif identity_only:
        prompt = _build_grounded_component_identity_prompt(listing_context)
    else:
        prompt = _build_grounded_search_prompt(listing_context)
    tracking_phase = (
        f"market_grounding_{market_pass}"
        if market_only
        else "component_identity_grounding"
        if identity_only
        else "grounding"
    )
    last_error_text = ""
    unavailable_models = []
    rate_limited_models = []

    for candidate_model in model_candidates:
        started_at = time.perf_counter()
        input_tokens = estimate_text_tokens(prompt)
        payload = {
            "model": candidate_model,
            "input": prompt,
            "tools": [{"type": "google_search"}],
            "store": False,
        }
        if market_only:
            payload["response_format"] = MARKET_RESPONSE_FORMAT

        try:
            response = requests.post(
                GEMINI_INTERACTIONS_API_URL,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": api_key.strip(),
                },
                timeout=120,
            )
        except requests.exceptions.Timeout:
            default_tracker.record_request(
                model=candidate_model,
                request_type="grounded_search",
                phase=tracking_phase,
                listing_slug=listing_slug,
                input_tokens=input_tokens,
                output_tokens=0,
                status="timeout",
                duration_ms=round((time.perf_counter() - started_at) * 1000),
                error="Google Search grounding timeout.",
            )
            raise GroundingTransientError("Google Search grounding casovy limit (120s).")
        except requests.exceptions.ConnectionError:
            default_tracker.record_request(
                model=candidate_model,
                request_type="grounded_search",
                phase=tracking_phase,
                listing_slug=listing_slug,
                input_tokens=input_tokens,
                output_tokens=0,
                status="connection_error",
                duration_ms=round((time.perf_counter() - started_at) * 1000),
                error="Google Search grounding connection error.",
            )
            raise GroundingTransientError("Nie je pripojenie k internetu pre Google Search grounding.")

        last_error_text = response.text[:600] if response.text else ""
        safe_log(
            f"DEBUG: Gemini grounding model {candidate_model} status {response.status_code}, "
            f"response preview: {last_error_text[:200]}"
        )

        if _is_retryable_gemini_model_error(response.status_code, last_error_text):
            unavailable_models.append(candidate_model)
            if candidate_model != model_candidates[-1]:
                time.sleep(1)
                continue

        if response.status_code == 429:
            detail = last_error_text[:300].replace("\n", " ").strip()
            rate_limited_models.append(candidate_model)
            default_tracker.record_request(
                model=candidate_model,
                request_type="grounded_search",
                phase=tracking_phase,
                listing_slug=listing_slug,
                input_tokens=input_tokens,
                output_tokens=0,
                status="rate_limited",
                duration_ms=round((time.perf_counter() - started_at) * 1000),
                error=detail,
            )
            # Let the outer API-key fallback handle quota exhaustion. Trying
            # two more models with the same key multiplied paid input calls
            # and did not help when the key/project quota was exhausted.
            raise RateLimitError(
                "Gemini Google Search grounding limit prekroceny."
                + (f" Detail: {detail}" if detail else "")
            )

        if response.status_code in {401, 403}:
            default_tracker.record_request(
                model=candidate_model,
                request_type="grounded_search",
                phase=tracking_phase,
                listing_slug=listing_slug,
                input_tokens=input_tokens,
                output_tokens=0,
                status="auth_error",
                duration_ms=round((time.perf_counter() - started_at) * 1000),
                error=last_error_text[:200],
            )
            raise ApiKeyError(
                f"Google Search grounding odmietol API kluc (HTTP {response.status_code}). "
                f"Gemini odpoved: {last_error_text[:200]}"
            )

        if response.status_code != 200:
            default_tracker.record_request(
                model=candidate_model,
                request_type="grounded_search",
                phase=tracking_phase,
                listing_slug=listing_slug,
                input_tokens=input_tokens,
                output_tokens=0,
                status=f"http_{response.status_code}",
                duration_ms=round((time.perf_counter() - started_at) * 1000),
                error=last_error_text[:300],
            )
            if _is_retryable_gemini_model_error(response.status_code, last_error_text) or 500 <= response.status_code < 600:
                unavailable_models.append(candidate_model)
                if candidate_model != model_candidates[-1]:
                    time.sleep(1)
                    continue
            break

        try:
            data = response.json()
        except json.JSONDecodeError:
            raise GroundingTransientError("Google Search grounding vratil necitatelnu JSON odpoved.")

        if "error" in data:
            error_info = data["error"]
            message = error_info.get("message", str(error_info)) if isinstance(error_info, dict) else str(error_info)
            raise ConnectionError(f"Gemini Google Search grounding chyba: {message}")

        research_text = _extract_interaction_text_and_citations(data)
        # Resolve Google/Vertex redirect URLs to real public URLs
        research_text = _resolve_annotation_redirects(research_text)
        output_text = research_text or "Google Search grounding prebehol, ale nevratil pouzitelny text."
        default_tracker.record_request(
            model=candidate_model,
            request_type="grounded_search",
            phase=tracking_phase,
            listing_slug=listing_slug,
            input_tokens=input_tokens,
            output_tokens=estimate_output_tokens(output_text),
            status="success",
            duration_ms=round((time.perf_counter() - started_at) * 1000),
        )
        if research_text:
            return research_text
        return "Google Search grounding prebehol, ale nevratil pouzitelny text."

    if unavailable_models and len(unavailable_models) == len(model_candidates):
        raise GroundingTransientError(
            "Gemini Google Search grounding je momentalne pretazeny. "
            f"Skusene modely: {', '.join(unavailable_models)}."
        )

    if rate_limited_models and len(rate_limited_models) == len(model_candidates):
        raise RateLimitError(
            "Gemini Google Search grounding limit prekroceny pre vsetky skusene modely. "
            f"Skusene modely: {', '.join(rate_limited_models)}."
        )

    raise GroundingTransientError(
        f"Google Search grounding chyba API: {last_error_text[:300]}"
    )


def _call_gemini(
    api_key: str,
    system_prompt: str,
    user_content: str,
    image_data_list: list[tuple[str, str, str]] | None = None,
    model: str | None = None,
    listing_slug: str | None = None,
    allow_image_text_fallback: bool = True,
    fallback_models: list[str] | None = None,
    phase: str | None = None,
    max_output_tokens: int | None = None,
    temperature: float | None = None,
    diagnostics_callback: Callable[[dict[str, Any]], None] | None = None,
) -> Iterator[str]:
    """Call Google Gemini API with proper system_instruction support.
    
    Args:
        api_key: Gemini API key
        system_prompt: System instruction
        user_content: User content to analyze
        image_data_list: Optional list of (filename, base64_data, mime_type) tuples for images
        model: Optional model name override (e.g., "gemini-3.5-flash")
        allow_image_text_fallback: Retry image quota failures as text-only output.
        fallback_models: Optional ordered fallback chain after the primary model.
    """
    # Use provided model or fall back to default. If Gemini is overloaded,
    # try lighter models before surfacing the temporary provider failure.
    model_to_use = model if model else GEMINI_MODEL
    model_candidates = _ordered_unique_models(model_to_use, fallback_models or GEMINI_FALLBACK_MODELS)

    # Build content parts - start with text
    content_parts: list[dict[str, Any]] = [{"text": user_content}]
    
    # Add images if provided (multimodal input)
    if image_data_list:
        for img_filename, img_base64, mime_type in image_data_list:
            content_parts.append({
                "inline_data": {
                    "mime_type": mime_type,
                    "data": img_base64
                }
            })

    generation_settings = _generation_settings(
        phase,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
    )
    generation_config: dict[str, Any] = {
        "temperature": generation_settings["temperature"],
        "topP": 0.95,
        "topK": 40,
        "maxOutputTokens": generation_settings["max_output_tokens"],
    }
    if phase in {"text_research", "vision"}:
        generation_config["responseMimeType"] = "application/json"
    payload: dict[str, Any] = {
        "system_instruction": {
            "parts": [{"text": system_prompt}]
        },
        "contents": [
            {
                "role": "user",
                "parts": content_parts
            }
        ],
        "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ],
        "generationConfig": generation_config,
    }

    try:
        response = None
        unavailable_errors = []
        rate_limited_errors = []
        error_text = ""
        started_at = time.perf_counter()
        request_model = model_to_use
        input_tokens = estimate_request_tokens(system_prompt, user_content, image_data_list)
        for candidate_model in model_candidates:
            request_model = candidate_model
            started_at = time.perf_counter()
            url = f"{GEMINI_API_BASE}/{candidate_model}:streamGenerateContent?key={api_key.strip()}&alt=sse"
            candidate_generation_config = dict(generation_config)
            thinking_config = _thinking_config(phase, candidate_model)
            if thinking_config:
                candidate_generation_config["thinkingConfig"] = thinking_config
            candidate_payload = dict(payload)
            candidate_payload["generationConfig"] = candidate_generation_config
            response = requests.post(
                url,
                json=candidate_payload,
                headers={"Content-Type": "application/json"},
                stream=True,
                timeout=120,
            )

            # Do not read response.text for successful streamed responses:
            # it consumes the stream and delays all UI output until completion.
            if response.status_code == 200:
                error_text = ""
            else:
                error_text = response.text[:500] if response.text else ""

            safe_log(
                f"DEBUG: Gemini API model {candidate_model} status {response.status_code}, "
                f"response preview: {error_text[:200] if error_text else '(streaming response)'}"
            )
            if response.status_code != 200:
                _emit_provider_diagnostics(
                    diagnostics_callback,
                    {
                        "model": candidate_model,
                        "status": f"http_{response.status_code}",
                        "http_status": response.status_code,
                    },
                )

            if not (
                _is_retryable_gemini_model_error(response.status_code, error_text)
                or _is_gemini_rate_limit_error(response.status_code, error_text)
            ):
                break

            if _is_gemini_rate_limit_error(response.status_code, error_text):
                rate_limited_errors.append((candidate_model, error_text))
            else:
                unavailable_errors.append((candidate_model, error_text))
            if candidate_model != model_candidates[-1]:
                time.sleep(1)

        if response is not None and _is_retryable_gemini_model_error(response.status_code, error_text):
            tried_models = ", ".join(model for model, _ in unavailable_errors)
            status_label = "unavailable" if response.status_code == 503 else "model_not_found"
            default_tracker.record_request(
                model=request_model,
                request_type="stream_generate_content",
                phase=phase,
                listing_slug=listing_slug,
                input_tokens=input_tokens,
                output_tokens=0,
                status=status_label,
                duration_ms=round((time.perf_counter() - started_at) * 1000),
                error=error_text[:300],
            )
            if response.status_code == 404:
                raise ConnectionError(
                    "Google Gemini model nie je dostupny alebo bol vyradeny. "
                    f"Skusene modely: {tried_models}. "
                    f"Gemini odpoved: {error_text[:200]}"
                )
            raise RateLimitError(
                "⚠️ Gemini je momentálne preťažený (HTTP 503: high demand). "
                f"Skúsil som tieto modely: {tried_models}. "
                "Skus znova o par minut alebo pouzi zalozny Gemini kluc."
            )

        if response is not None and _is_gemini_rate_limit_error(response.status_code, error_text):
            if image_data_list and allow_image_text_fallback:
                default_tracker.record_request(
                    model=request_model,
                    request_type="stream_generate_content",
                    phase=phase,
                    listing_slug=listing_slug,
                    input_tokens=input_tokens,
                    output_tokens=0,
                    status="rate_limited_retrying_text_only",
                    duration_ms=round((time.perf_counter() - started_at) * 1000),
                    error=error_text[:300],
                )
                safe_log("Gemini quota/rate limit hit with images after model fallback; retrying text-only.")
                yield (
                    "\n\n⚠️ **Gemini narazil na limit pri spracovaní fotografií. "
                    "Pokračujem textovou analýzou bez fotiek.**\n\n"
                )
                text_only_content = (
                    f"{user_content}\n\n"
                    "Poznámka pre analýzu: Gemini API odmietlo požiadavku s fotografiami "
                    "kvôli quota/rate limitu, preto vyhodnoť najmä textové údaje z inzerátu."
                )
                yield from _call_gemini(
                    api_key,
                    system_prompt,
                    text_only_content,
                    image_data_list=None,
                    model=model_to_use,
                    listing_slug=listing_slug,
                    allow_image_text_fallback=allow_image_text_fallback,
                    fallback_models=fallback_models,
                    phase=phase,
                    max_output_tokens=max_output_tokens,
                    temperature=temperature,
                    diagnostics_callback=diagnostics_callback,
                )
                return

            tried_models = ", ".join(model for model, _ in rate_limited_errors)
            detail = error_text[:300].replace("\n", " ").strip()
            default_tracker.record_request(
                model=request_model,
                request_type="stream_generate_content",
                phase=phase,
                listing_slug=listing_slug,
                input_tokens=input_tokens,
                output_tokens=0,
                status="rate_limited",
                duration_ms=round((time.perf_counter() - started_at) * 1000),
                error=detail,
            )
            raise RateLimitError(
                "Gemini API limit prekroceny pre vsetky skusene modely. "
                f"Skusene modely: {tried_models}. "
                "Skus znova o par minut alebo pouzi zalozny Gemini kluc."
                + (f"\n\nDetail Gemini: {detail}" if detail else "")
            )

        if response is None:
            raise ConnectionError("Google Gemini did not return an HTTP response.")

        if response.status_code == 429:
            # Check if it's actually a quota/rate limit or something else
            if "quota" in error_text.lower() or "rate limit" in error_text.lower():
                if image_data_list and allow_image_text_fallback:
                    default_tracker.record_request(
                        model=request_model,
                        request_type="stream_generate_content",
                        phase=phase,
                        listing_slug=listing_slug,
                        input_tokens=input_tokens,
                        output_tokens=0,
                        status="rate_limited_retrying_text_only",
                        duration_ms=round((time.perf_counter() - started_at) * 1000),
                        error=error_text[:300],
                    )
                    safe_log("Gemini quota/rate limit hit with images; retrying text-only.")
                    yield (
                        "\n\n⚠️ **Gemini narazil na limit pri spracovaní fotografií. "
                        "Pokračujem textovou analýzou bez fotiek.**\n\n"
                    )
                    text_only_content = (
                        f"{user_content}\n\n"
                        "Poznámka pre analýzu: Gemini API odmietlo požiadavku s fotografiami "
                        "kvôli quota/rate limitu, preto vyhodnoť najmä textové údaje z inzerátu."
                    )
                    yield from _call_gemini(
                        api_key,
                        system_prompt,
                        text_only_content,
                        image_data_list=None,
                        model=model_to_use,
                        listing_slug=listing_slug,
                        allow_image_text_fallback=allow_image_text_fallback,
                        fallback_models=fallback_models,
                        phase=phase,
                        max_output_tokens=max_output_tokens,
                        temperature=temperature,
                        diagnostics_callback=diagnostics_callback,
                    )
                    return

                detail = error_text[:300].replace("\n", " ").strip()
                default_tracker.record_request(
                    model=request_model,
                    request_type="stream_generate_content",
                    phase=phase,
                    listing_slug=listing_slug,
                    input_tokens=input_tokens,
                    output_tokens=0,
                    status="rate_limited",
                    duration_ms=round((time.perf_counter() - started_at) * 1000),
                    error=detail,
                )
                raise RateLimitError(
                    "⚠️ Gemini API limit prekročený. Môže ísť o per-minute, per-model, "
                    "tokenový alebo denný quota limit, nie nutne o počet požiadaviek. "
                    "Skus znova o par minut alebo pouzi zalozny Gemini kluc."
                    + (f"\n\nDetail Gemini: {detail}" if detail else "")
                )
            else:
                # 429 might actually be an auth error
                default_tracker.record_request(
                    model=request_model,
                    request_type="stream_generate_content",
                    phase=phase,
                    listing_slug=listing_slug,
                    input_tokens=input_tokens,
                    output_tokens=0,
                    status="auth_error",
                    duration_ms=round((time.perf_counter() - started_at) * 1000),
                    error=error_text[:300],
                )
                raise ApiKeyError(
                    f"❌ Prístup zamietnutý (HTTP 429). API kľúč je pravdepodobne neplatný alebo expirovaný.\n\n"
                    f"Gemini odpoveď: {error_text[:200]}\n\n"
                    f"Skontroluj:\n"
                    f"  ? API kluc je spravny a patri ku Gemini API\n"
                    f"  • Kľúč nie je expirovaný\n"
                    f"  • Máš prístup k Gemini API na https://aistudio.google.com/"
                )

        if response.status_code == 401 or response.status_code == 403:
            default_tracker.record_request(
                model=request_model,
                request_type="stream_generate_content",
                phase=phase,
                listing_slug=listing_slug,
                input_tokens=input_tokens,
                output_tokens=0,
                status="auth_error",
                duration_ms=round((time.perf_counter() - started_at) * 1000),
                error=error_text[:300],
            )
            raise ApiKeyError(
                f"❌ Google Gemini API kľúč je neplatný (HTTP {response.status_code}).\n\n"
                f"Gemini odpoveď: {error_text[:200]}\n\n"
                "Vytvor nový kľúč na https://aistudio.google.com/"
            )

        if response.status_code != 200:
            default_tracker.record_request(
                model=request_model,
                request_type="stream_generate_content",
                phase=phase,
                listing_slug=listing_slug,
                input_tokens=input_tokens,
                output_tokens=0,
                status=f"http_{response.status_code}",
                duration_ms=round((time.perf_counter() - started_at) * 1000),
                error=error_text[:300],
            )
            raise ConnectionError(
                f"❌ Google Gemini chyba API ({response.status_code}): {error_text}"
            )

        # Force UTF-8 encoding to properly handle emojis and diacritics
        response.encoding = 'utf-8'
        
        # Parse SSE stream
        full_text = ""
        actual_prompt_tokens = None
        actual_output_tokens = None
        actual_thinking_tokens = None
        actual_total_tokens = None
        finish_reason_seen = ""
        for line in response.iter_lines():
            if isinstance(line, bytes):
                line = line.decode('utf-8')
            if not line or not line.startswith("data: "):
                continue

            data_str = line[6:]  # Remove "data: " prefix
            if data_str == "[DONE]":
                break

            try:
                data = json.loads(data_str)
                usage_metadata = data.get("usageMetadata") or data.get("usage_metadata") or {}
                if usage_metadata:
                    actual_prompt_tokens = (
                        usage_metadata.get("promptTokenCount")
                        or usage_metadata.get("prompt_token_count")
                        or actual_prompt_tokens
                    )
                    actual_output_tokens = (
                        usage_metadata.get("candidatesTokenCount")
                        or usage_metadata.get("candidates_token_count")
                        or actual_output_tokens
                    )
                    actual_thinking_tokens = (
                        usage_metadata.get("thoughtsTokenCount")
                        or usage_metadata.get("thoughts_token_count")
                        or actual_thinking_tokens
                    )
                    actual_total_tokens = (
                        usage_metadata.get("totalTokenCount")
                        or usage_metadata.get("total_token_count")
                        or actual_total_tokens
                    )
                
                # Check for errors in the response
                if "error" in data:
                    error_info = data["error"]
                    error_msg = error_info.get("message", str(error_info))
                    raise ConnectionError(f"❌ Gemini API error: {error_msg}")
                
                candidates = data.get("candidates", [])
                if candidates:
                    content = candidates[0].get("content", {})
                    parts = content.get("parts", [])
                    for part in parts:
                        text = part.get("text", "")
                        if text:
                            full_text += text
                            yield text

                # Check for finish reason
                finish_reason = candidates[0].get("finishReason", "") if candidates else ""
                if finish_reason and finish_reason != "STOP":
                    finish_reason_seen = finish_reason

            except json.JSONDecodeError:
                continue

        default_tracker.record_request(
            model=request_model,
            request_type="stream_generate_content",
            phase=phase,
            listing_slug=listing_slug,
            input_tokens=input_tokens,
            output_tokens=estimate_output_tokens(full_text),
            actual_input_tokens=actual_prompt_tokens,
            actual_output_tokens=actual_output_tokens,
            actual_thinking_tokens=actual_thinking_tokens,
            actual_total_tokens=actual_total_tokens,
            status="truncated" if finish_reason_seen else "success",
            duration_ms=round((time.perf_counter() - started_at) * 1000),
        )
        _emit_provider_diagnostics(
            diagnostics_callback,
            {
                "model": request_model,
                "status": "truncated" if finish_reason_seen else "success",
                "http_status": response.status_code,
                "finish_reason": finish_reason_seen or "STOP",
                "actual_input_tokens": actual_prompt_tokens,
                "actual_output_tokens": actual_output_tokens,
                "actual_thinking_tokens": actual_thinking_tokens,
                "actual_total_tokens": actual_total_tokens,
                "output_chars": len(full_text),
                # The caller may retain this only in a protected debugging
                # artifact. It is never written to telemetry or public output.
                "output": full_text,
            },
        )
        if finish_reason_seen:
            raise ModelOutputLimitError(
                f"Gemini {phase or 'analysis'} stopped before completing its output "
                f"(finish reason: {finish_reason_seen})."
            )

    except requests.exceptions.Timeout:
        default_tracker.record_request(
            model=locals().get("request_model", model if model else GEMINI_MODEL),
            request_type="stream_generate_content",
            phase=phase,
            listing_slug=listing_slug,
            input_tokens=locals().get("input_tokens", estimate_request_tokens(system_prompt, user_content, image_data_list)),
            output_tokens=0,
            status="timeout",
            duration_ms=round((time.perf_counter() - locals().get("started_at", time.perf_counter())) * 1000),
            error="Gemini API timeout.",
        )
        raise ConnectionError(
            "❌ Google Gemini API časový limit (120s). Skús znova alebo skontroluj internet."
        )
    except requests.exceptions.ConnectionError:
        default_tracker.record_request(
            model=locals().get("request_model", model if model else GEMINI_MODEL),
            request_type="stream_generate_content",
            phase=phase,
            listing_slug=listing_slug,
            input_tokens=locals().get("input_tokens", estimate_request_tokens(system_prompt, user_content, image_data_list)),
            output_tokens=0,
            status="connection_error",
            duration_ms=round((time.perf_counter() - locals().get("started_at", time.perf_counter())) * 1000),
            error="Gemini API connection error.",
        )
        raise ConnectionError(
            "❌ Nie je pripojenie k internetu. Skontroluj sieť pre Google API."
        )


analyze = analyze_with_llm
stream_generate = _call_gemini
grounded_research = run_grounded_web_research
