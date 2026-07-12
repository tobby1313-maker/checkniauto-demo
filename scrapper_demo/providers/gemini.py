"""Google Gemini text, vision, and grounded-search provider adapter."""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Iterator

import requests

from token_tracker import (
    default_tracker,
    estimate_output_tokens,
    estimate_request_tokens,
    estimate_text_tokens,
)

from scrapper_demo.logging import safe_log
from .errors import ApiKeyError, GroundingTransientError, RateLimitError


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
    "text_research": {"max_output_tokens": 8000, "temperature": 0.2},
    "vision": {"max_output_tokens": 3500, "temperature": 0.2},
    "final_synthesis": {"max_output_tokens": 7000, "temperature": 0.5},
}
GROUNDING_CONTEXT_MAX_CHARS = 6000
GROUNDING_REDIRECT_HOST = "vertexaisearch.cloud.google.com"
GROUNDING_RESOLVE_TIMEOUT = 6     # seconds per redirect resolution (HEAD request)
GROUNDING_RESOLVE_MAX_REDIRECTS = 5


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

    redirect_pattern = re.compile(
        r"\[([^\]]+)\]\((https://" + re.escape(GROUNDING_REDIRECT_HOST) + r"[^)\s]+)\)"
    )
    seen_final = set()
    result = str(research_text)

    for label, redirect_url in redirect_pattern.findall(research_text):
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
                if final_url in seen_final:
                    continue
                seen_final.add(final_url)
                # Replace the redirect URL with the resolved public URL
                result = result.replace(
                    f"({redirect_url})",
                    f"({final_url})",
                    1,
                )
        except Exception:
            # If resolution fails (timeout, connection error, etc.), keep original
            continue

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
1. Najprv identifikuj najpravdepodobnejsiu generaciu/platformu, kod alebo rodinu
   motora, kod alebo rodinu prevodovky a typ pohonu. Ak vstup obsahuje rozpor,
   uved kandidata a neistotu; nevynucuj presny kod bez opory v zdroji.
2. Samostatne vyhladaj motor: spolahlivost, typicke poruchy, servisne intervaly,
   kilometrove/vekove spustace a prakticke kontroly pred kupou.
3. Samostatne vyhladaj prevodovku a AWD/4x4/hybrid/EV komponenty: kvapaliny,
   zname prejavy, diagnostiku, typicke spustace a nakladne scenare.
4. Samostatne vyhladaj generacne problemy karoserie a podvozka: korozia,
   napravy, loziska, brzdy, elektronika, zatekanie a ine opakujuce sa body.
5. Over zvolavacie a servisne kampane, prednostne cez oficialny web vyrobcu
   alebo regulatorny zdroj. Produkcne obdobie znamena iba kontrolu cez VIN,
   nie potvrdenu otvorenu akciu na konkretnom aute.
6. Zisti orientacne SK/CZ/EU ceny servisu a opravy. Rozlis bezny vstupny servis,
   diagnostiku, podmienene opravy a drahy downside; podmienene opravy nikdy
   neprezentuj ako ocakavany sucet.
7. Najdi 3-5 co najblizsich aktualnych porovnatelnych ponuk: rovnaka generacia,
   motor, pohon, prevodovka, podobny rok a najazd. Pri kazdej uved materialny
   rozdiel; ak su porovnania slabe, povedz to.
8. Ak je uvedeny VIN, urob aj lahku samostatnu kontrolu presneho VIN vo vyhladavani
   (hladaj cely retazec v uvodzovkach). Uved iba konkretnu relevantnu verejnu
   zmienku o aukcii, poistnej udalosti, servise alebo inom zazname, ak sa najde.
   Ak sa relevantny verejne indexovany zaznam nenajde, uved to raz neutralne ako
   vysledok tejto lahkej kontroly a odporuc manualne overenie oficialnej historie;
   absenciu vysledku nikdy neprezentuj ako riziko ani ako nejasnu historiu vozidla.

Pravidla:
- Nevymyslaj zdroje ani odkazy.
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

