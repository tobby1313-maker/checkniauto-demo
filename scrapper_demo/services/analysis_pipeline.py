from __future__ import annotations

import json
import os
import re
import unicodedata
from urllib.parse import urlparse
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Unpack

from llm_client import extract_kb_save_blocks
from risk_scorer import calculate_risk_score
from scrapper_demo.contracts import (
    GeminiKeyEntry,
    ListingJobRepositoryProtocol,
    RiskScoreResult,
    SSEPayload,
)
from scrapper_demo.providers.errors import (
    ApiKeyError,
    GroundingTransientError,
    RateLimitError,
)
from scrapper_demo.providers.gemini import (
    GEMINI_FINAL_FALLBACK_MODELS,
    GEMINI_FINAL_MODEL,
    GEMINI_GROUNDING_MODEL,
    GEMINI_TEXT_RESEARCH_MODEL,
    GEMINI_VISION_MODEL,
    grounded_research as run_grounded_web_research,
    stream_generate as _call_gemini,
)
from scrapper_demo.providers.retry import (
    collect_gemini_with_key_fallback,
    gemini_retry_status,
    normalize_gemini_key_entries,
)
from scrapper_demo.scorecard import build_buyer_scorecard
from scrapper_demo.market_comparables import deduplicate_market_comparables
from scrapper_demo.validation import (
    _ensure_end_analysis_marker,
    _soft_validate_final_report,
    _soft_validate_json_contract,
    _write_validation_warnings,
)
from token_tracker import estimate_output_tokens, estimate_request_tokens


@dataclass(frozen=True, slots=True)
class AnalysisPipelineDependencies:
    """Explicit collaborators supplied by the application composition layer."""

    repository: ListingJobRepositoryProtocol
    prompt_dir: Path
    build_final_synthesis_context: Callable[..., str]
    build_text_research_context: Callable[..., str]
    compact_json_for_prompt: Callable[[Any], str]
    output_language: Callable[[str], str]
    inject_photo_vin: Callable[..., str]
    listing_context_text: Callable[..., str]
    model_display_name: Callable[[str], str]
    no_photos_vision_result: Callable[..., str]
    normalize_report_headings: Callable[[str], str]
    public_analysis_markdown: Callable[[str, str], str]
    replace_photo_analysis_section: Callable[..., str]
    replace_quick_summary_scorecard: Callable[..., str]
    move_pros_cons_after_quick_summary: Callable[[str], str]
    save_kb_blocks: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]
    safe_model_json: Callable[[str], dict[str, Any]]
    strip_kb_section: Callable[[str], str]
    prepare_images: Callable[[str], tuple[list[Any], Any]]
    stream_text_model: Callable[..., Any]
    log: Callable[[Any], None]
    normalize_gemini_keys: Callable[..., list[GeminiKeyEntry]] = normalize_gemini_key_entries
    collect_gemini: Callable[..., Any] = collect_gemini_with_key_fallback
    grounded_research: Callable[..., str] = run_grounded_web_research
    call_gemini: Callable[..., Any] = _call_gemini
    gemini_retry_status: Callable[..., str] = gemini_retry_status
    calculate_risk_score: Callable[..., RiskScoreResult] = calculate_risk_score
    estimate_request_tokens: Callable[..., int] = estimate_request_tokens
    estimate_output_tokens: Callable[[str], int] = estimate_output_tokens
    validate_json_contract: Callable[..., list[dict[str, Any]]] = _soft_validate_json_contract
    validate_final_report: Callable[..., list[dict[str, Any]]] = _soft_validate_final_report
    ensure_end_analysis_marker: Callable[[str], str] = _ensure_end_analysis_marker
    write_validation_warnings: Callable[..., str | None] = _write_validation_warnings
    extract_kb_blocks: Callable[[str], list[dict[str, Any]]] = extract_kb_save_blocks


def _sse_event(**payload: Unpack[SSEPayload]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _status_event(message: str) -> str:
    return _sse_event(status=message)


def _error_event(message: str) -> str:
    return _sse_event(error=message)


def _token_event(input_tokens: int, output_tokens: int) -> str:
    return _sse_event(token_usage={"input_tokens": input_tokens, "output_tokens": output_tokens})


def _fold_market_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in normalized if not unicodedata.combining(char)).lower()


