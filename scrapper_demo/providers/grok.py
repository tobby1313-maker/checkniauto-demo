"""xAI Grok streaming provider adapter."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator

import requests

from token_tracker import default_tracker, estimate_output_tokens, estimate_request_tokens

from scrapper_demo.logging import safe_log
from .errors import ApiKeyError, GrokApiKeyError, RateLimitError


# xAI Grok API configuration
GROK_API_BASE = "https://api.x.ai/v1"
GROK_MODEL = "grok-2"  # or grok-2-latest, grok-3
GROK_API_URL = f"{GROK_API_BASE}/chat/completions"
GROK_FALLBACK_MODELS = [
    "grok-2-latest",
    "grok-2",
]


def analyze_with_grok(
    api_key: str,
    system_prompt: str,
    user_content: str,
    model: str | None = None,
    listing_slug: str | None = None,
) -> Iterator[str]:
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


def _call_grok(
    api_key: str,
    system_prompt: str,
    user_content: str,
    model: str | None = None,
    listing_slug: str | None = None,
) -> Iterator[str]:
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


analyze = analyze_with_grok
stream_generate = _call_grok
