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
from scrapper_demo.component_identity import (
    normalize_component_identity,
    parse_first_json_object,
    unknown_component_identity,
)
from scrapper_demo.market_comparables import (
    build_market_benchmark,
    deduplicate_market_comparables,
    fetch_ecb_reference_rates,
    reconcile_market_comparable_urls,
)
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


def _research_parse_failed(value: Any) -> bool:
    return (
        not isinstance(value, dict)
        or value.get("_parse_error") is True
        or ("raw_preview" in value and "source_role" not in value)
    )


def _merge_backend_evidence(
    research: dict[str, Any],
    listing_context: dict[str, Any],
    component_identity: dict[str, Any],
    vin_light_decode: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Lock deterministic listing facts and grounded identity into model output."""
    merged = dict(research)
    merged["component_identity"] = component_identity
    facts = merged.get("listing_facts")
    if not isinstance(facts, dict):
        facts = {}
    else:
        facts = dict(facts)

    canonical = {
        "title": listing_context.get("title"),
        "price": listing_context.get("price"),
        "vin": listing_context.get("vin"),
        "mileage": listing_context.get("mileage"),
        "year": listing_context.get("year"),
        "engine": listing_context.get("engine"),
        "power": listing_context.get("power"),
        "fuel": listing_context.get("fuel"),
        "color": listing_context.get("color"),
        "transmission": listing_context.get("transmission"),
        "drive": listing_context.get("drive"),
    }
    for key, value in canonical.items():
        if value not in (None, "", [], {}):
            facts[key] = str(value) if key == "price" else value
    mileage_km = listing_context.get("mileage_km")
    if isinstance(mileage_km, (int, float)) and mileage_km > 0:
        facts["advertised_mileage_km"] = int(mileage_km)
        if not facts.get("mileage"):
            facts["mileage"] = f"{int(mileage_km)} km"
    merged["listing_facts"] = facts

    local_vin = vin_light_decode if isinstance(vin_light_decode, dict) else {}
    local_vin_value = str(local_vin.get("vin") or "").strip().upper()
    facts_vin_value = str(facts.get("vin") or "").strip().upper()
    local_vin_applies = bool(local_vin_value) and (
        not facts_vin_value or local_vin_value == facts_vin_value
    )
    if local_vin_applies:
        vin_check = merged.get("vin_check")
        vin_check = dict(vin_check) if isinstance(vin_check, dict) else {}
        local_valid = local_vin.get("valid") is True
        vin_check["vin_present"] = True
        vin_check["format_check"] = "ok" if local_valid else "problem"
        vin_check["decoded_information"] = str(
            local_vin.get("validation_message") or "Local VIN format check completed."
        )
        vin_check["local_validation"] = local_vin
        merged["vin_check"] = vin_check

        local_check_is_info = (
            str(local_vin.get("check_digit_severity") or "").lower() == "info"
        )
        local_year_is_ambiguous = local_vin.get("model_year_hint") is None

        def locally_refuted_vin_interpretation(item: Any) -> bool:
            if not isinstance(item, dict):
                return False
            text = _fold_market_text(
                " ".join(
                    str(item.get(key) or "")
                    for key in (
                        "check",
                        "issue",
                        "explanation",
                        "source_a",
                        "source_b",
                    )
                )
            )
            vin_related = "vin" in text or "check digit" in text or "kontroln" in text
            check_digit_related = "check digit" in text or "kontroln" in text
            model_year_related = (
                "model year" in text
                or "modelovy rok" in text
                or "year code" in text
                or "kod roku" in text
            )
            if local_valid and vin_related and (
                check_digit_related or "format" in text
            ) and any(term in text for term in ("invalid", "neplat", "problem")):
                return True
            if local_check_is_info and check_digit_related:
                return True
            return local_year_is_ambiguous and model_year_related and vin_related

        for field in ("consistency_checks", "data_conflicts"):
            values = merged.get(field)
            if isinstance(values, list):
                merged[field] = [
                    item
                    for item in values
                    if not locally_refuted_vin_interpretation(item)
                ]

        checks = merged.get("consistency_checks")
        if not isinstance(checks, list):
            checks = []
        checks.append(
            {
                "check": "Deterministic local VIN format validation",
                "result": "ok" if local_valid else "concern",
                "explanation": vin_check["decoded_information"],
            }
        )
        merged["consistency_checks"] = checks

    known_missing_terms: set[str] = set()
    if facts.get("mileage") or facts.get("advertised_mileage_km"):
        known_missing_terms.update({"mileage", "najazd", "najazdene", "kilomet"})
    if facts.get("year"):
        known_missing_terms.update({"year", "rok", "registr"})
    if facts.get("vin"):
        known_missing_terms.add("vin")
    if facts.get("transmission"):
        known_missing_terms.update({"transmission", "prevodov"})
    if facts.get("engine"):
        known_missing_terms.update({"engine", "motor"})

    if known_missing_terms and isinstance(merged.get("missing_or_uncertain_data"), list):
        filtered: list[Any] = []
        for item in merged["missing_or_uncertain_data"]:
            if not isinstance(item, dict):
                filtered.append(item)
                continue
            normalized = _fold_market_text(item.get("item", ""))
            if any(term in normalized for term in known_missing_terms):
                continue
            filtered.append(item)
        merged["missing_or_uncertain_data"] = filtered
    return merged


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
    if any(fragment in path for fragment in ("/vysledky", "/inzeraty/", "/search", "/category", "/katalog", "/filter")):
        return False
    direct_markers = (
        "/detail",
        "/inzerat/",
        "/offer/",
        "/offers/",
        "/angebote/",
        "/offres/",
        "/annunci/",
        "/auto-inserat/",
        "/fahrzeuge/details.html",
        "/osobowe/oferta/",
    )
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
        "autoscout24.de",
        "autoscout24.at",
        "autoscout24.be",
        "autoscout24.fr",
        "autoscout24.it",
        "otomoto.pl",
    )
    return any(host == item or host.endswith(f".{item}") for item in marketplace_hosts) and bool(path.strip("/"))


def _supported_customer_market_url(value: str) -> bool:
    try:
        host = (urlparse(str(value or "").strip()).hostname or "").lower()
    except ValueError:
        return False
    supported = (
        "autobazar.eu",
        "autobazar.sk",
        "bazos.sk",
        "bazos.cz",
        "sauto.cz",
        "tipcars.com",
    )
    return any(host == item or host.endswith(f".{item}") for item in supported)


def _has_linked_market_comparable(
    research_text: str,
    *,
    market_only: bool = False,
    customer_facing_only: bool = False,
) -> bool:
    """Return True when grounded output contains a probable direct ad URL."""
    return _linked_market_comparable_count(
        research_text,
        market_only=market_only,
        customer_facing_only=customer_facing_only,
    ) > 0


def _linked_market_comparable_count(
    research_text: str,
    *,
    market_only: bool = False,
    customer_facing_only: bool = False,
) -> int:
    """Count unique direct-ad citations, excluding narrative-only stale URLs."""
    in_market_section = False
    in_citation_section = False
    found: set[str] = set()
    for line in str(research_text or "").splitlines():
        stripped = line.strip()
        if re.match(r"^#{2,4}\s+", stripped):
            heading = _fold_market_text(re.sub(r"^#{2,4}\s+", "", stripped))
            in_citation_section = (
                "citacie z google search" in heading
                or "google search citations" in heading
            )
            in_market_section = (
                ("cena" in heading and "trh" in heading)
                or "porovnatelne inzeraty" in heading
                or "comparable ads" in heading
                or "market comparables" in heading
                or "citacie z google search" in heading
            )
            continue
        # Only the grounding citation block is authoritative. Narrative links
        # can contain an expired marketplace ID and are reconciled later.
        if not in_citation_section:
            continue
        for match in re.finditer(r"\[[^\]\n]+\]\((https?://[^\s)]+(?:\([^\s)]*\)[^\s)]*)*)\)", line, re.IGNORECASE):
            url = match.group(1)
            if _probable_market_detail_url(url) and (
                not customer_facing_only or _supported_customer_market_url(url)
            ):
                found.add(url)
    return len(found)


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
    listing_context_data = parse_first_json_object(model_listing_context)
    repository.write_json(slug, "listing_facts.json", listing_context_data)
    grounding_listing_context = dependencies.listing_context_text(car_info_text, description_chars=700)
    if vin_light_decode:
        vin_context = dependencies.compact_json_for_prompt(vin_light_decode)
        model_listing_context += f"\n\nVIN_LIGHT_CHECK (local prefix decoder; not a history result):\n{vin_context}"
        grounding_listing_context += f"\n\nVIN_LIGHT_CHECK (local prefix decoder; not a history result):\n{vin_context}"
    validation_warnings = []

    component_identity = unknown_component_identity(
        "Grounded component identification was unavailable."
    )
    try:
        yield _status_event("Identifying generation, engine, transmission, and drivetrain...")
        identity_grounded, _identity_key = yield from dependencies.collect_gemini(
            gemini_key_entries,
            "component identity research",
            lambda key: [
                dependencies.grounded_research(
                    key,
                    grounding_listing_context,
                    model=GEMINI_GROUNDING_MODEL,
                    listing_slug=slug,
                    research_mode="identity",
                )
            ],
            retry_exceptions=(ApiKeyError, RateLimitError, GroundingTransientError),
            same_key_retries=1,
            same_key_retry_exceptions=(GroundingTransientError,),
        )
        repository.write_text(
            slug, "component_identity_research.md", identity_grounded
        )
        component_identity = normalize_component_identity(identity_grounded)
        status = component_identity.get("identification_status", "UNKNOWN")
        yield _status_event(f"Component identification saved: {status}.")
    except Exception as identity_exc:
        dependencies.log(f"Component identity research warning: {identity_exc}")
        yield _status_event(
            "Component identification unavailable; continuing without guessing exact codes."
        )
    component_identity_json = json.dumps(
        component_identity, indent=2, ensure_ascii=False
    )
    repository.write_text(slug, "component_identity.json", component_identity_json)
    validation_warnings.extend(
        dependencies.validate_json_contract(
            "component_identity.json",
            component_identity_json,
            "component_identity.schema.json",
        )
    )
    identity_context = dependencies.compact_json_for_prompt(component_identity)
    grounded_listing_with_identity = (
        grounding_listing_context
        + "\n\nCOMPONENT_IDENTITY (grounded candidate; preserve resolution):\n"
        + identity_context
    )

    web_research_text = ""
    try:
        yield _status_event("Preparing web research via Gemini Google Search...")
        grounded, _grounding_key = yield from dependencies.collect_gemini(
            gemini_key_entries,
            "web research",
            lambda key: [
                dependencies.grounded_research(
                    key,
                    grounded_listing_with_identity,
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
            repository.write_text(slug, "reliability_research.md", grounded)
            linked_count = _linked_market_comparable_count(grounded, market_only=True)
            public_link_count = _linked_market_comparable_count(
                grounded, customer_facing_only=True
            )
            background_link_count = linked_count - public_link_count
            if linked_count < 3 or public_link_count == 0 or background_link_count == 0:
                yield _status_event(
                    "Building a broader SK/CZ and background EU market sample..."
                )
                try:
                    market_grounded, _market_key = yield from dependencies.collect_gemini(
                        gemini_key_entries,
                        "market comparable research",
                        lambda key: [
                            dependencies.grounded_research(
                                key,
                                grounded_listing_with_identity,
                                model=GEMINI_GROUNDING_MODEL,
                                listing_slug=slug,
                                research_mode="market",
                            )
                        ],
                        retry_exceptions=(ApiKeyError, RateLimitError, GroundingTransientError),
                        same_key_retries=0,
                    )
                    if market_grounded:
                        repository.write_text(slug, "market_research.md", market_grounded)
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
    text_research_content = dependencies.build_text_research_context(
        model_listing_context,
        output_language,
        web_research_text,
        component_identity,
    )
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
    except Exception:
        research_data = {"_parse_error": True}
    if _research_parse_failed(research_data):
        yield _status_event("Text/research JSON was incomplete; retrying once with a compact recovery response...")
        recovery_content = (
            text_research_content
            + "\n\nRECOVERY REQUIREMENT: The previous response was incomplete JSON. "
            "Regenerate the complete schema from the supplied evidence. Be concise: use at most "
            "4 technical risks, 4 web findings, 4 comparables, 6 expected costs, and short strings. "
            "Return one complete JSON object and close every array/object."
        )
        try:
            text_research_json_text, _text_key = yield from dependencies.collect_gemini(
                gemini_key_entries,
                "text/research JSON recovery",
                lambda key: dependencies.call_gemini(
                    key,
                    text_research_system_prompt,
                    recovery_content,
                    image_data_list=None,
                    model=GEMINI_TEXT_RESEARCH_MODEL,
                    listing_slug=slug,
                    phase="text_research",
                ),
            )
            research_data = dependencies.safe_model_json(text_research_json_text)
        except Exception as recovery_exc:
            dependencies.log(f"Text/research JSON recovery failed: {recovery_exc}")
            research_data = {"_parse_error": True}
        if _research_parse_failed(research_data):
            repository.write_text(slug, "grok_research.json", text_research_json_text)
            yield _error_event(
                "Text/research analysis returned incomplete JSON twice. Analysis stopped before creating an unreliable report."
            )
            return
    research_data = _merge_backend_evidence(
        research_data,
        listing_context_data,
        component_identity,
        vin_light_decode,
    )
    try:
        if isinstance(research_data.get("market_comparables"), list):
            comparable_count_before = len(research_data["market_comparables"])
            research_data = reconcile_market_comparable_urls(research_data, web_research_text)
            research_data = deduplicate_market_comparables(research_data, car_info_text)
            try:
                exchange_rates = fetch_ecb_reference_rates()
            except Exception as rate_exc:
                dependencies.log(f"ECB exchange-rate warning: {rate_exc}")
                exchange_rates = {}
            market_benchmark = build_market_benchmark(
                research_data,
                car_info_text,
                exchange_rates=exchange_rates,
            )
            repository.write_json(slug, "market_benchmark.json", market_benchmark)
            validation_warnings.extend(
                dependencies.validate_json_contract(
                    "market_benchmark.json",
                    json.dumps(market_benchmark, ensure_ascii=False),
                    "market_benchmark.schema.json",
                )
            )
            comparable_count_after = len(research_data.get("market_comparables") or [])
            text_research_json_text = dependencies.compact_json_for_prompt(research_data)
            if comparable_count_after < comparable_count_before:
                yield _status_event(
                    f"Removed {comparable_count_before - comparable_count_after} invalid, duplicate, or cross-posted market ad(s)."
                )
    except Exception as comparable_exc:
        dependencies.log(f"Market comparable deduplication warning: {comparable_exc}")
    text_research_json_text = dependencies.compact_json_for_prompt(research_data)
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