def _probable_market_detail_url(value: str) -> bool:
    """Reject search/category pages while accepting common direct-ad shapes."""
    try:
        parsed = urlparse(str(value or "").strip())
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    if host == "vertexaisearch.cloud.google.com" or host.endswith(".vertexaisearch.cloud.google.com"):
        return False
    path = parsed.path.lower()
    if any(fragment in path for fragment in ("/vysledky", "/search", "/category", "/katalog", "/filter")):
        return False
    direct_markers = ("/detail", "/inzerat/", "/offer/", "/offers/", "/auto-inserat/")
    if any(marker in path for marker in direct_markers):
        return True
    marketplace_hosts = (
        "autobazar.sk",
        "bazos.sk",
        "bazos.cz",
        "sauto.cz",
        "tipcars.com",
        "tipcars.sk",
        "mobile.de",
        "autoscout24.com",
    )
    return any(host == item or host.endswith(f".{item}") for item in marketplace_hosts) and bool(path.strip("/"))


def _supported_customer_market_url(value: str) -> bool:
    try:
        host = (urlparse(str(value or "").strip()).hostname or "").lower()
    except ValueError:
        return False
    supported = ("autobazar.eu", "autobazar.sk", "bazos.sk", "bazos.cz")
    return any(host == item or host.endswith(f".{item}") for item in supported)


def _has_linked_market_comparable(
    research_text: str,
    *,
    market_only: bool = False,
    customer_facing_only: bool = False,
) -> bool:
    """Return True when grounded output contains a probable direct ad URL."""
    in_market_section = False
    for line in str(research_text or "").splitlines():
        stripped = line.strip()
        if re.match(r"^#{2,4}\s+", stripped):
            heading = _fold_market_text(re.sub(r"^#{2,4}\s+", "", stripped))
            in_market_section = (
                ("cena" in heading and "trh" in heading)
                or "porovnatelne inzeraty" in heading
                or "comparable ads" in heading
                or "market comparables" in heading
                or "citacie z google search" in heading
            )
            continue
        if not market_only and not in_market_section:
            continue
        for match in re.finditer(r"\[[^\]\n]+\]\((https?://[^\s)]+(?:\([^\s)]*\)[^\s)]*)*)\)", line, re.IGNORECASE):
            url = match.group(1)
            if _probable_market_detail_url(url) and (
                not customer_facing_only or _supported_customer_market_url(url)
            ):
                return True
    return False


def _text_event(chunk: str) -> str:
    return _sse_event(text=chunk)


def _done_event(slug: str, kb_blocks: list[dict[str, Any]], saved_kb: list[dict[str, Any]]) -> str:
    return _sse_event(
        done=True,
        slug=slug,
        has_kb_blocks=bool(kb_blocks),
        saved_kb=saved_kb,
    )


def _read_vin_light_decode(repository: ListingJobRepositoryProtocol, slug: str) -> dict[str, Any]:
    """Load the scraper's local VIN decode without failing the analysis pass."""
    raw_text = repository.read_text(slug, "vin_decoded.json", default="") or ""
    value = {}
    if raw_text:
        try:
            value = json.loads(raw_text)
        except (TypeError, json.JSONDecodeError):
            value = {}
    if not isinstance(value, dict) or not str(value.get("vin") or "").strip():
        # Older/manual jobs may have a VIN in car_info.md but no persisted
        # decoder artifact. Recreate the same local light check cheaply.
        car_info = repository.read_text(slug, "car_info.md", default="") or ""
        try:
            from vin_utils import extract_vin_from_text, validate_vin

            fallback_vin = extract_vin_from_text(car_info)
            value = validate_vin(fallback_vin) if fallback_vin else {}
        except Exception:
            value = {}
    if not isinstance(value, dict) or not str(value.get("vin") or "").strip():
        return {}
    return {
        key: value.get(key)
        for key in (
            "vin",
            "valid",
            "wmi",
            "manufacturer",
            "vds",
            "model_year_code",
            "model_year_candidates",
            "region",
            "plant_hint",
            "check_digit_valid",
            "check_digit_severity",
        )
        if value.get(key) not in (None, "", [], {})
    }


