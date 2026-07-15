"""OpenRouter OpenAI-compatible streaming provider adapter."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from typing import Any

import requests

from token_tracker import default_tracker, estimate_output_tokens, estimate_request_tokens

from scrapper_demo.logging import safe_log
from .errors import OpenRouterApiKeyError, RateLimitError


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


def _usage_value(
    usage: dict[str, Any],
    *names: str,
    previous: int | None = None,
) -> int | None:
    for name in names:
        if name in usage and usage[name] is not None:
            try:
                return int(usage[name])
            except (TypeError, ValueError):
                continue
    return previous


def _openrouter_model_candidates(model: str | None = None) -> list[str]:
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


def analyze_with_openrouter(
    api_key: str,
    system_prompt: str,
    user_content: str,
    model: str | None = None,
    listing_slug: str | None = None,
) -> Iterator[str]:
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


def _call_openrouter(
    api_key: str,
    system_prompt: str,
    user_content: str,
    model: str | None = None,
    listing_slug: str | None = None,
) -> Iterator[str]:
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
            actual_thinking_tokens = None
            cached_input_tokens = None
            actual_total_tokens = None
            provider_request_id = None
            finish_reason_seen = ""
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

                provider_request_id = data.get("id") or provider_request_id

                if "error" in data:
                    error_info = data["error"]
                    if isinstance(error_info, dict):
                        stream_error = error_info.get("message", str(error_info))
                    else:
                        stream_error = str(error_info)
                    break

                usage = data.get("usage") or {}
                if isinstance(usage, dict):
                    actual_prompt_tokens = _usage_value(
                        usage, "prompt_tokens", previous=actual_prompt_tokens
                    )
                    actual_output_tokens = _usage_value(
                        usage,
                        "completion_tokens",
                        "output_tokens",
                        previous=actual_output_tokens,
                    )
                    actual_total_tokens = _usage_value(
                        usage, "total_tokens", previous=actual_total_tokens
                    )
                    prompt_details = usage.get("prompt_tokens_details") or {}
                    completion_details = usage.get("completion_tokens_details") or {}
                    if isinstance(prompt_details, dict):
                        cached_input_tokens = _usage_value(
                            prompt_details,
                            "cached_tokens",
                            previous=cached_input_tokens,
                        )
                    if isinstance(completion_details, dict):
                        actual_thinking_tokens = _usage_value(
                            completion_details,
                            "reasoning_tokens",
                            "thinking_tokens",
                            previous=actual_thinking_tokens,
                        )

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
                    finish_reason_seen = "length"
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
                actual_thinking_tokens=actual_thinking_tokens,
                cached_input_tokens=cached_input_tokens,
                actual_total_tokens=actual_total_tokens,
                provider_request_id=provider_request_id,
                finish_reason=finish_reason_seen or "STOP",
                output_chars=len(full_text),
                max_output_tokens=32768,
                thinking_mode="provider_default",
                grounding_enabled=False,
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


analyze = analyze_with_openrouter
stream_generate = _call_openrouter
