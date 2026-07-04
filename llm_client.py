"""

LLM Client for In-App AI Analysis.
Supports Google Gemini.

Usage:
    from llm_client import analyze_with_llm
    for chunk in analyze_with_llm(api_key, system_prompt, user_content):
        print(chunk, end="")
"""

import json
import requests
import re
import sys
import time


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
GEMINI_MODEL = "gemini-2.5-flash"  # Better reasoning, vision support, search grounding support
GEMINI_API_URL = f"{GEMINI_API_BASE}/{GEMINI_MODEL}:streamGenerateContent"
GEMINI_FALLBACK_MODELS = [
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
]
GEMINI_GROUNDING_FALLBACK_MODELS = [
    "gemini-2.5-flash-lite",
]
GROUNDING_CONTEXT_MAX_CHARS = 16000



def analyze_with_llm(api_key: str, system_prompt: str, user_content: str, image_data_list: list = None, model: str = None):
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
    )


def _build_grounded_search_prompt(listing_context: str) -> str:
    context = listing_context
    if len(context) > GROUNDING_CONTEXT_MAX_CHARS:
        context = context[:GROUNDING_CONTEXT_MAX_CHARS] + "\n\n[Context truncated for web research pass.]"

    return f"""Si web research modul pre analyzu ojazdeneho auta.

Pouzi Google Search grounding. Vyhladavaj cielene a skromne, hlavne SK/CZ/EU zdroje.

Uloha:
1. Over typicke problemy modelu, generacie, motora, prevodovky alebo hybrid/EV systemu.
2. Najdi orientacne aktualne trhove informacie k podobnym autam v SK/CZ/EU, ak sa daju najst.
3. Ak je v inzerate VIN, skus najst verejne zmienky o VIN. Ak nic nenajdes, napis to.
4. Vrat kratky markdown v slovencine, s URL citaciami pri kazdom webovom tvrdeni.

Pravidla:
- Nevymyslaj zdroje ani odkazy.
- Ak nenajdes spolahlive zdroje, napis "Nenasiel som spolahlivy webovy zdroj".
- Neanalyzuj fotografie, tento pass je iba textovy web research.
- Daj prednost praktickym zisteniam pre kupujuceho.
- Vystup udrz pod 1200 slov.
- URL citacie: pouzi iba odkazy, ktore vyzeraju ako realne existujuce stranky. Ak URL vyzera podozrivo, nekompletna alebo by mohla vest na 404, nepouziju ju. Namiesto URL napis: "URL citacia nie je overitelna."

Format:

## Webove overenie cez Google Search

### Zdroje
- [nazov](url) - co zdroj potvrdzuje

### Zname problemy a servisne rizika
- zistenie + URL citacia

### Orientacna cena / trh
- zistenie + URL citacia, alebo jasne napis, ze sa nepodarilo najst porovnanie

### VIN / historia / transparentnost
- zistenie + URL citacia, alebo jasne napis, ze sa nenasla verejna zmienka

### Najdolezitejsie webove zistenia pre finalnu analyzu
- 3 az 6 bodov

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
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            title = annotation.get("title") or annotation.get("source") or url
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


def run_grounded_web_research(api_key: str, listing_context: str, model: str = None) -> str:
    """Run a text-only Gemini Interactions API pass with Google Search grounding."""
    if not api_key or not api_key.strip():
        raise ApiKeyError("API kluc nie je nastaveny. Pridaj ho v Nastaveniach.")

    model_to_use = model if model else GEMINI_MODEL
    model_candidates = [model_to_use]
    for fallback_model in GEMINI_GROUNDING_FALLBACK_MODELS:
        if fallback_model not in model_candidates:
            model_candidates.append(fallback_model)

    prompt = _build_grounded_search_prompt(listing_context)
    last_error_text = ""
    unavailable_models = []

    for candidate_model in model_candidates:
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
            raise ConnectionError("Google Search grounding casovy limit (120s).")
        except requests.exceptions.ConnectionError:
            raise ConnectionError("Nie je pripojenie k internetu pre Google Search grounding.")

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
            raise RateLimitError(
                "Gemini Google Search grounding limit prekroceny."
                + (f" Detail: {detail}" if detail else "")
            )

        if response.status_code in {401, 403}:
            raise ApiKeyError(
                f"Google Search grounding odmietol API kluc (HTTP {response.status_code}). "
                f"Gemini odpoved: {last_error_text[:200]}"
            )

        if response.status_code != 200:
            break

        try:
            data = response.json()
        except json.JSONDecodeError:
            raise ConnectionError("Google Search grounding vratil necitatelnu JSON odpoved.")

        if "error" in data:
            error_info = data["error"]
            message = error_info.get("message", str(error_info)) if isinstance(error_info, dict) else str(error_info)
            raise ConnectionError(f"Gemini Google Search grounding chyba: {message}")

        research_text = _extract_interaction_text_and_citations(data)
        if research_text:
            return research_text
        return "Google Search grounding prebehol, ale nevratil pouzitelny text."

    if unavailable_models and len(unavailable_models) == len(model_candidates):
        raise RateLimitError(
            "Gemini Google Search grounding je momentalne pretazeny. "
            f"Skusene modely: {', '.join(unavailable_models)}."
        )

    raise ConnectionError(
        f"Google Search grounding chyba API: {last_error_text[:300]}"
    )


def _call_gemini(api_key: str, system_prompt: str, user_content: str, image_data_list: list = None, model: str = None):
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
            "stopSequences": ["<!-- END_ANALYSIS -->"],
        }
    }

    try:
        response = None
        unavailable_errors = []
        error_text = ""
        for candidate_model in model_candidates:
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
            raise RateLimitError(
                "⚠️ Gemini je momentálne preťažený (HTTP 503: high demand). "
                f"Skúsil som tieto modely: {tried_models}. "
                "Skus znova o par minut alebo pouzi zalozny Gemini kluc."
            )

        if response.status_code == 429:
            # Check if it's actually a quota/rate limit or something else
            if "quota" in error_text.lower() or "rate limit" in error_text.lower():
                if image_data_list:
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
                    )
                    return

                detail = error_text[:300].replace("\n", " ").strip()
                raise RateLimitError(
                    "⚠️ Gemini API limit prekročený. Môže ísť o per-minute, per-model, "
                    "tokenový alebo denný quota limit, nie nutne o počet požiadaviek. "
                    "Skus znova o par minut alebo pouzi zalozny Gemini kluc."
                    + (f"\n\nDetail Gemini: {detail}" if detail else "")
                )
            else:
                # 429 might actually be an auth error
                raise ApiKeyError(
                    f"❌ Prístup zamietnutý (HTTP 429). API kľúč je pravdepodobne neplatný alebo expirovaný.\n\n"
                    f"Gemini odpoveď: {error_text[:200]}\n\n"
                    f"Skontroluj:\n"
                    f"  ? API kluc je spravny a patri ku Gemini API\n"
                    f"  • Kľúč nie je expirovaný\n"
                    f"  • Máš prístup k Gemini API na https://aistudio.google.com/"
                )

        if response.status_code == 401 or response.status_code == 403:
            raise ApiKeyError(
                f"❌ Google Gemini API kľúč je neplatný (HTTP {response.status_code}).\n\n"
                f"Gemini odpoveď: {error_text[:200]}\n\n"
                "Vytvor nový kľúč na https://aistudio.google.com/"
            )

        if response.status_code != 200:
            raise ConnectionError(
                f"❌ Google Gemini chyba API ({response.status_code}): {error_text}"
            )

        # Force UTF-8 encoding to properly handle emojis and diacritics
        response.encoding = 'utf-8'
        
        # Parse SSE stream
        full_text = ""
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

    except requests.exceptions.Timeout:
        raise ConnectionError(
            "❌ Google Gemini API časový limit (120s). Skús znova alebo skontroluj internet."
        )
    except requests.exceptions.ConnectionError:
        raise ConnectionError(
            "❌ Nie je pripojenie k internetu. Skontroluj sieť pre Google API."
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


class RateLimitError(Exception):
    """Raised when API returns 429 (quota exceeded)."""
    pass


class ApiKeyError(Exception):
    """Raised when API key is missing or invalid."""
    pass
