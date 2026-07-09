"""

LLM Client for In-App AI Analysis.
Supports Google Gemini.

Usage:
    from llm_client import analyze_with_llm
    for chunk in analyze_with_llm(api_key, system_prompt, user_content):
        print(chunk, end="")
"""

import json
import os
import requests
import re
import sys
import time

from token_tracker import default_tracker, estimate_output_tokens, estimate_request_tokens, estimate_text_tokens


def _configure_console_encoding():
    """Prefer UTF-8 console output, but never fail app startup over logging."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def safe_log(message):
    """Log text without crashing on Windows charmap consoles."""
    text = str(message)
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        safe_text = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
        print(safe_text)


_configure_console_encoding()

# Google Gemini API URL (model placeholder will be substituted)
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_INTERACTIONS_API_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
GEMINI_FLASH_MODEL = "gemini-2.5-flash"
GEMINI_FLASH_LITE_MODEL = "gemini-2.5-flash-lite"
GEMINI_GROUNDING_MODEL = GEMINI_FLASH_LITE_MODEL
GEMINI_MODEL = GEMINI_FLASH_MODEL  # Better reasoning, vision support, search grounding support
GEMINI_API_URL = f"{GEMINI_API_BASE}/{GEMINI_MODEL}:streamGenerateContent"
GEMINI_FALLBACK_MODELS = [
    GEMINI_FLASH_LITE_MODEL,
    GEMINI_FLASH_MODEL,
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
]
GEMINI_GROUNDING_FALLBACK_MODELS = [
    GEMINI_FLASH_MODEL,
]
GROUNDING_CONTEXT_MAX_CHARS = 6000
GROUNDING_REDIRECT_HOST = "vertexaisearch.cloud.google.com"

# xAI Grok API configuration
GROK_API_BASE = "https://api.x.ai/v1"
GROK_MODEL = "grok-2"  # or grok-2-latest, grok-3
GROK_API_URL = f"{GROK_API_BASE}/chat/completions"
GROK_FALLBACK_MODELS = [
    "grok-2-latest",
    "grok-2",
]

# OpenRouter uses one API key for all models. The selected model is controlled
# by the request payload's "model" field.
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "qwen/qwen3-next-80b-a3b-instruct:free"
OPENROUTER_FALLBACK_MODELS = [
    "google/gemma-4-26b-a4b-it:free",
    "openai/gpt-oss-20b:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "openrouter/free",
]


class GroundingTransientError(ConnectionError):
    """Raised for retryable Gemini Google Search grounding failures."""
    pass



def analyze_with_llm(api_key: str, system_prompt: str, user_content: str, image_data_list: list = None, model: str = None, listing_slug: str = None):
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
    )


def _build_grounded_search_prompt(listing_context: str) -> str:
    context = listing_context
    if len(context) > GROUNDING_CONTEXT_MAX_CHARS:
        context = context[:GROUNDING_CONTEXT_MAX_CHARS] + "\n\n[Context truncated for web research pass.]"

    return f"""Si web research modul pre analyzu ojazdeneho auta.

Pouzi Google Search grounding. Vyhladavaj cielene a skromne, hlavne SK/CZ/EU zdroje.

Uloha:
1. Over typicke problemy modelu, generacie, motora, prevodovky, AWD/4x4 alebo hybrid/EV systemu.
2. Zisti prakticke servisne dopady: co kontrolovat, kedy sa problem typicky objavuje a orientacny rozsah opravy v EUR, ak ho zdroj alebo bezna servisna logika podporuje.
3. Najdi aktualne trhove informacie k podobnym autam v SK/CZ/EU, ak sa daju najst: rozpatie cien, pocet/typ porovnatelnych ponuk, a ci inzerovana cena posobi ferovo.
4. Ak je v inzerate VIN, skus najst verejne zmienky o VIN. Ak nic nenajdes, napis to.
5. Vrat kratky markdown v slovencine, s nazvom zdroja a pouzitelnou URL citaciou pri kazdom webovom tvrdeni.

