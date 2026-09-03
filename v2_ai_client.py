from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any, Iterable

import requests

from v2_config import (
    GENERATE_CONTENT_BASE,
    INTERACTIONS_URL,
    REQUEST_TIMEOUT_SECONDS,
    ProviderError,
    _unique,
    api_keys,
)


def _json_from_text(text: str) -> dict[str, Any]:
    value = (text or "").strip()
    if not value:
        raise ProviderError("AI nevrátilo žiadny výsledok.")
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.I)
        value = re.sub(r"\s*```$", "", value)
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        start = value.find("{")
        end = value.rfind("}")
        if start < 0 or end <= start:
            raise ProviderError("AI vrátilo nečitateľný výsledok.")
        try:
            parsed = json.loads(value[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ProviderError("AI vrátilo neplatný JSON výsledok.") from exc
    if not isinstance(parsed, dict):
        raise ProviderError("AI výsledok nemá očakávaný objektový formát.")
    return parsed


def _extract_generate_text(data: dict[str, Any]) -> str:
    blocks: list[str] = []
    for candidate in data.get("candidates", []) or []:
        content = candidate.get("content") or {}
        for part in content.get("parts", []) or []:
            text = part.get("text") if isinstance(part, dict) else None
            if isinstance(text, str) and text.strip():
                blocks.append(text)
    return "\n".join(blocks).strip()


def _extract_interaction(data: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    root = data.get("interaction") if isinstance(data.get("interaction"), dict) else data
    texts: list[str] = []
    citations: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    direct = root.get("output_text") or root.get("outputText") or root.get("text")
    if isinstance(direct, str) and direct.strip():
        texts.append(direct.strip())

    for step in root.get("steps", []) or []:
        if not isinstance(step, dict):
            continue
        if (step.get("type") or "") not in {"model_output", "modelOutput"}:
            continue
        content_blocks = step.get("content") or step.get("contents") or []
        if isinstance(content_blocks, dict):
            content_blocks = [content_blocks]
        for block in content_blocks:
            if isinstance(block, str):
                if block.strip():
                    texts.append(block.strip())
                continue
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                texts.append(text.strip())
            for annotation in block.get("annotations", []) or []:
                if not isinstance(annotation, dict):
                    continue
                url = str(annotation.get("url") or "").strip()
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                citations.append(
                    {
                        "title": str(
                            annotation.get("title") or annotation.get("source") or url
                        ),
                        "url": url,
                    }
                )
    return "\n\n".join(_unique(texts)), citations


def _request_error(response: requests.Response) -> str:
    try:
        data = response.json()
        error = data.get("error", {}) if isinstance(data, dict) else {}
        message = error.get("message") if isinstance(error, dict) else None
        if message:
            return str(message)[:400]
    except Exception:
        pass
    return (response.text or "").replace("\n", " ")[:400]


def call_generate_content_json(
    prompt: str,
    schema: dict[str, Any],
    image_items: list[dict[str, Any]],
    models: Iterable[str],
) -> dict[str, Any]:
    keys = api_keys()
    if not keys:
        raise ProviderError("Gemini API kľúč nie je nastavený na serveri.")

    parts: list[dict[str, Any]] = [{"text": prompt}]
    for item in image_items:
        path = Path(item["path"])
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        parts.append(
            {
                "inline_data": {
                    "mime_type": "image/jpeg",
                    "data": encoded,
                }
            }
        )

    last_error = ""
    for model in models:
        url = f"{GENERATE_CONTENT_BASE}/{model}:generateContent"
        for key in keys:
            for schema_key in ("responseJsonSchema", "responseSchema"):
                payload = {
                    "contents": [{"role": "user", "parts": parts}],
                    "generationConfig": {
                        "temperature": 0.15,
                        "topP": 0.85,
                        "maxOutputTokens": 6_000,
                        "responseMimeType": "application/json",
                        schema_key: schema,
                    },
                }
                try:
                    response = requests.post(
                        url,
                        headers={
                            "Content-Type": "application/json",
                            "x-goog-api-key": key,
                        },
                        json=payload,
                        timeout=REQUEST_TIMEOUT_SECONDS,
                    )
                except requests.RequestException as exc:
                    last_error = str(exc)
                    continue
                if response.status_code == 200:
                    text = _extract_generate_text(response.json())
                    return _json_from_text(text)
                last_error = _request_error(response)
                if response.status_code == 400 and "schema" in last_error.lower():
                    continue
                if response.status_code in {401, 403}:
                    break
                if response.status_code not in {429, 500, 502, 503, 504}:
                    break
    raise ProviderError(
        f"Gemini vision modul zlyhal: {last_error or 'neznáma chyba'}"
    )


def call_interaction_json(
    prompt: str,
    schema: dict[str, Any],
    models: Iterable[str],
    use_search: bool = False,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    keys = api_keys()
    if not keys:
        raise ProviderError("Gemini API kľúč nie je nastavený na serveri.")

    last_error = ""
    for model in models:
        for key in keys:
            payload: dict[str, Any] = {
                "model": model,
                "input": prompt,
                "store": False,
                "response_format": {
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": schema,
                },
            }
            if use_search:
                payload["tools"] = [{"type": "google_search"}]
            try:
                response = requests.post(
                    INTERACTIONS_URL,
                    headers={"Content-Type": "application/json", "x-goog-api-key": key},
                    json=payload,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
            except requests.RequestException as exc:
                last_error = str(exc)
                continue
            if response.status_code == 200:
                text, citations = _extract_interaction(response.json())
                return _json_from_text(text), citations
            last_error = _request_error(response)
            if response.status_code in {401, 403}:
                break
            if response.status_code not in {429, 500, 502, 503, 504}:
                break
    raise ProviderError(
        f"Gemini textový modul zlyhal: {last_error or 'neznáma chyba'}"
    )