### Orientacna cena / trh
- 3-5 najblizsich ponuk s cenou, najazdom, podstatnym rozdielom a URL; potom obmedzenia porovnania

### Naklady: pravdepodobne vs. podmienene
- pravdepodobny vstupny servis a diagnostika
- podmienene opravy iba ak kontrola potvrdi problem; nesucitavaj ich do ocakavaneho totalu

### VIN / historia / transparentnost
- lahke dekodovanie z VIN_LIGHT_CHECK; potom presny VIN search a iba konkretne verejne zistenie s URL
- ak sa nic relevantne nenajde: jedna neutralna veta o vysledku tejto kontroly a odporucanie na manualne overenie

### Najdolezitejsie webove zistenia pre finalnu analyzu
- 5 az 8 bodov napriec motorom, prevodovkou/pohonom, generaciou, trhom a nakladmi

Kontext inzeratu:

{context}
"""


def _extract_interaction_text_and_citations(data: dict) -> str:
    """Extract model text and URL citations from an Interactions API response."""
    text_blocks = []
    seen_texts = set()
    citations = []
    seen_urls = set()

    def add_text(value):
        if not isinstance(value, str) or not value.strip():
            return
        text = value.strip()
        if text in seen_texts:
            return
        seen_texts.add(text)
        text_blocks.append(text)

    def add_annotations(annotations):
        if not isinstance(annotations, list):
            return
        for annotation in annotations:
            if not isinstance(annotation, dict):
                continue
            url = annotation.get("url")
            if not url:
                continue
            title = _clean_citation_label(annotation.get("title") or annotation.get("source") or url)
            if GROUNDING_REDIRECT_HOST in url:
                if title and title not in seen_urls:
                    seen_urls.add(title)
                    citations.append(f"- {title} (URL citacia nie je overitelna)")
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
) -> str:
    """Run a text-only Gemini Interactions API pass with Google Search grounding."""
    if not api_key or not api_key.strip():
        raise ApiKeyError("API kluc nie je nastaveny. Pridaj ho v Nastaveniach.")

    model_to_use = model if model else GEMINI_GROUNDING_MODEL
    model_candidates = _ordered_unique_models(model_to_use, GEMINI_GROUNDING_FALLBACK_MODELS)

    prompt = _build_grounded_search_prompt(listing_context)
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
                phase="grounding",
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
                phase="grounding",
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
                phase="grounding",
                listing_slug=listing_slug,
                input_tokens=input_tokens,
                output_tokens=0,
                status="rate_limited_retrying" if candidate_model != model_candidates[-1] else "rate_limited",
                duration_ms=round((time.perf_counter() - started_at) * 1000),
                error=detail,
            )
            if candidate_model != model_candidates[-1]:
                time.sleep(1)
                continue
            raise RateLimitError(
                "Gemini Google Search grounding limit prekroceny."
                + (f" Detail: {detail}" if detail else "")
            )

        if response.status_code in {401, 403}:
            default_tracker.record_request(
                model=candidate_model,
                request_type="grounded_search",
                phase="grounding",
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
                phase="grounding",
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
            phase="grounding",
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
    content_parts = [{"text": user_content}]
    
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
    payload = {
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
        "generationConfig": {
            "temperature": generation_settings["temperature"],
            "topP": 0.95,
            "topK": 40,
            "maxOutputTokens": generation_settings["max_output_tokens"],
        }
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
            response = requests.post(
                url,
                json=payload,
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
                    if finish_reason == "SAFETY":
                        yield (
                            "\n\n⚠️ **Analýza bola zastavená bezpečnostným filtrom Gemini.** "
                            "Skús upraviť prompt alebo použiť iný model."
                        )
                    elif finish_reason == "MAX_TOKENS":
                        yield (
                            "\n\n⚠️ **Analýza dosiahla limit tokenov.** "
                            "Výstup je neúplný. Skús zjednodušiť prompt."
                        )

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
            status="success",
            duration_ms=round((time.perf_counter() - started_at) * 1000),
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