Pravidla:
- Nevymyslaj zdroje ani odkazy.
- Ak nenajdes spolahlive zdroje, napis "Nenasiel som spolahlivy webovy zdroj".
- Neanalyzuj fotografie, tento pass je iba textovy web research.
- Daj prednost praktickym zisteniam pre kupujuceho.
- Vystup udrz pod 650 slov.
- URL citacie: pouzi iba odkazy, ktore vyzeraju ako realne existujuce verejne stranky.
- Nepouzivaj presmerovacie URL z Google/Vertex AI ako verejne zdroje. Ak mas iba taky odkaz, uveď nazov zdroja a napis: "URL citacia nie je overitelna."
- Ak nevies dolozit cenu alebo naklad, napis, ze ide iba o orientacny odhad.

Format:

## Webove overenie cez Google Search

### Zdroje
- [nazov](url) - co zdroj potvrdzuje

### Zname problemy a servisne rizika
- komponent/problem - dopad pre kupujuceho - typicky km/vek alebo spustac - odhad EUR ak je rozumne dostupny - URL citacia

### Orientacna cena / trh
- rozpatie alebo porovnanie podobnych ponuk + URL citacia, alebo jasne napis, ze sa nepodarilo najst porovnanie

### VIN / historia / transparentnost
- zistenie + URL citacia, alebo jasne napis, ze sa nenasla verejna zmienka

### Najdolezitejsie webove zistenia pre finalnu analyzu
- 3 az 5 bodov: riziko, trh/cena, naklady, VIN transparentnost

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