def multi_model_analysis_events(
    slug: str,
    grok_key: str,
    gemini_keys: Sequence[str],
    output_language: str = "sk",
    openrouter_key: str = "",
    *,
    dependencies: AnalysisPipelineDependencies,
) -> Iterator[str]:
    """Run separated text/research, Gemini vision, scoring, and final synthesis."""
    repository = dependencies.repository
    try:
        slug_dir = str(repository.job_dir(slug, require=True))
    except FileNotFoundError:
        yield _error_event("Listing job not found.")
        return

    car_info_path = repository.artifact_path(slug, "car_info.md")
    if not os.path.exists(car_info_path):
        yield _error_event("car_info.md not found.")
        return

    gemini_key_entries = dependencies.normalize_gemini_keys(gemini_keys)
    if not gemini_key_entries:
        yield _error_event("Gemini API keys are not configured on the server.")
        return

    car_info_text = repository.read_text(slug, "car_info.md", default="") or ""
    vin_light_decode = _read_vin_light_decode(repository, slug)
    model_listing_context = dependencies.listing_context_text(car_info_text)
    grounding_listing_context = dependencies.listing_context_text(car_info_text, description_chars=700)
    if vin_light_decode:
        vin_context = dependencies.compact_json_for_prompt(vin_light_decode)
        model_listing_context += f"\n\nVIN_LIGHT_CHECK (local prefix decoder; not a history result):\n{vin_context}"
        grounding_listing_context += f"\n\nVIN_LIGHT_CHECK (local prefix decoder; not a history result):\n{vin_context}"
    validation_warnings = []

    web_research_text = ""
    try:
        yield _status_event("Preparing web research via Gemini Google Search...")
        grounded, _grounding_key = yield from dependencies.collect_gemini(
            gemini_key_entries,
            "web research",
            lambda key: [
                dependencies.grounded_research(
                    key,
                    grounding_listing_context,
                    model=GEMINI_GROUNDING_MODEL,
                    listing_slug=slug,
                )
            ],
            retry_exceptions=(ApiKeyError, RateLimitError, GroundingTransientError),
            same_key_retries=1,
            same_key_retry_exceptions=(GroundingTransientError,),
        )
        if grounded:
            web_research_text = grounded
            if not _has_linked_market_comparable(grounded, customer_facing_only=True):
                yield _status_event("No supported SK/CZ comparable links found; running targeted market search...")
                try:
                    market_grounded, _market_key = yield from dependencies.collect_gemini(
                        gemini_key_entries,
                        "market comparable research",
                        lambda key: [
                            dependencies.grounded_research(
                                key,
                                grounding_listing_context,
                                model=GEMINI_GROUNDING_MODEL,
                                listing_slug=slug,
                                research_mode="market",
                            )
                        ],
                        retry_exceptions=(ApiKeyError, RateLimitError, GroundingTransientError),
                        same_key_retries=0,
                    )
                    if market_grounded:
                        web_research_text = (
                            grounded.rstrip()
                            + "\n\n## Cielene porovnanie trhu\n\n"
                            + market_grounded.strip()
                        )
                    if market_grounded and _has_linked_market_comparable(market_grounded, market_only=True):
                        yield _status_event("Targeted market search found linked comparable ads.")
                    else:
                        yield _status_event("Targeted market search found no directly linked comparable ads.")
                except Exception as market_exc:
                    dependencies.log(f"Targeted market research warning: {market_exc}")
                    yield _status_event("Targeted market search unavailable; continuing with verified research.")
            repository.write_text(slug, "web_research.md", web_research_text)
            yield _status_event("Web research ready for text/research analysis.")
    except Exception as exc:
        dependencies.log(f"Web research warning: {exc}")
        yield _status_event("Web research unavailable; continuing with listing data.")

    if grok_key:
        text_provider = "grok"
        text_api_key = grok_key
    elif openrouter_key:
        text_provider = "openrouter"
        text_api_key = openrouter_key
    else:
        text_provider = "gemini"
        text_api_key = ""
    text_model_name = dependencies.model_display_name(text_provider)

    yield _status_event(f"Phase 1/4: {text_model_name} text and research analysis...")
    text_research_prompt_path = dependencies.prompt_dir / "grok_text_research_system.md"
    if not os.path.exists(text_research_prompt_path):
        yield _error_event("grok_text_research_system.md not found.")
        return
    with open(text_research_prompt_path, "r", encoding="utf-8") as f:
        text_research_system_prompt = f.read()

    text_research_json_text = ""
    text_research_content = dependencies.build_text_research_context(model_listing_context, output_language, web_research_text)
    input_tokens = dependencies.estimate_request_tokens(text_research_system_prompt, text_research_content)
    yield _token_event(input_tokens, 0)
    if text_provider == "gemini":
        text_research_json_text, _text_key = yield from dependencies.collect_gemini(
            gemini_key_entries,
            "text/research analysis",
            lambda key: dependencies.call_gemini(
                key,
                text_research_system_prompt,
                text_research_content,
                image_data_list=None,
                model=GEMINI_TEXT_RESEARCH_MODEL,
                listing_slug=slug,
                phase="text_research",
            ),
        )
    else:
        try:
            for chunk in dependencies.stream_text_model(text_provider, text_api_key, text_research_system_prompt, text_research_content, listing_slug=slug):
                text_research_json_text += chunk
        except (RateLimitError, ConnectionError) as exc:
            if text_provider != "openrouter":
                raise
            dependencies.log(f"OpenRouter text/research failed; falling back to Gemini: {exc}")
            yield _status_event("OpenRouter text/research unavailable; falling back to Gemini.")
            text_provider = "gemini"
            text_api_key = ""
            text_model_name = dependencies.model_display_name(text_provider)
            text_research_json_text, _text_key = yield from dependencies.collect_gemini(
                gemini_key_entries,
                "text/research analysis",
                lambda key: dependencies.call_gemini(
                    key,
                    text_research_system_prompt,
                    text_research_content,
                    image_data_list=None,
                    model=GEMINI_TEXT_RESEARCH_MODEL,
                    listing_slug=slug,
                    phase="text_research",
                ),
            )
    try:
        research_data = dependencies.safe_model_json(text_research_json_text)
        if isinstance(research_data.get("market_comparables"), list):
            comparable_count_before = len(research_data["market_comparables"])
            research_data = deduplicate_market_comparables(research_data, car_info_text)
            comparable_count_after = len(research_data.get("market_comparables") or [])
            text_research_json_text = dependencies.compact_json_for_prompt(research_data)
            if comparable_count_after < comparable_count_before:
                yield _status_event(
                    f"Removed {comparable_count_before - comparable_count_after} duplicate or cross-posted market ad(s)."
                )
    except Exception as comparable_exc:
        dependencies.log(f"Market comparable deduplication warning: {comparable_exc}")
    repository.write_text(slug, "grok_research.json", text_research_json_text)
    validation_warnings.extend(
        dependencies.validate_json_contract(
            "grok_research.json",
            text_research_json_text,
            "grok_research.schema.json",
        )
    )
    yield _status_event(f"{text_model_name} text/research JSON saved.")

    yield _status_event("Phase 2/4: Gemini vision analysis...")
    vision_prompt_path = dependencies.prompt_dir / "gemini_vision_system.md"
    if not os.path.exists(vision_prompt_path):
        yield _error_event("gemini_vision_system.md not found.")
        return
    with open(vision_prompt_path, "r", encoding="utf-8") as f:
        vision_system_prompt = f.read()

    vision_result_json = ""
    image_data_list, _image_meta = dependencies.prepare_images(slug_dir)
    if image_data_list:
        image_payload_context = dependencies.compact_json_for_prompt(_image_meta)
        vision_language = "Slovak" if dependencies.output_language(output_language) == "sk" else "English"
        vision_content = (
            "Analyze only the attached vehicle photos/collages. "
            f"Write all human-readable JSON string values in {vision_language}. "
            "Use listing text only for labels and mileage context.\n"
            "Image payload metadata follows. If full_gallery_included is true, overview sheets cover the full listing gallery; "
            "do not mark a buyer-relevant view as missing from the listing unless it is absent from those overview sheets. "
            "Use 'not assessable in detail' for views visible only in overview thumbnails.\n\n"
            f"IMAGE_PAYLOAD_METADATA:\n{image_payload_context}\n\n"
            f"{model_listing_context}"
        )
        try:
            vision_result_json, _vision_key = yield from dependencies.collect_gemini(
                gemini_key_entries,
                "vision analysis",
                lambda key: dependencies.call_gemini(
                    key,
                    vision_system_prompt,
                    vision_content,
                    image_data_list=image_data_list,
                    model=GEMINI_VISION_MODEL,
                    listing_slug=slug,
                    allow_image_text_fallback=False,
                    phase="vision",
                ),
            )
        except Exception as exc:
            dependencies.log(f"Gemini vision error: {exc}")
            vision_result_json = dependencies.no_photos_vision_result("Fotografie sa nepodarilo spolahlivo analyzovat.")
            yield _status_event("Gemini vision failed; continuing without reliable photo analysis.")
    else:
        vision_result_json = dependencies.no_photos_vision_result()
        yield _status_event("No photos available for Gemini vision.")

    repository.write_text(slug, "gemini_vision.json", vision_result_json)
    validation_warnings.extend(
        dependencies.validate_json_contract(
            "gemini_vision.json",
            vision_result_json,
            "gemini_vision.schema.json",
        )
    )
    yield _status_event("Gemini vision JSON saved.")

    injected_vin_note = dependencies.inject_photo_vin(
        slug_dir, car_info_text, text_research_json_text, vision_result_json,
        car_info_path
    )
    if injected_vin_note:
        car_info_text = repository.read_text(slug, "car_info.md", default="") or ""
        text_research_data = dependencies.safe_model_json(text_research_json_text)
        vision_parsed_for_vin = dependencies.safe_model_json(vision_result_json)
        photo_vin = str(vision_parsed_for_vin.get("visible_vin") or "").strip().upper()
        if not text_research_data.get("_parse_error") and photo_vin:
            if "vin_check" not in text_research_data or not isinstance(text_research_data.get("vin_check"), dict):
                text_research_data["vin_check"] = {}
            try:
                from vin_utils import validate_vin
                decoded = validate_vin(photo_vin)
            except Exception:
                decoded = {}
            text_research_data["vin_check"]["vin_present"] = True
            text_research_data["vin_check"]["format_check"] = "ok" if decoded.get("valid") else "problem"
            text_research_data["vin_check"]["decoded_information"] = decoded.get("validation_message", "")
            text_research_data["vin_check"]["online_history"] = "requires_manual_verification"
            text_research_data["vin_check"]["notes"] = "VIN was not in listing text; found in photos by Gemini vision."
            if "listing_facts" not in text_research_data or not isinstance(text_research_data.get("listing_facts"), dict):
                text_research_data["listing_facts"] = {}
            text_research_data["listing_facts"]["vin"] = photo_vin
            text_research_json_text = dependencies.compact_json_for_prompt(text_research_data)
        vin_light_decode = _read_vin_light_decode(repository, slug)
        yield _status_event(injected_vin_note)

    yield _status_event("Phase 3/4: Backend deterministic risk scoring...")
    risk_score = dependencies.calculate_risk_score(
        text_research_json_text,
        vision_result_json,
        listing_text=car_info_text,
        output_language=dependencies.output_language(output_language),
    )
    risk_score["buyer_scorecard"] = build_buyer_scorecard(
        text_research_json_text,
        vision_result_json,
        risk_score,
    )
    risk_score_json = json.dumps(risk_score, indent=2, ensure_ascii=False)
    repository.write_text(slug, "risk_score.json", risk_score_json)
    validation_warnings.extend(
        dependencies.validate_json_contract(
            "risk_score.json",
            risk_score_json,
            "risk_score.schema.json",
        )
    )
    verdict = risk_score.get("allowed_final_verdict", "unknown")
    yield _status_event(f"Backend risk score saved: {verdict}")

    yield _status_event(f"Phase 4/4: {text_model_name} final synthesis...")
    final_synthesis_prompt_path = dependencies.prompt_dir / "grok_final_synthesis_system.md"
    if not os.path.exists(final_synthesis_prompt_path):
        yield _error_event("grok_final_synthesis_system.md not found.")
        return
    with open(final_synthesis_prompt_path, "r", encoding="utf-8") as f:
        final_system_prompt = f.read()

    final_content = dependencies.build_final_synthesis_context(
        output_language,
        car_info_text,
        text_research_json_text,
        vision_result_json,
        risk_score_json,
        web_research_text,
        _image_meta,
        vin_light_decode,
    )

    full_report = ""
    output_tokens = 0
    next_token_update = 250
    final_input_tokens = dependencies.estimate_request_tokens(final_system_prompt, final_content)
    yield _token_event(final_input_tokens, output_tokens)
    if text_provider == "gemini":
        final_done = False
        for index, entry in enumerate(gemini_key_entries):
            attempt_text = ""
            attempt_output_tokens = 0
            try:
                for chunk in dependencies.call_gemini(
                    entry["key"],
                    final_system_prompt,
                    final_content,
                    image_data_list=None,
                    model=GEMINI_FINAL_MODEL,
                    listing_slug=slug,
                    fallback_models=GEMINI_FINAL_FALLBACK_MODELS,
                    phase="final_synthesis",
                ):
                    attempt_text += chunk
                    attempt_output_tokens += dependencies.estimate_output_tokens(chunk)
                    if chunk:
                        yield _text_event(chunk)
                    if attempt_output_tokens >= next_token_update:
                        yield _token_event(final_input_tokens, attempt_output_tokens)
                        next_token_update += 250
                full_report = attempt_text
                output_tokens = attempt_output_tokens
                final_done = True
                break
            except (ApiKeyError, RateLimitError) as exc:
                if attempt_text or index >= len(gemini_key_entries) - 1:
                    raise
                status = dependencies.gemini_retry_status(entry, gemini_key_entries[index + 1], "final synthesis", exc)
                yield _status_event(status)

        if not final_done:
            raise RateLimitError("Gemini final synthesis failed for all configured API keys.")
    else:
        try:
            for chunk in dependencies.stream_text_model(text_provider, text_api_key, final_system_prompt, final_content, listing_slug=slug):
                full_report += chunk
                output_tokens += dependencies.estimate_output_tokens(chunk)
                if chunk:
                    yield _text_event(chunk)
                if output_tokens >= next_token_update:
                    yield _token_event(final_input_tokens, output_tokens)
                    next_token_update += 250
        except (RateLimitError, ConnectionError) as exc:
            if text_provider != "openrouter" or full_report:
                raise
            dependencies.log(f"OpenRouter final synthesis failed; falling back to Gemini: {exc}")
            yield _status_event("OpenRouter final synthesis unavailable; falling back to Gemini.")
            final_done = False
            for index, entry in enumerate(gemini_key_entries):
                attempt_text = ""
                attempt_output_tokens = 0
                try:
                    for chunk in dependencies.call_gemini(
                        entry["key"],
                        final_system_prompt,
                        final_content,
                        image_data_list=None,
                        model=GEMINI_FINAL_MODEL,
                        listing_slug=slug,
                        fallback_models=GEMINI_FINAL_FALLBACK_MODELS,
                        phase="final_synthesis",
                    ):
                        attempt_text += chunk
                        attempt_output_tokens += dependencies.estimate_output_tokens(chunk)
                        if chunk:
                            yield _text_event(chunk)
                        if attempt_output_tokens >= next_token_update:
                            yield _token_event(final_input_tokens, attempt_output_tokens)
                            next_token_update += 250
                    full_report = attempt_text
                    output_tokens = attempt_output_tokens
                    final_done = True
                    break
                except (ApiKeyError, RateLimitError) as gemini_exc:
                    if attempt_text or index >= len(gemini_key_entries) - 1:
                        raise
                    status = dependencies.gemini_retry_status(entry, gemini_key_entries[index + 1], "final synthesis", gemini_exc)
                    yield _status_event(status)

            if not final_done:
                raise RateLimitError("Gemini final synthesis failed for all configured API keys.")

    repository.write_text(slug, "analysis_result_raw.md", full_report)
    public_text = dependencies.normalize_report_headings(
        dependencies.ensure_end_analysis_marker(
            dependencies.public_analysis_markdown(dependencies.strip_kb_section(full_report), slug_dir)
        )
    )
    public_text = dependencies.replace_photo_analysis_section(public_text, vision_result_json, output_language)
    public_text = dependencies.replace_quick_summary_scorecard(public_text, risk_score_json, output_language)
    public_text = dependencies.move_pros_cons_after_quick_summary(public_text)
    repository.write_text(slug, "analysis_result.md", public_text)
    validation_warnings.extend(dependencies.validate_final_report(public_text, verdict))
    warnings_path = dependencies.write_validation_warnings(
        slug_dir,
        validation_warnings,
        log=dependencies.log,
    )
    if warnings_path:
        yield _status_event(
            f"Analysis completed with {len(validation_warnings)} validation warning(s)."
        )

    kb_blocks = dependencies.extract_kb_blocks(full_report)
    saved_kb = []
    if kb_blocks:
        try:
            saved_kb = dependencies.save_kb_blocks(kb_blocks)
            if saved_kb:
                repository.write_json(
                    slug,
                    "kb_autosave.json",
                    {
                        "saved_at": datetime.now().isoformat(timespec="seconds"),
                        "saved": saved_kb,
                    },
                )
        except Exception as exc:
            dependencies.log(f"KB autosave error: {exc}")

    yield _done_event(slug, kb_blocks, saved_kb)
