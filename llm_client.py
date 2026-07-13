"""Compatibility facade for the extracted LLM provider adapters."""

from __future__ import annotations

import json
import re

from scrapper_demo.logging import (
    configure_console_encoding as _configure_console_encoding,
    safe_log,
)
from scrapper_demo.providers.errors import (
    ApiKeyError,
    GrokApiKeyError,
    GroundingTransientError,
    OpenRouterApiKeyError,
    RateLimitError,
)
from scrapper_demo.providers.gemini import (
    GEMINI_ADVANCED_FLASH_MODEL,
    GEMINI_API_BASE,
    GEMINI_API_URL,
    GEMINI_FALLBACK_MODELS,
    GEMINI_FINAL_FALLBACK_MODELS,
    GEMINI_FINAL_MODEL,
    GEMINI_FLASH_LITE_MODEL,
    GEMINI_FLASH_MODEL,
    GEMINI_GROUNDING_FALLBACK_MODELS,
    GEMINI_GROUNDING_MODEL,
    GEMINI_INTERACTIONS_API_URL,
    GEMINI_MODEL,
    GEMINI_TEXT_RESEARCH_MODEL,
    GEMINI_VISION_MODEL,
    GROUNDING_CONTEXT_MAX_CHARS,
    GROUNDING_REDIRECT_HOST,
    GROUNDING_RESOLVE_MAX_REDIRECTS,
    GROUNDING_RESOLVE_TIMEOUT,
    _build_grounded_search_prompt,
    _build_grounded_market_prompt,
    _call_gemini,
    _clean_citation_label,
    _extract_interaction_text_and_citations,
    _is_gemini_rate_limit_error,
    _is_retryable_gemini_model_error,
    _ordered_unique_models,
    _resolve_annotation_redirects,
    analyze_with_llm,
    run_grounded_web_research,
)
from scrapper_demo.providers.grok import (
    GROK_API_BASE,
    GROK_API_URL,
    GROK_FALLBACK_MODELS,
    GROK_MODEL,
    _call_grok,
    analyze_with_grok,
)
from scrapper_demo.providers.openrouter import (
    OPENROUTER_API_URL,
    OPENROUTER_FALLBACK_MODELS,
    OPENROUTER_MODEL,
    _call_openrouter,
    _openrouter_model_candidates,
    analyze_with_openrouter,
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