def run_grounded_web_research(api_key: str, listing_context: str, model: str = None, listing_slug: str = None) -> str:
    """Run a text-only Gemini Interactions API pass with Google Search grounding."""
    if not api_key or not api_key.strip():
        raise ApiKeyError("API kluc nie je nastaveny. Pridaj ho v Nastaveniach.")

    model_to_use = model if model else GEMINI_GROUNDING_MODEL
    model_candidates = [model_to_use]
    for fallback_model in GEMINI_GROUNDING_FALLBACK_MODELS:
        if fallback_model not in model_candidates:
            model_candidates.append(fallback_model)

    prompt = _build_grounded_search_prompt(listing_context)
    last_error_text = ""
    unavailable_models = []

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

        if response.status_code == 503:
            unavailable_models.append(candidate_model)
            if candidate_model != model_candidates[-1]:
                time.sleep(1)
                continue

        if response.status_code == 429:
            detail = last_error_text[:300].replace("\n", " ").strip()
            default_tracker.record_request(
                model=candidate_model,
                request_type="grounded_search",
                listing_slug=listing_slug,
                input_tokens=input_tokens,
                output_tokens=0,
                status="rate_limited",
                duration_ms=round((time.perf_counter() - started_at) * 1000),
                error=detail,
            )
            raise RateLimitError(
                "Gemini Google Search grounding limit prekroceny."
                + (f" Detail: {detail}" if detail else "")
            )

        if response.status_code in {401, 403}:
            default_tracker.record_request(
                model=candidate_model,
                request_type="grounded_search",
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
                listing_slug=listing_slug,
                input_tokens=input_tokens,
                output_tokens=0,
                status=f"http_{response.status_code}",
                duration_ms=round((time.perf_counter() - started_at) * 1000),
                error=last_error_text[:300],
            )
            if 500 <= response.status_code < 600:
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
        output_text = research_text or "Google Search grounding prebehol, ale nevratil pouzitelny text."
        default_tracker.record_request(
            model=candidate_model,
            request_type="grounded_search",
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

    raise GroundingTransientError(
        f"Google Search grounding chyba API: {last_error_text[:300]}"
    )


def _call_gemini(api_key: str, system_prompt: str, user_content: str, image_data_list: list = None, model: str = None, listing_slug: str = None):
    """Call Google Gemini API with proper system_instruction support.
    
    Args:
        api_key: Gemini API key
        system_prompt: System instruction
        user_content: User content to analyze
        image_data_list: Optional list of (filename, base64_data, mime_type) tuples for images
        model: Optional model name override (e.g., "gemini-2.5-flash")
    """
    # Use provided model or fall back to default. If Gemini is overloaded,
    # try lighter models before surfacing the temporary provider failure.
    model_to_use = model if model else GEMINI_MODEL
    model_candidates = [model_to_use]
    for fallback_model in GEMINI_FALLBACK_MODELS:
        if fallback_model not in model_candidates:
            model_candidates.append(fallback_model)

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
            "temperature": 0.7,
            "topP": 0.95,
            "topK": 40,
            "maxOutputTokens": 65536,
        }
    }

    try:
        response = None
        unavailable_errors = []
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

            if response.status_code != 503:
                break

            unavailable_errors.append((candidate_model, error_text))
            if candidate_model != model_candidates[-1]:
                time.sleep(1)

        if response is not None and response.status_code == 503:
            tried_models = ", ".join(model for model, _ in unavailable_errors)
            default_tracker.record_request(
                model=request_model,
                request_type="stream_generate_content",
                listing_slug=listing_slug,
                input_tokens=input_tokens,
                output_tokens=0,
                status="unavailable",
                duration_ms=round((time.perf_counter() - started_at) * 1000),
                error=error_text[:300],
            )
            raise RateLimitError(
                "⚠️ Gemini je momentálne preťažený (HTTP 503: high demand). "
                f"Skúsil som tieto modely: {tried_models}. "
                "Skus znova o par minut alebo pouzi zalozny Gemini kluc."
            )

        if response.status_code == 429:
            # Check if it's actually a quota/rate limit or something else
            if "quota" in error_text.lower() or "rate limit" in error_text.lower():
                if image_data_list:
                    default_tracker.record_request(
                        model=request_model,
                        request_type="stream_generate_content",
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
                    )
                    return

                detail = error_text[:300].replace("\n", " ").strip()
                default_tracker.record_request(
                    model=request_model,
                    request_type="stream_generate_content",
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
            listing_slug=listing_slug,
            input_tokens=input_tokens,
            output_tokens=estimate_output_tokens(full_text),
            actual_input_tokens=actual_prompt_tokens,
            actual_output_tokens=actual_output_tokens,
            status="success",
            duration_ms=round((time.perf_counter() - started_at) * 1000),
        )

    except requests.exceptions.Timeout:
        default_tracker.record_request(
            model=locals().get("request_model", model if model else GEMINI_MODEL),
            request_type="stream_generate_content",
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



class RateLimitError(Exception):
    """Raised when API returns 429 (quota exceeded)."""
    pass


class ApiKeyError(Exception):
    """Raised when API key is missing or invalid."""
    pass


class GrokApiKeyError(ApiKeyError):
    """Raised when Grok API key is missing or invalid."""
    pass


# ─── Grok (xAI) API Client ────────────────────────────────────────


class OpenRouterApiKeyError(ApiKeyError):
    """Raised when OpenRouter API key is missing or invalid."""
    pass


def _openrouter_model_candidates(model: str = None) -> list[str]:
    configured_primary = os.environ.get("OPENROUTER_MODEL", "").strip()
    configured_fallbacks = [
        value.strip()
        for value in os.environ.get("OPENROUTER_FALLBACK_MODELS", "").split(",")
        if value.strip()
    ]
    candidates = [model or configured_primary or OPENROUTER_MODEL]
    candidates.extend(configured_fallbacks or OPENROUTER_FALLBACK_MODELS)

    deduped = []
    seen = set()
    for candidate in candidates:
        if candidate and candidate not in seen:
            seen.add(candidate)
            deduped.append(candidate)
    return deduped


def analyze_with_grok(api_key: str, system_prompt: str, user_content: str, model: str = None, listing_slug: str = None):
    """
    Send a request to xAI Grok and yield response chunks.
    Grok uses OpenAI-compatible chat completions format.
    Text-only — no image support.
    """
    if not api_key or not api_key.strip():
        raise GrokApiKeyError("Grok API kluc nie je nastaveny. Pridaj GROK_API_KEY v Nastaveniach.")

    yield from _call_grok(
        api_key,
        system_prompt,
        user_content,
        model=model,
        listing_slug=listing_slug,
    )


def _call_grok(api_key: str, system_prompt: str, user_content: str, model: str = None, listing_slug: str = None):
    """
    Call xAI Grok API (OpenAI-compatible chat completions format).
    Text-only streaming. No image support.
    """
    model_to_use = model if model else GROK_MODEL
    model_candidates = [model_to_use]
    for fallback_model in GROK_FALLBACK_MODELS:
        if fallback_model not in model_candidates:
            model_candidates.append(fallback_model)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    payload = {
        "model": model_to_use,
        "messages": messages,
        "stream": True,
        "temperature": 0.7,
        "max_tokens": 65536,
    }

    try:
        response = None
        unavailable_errors = []
        error_text = ""
        started_at = time.perf_counter()
        request_model = model_to_use
        input_tokens = estimate_request_tokens(system_prompt, user_content)

        for candidate_model in model_candidates:
            request_model = candidate_model
            started_at = time.perf_counter()
            payload["model"] = candidate_model

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key.strip()}",
            }

            response = requests.post(
                GROK_API_URL,
                json=payload,
                headers=headers,
                stream=True,
                timeout=120,
            )

            if response.status_code == 200:
                error_text = ""
            else:
                error_text = response.text[:500] if response.text else ""

            safe_log(
                f"DEBUG: Grok API model {candidate_model} status {response.status_code}, "
                f"response preview: {error_text[:200] if error_text else '(streaming response)'}"
            )

            if response.status_code != 503:
                break

            unavailable_errors.append((candidate_model, error_text))
            if candidate_model != model_candidates[-1]:
                time.sleep(1)

        if response is not None and response.status_code == 503:
            tried_models = ", ".join(model for model, _ in unavailable_errors)
            default_tracker.record_request(
                model=request_model,
                request_type="grok_stream",
                listing_slug=listing_slug,
                input_tokens=input_tokens,
                output_tokens=0,
                status="unavailable",
                duration_ms=round((time.perf_counter() - started_at) * 1000),
                error=error_text[:300],
            )
            raise RateLimitError(
                "Grok je momentalne pretazeny (HTTP 503). "
                f"Skusil som tieto modely: {tried_models}."
            )

        if response.status_code == 401 or response.status_code == 403:
            default_tracker.record_request(
                model=request_model,
                request_type="grok_stream",
                listing_slug=listing_slug,
                input_tokens=input_tokens,
                output_tokens=0,
                status="auth_error",
                duration_ms=round((time.perf_counter() - started_at) * 1000),
                error=error_text[:300],
            )
            raise GrokApiKeyError(
                f"Grok API kluc je neplatny (HTTP {response.status_code}). "
                f"Over GROK_API_KEY. Odpoved: {error_text[:200]}"
            )

        if response.status_code == 429:
            detail = error_text[:300].replace("\n", " ").strip()
            default_tracker.record_request(
                model=request_model,
                request_type="grok_stream",
                listing_slug=listing_slug,
                input_tokens=input_tokens,
                output_tokens=0,
                status="rate_limited",
                duration_ms=round((time.perf_counter() - started_at) * 1000),
                error=detail,
            )
            raise RateLimitError(
                "Grok API limit prekroceny."
                + (f" Detail: {detail}" if detail else "")
            )

        if response.status_code != 200:
            default_tracker.record_request(
                model=request_model,
                request_type="grok_stream",
                listing_slug=listing_slug,
                input_tokens=input_tokens,
                output_tokens=0,
                status=f"http_{response.status_code}",
                duration_ms=round((time.perf_counter() - started_at) * 1000),
                error=error_text[:300],
            )
            raise ConnectionError(
                f"Grok API chyba ({response.status_code}): {error_text}"
            )

        response.encoding = 'utf-8'

        # Parse OpenAI-compatible SSE stream
        full_text = ""
        for line in response.iter_lines():
            if isinstance(line, bytes):
                try:
                    line = line.decode('utf-8')
                except UnicodeDecodeError:
                    continue
            if not line or not line.startswith("data: "):
                continue

            data_str = line[6:]
            if data_str == "[DONE]":
                break

            try:
                data = json.loads(data_str)

                if "error" in data:
                    error_msg = data["error"].get("message", str(data["error"]))
                    raise ConnectionError(f"Grok API error: {error_msg}")

                choices = data.get("choices", [])
                if choices:
                    delta = choices[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        full_text += content
                        yield content

                    finish_reason = choices[0].get("finish_reason", "")
                    if finish_reason and finish_reason != "null" and finish_reason not in (None, ""):
                        if finish_reason == "length":
                            yield "\n\n⚠️ Analyza dosiahla limit tokenov. Vystup je neuplny."

            except json.JSONDecodeError:
                continue

        default_tracker.record_request(
            model=request_model,
            request_type="grok_stream",
            listing_slug=listing_slug,
            input_tokens=input_tokens,
            output_tokens=estimate_output_tokens(full_text),
            status="success",
            duration_ms=round((time.perf_counter() - started_at) * 1000),
        )

    except requests.exceptions.Timeout:
        default_tracker.record_request(
            model=locals().get("request_model", model if model else GROK_MODEL),
            request_type="grok_stream",
            listing_slug=listing_slug,
            input_tokens=locals().get("input_tokens", estimate_request_tokens(system_prompt, user_content)),
            output_tokens=0,
            status="timeout",
            duration_ms=round((time.perf_counter() - locals().get("started_at", time.perf_counter())) * 1000),
            error="Grok API timeout.",
        )
        raise ConnectionError(
            "Grok API casovy limit (120s). Skus znova alebo skontroluj internet."
        )
    except requests.exceptions.ConnectionError:
        default_tracker.record_request(
            model=locals().get("request_model", model if model else GROK_MODEL),
            request_type="grok_stream",
            listing_slug=listing_slug,
            input_tokens=locals().get("input_tokens", estimate_request_tokens(system_prompt, user_content)),
            output_tokens=0,
            status="connection_error",
            duration_ms=round((time.perf_counter() - locals().get("started_at", time.perf_counter())) * 1000),
            error="Grok API connection error.",
        )
        raise ConnectionError(
            "Nie je pripojenie k internetu pre Grok API."
        )


class GrokApiKeyError(ApiKeyError):
    """Raised when Grok API key is missing or invalid."""
    pass


def analyze_with_openrouter(api_key: str, system_prompt: str, user_content: str, model: str = None, listing_slug: str = None):
    """
    Send a text-only request through OpenRouter.
    One OpenRouter key can call many models; the model id is selected per request.
    """
    if not api_key or not api_key.strip():
        raise OpenRouterApiKeyError("OpenRouter API kluc nie je nastaveny. Pridaj OPENROUTER_API_KEY.")

    yield from _call_openrouter(
        api_key,
        system_prompt,
        user_content,
        model=model,
        listing_slug=listing_slug,
    )


def _call_openrouter(api_key: str, system_prompt: str, user_content: str, model: str = None, listing_slug: str = None):
    """Call OpenRouter's OpenAI-compatible streaming chat completions API."""
    model_candidates = _openrouter_model_candidates(model)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    payload = {
        "model": model_candidates[0],
        "messages": messages,
        "stream": True,
        "temperature": 0.4,
        "max_tokens": 32768,
    }

    response = None
    error_text = ""
    unavailable_errors = []
    request_model = model_candidates[0]
    started_at = time.perf_counter()
    input_tokens = estimate_request_tokens(system_prompt, user_content)

    try:
        for candidate_index, candidate_model in enumerate(model_candidates):
            request_model = candidate_model
            started_at = time.perf_counter()
            payload["model"] = candidate_model
            response = requests.post(
                OPENROUTER_API_URL,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key.strip()}",
                    "HTTP-Referer": os.environ.get("OPENROUTER_SITE_URL", "https://checkni-auto.local"),
                    "X-Title": os.environ.get("OPENROUTER_APP_NAME", "Checkni Auto Demo"),
                },
                stream=True,
                timeout=120,
            )

            if response.status_code == 200:
                error_text = ""
            else:
                error_text = response.text[:500] if response.text else ""

            safe_log(
                f"DEBUG: OpenRouter API model {candidate_model} status {response.status_code}, "
                f"response preview: {error_text[:200] if error_text else '(streaming response)'}"
            )

            if response.status_code == 401 or response.status_code == 403:
                default_tracker.record_request(
                    model=request_model,
                    request_type="openrouter_stream",
                    listing_slug=listing_slug,
                    input_tokens=input_tokens,
                    output_tokens=0,
                    status="auth_error",
                    duration_ms=round((time.perf_counter() - started_at) * 1000),
                    error=error_text[:300],
                )
                raise OpenRouterApiKeyError(
                    f"OpenRouter API kluc je neplatny (HTTP {response.status_code}). "
                    f"Over OPENROUTER_API_KEY. Odpoved: {error_text[:200]}"
                )

            if response.status_code == 402:
                default_tracker.record_request(
                    model=request_model,
                    request_type="openrouter_stream",
                    listing_slug=listing_slug,
                    input_tokens=input_tokens,
                    output_tokens=0,
                    status="payment_required",
                    duration_ms=round((time.perf_counter() - started_at) * 1000),
                    error=error_text[:300],
                )
                raise RateLimitError(
                    "OpenRouter odmietol poziadavku pre kredit/limit uctu. "
                    f"Pouzity model: {request_model}. Odpoved: {error_text[:200]}"
                )

            if response.status_code in {429, 503}:
                unavailable_errors.append((candidate_model, error_text))
                default_tracker.record_request(
                    model=request_model,
                    request_type="openrouter_stream",
                    listing_slug=listing_slug,
                    input_tokens=input_tokens,
                    output_tokens=0,
                    status="rate_limited" if response.status_code == 429 else "unavailable",
                    duration_ms=round((time.perf_counter() - started_at) * 1000),
                    error=error_text[:300],
                )
                if candidate_index < len(model_candidates) - 1:
                    time.sleep(1)
                    continue
                break

            if response.status_code != 200:
                default_tracker.record_request(
                    model=request_model,
                    request_type="openrouter_stream",
                    listing_slug=listing_slug,
                    input_tokens=input_tokens,
                    output_tokens=0,
                    status=f"http_{response.status_code}",
                    duration_ms=round((time.perf_counter() - started_at) * 1000),
                    error=error_text[:300],
                )
                raise ConnectionError(
                    f"OpenRouter API chyba ({response.status_code}): {error_text}"
                )

            response.encoding = "utf-8"
            full_text = ""
            actual_prompt_tokens = None
            actual_output_tokens = None
            stream_error = ""
            for line in response.iter_lines():
                if isinstance(line, bytes):
                    try:
                        line = line.decode("utf-8")
                    except UnicodeDecodeError:
                        continue
                if not line or not line.startswith("data: "):
                    continue

                data_str = line[6:]
                if data_str == "[DONE]":
                    break

                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                if "error" in data:
                    error_info = data["error"]
                    if isinstance(error_info, dict):
                        stream_error = error_info.get("message", str(error_info))
                    else:
                        stream_error = str(error_info)
                    break

                usage = data.get("usage") or {}
                actual_prompt_tokens = usage.get("prompt_tokens") or actual_prompt_tokens
                actual_output_tokens = usage.get("completion_tokens") or actual_output_tokens

                choices = data.get("choices", [])
                if not choices:
                    continue

                delta = choices[0].get("delta", {}) or {}
                content = delta.get("content", "")
                if content:
                    full_text += content
                    yield content

                finish_reason = choices[0].get("finish_reason", "")
                if finish_reason == "length":
                    yield "\n\nAnalyza dosiahla limit tokenov. Vystup je neuplny."

            if stream_error:
                default_tracker.record_request(
                    model=request_model,
                    request_type="openrouter_stream",
                    listing_slug=listing_slug,
                    input_tokens=input_tokens,
                    output_tokens=estimate_output_tokens(full_text),
                    actual_input_tokens=actual_prompt_tokens,
                    actual_output_tokens=actual_output_tokens,
                    status="upstream_error",
                    duration_ms=round((time.perf_counter() - started_at) * 1000),
                    error=stream_error[:300],
                )
                unavailable_errors.append((candidate_model, stream_error))
                if not full_text and candidate_index < len(model_candidates) - 1:
                    safe_log(
                        f"OpenRouter upstream error from {candidate_model}; "
                        f"retrying {model_candidates[candidate_index + 1]}."
                    )
                    time.sleep(1)
                    continue
                raise ConnectionError(f"OpenRouter API error: {stream_error}")

            default_tracker.record_request(
                model=request_model,
                request_type="openrouter_stream",
                listing_slug=listing_slug,
                input_tokens=input_tokens,
                output_tokens=estimate_output_tokens(full_text),
                actual_input_tokens=actual_prompt_tokens,
                actual_output_tokens=actual_output_tokens,
                status="success",
                duration_ms=round((time.perf_counter() - started_at) * 1000),
            )
            return

        tried_models = ", ".join(model for model, _ in unavailable_errors)
        detail = (unavailable_errors[-1][1] if unavailable_errors else error_text)[:300].replace("\n", " ").strip()
        raise RateLimitError(
            "OpenRouter free modely su momentalne limitovane alebo nedostupne. "
            f"Skusene modely: {tried_models or ', '.join(model_candidates)}."
            + (f" Detail: {detail}" if detail else "")
        )

    except requests.exceptions.Timeout:
        default_tracker.record_request(
            model=locals().get("request_model", model if model else OPENROUTER_MODEL),
            request_type="openrouter_stream",
            listing_slug=listing_slug,
            input_tokens=locals().get("input_tokens", estimate_request_tokens(system_prompt, user_content)),
            output_tokens=0,
            status="timeout",
            duration_ms=round((time.perf_counter() - locals().get("started_at", time.perf_counter())) * 1000),
            error="OpenRouter API timeout.",
        )
        raise ConnectionError(
            "OpenRouter API casovy limit (120s). Skus znova alebo skontroluj internet."
        )
    except requests.exceptions.ConnectionError:
        default_tracker.record_request(
            model=locals().get("request_model", model if model else OPENROUTER_MODEL),
            request_type="openrouter_stream",
            listing_slug=listing_slug,
            input_tokens=locals().get("input_tokens", estimate_request_tokens(system_prompt, user_content)),
            output_tokens=0,
            status="connection_error",
            duration_ms=round((time.perf_counter() - locals().get("started_at", time.perf_counter())) * 1000),
            error="OpenRouter API connection error.",
        )
        raise ConnectionError(
            "Nie je pripojenie k internetu pre OpenRouter API."
        )


def extract_kb_save_blocks(text: str) -> list[dict]:
    """
    Parse analysis result for [SAVE AS knowledge_base/...] blocks.
    Returns list of {category, filename, json_data} dicts.
    Handles both:
      [SAVE AS ...] ```json { ... } ```
      [SAVE AS ...] ``` ```json { ... } ```  (Gemini extra fence)
    Also normalises singular category names to plural (engine->engines, generation->generations).
    """
    blocks = []
    # Match [SAVE AS ...] followed by optional ```, then ```json, then JSON, then ```
    pattern = r'\[SAVE AS knowledge_base/([^/]+)/([^\]]+\.json)\]\s*(?:```\s*)?```json\s*\n(.*?)\n```'
    matches = re.findall(pattern, text, re.DOTALL)

    # Normalise category names: singular -> plural
    SINGULAR_TO_PLURAL = {
        "engine": "engines",
        "transmission": "transmissions",
        "generation": "generations",
        "electric_motor": "electric_motors",
        "battery": "batteries",
        "charging": "charging",
        "hybrid_system": "hybrid_systems",
    }

    for category, filename, json_str in matches:
        # Normalise category
        category = SINGULAR_TO_PLURAL.get(category, category)
        try:
            data = json.loads(json_str)
            blocks.append({
                "category": category,
                "filename": filename,
                "data": data,
            })
        except json.JSONDecodeError:
            continue

    return blocks
