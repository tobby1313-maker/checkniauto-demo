from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from urllib.parse import urlparse
from collections.abc import Callable, Iterator, Mapping, Sequence
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
    ModelOutputLimitError,
    RateLimitError,
)
from scrapper_demo.ai_policy import (
    BudgetResult,
    analysis_profile,
    check_and_compact_input,
    get_phase_policy,
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
from scrapper_demo.direct_market_search import search_all_marketplaces
from scrapper_demo.market_comparables import (
    build_market_benchmark,
    build_market_search_results,
    deduplicate_market_comparables,
    extract_grounded_market_search_pass,
    fetch_ecb_reference_rates,
    is_customer_facing_market_comparable,
)
from scrapper_demo.validation import (
    _ensure_end_analysis_marker,
    _soft_validate_final_report,
    _soft_validate_json_contract,
    _write_validation_warnings,
)
from token_tracker import (
    analysis_run_context,
    current_tracking_value,
    default_tracker,
    estimate_output_tokens,
    estimate_request_tokens,
    new_analysis_run_id,
    tracking_context,
)


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
    direct_market_search: Callable[[dict[str, Any]], list[dict[str, Any]]] = search_all_marketplaces
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
    count_input_tokens: Callable[..., tuple[int, str]] | None = None


def _sse_event(**payload: Unpack[SSEPayload]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _status_event(message: str) -> str:
    return _sse_event(status=message)


def _error_event(message: str) -> str:
    return _sse_event(error=message)


def _token_event(input_tokens: int, output_tokens: int) -> str:
    return _sse_event(token_usage={"input_tokens": input_tokens, "output_tokens": output_tokens})


def _selected_key_label(entry: GeminiKeyEntry | None) -> str:
    return entry["label"] if entry else ""


def _promote_selected_key(
    entries: list[GeminiKeyEntry], selected: GeminiKeyEntry | None
) -> None:
    """Reuse the key that just succeeded before probing a known-limited key."""
    if selected is None:
        return
    for index, entry in enumerate(entries):
        if entry.get("key") == selected.get("key"):
            if index:
                entries.insert(0, entries.pop(index))
            return


def _research_parse_failed(value: Any) -> bool:
    return (
        not isinstance(value, dict)
        or value.get("_parse_error") is True
        or ("raw_preview" in value and "source_role" not in value)
    )


RESEARCH_V2_ARRAY_LIMITS = {
    "seller_claims": 3,
    "missing_or_uncertain_data": 3,
    "data_conflicts": 2,
    "consistency_checks": 3,
    "web_research_findings": 3,
    "technical_risks": 3,
    "expected_costs": 3,
    "text_research_risk_flags": 2,
    "sources_used": 5,
}
RESEARCH_V2_REQUIRED_FIELDS = {
    "schema_version",
    "source_role",
    "evidence_summary",
    "safety_and_recall",
    *RESEARCH_V2_ARRAY_LIMITS,
}


def _research_v2_response_schema(prompt_dir: Path) -> dict[str, Any]:
    """Return a low-state Gemini serving schema; backend validation stays strict."""
    reference_shapes = {
        "#/$defs/evidence_category": {"type": "string"},
        "#/$defs/confidence": {"type": "string"},
        "#/$defs/source_ids": {"type": "array", "items": {"type": "string"}},
    }

    def serving_subset(value: Any) -> Any:
        if isinstance(value, dict):
            if value.get("$ref") in reference_shapes:
                return dict(reference_shapes[value["$ref"]])
            normalized: dict[str, Any] = {}
            for key, child in value.items():
                if key in {
                    "$schema", "$defs", "title", "description", "maxItems", "minItems",
                    "minimum", "maximum", "enum",
                }:
                    continue
                if key == "const":
                    normalized["enum"] = [child]
                else:
                    normalized[key] = serving_subset(child)
            return normalized
        if isinstance(value, list):
            return [serving_subset(item) for item in value]
        return value

    candidates = (
        prompt_dir.parent / "schemas" / "research_model_output.schema.json",
        Path(__file__).resolve().parents[2] / "schemas" / "research_model_output.schema.json",
    )
    for candidate in candidates:
        try:
            schema = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(schema, dict):
            return serving_subset(schema)
    raise FileNotFoundError("research_model_output.schema.json not found")


RESEARCH_V2_NESTED_REQUIRED_FIELDS = {
    "evidence_summary": {
        "data_completeness_score", "overall_confidence", "strongest_evidence", "weakest_evidence"
    },
    "safety_and_recall": {
        "status", "summary", "required_action", "evidence_category", "source_ids"
    },
    "seller_claims": {"claim", "evidence_category", "verification_status", "buyer_relevance"},
    "missing_or_uncertain_data": {"item", "why_it_matters", "severity"},
    "data_conflicts": {"issue", "source_a", "source_b", "interpretation", "importance"},
    "consistency_checks": {"check", "result", "explanation"},
    "web_research_findings": {
        "claim", "evidence_category", "buyer_impact", "confidence", "source_ids"
    },
    "technical_risks": {
        "component", "issue", "risk_level", "evidence_category", "buyer_impact",
        "specific_vehicle_evidence", "verification_action", "estimated_cost_eur_low",
        "estimated_cost_eur_high", "confidence", "source_ids"
    },
    "expected_costs": {
        "item", "why", "estimated_cost_eur_low", "estimated_cost_eur_high",
        "cost_type", "urgency", "basis", "source_ids"
    },
    "text_research_risk_flags": {
        "risk", "why_it_matters_to_buyer", "evidence", "confidence"
    },
    "sources_used": {
        "source_id", "source_name", "source_type", "reliability",
        "source_url", "verified_url", "used_for"
    },
}

RESEARCH_V2_STRING_FIELDS = {
    "seller_claims": {"claim", "evidence_category", "verification_status", "buyer_relevance"},
    "missing_or_uncertain_data": {"item", "why_it_matters", "severity"},
    "data_conflicts": {"issue", "source_a", "source_b", "interpretation", "importance"},
    "consistency_checks": {"check", "result", "explanation"},
    "web_research_findings": {"claim", "evidence_category", "buyer_impact", "confidence"},
    "technical_risks": {
        "component", "issue", "risk_level", "evidence_category", "buyer_impact",
        "specific_vehicle_evidence", "verification_action", "confidence",
    },
    "expected_costs": {"item", "why", "cost_type", "urgency", "basis"},
    "text_research_risk_flags": {"risk", "why_it_matters_to_buyer", "evidence", "confidence"},
    "sources_used": {
        "source_id", "source_name", "source_type", "reliability", "source_url", "used_for",
    },
}

RESEARCH_V2_ENUMS = {
    "overall_confidence": {"LOW", "MEDIUM", "HIGH"},
    "evidence_category": {
        "CONFIRMED", "LISTING_CLAIM", "VISUAL_INDICATION",
        "MODEL_LEVEL_RISK", "NEEDS_VERIFICATION",
    },
    "severity": {"low", "medium", "high"},
    "importance": {"LOW", "MEDIUM", "HIGH"},
    "check_result": {"ok", "concern", "unknown"},
    "recall_status": {
        "NO_RELEVANT_CAMPAIGN_FOUND", "CAMPAIGN_CONFIRMED_COMPLETED",
        "POSSIBLE_CAMPAIGN_NEEDS_VIN_CHECK", "OPEN_CAMPAIGN", "INSUFFICIENT_DATA",
    },
    "risk_level": {"HIGH", "MEDIUM", "CHECK"},
    "confidence": {"Vysoka", "Stredna", "Nizka"},
    "cost_type": {"initial_service", "diagnostic", "conditional_repair", "major_downside"},
    "urgency": {"low", "medium", "high", "critical"},
    "source_type": {
        "OFFICIAL", "REGULATORY", "LISTING", "VEHICLE_HISTORY",
        "MARKET_COMPARABLE", "TECHNICAL_PUBLICATION", "REPAIR_SOURCE",
        "OWNER_REPORT", "OTHER",
    },
    "reliability": {"HIGH", "MEDIUM", "LOW"},
}


def _normalize_research_model_output(value: Any) -> Any:
    """Normalize known provider aliases without accepting unknown enum values."""
    if not isinstance(value, dict):
        return value
    packet = json.loads(json.dumps(value, ensure_ascii=False))

    def alias(raw: Any, aliases: Mapping[str, str], *, upper: bool = True) -> Any:
        text = str(raw or "").strip()
        key = text.upper() if upper else text.lower()
        return aliases.get(key, text)

    evidence_aliases = {
        "SELLER_CLAIM": "LISTING_CLAIM",
        "MODEL_LEVEL_ISSUE": "MODEL_LEVEL_RISK",
        "MODEL_LEVEL_MAINTENANCE": "MODEL_LEVEL_RISK",
        "MODEL_RISK": "MODEL_LEVEL_RISK",
    }
    confidence_aliases = {
        "HIGH": "Vysoka", "VYSOKA": "Vysoka",
        "MEDIUM": "Stredna", "STREDNA": "Stredna",
        "LOW": "Nizka", "NIZKA": "Nizka",
    }
    cost_aliases = {
        "MANDATORY_INSPECTION_AND_SERVICE": "initial_service",
        "INITIAL_MAINTENANCE": "initial_service",
        "INSPECTION": "diagnostic",
        "DIAGNOSTICS": "diagnostic",
        "POTENTIAL_REPAIR": "conditional_repair",
        "CONDITIONAL": "conditional_repair",
        "MAJOR_REPAIR": "major_downside",
    }
    source_aliases = {
        "PARTS_CATALOG": "OTHER",
        "PARTS_RETAILER": "OTHER",
        "OWNER_FORUM": "OWNER_REPORT",
        "FORUM": "OWNER_REPORT",
    }
    urgency_aliases = {"IMMEDIATE": "high", "URGENT": "critical"}
    check_aliases = {
        "CONSISTENT": "ok",
        "MATCH": "ok",
        "POTENTIALLY_INCONSISTENT": "concern",
        "INCONSISTENT": "concern",
        "MISMATCH": "concern",
        "NOT_CHECKED": "unknown",
    }

    summary = packet.get("evidence_summary")
    if isinstance(summary, dict):
        summary["overall_confidence"] = str(summary.get("overall_confidence") or "").upper()
    safety = packet.get("safety_and_recall")
    if isinstance(safety, dict):
        safety["status"] = str(safety.get("status") or "").upper()
        safety["evidence_category"] = alias(safety.get("evidence_category"), evidence_aliases)
    for field in ("seller_claims", "web_research_findings", "technical_risks"):
        for item in packet.get(field) if isinstance(packet.get(field), list) else []:
            if isinstance(item, dict):
                item["evidence_category"] = alias(item.get("evidence_category"), evidence_aliases)
    for item in packet.get("missing_or_uncertain_data") if isinstance(packet.get("missing_or_uncertain_data"), list) else []:
        if isinstance(item, dict):
            item["severity"] = str(item.get("severity") or "").lower()
    for item in packet.get("data_conflicts") if isinstance(packet.get("data_conflicts"), list) else []:
        if isinstance(item, dict):
            item["importance"] = str(item.get("importance") or "").upper()
    for item in packet.get("consistency_checks") if isinstance(packet.get("consistency_checks"), list) else []:
        if isinstance(item, dict):
            item["result"] = alias(item.get("result"), check_aliases).lower()
    for field in ("web_research_findings", "technical_risks", "text_research_risk_flags"):
        for item in packet.get(field) if isinstance(packet.get(field), list) else []:
            if isinstance(item, dict):
                item["confidence"] = alias(item.get("confidence"), confidence_aliases)
    for item in packet.get("technical_risks") if isinstance(packet.get("technical_risks"), list) else []:
        if isinstance(item, dict):
            item["risk_level"] = str(item.get("risk_level") or "").upper()
    for item in packet.get("expected_costs") if isinstance(packet.get("expected_costs"), list) else []:
        if isinstance(item, dict):
            item["cost_type"] = alias(item.get("cost_type"), cost_aliases)
            item["urgency"] = alias(item.get("urgency"), urgency_aliases).lower()
    for item in packet.get("sources_used") if isinstance(packet.get("sources_used"), list) else []:
        if isinstance(item, dict):
            original_type = str(item.get("source_type") or "").upper()
            item["source_type"] = source_aliases.get(original_type, original_type)
            item["reliability"] = str(item.get("reliability") or "").upper()
            if original_type in {"PARTS_CATALOG", "PARTS_RETAILER"}:
                item["reliability"] = "LOW"
    for field, limit in RESEARCH_V2_ARRAY_LIMITS.items():
        if field != "sources_used" and isinstance(packet.get(field), list):
            packet[field] = packet[field][:limit]
    return packet


_SOURCE_MATCH_STOPWORDS = {
    "about", "after", "auto", "buyer", "component", "engine", "issue", "model",
    "motor", "naklady", "potential", "problem", "repair", "risk", "system",
    "technical", "vehicle", "vozidlo", "known", "regular", "generation",
    "volkswagen", "tiguan", "transmission", "gearbox", "prevodovka", "pohon",
    "chassis", "suspension", "karoseria", "podvozok",
}


def _source_topic_matches(source: Mapping[str, Any], claim_text: str) -> bool:
    def tokens(text: Any) -> set[str]:
        return {
            token for token in re.findall(r"[a-z0-9]+", _fold_market_text(str(text or "")))
            if len(token) >= 4
            and token not in _SOURCE_MATCH_STOPWORDS
            and not any(char.isdigit() for char in token)
        }

    claim_tokens = tokens(claim_text)
    used_for_tokens = tokens(source.get("used_for"))
    return len(claim_tokens & used_for_tokens) >= 2


def _contains_fixed_service_interval(value: Any) -> bool:
    text = _fold_market_text(str(value or ""))
    return bool(
        re.search(r"\b\d[\d\s.,]*(?:km|kilomet|mile|mesiac|month|rok|year)s?\b", text)
        and any(term in text for term in ("interval", "every", "kazd", "menen", "change", "service"))
    )


def _enforce_research_source_policy(packet: dict[str, Any]) -> dict[str, Any]:
    """Keep only claims backed by an existing, verified, topic-matched source."""
    sources = [item for item in packet.get("sources_used") or [] if isinstance(item, dict)]
    source_map = {
        str(item.get("source_id") or "").strip(): item
        for item in sources
        if str(item.get("source_id") or "").strip()
    }
    evidence_source_types = {
        "OFFICIAL", "REGULATORY", "VEHICLE_HISTORY", "TECHNICAL_PUBLICATION",
        "REPAIR_SOURCE", "OWNER_REPORT",
    }

    def supported_ids(item: Mapping[str, Any], text_key: str) -> list[str]:
        claim_text = str(item.get(text_key) or "")
        policy_text = " ".join(
            str(item.get(key) or "")
            for key in (
                text_key, "buyer_impact", "verification_action", "why", "basis"
            )
        )
        fixed_interval = _contains_fixed_service_interval(policy_text)
        accepted: list[str] = []
        for raw_id in item.get("source_ids") if isinstance(item.get("source_ids"), list) else []:
            source_id = str(raw_id or "").strip()
            source = source_map.get(source_id)
            if not source or source.get("verified_url") is not True:
                continue
            if not str(source.get("source_url") or "").startswith(("http://", "https://")):
                continue
            source_type = str(source.get("source_type") or "").upper()
            if source_type not in evidence_source_types:
                continue
            if fixed_interval and source_type not in {"OFFICIAL", "REGULATORY"}:
                continue
            if not _source_topic_matches(source, claim_text):
                continue
            if source_id not in accepted:
                accepted.append(source_id)
        return accepted[:3]

    for field, text_key in (
        ("web_research_findings", "claim"),
        ("technical_risks", "issue"),
        ("expected_costs", "item"),
    ):
        filtered: list[dict[str, Any]] = []
        for raw in packet.get(field) if isinstance(packet.get(field), list) else []:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            item["source_ids"] = supported_ids(item, text_key)
            if not item["source_ids"]:
                continue
            if item.get("confidence") == "Vysoka" and not any(
                str(source_map[source_id].get("source_type") or "").upper()
                in {"OFFICIAL", "REGULATORY"}
                for source_id in item["source_ids"]
            ):
                item["confidence"] = "Stredna"
            filtered.append(item)
        packet[field] = filtered[:RESEARCH_V2_ARRAY_LIMITS[field]]

    safety = packet.get("safety_and_recall")
    if isinstance(safety, dict):
        safety_ids = [
            str(source_id) for source_id in safety.get("source_ids") or []
            if str(source_id) in source_map
            and source_map[str(source_id)].get("verified_url") is True
            and str(source_map[str(source_id)].get("source_type") or "").upper()
            in {"OFFICIAL", "REGULATORY"}
        ]
        safety["source_ids"] = safety_ids[:3]
        if safety.get("status") != "INSUFFICIENT_DATA" and not safety["source_ids"]:
            safety.update({
                "status": "INSUFFICIENT_DATA",
                "summary": "No authoritative recall conclusion was available.",
                "required_action": "Verify campaigns manually with the VIN.",
                "evidence_category": "NEEDS_VERIFICATION",
                "source_ids": [],
            })

    referenced = {
        str(source_id)
        for field in ("web_research_findings", "technical_risks", "expected_costs")
        for item in packet.get(field) or []
        for source_id in item.get("source_ids") or []
    }
    referenced.update(
        str(source_id)
        for source_id in (safety.get("source_ids", []) if isinstance(safety, dict) else [])
    )
    packet["sources_used"] = [
        item for item in sources if str(item.get("source_id") or "") in referenced
    ][:RESEARCH_V2_ARRAY_LIMITS["sources_used"]]
    return packet


def _research_contract_diagnostics(before: Any, after: Any, *, attempt: str) -> dict[str, Any]:
    before_map = before if isinstance(before, dict) else {}
    after_map = after if isinstance(after, dict) else {}
    tracked_fields = (
        "web_research_findings", "technical_risks", "expected_costs", "sources_used"
    )
    return {
        "attempt": attempt,
        "normalized_or_filtered": before_map != after_map,
        "removed_counts": {
            field: max(
                0,
                len(before_map.get(field) or []) - len(after_map.get(field) or []),
            )
            for field in tracked_fields
        },
    }


def _valid_research_model_output(value: Any) -> bool:
    """Validate the strict top-level Research V2 contract before canonical merge."""
    if (
        not isinstance(value, dict)
        or value.get("_parse_error") is True
        or value.get("schema_version") != 2
        or value.get("source_role") != "research_model_output"
        or set(value) != RESEARCH_V2_REQUIRED_FIELDS
        or not isinstance(value.get("evidence_summary"), dict)
        or not isinstance(value.get("safety_and_recall"), dict)
    ):
        return False
    if any(
        set(value[field]) != RESEARCH_V2_NESTED_REQUIRED_FIELDS[field]
        for field in ("evidence_summary", "safety_and_recall")
    ):
        return False
    if not all(
        isinstance(value.get(field), list) and len(value[field]) <= limit
        for field, limit in RESEARCH_V2_ARRAY_LIMITS.items()
    ):
        return False
    if not all(
        isinstance(item, dict)
        and set(item) == RESEARCH_V2_NESTED_REQUIRED_FIELDS[field]
        for field in RESEARCH_V2_ARRAY_LIMITS
        for item in value[field]
    ):
        return False

    summary = value["evidence_summary"]
    score = summary.get("data_completeness_score")
    if (
        not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 100
        or summary.get("overall_confidence") not in RESEARCH_V2_ENUMS["overall_confidence"]
        or not all(
            isinstance(summary.get(field), list)
            and len(summary[field]) <= 3
            and all(isinstance(item, str) for item in summary[field])
            for field in ("strongest_evidence", "weakest_evidence")
        )
    ):
        return False

    safety = value["safety_and_recall"]
    if (
        safety.get("status") not in RESEARCH_V2_ENUMS["recall_status"]
        or safety.get("evidence_category") not in RESEARCH_V2_ENUMS["evidence_category"]
        or not all(isinstance(safety.get(field), str) for field in ("summary", "required_action"))
        or not isinstance(safety.get("source_ids"), list)
        or len(safety["source_ids"]) > 3
        or not all(isinstance(item, str) for item in safety["source_ids"])
    ):
        return False

    enum_checks = (
        ("seller_claims", "evidence_category", "evidence_category"),
        ("missing_or_uncertain_data", "severity", "severity"),
        ("data_conflicts", "importance", "importance"),
        ("consistency_checks", "result", "check_result"),
        ("web_research_findings", "evidence_category", "evidence_category"),
        ("web_research_findings", "confidence", "confidence"),
        ("technical_risks", "risk_level", "risk_level"),
        ("technical_risks", "evidence_category", "evidence_category"),
        ("technical_risks", "confidence", "confidence"),
        ("expected_costs", "cost_type", "cost_type"),
        ("expected_costs", "urgency", "urgency"),
        ("text_research_risk_flags", "confidence", "confidence"),
        ("sources_used", "source_type", "source_type"),
        ("sources_used", "reliability", "reliability"),
    )
    if any(
        item.get(key) not in RESEARCH_V2_ENUMS[enum_name]
        for field, key, enum_name in enum_checks
        for item in value[field]
    ):
        return False
    if any(
        not isinstance(item.get(key), str)
        for field, keys in RESEARCH_V2_STRING_FIELDS.items()
        for item in value[field]
        for key in keys
    ):
        return False

    if any(
        not isinstance(item.get("source_ids"), list)
        or len(item["source_ids"]) > 3
        or not all(isinstance(source_id, str) for source_id in item["source_ids"])
        for field in ("web_research_findings", "technical_risks", "expected_costs")
        for item in value[field]
    ):
        return False

    for field in ("technical_risks", "expected_costs"):
        for item in value[field]:
            low = item.get("estimated_cost_eur_low")
            high = item.get("estimated_cost_eur_high")
            if any(
                amount is not None
                and (not isinstance(amount, (int, float)) or isinstance(amount, bool) or amount < 0)
                for amount in (low, high)
            ):
                return False
            if low is not None and high is not None and low > high:
                return False
    return all(
        isinstance(item.get("verified_url"), bool)
        and isinstance(item.get("source_url"), str)
        and isinstance(item.get("source_id"), str)
        for item in value["sources_used"]
    )


def _unavailable_research_model_output(
    reason: str = "Research model output was unavailable.",
) -> dict[str, Any]:
    """Return a schema-valid, claim-free Research V2 fallback."""
    message = str(reason or "Research model output was unavailable.")[:300]
    return {
        "schema_version": 2,
        "source_role": "research_model_output",
        "evidence_summary": {
            "data_completeness_score": 0,
            "overall_confidence": "LOW",
            "strongest_evidence": [],
            "weakest_evidence": [message],
        },
        "seller_claims": [],
        "missing_or_uncertain_data": [
            {
                "item": "Automatic technical research",
                "why_it_matters": message,
                "severity": "high",
            }
        ],
        "data_conflicts": [],
        "consistency_checks": [],
        "safety_and_recall": {
            "status": "INSUFFICIENT_DATA",
            "summary": "Automatic recall research was unavailable.",
            "required_action": "Verify campaigns manually with the VIN.",
            "evidence_category": "NEEDS_VERIFICATION",
            "source_ids": [],
        },
        "web_research_findings": [],
        "technical_risks": [],
        "expected_costs": [],
        "text_research_risk_flags": [],
        "sources_used": [],
    }


def _canonical_research_from_v2(
    packet: dict[str, Any],
    listing_context: dict[str, Any],
    component_identity: dict[str, Any],
    vin_light_decode: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge model-owned Research V2 fields with backend-owned canonical data."""
    fallback_unavailable = (
        packet.get("evidence_summary", {}).get("data_completeness_score") == 0
        and not packet.get("web_research_findings")
    )
    supported_technical_evidence = any(
        packet.get(field)
        for field in ("web_research_findings", "technical_risks", "expected_costs")
    )
    canonical = {
        "source_role": "text_research",
        "research_packet_schema_version": 2,
        "research_status": (
            "unavailable" if fallback_unavailable
            else "completed" if supported_technical_evidence
            else "limited"
        ),
        "component_identity": {},
        "evidence_summary": dict(packet.get("evidence_summary") or {}),
        "listing_facts": {},
        "seller_claims": list(packet.get("seller_claims") or []),
        "missing_or_uncertain_data": list(packet.get("missing_or_uncertain_data") or []),
        "data_conflicts": list(packet.get("data_conflicts") or []),
        "consistency_checks": list(packet.get("consistency_checks") or []),
        "vin_check": {},
        "safety_and_recall": dict(packet.get("safety_and_recall") or {}),
        "knowledge_base_findings": [],
        "web_research_findings": list(packet.get("web_research_findings") or []),
        "technical_risks": list(packet.get("technical_risks") or []),
        "market_assessment": {
            "available": False,
            "benchmark_available": False,
            "benchmark_confidence": "LOW",
            "benchmark_scope": "EU_MIXED_BACKGROUND",
            "summary": "Aktuálne porovnanie trhu vyžaduje manuálne online overenie.",
            "price_view": "requires_manual_verification",
        },
        "market_comparables": [],
        "expected_costs": list(packet.get("expected_costs") or []),
        "text_research_risk_flags": list(packet.get("text_research_risk_flags") or []),
        "sources_used": list(packet.get("sources_used") or []),
    }
    return _merge_backend_evidence(
        canonical,
        listing_context,
        component_identity,
        vin_light_decode,
    )


def _budget_diagnostics(result: BudgetResult) -> dict[str, Any]:
    return {
        "pre_tokens": result.pre_tokens,
        "post_tokens": result.post_tokens,
        "max_input_tokens": result.max_input_tokens,
        "within_budget": result.within_budget,
        "counting_method": result.counting_method,
        "applied_compactions": list(result.applied_compactions),
        "warnings": list(result.warnings),
    }


def _policy_diagnostics(policy: Any) -> dict[str, Any]:
    return {
        "max_input_tokens": policy.max_input_tokens,
        "max_output_tokens": policy.max_output_tokens,
        "visible_target_tokens": policy.visible_target_tokens,
        "temperature": policy.temperature,
        "thinking_mode": policy.thinking_mode,
        "max_attempts": policy.max_attempts,
    }


VISION_REQUIRED_FIELDS = {
    "source_role",
    "photos_provided",
    "photo_limitations",
    "exterior_observations",
    "interior_observations",
    "dashboard_or_warning_lights",
    "visible_red_flags",
    "mileage_wear_consistency",
    "visual_verdict",
    "must_not_infer",
}


def _valid_vision_payload(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get("_parse_error") is not True
        and VISION_REQUIRED_FIELDS <= set(value)
    )


def _unavailable_vision_payload(
    image_meta: Any,
    *,
    output_language: str = "sk",
    reason: str = "Automatic visual analysis did not return valid structured data.",
) -> dict[str, Any]:
    """Represent provider failure without pretending that listing photos are absent."""
    meta = image_meta if isinstance(image_meta, dict) else {}
    original_count = int(meta.get("original_count") or 0)
    analyzed_count = int(meta.get("selected_count") or original_count)
    message = (
        "Fotografie boli poskytnuté, ale automatická vizuálna analýza nevrátila platné štruktúrované dáta."
        if output_language == "sk"
        else "Photos were provided, but automatic visual analysis did not return valid structured data."
    )
    return {
        "source_role": "vision",
        "analysis_status": "unavailable",
        "photos_provided": True,
        "photo_coverage": {
            "coverage_mode": str(meta.get("coverage_mode") or "detail_limited"),
            "original_count": original_count,
            "analyzed_count": analyzed_count,
            "full_gallery_overview": bool(meta.get("full_gallery_included")),
            "notes": [message, str(reason)[:300]],
        },
        "view_coverage": {
            key: "unknown"
            for key in ("exterior", "interior", "dashboard", "engine_bay", "tires", "underbody")
        },
        "supported_observations": [],
        "missing_views": [],
        "photo_limitations": [message],
        "exterior_observations": [],
        "interior_observations": [],
        "dashboard_or_warning_lights": [],
        "visible_red_flags": [],
        "mileage_wear_consistency": {
            "assessment": "cannot_assess",
            "explanation": message,
            "confidence": "Nízka",
        },
        "visual_verdict": message,
        "visible_vin": "",
        "must_not_infer": [
            "accident history",
            "service history",
            "hidden defects",
            "odometer fraud",
            "market price",
            "overall buying verdict",
        ],
    }


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
        "asking_price_gross_eur": listing_context.get("asking_price_gross_eur"),
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
    elif not facts_vin_value:
        # Model output must not turn a scraper sentinel or missing VIN into an
        # invalid vehicle identity. Missing VIN is an information request only.
        vin_check = merged.get("vin_check")
        vin_check = dict(vin_check) if isinstance(vin_check, dict) else {}
        vin_check.update(
            {
                "vin_present": False,
                "format_check": "skipped",
                "decoded_information": "VIN was not supplied in the listing.",
                "online_history": "requires_manual_verification",
            }
        )
        vin_check.pop("local_validation", None)
        merged["vin_check"] = vin_check
        checks = merged.get("consistency_checks")
        if isinstance(checks, list):
            merged["consistency_checks"] = [
                item
                for item in checks
                if not (
                    isinstance(item, dict)
                    and "vin" in _fold_market_text(
                        f"{item.get('check', '')} {item.get('explanation', '')}"
                    )
                    and str(item.get("result") or "").lower() == "concern"
                )
            ]

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

    sources = merged.get("sources_used")
    authoritative_urls = {
        str(item.get("source_url") or "").strip()
        for item in (sources if isinstance(sources, list) else []) if isinstance(item, dict)
        if str(item.get("source_type") or "").upper() in {"OFFICIAL", "REGULATORY", "VEHICLE_HISTORY"}
        and str(item.get("source_url") or "").strip()
    }
    risks = merged.get("technical_risks")
    if isinstance(risks, list):
        sanitized_risks: list[Any] = []
        for raw in risks:
            if not isinstance(raw, dict):
                sanitized_risks.append(raw)
                continue
            item = dict(raw)
            if str(item.get("evidence_category") or "").upper() == "MODEL_LEVEL_RISK":
                # Age, mileage, and missing service records make a check more
                # relevant, but they are not evidence that this car has the
                # model-level defect.
                item["specific_vehicle_evidence"] = ""
                source_url = str(item.get("source_url") or "").strip()
                authoritative = source_url in authoritative_urls
                if not authoritative and _fold_market_text(item.get("confidence", "")) in {
                    "high", "vysoka"
                }:
                    item["confidence"] = "Stredna"
                trigger = str(item.get("typical_trigger_or_interval") or "")
                if not authoritative and re.search(r"\d", trigger):
                    item["typical_trigger_or_interval"] = "Vyžaduje overenie pre presný komponent a aplikáciu."
            sanitized_risks.append(item)
        merged["technical_risks"] = sanitized_risks
    return merged


def _fold_market_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in normalized if not unicodedata.combining(char)).lower()


def _remove_public_scorecard(report_text: str) -> str:
    """Remove provider- or backend-emitted numeric scorecards from customer output."""
    lines = str(report_text or "").splitlines()
    output: list[str] = []
    skipping = False
    for line in lines:
        heading = re.match(r"^\s*(#{2,6})\s+(.+?)\s*$", line)
        if heading:
            key = _fold_market_text(heading.group(2))
            if len(heading.group(1)) >= 3 and (
                "skore analyzy" in key or "analysis score" in key
            ):
                skipping = True
                continue
            if skipping:
                skipping = False
        if skipping:
            continue
        folded = _fold_market_text(line)
        numeric_score = re.search(r"\b\d{1,3}\s*(?:/|z|of)\s*100\b", folded)
        if numeric_score and any(
            term in folded
            for term in ("skore", "score", "hodnotenie", "rating")
        ):
            continue
        if any(
            term in folded
            for term in ("uncalibrated", "nekalibrovane skore", "nekalibrovany skorer")
        ):
            continue
        output.append(line)
    return "\n".join(output).rstrip() + "\n"


def _market_benchmark_is_usable(research: dict[str, Any]) -> bool:
    market = research.get("market_assessment")
    if not isinstance(market, dict):
        return False
    count = market.get("benchmark_comparable_count")
    if not isinstance(count, (int, float)):
        count = market.get("comparable_count")
    return (
        market.get("benchmark_available") is True
        and isinstance(count, (int, float))
        and int(count) >= 3
        and isinstance(market.get("benchmark_median_eur") or market.get("observed_market_average_eur"), (int, float))
    )


def _replace_report_section(
    report_text: str,
    section_terms: tuple[str, ...],
    body_lines: list[str],
) -> str:
    lines = str(report_text or "").splitlines()
    starts = [index for index, line in enumerate(lines) if re.match(r"^\s*##\s+", line)]
    for position, start in enumerate(starts):
        key = _fold_market_text(re.sub(r"^\s*##\s+", "", lines[start]))
        if not any(term in key for term in section_terms):
            continue
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        replacement = [lines[start], "", *body_lines, ""]
        return "\n".join(lines[:start] + replacement + lines[end:]).rstrip() + "\n"
    return report_text


def _comparable_price_label(item: Mapping[str, Any], *, language: str) -> str:
    """Format a comparable price for customers without leaking source currency."""
    normalized = item.get("normalized_price_eur")
    if normalized in (None, "") and item.get("price_eur") not in (None, ""):
        normalized = item.get("price_eur")
    try:
        amount = int(round(float(normalized)))
    except (TypeError, ValueError):
        return ""
    if amount <= 0:
        return ""

    currency = str(item.get("original_currency") or "").strip().upper()
    if not currency:
        display = str(item.get("price_display") or "").casefold()
        currency = (
            "CZK"
            if "czk" in display or "kč" in display or re.search(r"\bkc\b", display)
            else "EUR"
        )
    label = f"{amount:,} EUR".replace(",", " ")
    if currency == "EUR":
        return label
    prefix = "pribli\u017ene " if language == "sk" else "approximately "
    return prefix + label


def _lock_report_evidence_claims(
    report_text: str,
    text_research: Any,
    risk_score: Any,
    *,
    output_language: str = "sk",
) -> str:
    """Deterministically block unsupported market and missing-VIN narratives."""
    research = text_research if isinstance(text_research, dict) else {}
    risk = risk_score if isinstance(risk_score, dict) else {}
    text = _remove_public_scorecard(report_text)
    language = "en" if str(output_language).lower().startswith("en") else "sk"
    text = _lock_report_verdict(text, risk, language=language)

    unsupported_section_messages = {
        "web": (
            "No source-supported technical web finding passed backend validation."
            if language == "en"
            else "Backendová validácia nepotvrdila žiadne dostatočne podložené technické webové zistenie."
        ),
        "risks": (
            "No model-specific technical risk has sufficiently relevant source support; verify the vehicle in an independent workshop."
            if language == "en"
            else "Žiadne modelové technické riziko nemá dostatočne relevantný zdrojový podklad; vozidlo overte v nezávislom servise."
        ),
        "costs": (
            "No source-supported repair-cost estimate is available; request a vehicle-specific workshop quotation."
            if language == "en"
            else "Nie je dostupný dostatočne podložený odhad nákladov na opravy; vyžiadajte si cenovú ponuku pre konkrétne vozidlo."
        ),
    }
    if not research.get("web_research_findings"):
        text = _replace_report_section(
            text,
            ("webove overenie", "web verification"),
            [unsupported_section_messages["web"]],
        )
    if not research.get("technical_risks"):
        text = _replace_report_section(
            text,
            ("technicke rizika", "technical risks"),
            [unsupported_section_messages["risks"]],
        )
    if not research.get("expected_costs"):
        text = _replace_report_section(
            text,
            ("ocakavane naklady", "expected costs"),
            [unsupported_section_messages["costs"]],
        )
    if not research.get("technical_risks") and not research.get("web_research_findings"):
        unsupported_risk_terms = (
            "rozvod", "timing chain", "spotreba oleja", "oil consumption",
            "piestn", "piston ring", "mechatron", "haldex pump", "cerpadlo haldex",
            "karbon", "carbon buildup",
        )
        text = "\n".join(
            line
            for line in text.splitlines()
            if not (
                not re.match(r"^\s*##", line)
                and (
                    any(term in _fold_market_text(line) for term in unsupported_risk_terms)
                    or _contains_fixed_service_interval(line)
                )
            )
        ).rstrip() + "\n"

    if not _market_benchmark_is_usable(research):
        raw_market = research.get("market_assessment")
        raw_facts = research.get("listing_facts")
        market: dict[str, Any] = raw_market if isinstance(raw_market, dict) else {}
        facts: dict[str, Any] = raw_facts if isinstance(raw_facts, dict) else {}
        price = market.get("advertised_price_eur") or facts.get("asking_price_gross_eur") or facts.get("price")
        try:
            price_label = f"{int(float(str(price))):,} EUR".replace(",", " ")
        except (TypeError, ValueError):
            price_label = "the advertised price" if language == "en" else "inzerovaná cena"
        backend_market_message = str(market.get("summary") or "").strip()
        allowed_market_messages = {
            "Automatickému vyhľadávaniu sa nepodarilo zostaviť overenú vzorku.",
            "Boli nájdené ponuky, ale nepodarilo sa overiť ich detailné URL.",
            "Nájdené ponuky boli mimo nastavených tolerancií.",
            "Nájdené ponuky nezostavili overenú vzorku presnej konfigurácie vozidla.",
            "Automatické vyhľadávanie nenašlo použiteľné porovnateľné ponuky.",
        }
        if backend_market_message not in allowed_market_messages:
            backend_market_message = (
                "Automatickému vyhľadávaniu sa nepodarilo zostaviť overenú vzorku."
            )
        if language == "en":
            backend_market_message = {
                "Automatickému vyhľadávaniu sa nepodarilo zostaviť overenú vzorku.": "Automatic search could not assemble a verified sample.",
                "Boli nájdené ponuky, ale nepodarilo sa overiť ich detailné URL.": "Offers were found, but their detail URLs could not be verified.",
                "Nájdené ponuky boli mimo nastavených tolerancií.": "The offers found were outside the configured tolerances.",
                "Nájdené ponuky nezostavili overenú vzorku presnej konfigurácie vozidla.": "The offers found did not produce a verified sample of the vehicle's exact visible configuration.",
                "Automatické vyhľadávanie nenašlo použiteľné porovnateľné ponuky.": "Automatic search found no usable comparable offers.",
            }[backend_market_message]

        comparable_lines: list[str] = []
        for item in research.get("market_comparables", []) if isinstance(research.get("market_comparables"), list) else []:
            if (
                not isinstance(item, dict)
                or item.get("display_in_report") is not True
                or not is_customer_facing_market_comparable(item)
            ):
                continue
            url = str(item.get("source_url") or "").strip()
            label = str(
                item.get("description") or item.get("title") or "Porovnateľný inzerát"
            ).strip()
            price_display = _comparable_price_label(item, language=language)
            suffix = price_display
            comparable_lines.append(f"- [{label}]({url})" + (f" — {suffix}" if suffix else ""))
            if len(comparable_lines) >= 5:
                break

        if language == "en":
            neutral = [
                f"The advertised price is **{price_label}**.",
                backend_market_message
                + " The price therefore cannot be classified as cheap, expensive, fair, or suspicious and requires manual verification.",
            ]
            quick_price = "- **Price:** unclear — there are not enough verified comparable listings for a market classification."
            neutral_line = "Price position is unverified; insufficient comparable data cannot support a positive or negative claim about the car."
        else:
            neutral = [
                f"Inzerovaná cena je **{price_label}**.",
                backend_market_message
                + " Preto cenu nemožno označiť za lacnú, drahú, férovú ani podozrivú; pozíciu ceny treba overiť manuálne.",
            ]
            quick_price = "- **Cena:** nejasná — nie je dostatok overených porovnateľných inzerátov na trhové zaradenie."
            neutral_line = "Pozícia ceny nie je overená; nedostatok porovnateľných dát nepodporuje pozitívny ani negatívny záver o aute."
        if comparable_lines:
            neutral.extend(("", "Overené blízke ponuky:" if language == "sk" else "Verified nearby offers:", *comparable_lines))
        else:
            neutral.extend(
                (
                    "",
                    (
                        "Žiadna overená SK/CZ ponuka nesplnila prísny filter ±20 % ceny "
                        "a rovnakej viditeľnej konfigurácie."
                        if language == "sk"
                        else "No verified SK/CZ offer met the strict ±20% price and exact visible-configuration filters."
                    ),
                )
            )
        text = _replace_report_section(
            text,
            ("cena a vyjednavanie", "price and negotiation"),
            neutral,
        )

        rewritten: list[str] = []
        evaluative_terms = (
            "podozriv", "lacn", "drah", "spodnej hranici", "vyhodn",
            "cheap", "expensive", "underpriced", "overpriced", "below market", "above market", "suspicious",
        )
        for line in text.splitlines():
            folded = _fold_market_text(line)
            if re.match(r"^\s*-\s*\*\*(?:cena|price):\*\*", folded):
                rewritten.append(quick_price)
                continue
            if re.match(r"^\s*\|\s*(?:cena|price)\s*\|", folded):
                cells = line.split("|")
                if len(cells) >= 4:
                    cells[3] = " Cena bez overeného benchmarku " if language == "sk" else " No verified benchmark "
                    line = "|".join(cells)
                rewritten.append(line)
                continue
            price_context = "cena" in folded or "price" in folded
            if price_context and any(term in folded for term in evaluative_terms):
                if line.lstrip().startswith(("-", "*")):
                    rewritten.append(f"- **{'Cena bez benchmarku' if language == 'sk' else 'Price without a benchmark'}:** {neutral_line}")
                else:
                    rewritten.append(neutral_line)
                continue
            rewritten.append(line)
        text = "\n".join(rewritten).rstrip() + "\n"

    else:
        raw_market = research.get("market_assessment")
        market = raw_market if isinstance(raw_market, dict) else {}

        def eur_label(value: Any) -> str:
            try:
                return f"{int(round(float(value))):,} EUR".replace(",", " ")
            except (TypeError, ValueError):
                return "neuvedené" if language == "sk" else "unavailable"

        advertised_label = eur_label(market.get("advertised_price_eur"))
        median_label = eur_label(
            market.get("benchmark_median_eur")
            or market.get("observed_market_average_eur")
        )
        sample_count = int(market.get("benchmark_comparable_count") or 0)
        delta = market.get("price_delta_percent")
        try:
            delta_label = f"{float(str(delta)):+.1f} %"
        except (TypeError, ValueError):
            delta_label = "—"
        price_view = str(market.get("price_view") or "").strip().lower()
        view_labels = {
            "rather_cheap": ("skôr pod trhom", "rather below market"),
            "fair": ("v rámci trhu", "within the market range"),
            "rather_expensive": ("skôr nad trhom", "rather above market"),
        }
        sk_view, en_view = view_labels.get(
            price_view, ("bez jasného zaradenia", "without a clear classification")
        )
        view_label = en_view if language == "en" else sk_view

        public_links: list[str] = []
        for item in research.get("market_comparables", []) if isinstance(research.get("market_comparables"), list) else []:
            if (
                not isinstance(item, dict)
                or item.get("display_in_report") is not True
                or not is_customer_facing_market_comparable(item)
            ):
                continue
            url = str(item.get("source_url") or "").strip()
            label = str(item.get("description") or item.get("title") or "Porovnateľný inzerát").strip()
            price_display = _comparable_price_label(item, language=language)
            public_links.append(
                f"- [{label}]({url})" + (f" — {price_display}" if price_display else "")
            )
            if len(public_links) >= 5:
                break

        if language == "en":
            market_lines = [
                f"The backend benchmark classifies the advertised price as **{view_label}**.",
                "",
                "| Comparison | Value |",
                "| --- | ---: |",
                f"| Advertised price | {advertised_label} |",
                f"| Weighted market median | {median_label} |",
                f"| Difference from median | {delta_label} |",
                f"| Benchmark sample | {sample_count} offers |",
            ]
            if public_links:
                market_lines.extend(("", "Verified SK/CZ offers:", *public_links))
            else:
                market_lines.extend(
                    (
                        "",
                        "No verified SK/CZ offer met the strict ±20% price and exact visible-configuration filters.",
                    )
                )
            quick_price = f"- **Price:** {view_label} — {advertised_label} versus a market median of {median_label}."
        else:
            market_lines = [
                f"Backendový benchmark zaraďuje inzerovanú cenu ako **{view_label}**.",
                "",
                "| Porovnanie | Hodnota |",
                "| --- | ---: |",
                f"| Cena inzerátu | {advertised_label} |",
                f"| Vážený medián trhu | {median_label} |",
                f"| Rozdiel oproti mediánu | {delta_label} |",
                f"| Vzorka benchmarku | {sample_count} ponúk |",
            ]
            if public_links:
                market_lines.extend(("", "Overené SK/CZ ponuky:", *public_links))
            else:
                market_lines.extend(
                    (
                        "",
                        "Žiadna overená SK/CZ ponuka nesplnila prísny filter ±20 % ceny a rovnakej viditeľnej konfigurácie.",
                    )
                )
            quick_price = f"- **Cena:** {view_label} — {advertised_label} oproti trhovému mediánu {median_label}."

        text = _replace_report_section(
            text,
            ("cena a vyjednavanie", "price and negotiation"),
            market_lines,
        )
        rewritten = []
        for line in text.splitlines():
            if re.match(r"^\s*-\s*\*\*(?:Cena|Price):\*\*", line, re.IGNORECASE):
                rewritten.append(quick_price)
            else:
                rewritten.append(line)
        text = "\n".join(rewritten).rstrip() + "\n"

    raw_facts = research.get("listing_facts")
    raw_vin_check = research.get("vin_check")
    listing_facts: dict[str, Any] = raw_facts if isinstance(raw_facts, dict) else {}
    vin_check: dict[str, Any] = raw_vin_check if isinstance(raw_vin_check, dict) else {}
    text = _lock_registration_age_claims(text, listing_facts, language=language)
    vin_missing = not str(listing_facts.get("vin") or "").strip() and vin_check.get("vin_present") is not True
    if vin_missing:
        rewritten = []
        for line in text.splitlines():
            folded = _fold_market_text(line)
            if ("najvacsie riziko" in folded or "biggest risk" in folded) and "vin" in folded:
                rewritten.append(
                    "- **Najväčšie riziko:** Z dostupných údajov nie je potvrdená konkrétna zásadná vada; VIN treba vyžiadať a históriu následne štandardne preveriť."
                    if language == "sk"
                    else "- **Biggest risk:** The available evidence does not confirm a material vehicle defect; request the VIN and perform the standard history check."
                )
                continue
            if line.lstrip().startswith(("-", "*")) and "vin" in folded and any(term in folded for term in ("zavaz", "major risk", "serious defect", "negative history")):
                rewritten.append(
                    "- **VIN na vyžiadanie:** VIN nie je v texte inzerátu; požiadajte oň pred obhliadkou a následne preverte históriu. Samotné chýbanie VIN v inzeráte nie je dôkazom negatívnej histórie."
                    if language == "sk"
                    else "- **Request the VIN:** It is absent from the listing text; ask for it before viewing and then check history. Its absence from the ad is not evidence of negative history."
                )
                continue
            rewritten.append(line)
        text = "\n".join(rewritten).rstrip() + "\n"
    return text


def _lock_report_verdict(
    report_text: str, risk_score: dict[str, Any], *, language: str
) -> str:
    """Make the customer verdict a backend value, never a model decision."""
    verdict = str(risk_score.get("allowed_final_verdict") or "").strip()
    if not verdict:
        return report_text
    label = "Assessment" if language == "en" else "Hodnotenie"
    replacement = f"- **{label}:** {verdict}"
    pattern = re.compile(
        r"^\s*[-*]\s*\*\*(?:Hodnotenie|Assessment):\*\*.*$",
        re.IGNORECASE | re.MULTILINE,
    )
    if pattern.search(report_text):
        return pattern.sub(replacement, report_text, count=1)
    return report_text


def _lock_registration_age_claims(
    report_text: str,
    listing_facts: dict[str, Any],
    *,
    language: str,
    as_of: datetime | None = None,
) -> str:
    """Replace model-calculated vehicle age with deterministic registration age."""
    raw_date = str(
        listing_facts.get("registration_date")
        or listing_facts.get("first_registration")
        or ""
    ).strip()
    match = re.search(r"\b(0?[1-9]|1[0-2])\s*[/.-]\s*((?:19|20)\d{2})\b", raw_date)
    if not match:
        return report_text
    month, year = int(match.group(1)), int(match.group(2))
    current = as_of or datetime.now()
    total_months = (current.year - year) * 12 + current.month - month
    if total_months < 0:
        return report_text
    years, months = divmod(total_months, 12)
    if language == "en":
        parts = [f"{years} year{'s' if years != 1 else ''}"] if years else []
        if months:
            parts.append(f"{months} month{'s' if months != 1 else ''}")
        age = " and ".join(parts or ["0 months"])
        pattern = (
            r"(?i)\b(?:in|over)\s+(?:(?:less than|nearly|approximately)\s+)?"
            r"(?:\d+(?:[.,]\d+)?|one|two|three|four|five)\s+years?"
            r"(?:\s+of (?:use|operation))?"
        )
        return re.sub(pattern, f"over approximately {age}", report_text)

    def sk_unit(value: int, one: str, few: str, many: str) -> str:
        return f"{value} {one if value == 1 else few if 2 <= value <= 4 else many}"

    parts = [sk_unit(years, "rok", "roky", "rokov")] if years else []
    if months:
        parts.append(sk_unit(months, "mesiac", "mesiace", "mesiacov"))
    age = "približne " + " a ".join(parts or ["0 mesiacov"])
    pattern = (
        r"(?i)\bza\s+(?:(?:necel[eé]|menej ako|takmer|pribli[zž]ne)\s+)?"
        r"(?:\d+(?:[.,]\d+)?|jeden|dva|tri|[sš]tyri|p[aä]ť)\s+"
        r"rok(?:y|ov)?(?:\s+prev[aá]dzky)?"
    )
    return re.sub(pattern, f"za {age} prevádzky", report_text)


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
    persisted_vin = str(value.get("vin") or "").strip().upper() if isinstance(value, dict) else ""
    if persisted_vin in {"N/A", "NA", "NONE", "NULL", "UNKNOWN", "NEUVEDENE", "NEUVEDENÉ"}:
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


def _multi_model_analysis_events(
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
    active_profile = analysis_profile()
    research_v2_active = active_profile != "legacy"
    diagnostics: dict[str, Any] = {
        "schema_version": 1,
        "analysis_run_id": current_tracking_value("analysis_run_id", ""),
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "completed_at": "",
        "build_commit": os.environ.get("RENDER_GIT_COMMIT") or os.environ.get("GIT_COMMIT") or "",
        "risk_scorer_v2_active": os.environ.get("RISK_SCORER_V2_ACTIVE", "").strip().lower()
        in {"1", "true", "yes", "on"},
        "analysis_profile": active_profile,
        "research_packet_version": 2 if research_v2_active else 1,
        "models": {
            "component_identity_grounding": GEMINI_GROUNDING_MODEL,
            "reliability_grounding": GEMINI_GROUNDING_MODEL,
            "market_search": "direct-local-sk-cz-and-mobile-background-v2",
            "text_research": GEMINI_TEXT_RESEARCH_MODEL,
            "vision": GEMINI_VISION_MODEL,
            "final_synthesis": GEMINI_FINAL_MODEL,
        },
        "phases": {},
        "market": {},
        "validation": {},
    }

    def save_diagnostics() -> None:
        repository.write_json(slug, "analysis_diagnostics.json", diagnostics)

    def token_counter(
        model_name: str,
        response_json_schema: dict[str, Any] | None = None,
    ) -> Callable[[str, str], tuple[int, str]] | None:
        if dependencies.count_input_tokens is None or not gemini_key_entries:
            return None
        key = gemini_key_entries[0]["key"]
        def count(system: str, content: str) -> tuple[int, str]:
            kwargs: dict[str, Any] = {"model": model_name}
            if response_json_schema is not None:
                kwargs["response_json_schema"] = response_json_schema
            return dependencies.count_input_tokens(key, system, content, **kwargs)

        return count

    save_diagnostics()
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
        with tracking_context(
            phase="component_identity_grounding",
            attempt=1,
            retry_reason=None,
            grounding_enabled=True,
        ):
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
        _promote_selected_key(gemini_key_entries, _identity_key)
        repository.write_text(
            slug, "component_identity_research.md", identity_grounded
        )
        component_identity = normalize_component_identity(identity_grounded)
        status = component_identity.get("identification_status", "UNKNOWN")
        diagnostics["phases"]["component_identity"] = {
            "status": "completed",
            "identification_status": status,
            "selected_key_label": _selected_key_label(_identity_key),
        }
        save_diagnostics()
        yield _status_event(f"Component identification saved: {status}.")
    except Exception as identity_exc:
        dependencies.log(f"Component identity research warning: {identity_exc}")
        yield _status_event(
            "Component identification unavailable; continuing without guessing exact codes."
        )
        diagnostics["phases"]["component_identity"] = {
            "status": "failed",
            "error_type": type(identity_exc).__name__,
        }
        save_diagnostics()
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
        with tracking_context(
            phase="grounding",
            attempt=1,
            retry_reason=None,
            grounding_enabled=True,
        ):
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
        _promote_selected_key(gemini_key_entries, _grounding_key)
        if grounded:
            web_research_text = grounded
            repository.write_text(slug, "reliability_research.md", grounded)
            linked_count = _linked_market_comparable_count(grounded, market_only=True)
            public_link_count = _linked_market_comparable_count(
                grounded, customer_facing_only=True
            )
            background_link_count = linked_count - public_link_count
            diagnostics["market"].update(
                {
                    "broad_direct_citation_count": linked_count,
                    "broad_public_sk_cz_count": public_link_count,
                    "broad_background_eu_count": background_link_count,
                    "targeted_search_attempted": False,
                }
            )
            diagnostics["phases"]["grounded_research"] = {
                "status": "completed",
                "selected_key_label": _selected_key_label(_grounding_key),
            }
            save_diagnostics()
            repository.write_text(slug, "web_research.md", web_research_text)
            yield _status_event("Web research ready for text/research analysis.")
    except Exception as exc:
        dependencies.log(f"Web research warning: {exc}")
        yield _status_event("Web research unavailable; continuing with listing data.")
        diagnostics["phases"]["grounded_research"] = {
            "status": "failed",
            "error_type": type(exc).__name__,
        }
        save_diagnostics()

    # Market passes have their own deterministic parser and are merged after
    # structured research. Do not resend their raw prose/JSON to the text
    # model; that only adds tokens and gives a second model a chance to alter
    # the provenance-locked candidates.
    text_research_web_context = web_research_text

    # Price discovery is independent from the language model. Direct result-
    # card parsing preserves exact local-portal detail URLs and keeps foreign
    # observations out of the customer-facing link path.
    diagnostics["market"]["targeted_search_attempted"] = True
    yield _status_event("Searching local SK/CZ marketplaces and Mobile.de for comparable cars...")
    try:
        market_pass_results = dependencies.direct_market_search(listing_context_data)
    except Exception as market_exc:
        dependencies.log(f"Direct market search warning: {market_exc}")
        market_pass_results = [
            {
                "pass_id": "sk_cz",
                "portal": "SK/CZ local marketplaces",
                "language": "sk/cs",
                "market_scope": "PUBLIC_SK_CZ",
                "search_method": "DIRECT_PORTAL_HTML",
                "status": "ERROR",
                "error_type": type(market_exc).__name__,
                "citation_count": 0,
                "candidate_count": 0,
                "verified_detail_count": 0,
                "verified_background_count": 0,
                "url_unverified_count": 0,
                "candidates": [],
                "source_attempts": [],
            }
        ]

    # Mobile.de often blocks server-side HTTP clients with 403. If the direct
    # pass cannot provide a useful sample, reuse the already configured Gemini
    # grounding path for a portal-specific background-only search. The parser
    # accepts only exact Mobile.de grounding citations, and the benchmark
    # still applies its deterministic Tier A/year/mileage gates afterwards.
    mobile_pass = next(
        (
            item
            for item in market_pass_results
            if isinstance(item, dict) and item.get("pass_id") == "mobile_de"
        ),
        None,
    )
    if mobile_pass is not None and int(mobile_pass.get("candidate_count") or 0) < 3:
        yield _status_event("Mobile.de direct access limited; trying grounded background search...")
        direct_attempts = list(mobile_pass.get("source_attempts") or [])
        try:
            with tracking_context(
                phase="market_grounding_mobile_de",
                attempt=1,
                retry_reason="mobile_direct_insufficient",
                grounding_enabled=True,
            ):
                mobile_grounded_text, mobile_grounding_key = yield from dependencies.collect_gemini(
                    gemini_key_entries,
                    "Mobile.de background market search",
                    lambda key: [
                        dependencies.grounded_research(
                            key,
                            grounded_listing_with_identity,
                            model=GEMINI_GROUNDING_MODEL,
                            listing_slug=slug,
                            research_mode="market_mobile_de",
                        )
                    ],
                    retry_exceptions=(ApiKeyError, RateLimitError, GroundingTransientError),
                    same_key_retries=1,
                    same_key_retry_exceptions=(GroundingTransientError,),
                )
            grounded_mobile_pass = extract_grounded_market_search_pass(
                mobile_grounded_text,
                "mobile_de",
            )
            fallback_attempt = {
                "portal": "mobile_de_grounded",
                "search_url": "Google Search grounding (mobile.de only)",
                "status": grounded_mobile_pass.get("status") or "NOTHING_FOUND",
                "result_card_count": int(grounded_mobile_pass.get("citation_count") or 0),
                "parsed_candidate_count": int(grounded_mobile_pass.get("candidate_count") or 0),
                "verified_detail_count": int(
                    grounded_mobile_pass.get("verified_detail_count") or 0
                ),
                "selected_key_label": _selected_key_label(mobile_grounding_key),
            }
            direct_diagnostics = {
                key: mobile_pass.get(key)
                for key in (
                    "status",
                    "error_type",
                    "http_status",
                    "final_url",
                    "response_preview",
                    "error_message",
                )
                if mobile_pass.get(key) not in (None, "")
            }
            grounded_mobile_pass["search_query"] = mobile_pass.get("search_query") or ""
            grounded_mobile_pass["search_method"] = "GOOGLE_SEARCH_GROUNDING_FALLBACK"
            grounded_mobile_pass["direct_attempt"] = direct_diagnostics
            grounded_mobile_pass["source_attempts"] = direct_attempts + [fallback_attempt]
            market_pass_results = [
                grounded_mobile_pass if item is mobile_pass else item
                for item in market_pass_results
            ]
            if int(grounded_mobile_pass.get("candidate_count") or 0):
                yield _status_event("Mobile.de grounded background search ready.")
            else:
                yield _status_event("Mobile.de grounding returned no structured comparable ads.")
        except Exception as mobile_grounding_exc:
            dependencies.log(f"Mobile.de grounded fallback warning: {mobile_grounding_exc}")
            mobile_pass["grounded_fallback_error_type"] = type(mobile_grounding_exc).__name__
            mobile_pass.setdefault("source_attempts", []).append(
                {
                    "portal": "mobile_de_grounded",
                    "search_url": "Google Search grounding (mobile.de only)",
                    "status": "ERROR",
                    "error_type": type(mobile_grounding_exc).__name__,
                    "result_card_count": 0,
                    "parsed_candidate_count": 0,
                }
            )

    direct_pass = market_pass_results[0] if market_pass_results else {}
    market_lines = [
        "# Direct local SK/CZ market search",
        "",
        f"- Query: {direct_pass.get('search_query') or 'unavailable'}",
        f"- Status: {direct_pass.get('status') or 'ERROR'}",
        f"- Result cards: {int(direct_pass.get('citation_count') or 0)}",
        f"- Verified detail candidates: {int(direct_pass.get('verified_detail_count') or 0)}",
    ]
    for attempt in direct_pass.get("source_attempts") or []:
        if isinstance(attempt, dict):
            market_lines.append(
                "- "
                + str(attempt.get("country") or attempt.get("portal") or "?")
                + ": "
                + str(attempt.get("status") or "ERROR")
                + f", cards={int(attempt.get('result_card_count') or 0)}"
                + f", candidates={int(attempt.get('parsed_candidate_count') or 0)}"
                + ", "
                + str(attempt.get("search_url") or "")
            )
    for background_pass in market_pass_results[1:]:
        if not isinstance(background_pass, dict):
            continue
        market_lines.extend(
            (
                "",
                f"# Background {background_pass.get('portal') or 'market search'}",
                "",
                f"- Query: {background_pass.get('search_query') or 'unavailable'}",
                f"- Status: {background_pass.get('status') or 'ERROR'}",
                f"- Result cards: {int(background_pass.get('citation_count') or 0)}",
                f"- Verified background candidates: {int(background_pass.get('verified_detail_count') or 0)}",
            )
        )
        for attempt in background_pass.get("source_attempts") or []:
            if isinstance(attempt, dict):
                market_lines.append(
                    "- "
                    + str(attempt.get("portal") or "mobile_de")
                    + ": "
                    + str(attempt.get("status") or "ERROR")
                    + f", cards={int(attempt.get('result_card_count') or 0)}"
                    + f", candidates={int(attempt.get('parsed_candidate_count') or 0)}"
                    + ", "
                    + str(attempt.get("search_url") or "")
                )
    market_sections = ["\n".join(market_lines)]
    repository.write_text(slug, "market_research_sk_cz.md", market_sections[0])
    for background_pass in market_pass_results[1:]:
        if not isinstance(background_pass, dict) or background_pass.get("pass_id") != "mobile_de":
            continue
        mobile_lines = [
            "# Direct Mobile.de background market search",
            "",
            f"- Query: {background_pass.get('search_query') or 'unavailable'}",
            f"- Status: {background_pass.get('status') or 'ERROR'}",
            f"- Result cards: {int(background_pass.get('citation_count') or 0)}",
            f"- Verified background candidates: {int(background_pass.get('verified_detail_count') or 0)}",
        ]
        for key, label in (
            ("http_status", "HTTP status"),
            ("final_url", "Final URL"),
            ("response_preview", "Response preview"),
            ("error_message", "Error message"),
        ):
            value = background_pass.get(key)
            if value:
                mobile_lines.append(f"- {label}: {value}")
        direct_attempt = background_pass.get("direct_attempt")
        if isinstance(direct_attempt, dict):
            mobile_lines.append(
                "- Direct attempt: "
                + str(direct_attempt.get("status") or "ERROR")
                + (f", HTTP {direct_attempt['http_status']}" if direct_attempt.get("http_status") else "")
                + (f", {direct_attempt['error_type']}" if direct_attempt.get("error_type") else "")
            )
        for attempt in background_pass.get("source_attempts") or []:
            if isinstance(attempt, dict):
                mobile_lines.append(
                    "- mobile_de: "
                    + str(attempt.get("status") or "ERROR")
                    + f", cards={int(attempt.get('result_card_count') or 0)}"
                    + f", candidates={int(attempt.get('parsed_candidate_count') or 0)}"
                    + ", "
                    + str(attempt.get("search_url") or "")
                )
                for key, label in (
                    ("http_status", "HTTP status"),
                    ("final_url", "Final URL"),
                    ("response_preview", "Response preview"),
                    ("error_message", "Error message"),
                ):
                    value = attempt.get(key)
                    if value:
                        mobile_lines.append(f"  - {label}: {value}")
        repository.write_text(slug, "market_research_mobile_de.md", "\n".join(mobile_lines))

    market_search_results = build_market_search_results(market_pass_results)
    repository.write_json(slug, "market_search_results.json", market_search_results)
    validation_warnings.extend(
        dependencies.validate_json_contract(
            "market_search_results.json",
            json.dumps(market_search_results, ensure_ascii=False),
            "market_search_results.schema.json",
        )
    )
    market_research_text = "\n\n".join(market_sections).strip()
    repository.write_text(slug, "market_research.md", market_research_text)
    if market_research_text:
        web_research_text = (
            (web_research_text.rstrip() + "\n\n") if web_research_text else ""
        ) + "## Cielené porovnanie trhu podľa portálov\n\n" + market_research_text
    repository.write_text(slug, "web_research.md", web_research_text)
    diagnostics["market"].update(
        {
            "search_passes": [
                {
                    key: value
                    for key, value in result.items()
                    if key != "candidates"
                }
                for result in market_pass_results
            ],
            **market_search_results["summary"],
        }
    )
    save_diagnostics()

    if grok_key:
        text_provider = "grok"
        text_api_key = grok_key
    elif openrouter_key:
        text_provider = "openrouter"
        text_api_key = openrouter_key
    else:
        text_provider = "gemini"
        text_api_key = ""
    diagnostics["phases"]["text_research"] = {
        "provider": text_provider,
        "status": "started",
    }
    save_diagnostics()
    text_model_name = dependencies.model_display_name(text_provider)

    yield _status_event(f"Phase 1/4: {text_model_name} text and research analysis...")
    text_research_prompt_name = (
        "research_v2_system.md" if research_v2_active else "grok_text_research_system.md"
    )
    text_research_prompt_path = dependencies.prompt_dir / text_research_prompt_name
    if not os.path.exists(text_research_prompt_path):
        yield _error_event(f"{text_research_prompt_name} not found.")
        return
    with open(text_research_prompt_path, "r", encoding="utf-8") as f:
        text_research_system_prompt = f.read()

    text_research_json_text = ""
    _text_key: GeminiKeyEntry | None = None
    text_research_content = dependencies.build_text_research_context(
        model_listing_context,
        output_language,
        text_research_web_context,
        component_identity,
        research_v2=research_v2_active,
        listing_context=listing_context_data,
        vin_light_decode=vin_light_decode,
    )

    research_policy = get_phase_policy("text_research", profile=active_profile)
    research_response_schema = (
        _research_v2_response_schema(dependencies.prompt_dir)
        if research_v2_active and text_provider == "gemini"
        else None
    )
    if research_v2_active:
        text_research_system_prompt += (
            f"\n\nRuntime visible-output target: at most {research_policy.visible_target_tokens} tokens."
        )
    protected_research_values = tuple(
        value
        for value in (
            listing_context_data.get("vin"),
            listing_context_data.get("price"),
            listing_context_data.get("year"),
            listing_context_data.get("mileage"),
            listing_context_data.get("engine"),
            listing_context_data.get("transmission"),
            listing_context_data.get("drive"),
        )
        if value not in (None, "", [], {})
    )
    research_budget = check_and_compact_input(
        text_research_system_prompt,
        text_research_content,
        research_policy,
        count_tokens=token_counter(
            GEMINI_TEXT_RESEARCH_MODEL,
            research_response_schema,
        ),
        protected_values=protected_research_values,
    )
    text_research_content = research_budget.user_content
    diagnostics["phases"]["text_research"]["input_budget"] = _budget_diagnostics(
        research_budget
    )
    diagnostics["phases"]["text_research"]["policy"] = _policy_diagnostics(
        research_policy
    )
    save_diagnostics()


    def save_text_research_attempts(
        *,
        initial_valid: bool | None = None,
        recovery_valid: bool | None = None,
    ) -> None:
        """Persist sanitized initial/recovery usage, never provider raw text."""
        run_id = str(current_tracking_value("analysis_run_id") or "")
        provider_entries = [
            *default_tracker.get_requests_for_run(run_id, phase="text_research"),
            *default_tracker.get_requests_for_run(run_id, phase="text_recovery"),
        ]
        attempts: list[dict[str, Any]] = []
        def is_recovery_entry(item: dict[str, Any]) -> bool:
            return "json_recovery" in str(item.get("retry_reason") or "").lower()

        for attempt_number, label, schema_status in (
            (1, "initial", initial_valid),
            (2, "recovery", recovery_valid),
        ):
            if label == "recovery" and not any(
                is_recovery_entry(item)
                for item in provider_entries
            ) and schema_status is None:
                continue
            selected = [
                item
                for item in provider_entries
                if is_recovery_entry(item) == (label == "recovery")
            ]
            attempts.append(
                {
                    "attempt": label,
                    "attempt_number": attempt_number,
                    "schema_valid": schema_status,
                    "usage": {
                        "estimated_input_tokens": sum(
                            int(item.get("input_tokens") or 0) for item in selected
                        ),
                        "estimated_visible_output_tokens": sum(
                            int(item.get("output_tokens") or 0) for item in selected
                        ),
                        "provider_input_tokens": sum(
                            int(item["actual_input_tokens"])
                            for item in selected
                            if item.get("actual_input_tokens") is not None
                        ),
                        "provider_visible_output_tokens": sum(
                            int(item["actual_output_tokens"])
                            for item in selected
                            if item.get("actual_output_tokens") is not None
                        ),
                        "provider_thinking_tokens": sum(
                            int(item["actual_thinking_tokens"])
                            for item in selected
                            if item.get("actual_thinking_tokens") is not None
                        ),
                        "provider_cached_input_tokens": sum(
                            int(item["cached_input_tokens"])
                            for item in selected
                            if item.get("cached_input_tokens") is not None
                        ),
                        "estimated_cost": round(
                            sum(float(item.get("estimated_cost") or 0) for item in selected),
                            6,
                        ),
                    },
                    "finish_reason": next(
                        (
                            item.get("finish_reason")
                            for item in reversed(selected)
                            if item.get("finish_reason")
                        ),
                        None,
                    ),
                    "output_chars": sum(
                        int(item.get("output_chars") or 0) for item in selected
                    ),
                    "error": next(
                        (
                            str(item.get("error"))[:500]
                            for item in reversed(selected)
                            if item.get("error")
                        ),
                        None,
                    ),
                    "provider_calls": [
                        {
                            **{
                                key: item.get(key)
                                for key in (
                                    "id",
                                    "model",
                                    "status",
                                    "attempt",
                                    "retry_reason",
                                    "visible_output_tokens",
                                    "thinking_tokens",
                                    "total_tokens",
                                    "actual_input_tokens",
                                    "actual_output_tokens",
                                    "actual_thinking_tokens",
                                    "cached_input_tokens",
                                    "actual_total_tokens",
                                    "usage_source",
                                    "estimated_cost",
                                    "duration_ms",
                                    "finish_reason",
                                    "output_chars",
                                    "provider_request_id",
                                    "error",
                                )
                            },
                            "estimated_input_tokens": item.get("input_tokens"),
                            "estimated_visible_output_tokens": item.get("output_tokens"),
                        }
                        for item in selected
                    ],
                }
            )
        repository.write_json(
            slug,
            "text_research_provider_attempts.json",
            {
                "schema_version": 1,
                "analysis_run_id": current_tracking_value("analysis_run_id", ""),
                "provider": text_provider,
                "attempt_count": len(attempts),
                "attempts": attempts,
            },
        )

    input_tokens = research_budget.post_tokens
    yield _token_event(input_tokens, 0)
    initial_generation_error = ""
    if text_provider == "gemini":
        try:
            with tracking_context(
                phase="text_research",
                attempt=1,
                retry_reason=None,
                grounding_enabled=False,
            ):
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
                        max_output_tokens=research_policy.max_output_tokens,
                        temperature=research_policy.temperature,
                        response_json_schema=research_response_schema,
                    ),
                )
        except ModelOutputLimitError as exc:
            initial_generation_error = f"{type(exc).__name__}: {exc}"[:500]
            dependencies.log(f"Text/research output was truncated: {exc}")
    else:
        try:
            with tracking_context(
                phase="text_research",
                attempt=1,
                retry_reason=None,
                grounding_enabled=False,
            ):
                for chunk in dependencies.stream_text_model(
                    text_provider,
                    text_api_key,
                    text_research_system_prompt,
                    text_research_content,
                    listing_slug=slug,
                    phase="text_research",
                    max_output_tokens=research_policy.max_output_tokens,
                    temperature=research_policy.temperature,
                ):
                    text_research_json_text += chunk
        except ModelOutputLimitError as exc:
            initial_generation_error = f"{type(exc).__name__}: {exc}"[:500]
            dependencies.log(f"Text/research output was truncated: {exc}")
        except (RateLimitError, ConnectionError) as exc:
            if text_provider != "openrouter":
                raise
            dependencies.log(f"OpenRouter text/research failed; falling back to Gemini: {exc}")
            yield _status_event("OpenRouter text/research unavailable; falling back to Gemini.")
            text_provider = "gemini"
            text_api_key = ""
            text_model_name = dependencies.model_display_name(text_provider)
            with tracking_context(
                phase="text_research",
                attempt=1,
                retry_reason="openrouter_fallback",
                grounding_enabled=False,
            ):
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
                        max_output_tokens=research_policy.max_output_tokens,
                        temperature=research_policy.temperature,
                        response_json_schema=(
                            _research_v2_response_schema(dependencies.prompt_dir)
                            if research_v2_active
                            else None
                        ),
                    ),
                )
    try:
        research_data = dependencies.safe_model_json(text_research_json_text)
    except Exception:
        research_data = {"_parse_error": True}
    if research_v2_active and isinstance(research_data, dict):
        raw_research_data = research_data
        research_data = _enforce_research_source_policy(
            _normalize_research_model_output(research_data)
        )
        diagnostics["phases"]["text_research"]["contract_enforcement"] = (
            _research_contract_diagnostics(
                raw_research_data, research_data, attempt="initial"
            )
        )
    initial_research_valid = (
        _valid_research_model_output(research_data)
        if research_v2_active
        else not _research_parse_failed(research_data)
    )
    recovery_valid: bool | None = None
    if research_v2_active:
        repository.write_text(slug, "research_model_output.json", text_research_json_text)
    save_text_research_attempts(initial_valid=initial_research_valid)
    if not initial_research_valid:
        yield _status_event("Text/research JSON was incomplete; retrying once with a compact recovery response...")
        recovery_policy = get_phase_policy("text_recovery", profile=active_profile)
        recovery_content = text_research_content + (
            "\n\nRECOVERY REQUIREMENT: The previous response was invalid or truncated. "
            "Return one complete schema-valid JSON object. Do not search again, add facts, "
            "or reproduce backend-owned listing, identity, VIN, market, score, verdict, or report fields. "
            "Use at most 2 technical risks, 2 web findings, 2 expected costs, 1 seller claim, "
            "1 unknown, 1 consistency check, and 4 sources; keep every string to one short sentence."
            if research_v2_active
            else
            "\n\nRECOVERY REQUIREMENT: The previous response was incomplete JSON. "
            "Regenerate the complete schema from the supplied evidence. Be concise: use at most "
            "4 technical risks, 4 web findings, 4 comparables, 6 expected costs, and short strings. "
            "Return one complete JSON object and close every array/object."
        )
        recovery_budget = check_and_compact_input(
            text_research_system_prompt,
            recovery_content,
            recovery_policy,
            count_tokens=token_counter(
                GEMINI_TEXT_RESEARCH_MODEL,
                _research_v2_response_schema(dependencies.prompt_dir)
                if research_v2_active
                else None,
            ),
            protected_values=protected_research_values,
        )
        recovery_content = recovery_budget.user_content
        diagnostics["phases"]["text_research"]["recovery_input_budget"] = (
            _budget_diagnostics(recovery_budget)
        )
        save_diagnostics()
        try:
            with tracking_context(
                phase="text_recovery" if research_v2_active else "text_research",
                attempt=2,
                retry_reason="json_recovery",
                grounding_enabled=False,
            ):
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
                        phase="text_recovery" if research_v2_active else "text_research",
                        max_output_tokens=recovery_policy.max_output_tokens,
                        temperature=recovery_policy.temperature,
                        response_json_schema=(
                            _research_v2_response_schema(dependencies.prompt_dir)
                            if research_v2_active
                            else None
                        ),
                    ),
                )
            research_data = dependencies.safe_model_json(text_research_json_text)
            if research_v2_active and isinstance(research_data, dict):
                raw_research_data = research_data
                research_data = _enforce_research_source_policy(
                    _normalize_research_model_output(research_data)
                )
                diagnostics["phases"]["text_research"]["contract_enforcement"] = (
                    _research_contract_diagnostics(
                        raw_research_data, research_data, attempt="recovery"
                    )
                )
        except Exception as recovery_exc:
            dependencies.log(f"Text/research JSON recovery failed: {recovery_exc}")
            research_data = {"_parse_error": True}
        recovery_valid = (
            _valid_research_model_output(research_data)
            if research_v2_active
            else not _research_parse_failed(research_data)
        )
        if not recovery_valid:
            save_text_research_attempts(
                initial_valid=initial_research_valid,
                recovery_valid=False,
            )
            if not research_v2_active:
                repository.write_text(slug, "grok_research.json", text_research_json_text)
                yield _error_event(
                    "Text/research analysis returned incomplete JSON twice. Analysis stopped before creating an unreliable report."
                )
                return
            fallback_reason = initial_generation_error or "Research V2 returned invalid JSON twice."
            research_data = _unavailable_research_model_output(fallback_reason)
            text_research_json_text = dependencies.compact_json_for_prompt(research_data)
            repository.write_text(slug, "research_model_output.json", text_research_json_text)
            yield _status_event(
                "Text/research unavailable after one recovery; continuing with a safe limitation fallback."
            )
        save_text_research_attempts(
            initial_valid=initial_research_valid,
            recovery_valid=recovery_valid,
        )
    if research_v2_active:
        research_model_output_text = dependencies.compact_json_for_prompt(research_data)
        repository.write_text(
            slug,
            "research_model_output.json",
            research_model_output_text,
        )
        validation_warnings.extend(
            dependencies.validate_json_contract(
                "research_model_output.json",
                research_model_output_text,
                "research_model_output.schema.json",
            )
        )
        research_data = _canonical_research_from_v2(
            research_data,
            listing_context_data,
            component_identity,
            vin_light_decode,
        )
    else:
        research_data = _merge_backend_evidence(
            research_data,
            listing_context_data,
            component_identity,
            vin_light_decode,
        )
    research_status = str(research_data.get("research_status") or "completed")
    diagnostics["phases"]["text_research"].update(
        {
            "status": "completed" if research_status == "completed" else "degraded",
            "research_status": research_status,
            "provider_schema_valid": bool(initial_research_valid or recovery_valid),
            "recovery_attempted": not initial_research_valid,
            "recovered": recovery_valid is True,
            "selected_key_label": _selected_key_label(_text_key),
        }
    )
    save_diagnostics()
    # Replace, rather than merge, model-produced market candidates. Only the
    # separately grounded and backend-reconciled portal passes may feed price
    # benchmarking or customer links.
    research_data["market_comparables"] = list(
        market_search_results.get("candidates") or []
    )
    research_data["market_search_summary"] = dict(
        market_search_results.get("summary") or {}
    )
    try:
        if isinstance(research_data.get("market_comparables"), list):
            comparable_count_before = len(research_data["market_comparables"])
            research_data = deduplicate_market_comparables(research_data, car_info_text)
            try:
                exchange_rates = fetch_ecb_reference_rates()
                diagnostics["market"]["exchange_rate_status"] = "available"
            except Exception as rate_exc:
                dependencies.log(f"ECB exchange-rate warning: {rate_exc}")
                exchange_rates = {}
                diagnostics["market"]["exchange_rate_status"] = "unavailable"
                diagnostics["market"]["exchange_rate_error_type"] = type(
                    rate_exc
                ).__name__
            market_benchmark = build_market_benchmark(
                research_data,
                car_info_text,
                exchange_rates=exchange_rates,
            )
            repository.write_json(slug, "market_benchmark.json", market_benchmark)
            # The benchmark mutates the deduplicated records with the strict
            # customer-link decision. Mirror that decision into the raw search
            # artifact so debugging data cannot claim that every verified card
            # was recommended.
            recommendation_flags = {
                str(item.get("candidate_id") or ""): item.get("display_in_report") is True
                for item in research_data.get("market_comparables") or []
                if isinstance(item, dict)
            }
            for candidate in market_search_results.get("candidates") or []:
                if isinstance(candidate, dict):
                    candidate["display_in_report"] = recommendation_flags.get(
                        str(candidate.get("candidate_id") or ""), False
                    )
            repository.write_json(slug, "market_search_results.json", market_search_results)
            diagnostics["market"].update(
                {
                    "structured_comparable_count_before_filtering": comparable_count_before,
                    "verified_unique_comparable_count": len(
                        research_data.get("market_comparables") or []
                    ),
                    "benchmark_accepted_count": len(
                        market_benchmark.get("accepted_comparables") or []
                    ),
                    "benchmark_rejected_count": len(
                        market_benchmark.get("rejected_comparables") or []
                    ),
                    "benchmark_available": market_benchmark.get("available") is True,
                    "benchmark_tolerance_stage": market_benchmark.get(
                        "tolerance_stage"
                    ),
                    "benchmark_diagnostic_counts": market_benchmark.get(
                        "diagnostic_counts"
                    ),
                }
            )
            save_diagnostics()
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
    vision_policy = get_phase_policy("vision", profile=active_profile)
    if active_profile != "legacy":
        vision_system_prompt += (
            f"\n\nRuntime visible-output target: at most {vision_policy.visible_target_tokens} tokens."
        )
    diagnostics["phases"]["vision"] = {
        "status": "started",
        "policy": _policy_diagnostics(vision_policy),
    }
    save_diagnostics()

    vision_result_json = ""
    _vision_key: GeminiKeyEntry | None = None
    vision_attempts: list[dict[str, Any]] = []
    vision_provider_events: list[dict[str, Any]] = []
    vision_recovery_attempted = False
    current_vision_partial_output = ""

    def record_vision_provider_event(event: Any) -> None:
        nonlocal current_vision_partial_output
        if not isinstance(event, dict):
            return
        partial_output = event.get("output")
        if isinstance(partial_output, str) and partial_output:
            current_vision_partial_output = partial_output
        allowed = {
            key: event.get(key)
            for key in (
                "model",
                "status",
                "http_status",
                "finish_reason",
                "actual_input_tokens",
                "actual_output_tokens",
                "actual_thinking_tokens",
                "actual_total_tokens",
                "output_chars",
            )
            if event.get(key) not in (None, "")
        }
        if allowed:
            vision_provider_events.append(allowed)

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
        vision_budget = check_and_compact_input(
            vision_system_prompt,
            vision_content,
            vision_policy,
            protected_values=protected_research_values,
        )
        vision_content = vision_budget.user_content
        diagnostics["phases"]["vision"].update({
            "input_budget": _budget_diagnostics(vision_budget),
            "image_inputs_excluded_from_text_ceiling": True,
        })
        save_diagnostics()
        initial_error = ""
        current_vision_partial_output = ""
        try:
            with tracking_context(
                phase="vision",
                attempt=1,
                retry_reason=None,
                grounding_enabled=False,
            ):
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
                        max_output_tokens=vision_policy.max_output_tokens,
                        temperature=vision_policy.temperature,
                        diagnostics_callback=record_vision_provider_event,
                    ),
                )
        except Exception as exc:
            dependencies.log(f"Gemini vision error: {exc}")
            initial_error = f"{type(exc).__name__}: {exc}"[:500]
            if not vision_result_json and current_vision_partial_output:
                vision_result_json = current_vision_partial_output

        vision_attempts.append(
            {
                "attempt": "initial",
                "valid_json": False,
                "error": initial_error,
                "output": vision_result_json,
            }
        )
        try:
            initial_parsed = dependencies.safe_model_json(vision_result_json)
        except Exception:
            initial_parsed = {"_parse_error": True}
        initial_valid = _valid_vision_payload(initial_parsed)
        vision_attempts[-1]["valid_json"] = initial_valid

        if not initial_valid:
            vision_recovery_attempted = True
            vision_recovery_policy = get_phase_policy(
                "vision_recovery", profile=active_profile
            )
            yield _status_event("Vision JSON was incomplete; retrying a compact structured response...")
            recovery_content = (
                vision_content
                + "\n\nRECOVERY REQUIREMENT: Return one complete compact JSON object. "
                "Close every string, array, and object. Keep at most 2 exterior observations, "
                "2 interior observations, 2 dashboard observations, and 2 visible red flags. "
                "Do not omit required fields. Use analysis_status=completed."
            )
            recovery_result = ""
            recovery_error = ""
            current_vision_partial_output = ""
            try:
                with tracking_context(
                    phase="vision_recovery" if active_profile != "legacy" else "vision",
                    attempt=2,
                    retry_reason="json_recovery",
                    grounding_enabled=False,
                ):
                    recovery_result, recovery_key = yield from dependencies.collect_gemini(
                        gemini_key_entries,
                        "vision JSON recovery",
                        lambda key: dependencies.call_gemini(
                            key,
                            vision_system_prompt,
                            recovery_content,
                            image_data_list=image_data_list,
                            model=GEMINI_VISION_MODEL,
                            listing_slug=slug,
                            allow_image_text_fallback=False,
                            phase="vision_recovery" if active_profile != "legacy" else "vision",
                            max_output_tokens=vision_recovery_policy.max_output_tokens,
                            temperature=vision_recovery_policy.temperature,
                            diagnostics_callback=record_vision_provider_event,
                        ),
                    )
                if recovery_key:
                    _vision_key = recovery_key
            except Exception as exc:
                recovery_error = f"{type(exc).__name__}: {exc}"[:500]
                dependencies.log(f"Gemini vision recovery error: {exc}")
                if not recovery_result and current_vision_partial_output:
                    recovery_result = current_vision_partial_output
            try:
                recovery_parsed = dependencies.safe_model_json(recovery_result)
            except Exception:
                recovery_parsed = {"_parse_error": True}
            recovery_valid = _valid_vision_payload(recovery_parsed)
            vision_attempts.append(
                {
                    "attempt": "recovery",
                    "valid_json": recovery_valid,
                    "error": recovery_error,
                    "output": recovery_result,
                }
            )
            if recovery_valid:
                vision_result_json = recovery_result
                validation_warnings.append(
                    {
                        "artifact": "gemini_vision.json",
                        "type": "provider_output_recovered",
                        "message": "Initial vision output was invalid; a compact retry produced valid JSON.",
                    }
                )
            else:
                failure_reason = recovery_error or initial_error or "Both provider responses contained invalid JSON."
                vision_result_json = json.dumps(
                    _unavailable_vision_payload(
                        _image_meta,
                        output_language=dependencies.output_language(output_language),
                        reason=failure_reason,
                    ),
                    indent=2,
                    ensure_ascii=False,
                )
                validation_warnings.append(
                    {
                        "artifact": "gemini_vision.json",
                        "type": "provider_output_invalid",
                        "message": "Vision provider did not return valid JSON after a compact retry; a schema-valid unavailable-evidence fallback was saved.",
                    }
                )
                yield _status_event("Vision analysis remained unavailable; preserving the fact that listing photos were provided.")
    else:
        vision_result_json = dependencies.no_photos_vision_result()
        yield _status_event("No photos available for Gemini vision.")

    repository.write_json(
        slug,
        "vision_provider_attempts.json",
        {
            "schema_version": 1,
            "attempt_count": len(vision_attempts),
            "recovery_attempted": vision_recovery_attempted,
            "provider_events": vision_provider_events,
            "attempts": vision_attempts,
        },
    )
    repository.write_text(slug, "gemini_vision.json", vision_result_json)
    validation_warnings.extend(
        dependencies.validate_json_contract(
            "gemini_vision.json",
            vision_result_json,
            "gemini_vision.schema.json",
        )
    )
    vision_diagnostics = dependencies.safe_model_json(vision_result_json)
    diagnostics["phases"].setdefault("vision", {})
    diagnostics["phases"]["vision"].update({
        "status": "completed",
        "analysis_status": vision_diagnostics.get("analysis_status") or "completed",
        "photos_provided": vision_diagnostics.get("photos_provided") is True,
        "parse_error": vision_diagnostics.get("_parse_error") is True,
        "selected_key_label": _selected_key_label(_vision_key),
        "attempt_count": len(vision_attempts),
        "recovery_attempted": vision_recovery_attempted,
        "recovered": bool(vision_attempts and vision_attempts[-1].get("valid_json") and vision_recovery_attempted),
        "provider_events": vision_provider_events,
    })
    save_diagnostics()
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
    diagnostics["risk_scorer_v2_active"] = risk_score.get("schema_version") == 2
    diagnostics["phases"]["risk_scoring"] = {
        "status": "completed",
        "schema_version": risk_score.get("schema_version"),
        "policy_version": risk_score.get("policy_version"),
    }
    save_diagnostics()
    verdict = risk_score.get("allowed_final_verdict", "unknown")
    yield _status_event(f"Backend risk score saved: {verdict}")

    yield _status_event(f"Phase 4/4: {text_model_name} final synthesis...")
    final_synthesis_prompt_path = dependencies.prompt_dir / "grok_final_synthesis_system.md"
    if not os.path.exists(final_synthesis_prompt_path):
        yield _error_event("grok_final_synthesis_system.md not found.")
        return
    with open(final_synthesis_prompt_path, "r", encoding="utf-8") as f:
        final_system_prompt = f.read()
    final_policy = get_phase_policy("final_synthesis", profile=active_profile)
    if active_profile != "legacy":
        final_system_prompt += (
            f"\n\nRuntime visible-output target: at most {final_policy.visible_target_tokens} tokens."
        )
    if research_data.get("research_status") in {"unavailable", "limited"}:
        final_system_prompt += (
            "\n\nTechnical research is unavailable for this run. Do not supply model-specific "
            "failure claims, component codes, service intervals, recall conclusions, or repair-cost "
            "estimates from general knowledge. State the limitation and provide only generic "
            "pre-purchase inspection actions supported by backend facts."
        )

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

    market_for_budget = research_data.get("market_assessment")
    market_for_budget = market_for_budget if isinstance(market_for_budget, dict) else {}
    critical_final_evidence: list[Any] = list(
        market_for_budget.get("limitations")
        if isinstance(market_for_budget.get("limitations"), list)
        else []
    )
    for conflict in research_data.get("data_conflicts") or []:
        if isinstance(conflict, dict):
            critical_final_evidence.append(
                json.dumps(conflict, ensure_ascii=False, separators=(",", ":"))
            )
    for risk in research_data.get("technical_risks") or []:
        if isinstance(risk, dict) and str(risk.get("risk_level") or "").upper() == "HIGH":
            critical_final_evidence.extend(
                risk.get(key)
                for key in (
                    "component",
                    "issue",
                    "evidence_category",
                    "specific_vehicle_evidence",
                    "verification_action",
                )
                if risk.get(key) not in (None, "", [], {})
            )
    protected_final_values = tuple(
        value
        for value in (
            listing_context_data.get("vin"),
            listing_context_data.get("price"),
            listing_context_data.get("year"),
            listing_context_data.get("mileage"),
            listing_context_data.get("engine"),
            listing_context_data.get("transmission"),
            listing_context_data.get("drive"),
            listing_context_data.get("vat_context"),
            risk_score.get("allowed_final_verdict"),
            market_for_budget.get("summary"),
            *critical_final_evidence,
        )
        if value not in (None, "", [], {})
    )
    final_budget = check_and_compact_input(
        final_system_prompt,
        final_content,
        final_policy,
        count_tokens=token_counter(GEMINI_FINAL_MODEL),
        protected_values=protected_final_values,
    )
    final_content = final_budget.user_content
    diagnostics["phases"]["final_synthesis"] = {
        "status": "started",
        "provider": text_provider,
        "input_budget": _budget_diagnostics(final_budget),
        "policy": _policy_diagnostics(final_policy),
    }
    save_diagnostics()

    full_report = ""
    output_tokens = 0
    final_recovery_attempted = False
    final_recovered = False
    next_token_update = 250
    final_input_tokens = final_budget.post_tokens
    yield _token_event(final_input_tokens, output_tokens)
    if text_provider == "gemini":
        final_done = False
        for index, entry in enumerate(gemini_key_entries):
            attempt_text = ""
            attempt_output_tokens = 0
            try:
                with tracking_context(
                    phase="final_synthesis",
                    attempt=index + 1,
                    retry_reason="api_key_fallback" if index else None,
                    grounding_enabled=False,
                ):
                    for chunk in dependencies.call_gemini(
                        entry["key"],
                        final_system_prompt,
                        final_content,
                        image_data_list=None,
                        model=GEMINI_FINAL_MODEL,
                        listing_slug=slug,
                        fallback_models=GEMINI_FINAL_FALLBACK_MODELS,
                        phase="final_synthesis",
                        max_output_tokens=final_policy.max_output_tokens,
                        temperature=final_policy.temperature,
                    ):
                        attempt_text += chunk
                        attempt_output_tokens += dependencies.estimate_output_tokens(chunk)
                        if attempt_output_tokens >= next_token_update:
                            yield _token_event(final_input_tokens, attempt_output_tokens)
                            next_token_update += 250
                full_report = attempt_text
                output_tokens = attempt_output_tokens
                final_done = True
                break
            except ModelOutputLimitError:
                if final_recovery_attempted or final_policy.max_attempts < 2:
                    raise
                final_recovery_attempted = True
                yield _status_event(
                    "Final report reached the shared thinking/output limit; retrying once with a compact complete report..."
                )
                recovery_system_prompt = final_system_prompt + (
                    "\n\nFINAL RECOVERY: Return one complete Slovak report, not a continuation. "
                    "Use the required headings, keep all backend verdict and market facts unchanged, "
                    "omit repetition, and stay below 2,000 visible tokens."
                )
                attempt_text = ""
                attempt_output_tokens = 0
                with tracking_context(
                    phase="final_synthesis",
                    attempt=index + 2,
                    retry_reason="final_output_recovery",
                    grounding_enabled=False,
                ):
                    for chunk in dependencies.call_gemini(
                        entry["key"],
                        recovery_system_prompt,
                        final_content,
                        image_data_list=None,
                        model=GEMINI_FINAL_MODEL,
                        listing_slug=slug,
                        fallback_models=GEMINI_FINAL_FALLBACK_MODELS,
                        phase="final_synthesis",
                        max_output_tokens=final_policy.max_output_tokens,
                        temperature=0.1,
                    ):
                        attempt_text += chunk
                        attempt_output_tokens += dependencies.estimate_output_tokens(chunk)
                        if attempt_output_tokens >= next_token_update:
                            yield _token_event(final_input_tokens, attempt_output_tokens)
                            next_token_update += 250
                full_report = attempt_text
                output_tokens = attempt_output_tokens
                final_recovered = True
                final_done = True
                break
            except (ApiKeyError, RateLimitError) as exc:
                if attempt_text or index >= len(gemini_key_entries) - 1:
                    raise
                status = dependencies.gemini_retry_status(entry, gemini_key_entries[index + 1], "final synthesis", exc)
                yield _status_event(status)

        if not final_done:
            raise RateLimitError("Gemini final synthesis failed for all configured API keys.")
        if full_report:
            yield _text_event(full_report)
            yield _token_event(final_input_tokens, output_tokens)
    else:
        try:
            with tracking_context(
                phase="final_synthesis",
                attempt=1,
                retry_reason=None,
                grounding_enabled=False,
            ):
                for chunk in dependencies.stream_text_model(
                    text_provider,
                    text_api_key,
                    final_system_prompt,
                    final_content,
                    listing_slug=slug,
                    phase="final_synthesis",
                    max_output_tokens=final_policy.max_output_tokens,
                    temperature=final_policy.temperature,
                ):
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
                    with tracking_context(
                        phase="final_synthesis",
                        attempt=index + 2,
                        retry_reason="provider_fallback",
                        grounding_enabled=False,
                    ):
                        for chunk in dependencies.call_gemini(
                            entry["key"],
                            final_system_prompt,
                            final_content,
                            image_data_list=None,
                            model=GEMINI_FINAL_MODEL,
                            listing_slug=slug,
                            fallback_models=GEMINI_FINAL_FALLBACK_MODELS,
                            phase="final_synthesis",
                            max_output_tokens=final_policy.max_output_tokens,
                            temperature=final_policy.temperature,
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
    public_text = _lock_report_evidence_claims(
        public_text,
        research_data,
        risk_score,
        output_language=dependencies.output_language(output_language),
    )
    public_text = dependencies.move_pros_cons_after_quick_summary(public_text)
    repository.write_text(slug, "analysis_result.md", public_text)
    validation_warnings.extend(dependencies.validate_final_report(public_text, verdict))
    warnings_path = dependencies.write_validation_warnings(
        slug_dir,
        validation_warnings,
        log=dependencies.log,
    )
    diagnostics["phases"]["final_synthesis"].update({
        "status": "completed",
        "provider": text_provider,
        "output_tokens_estimate": output_tokens,
        "recovery_attempted": final_recovery_attempted,
        "recovered": final_recovered,
    })
    diagnostics["validation"] = {
        "warning_count": len(validation_warnings),
        "warning_types": sorted(
            {
                str(item.get("type") or "")
                for item in validation_warnings
                if isinstance(item, dict) and item.get("type")
            }
        ),
    }
    diagnostics["completed_at"] = datetime.now().isoformat(timespec="seconds")
    save_diagnostics()
    if validation_warnings:
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


def multi_model_analysis_events(
    slug: str,
    grok_key: str,
    gemini_keys: Sequence[str],
    output_language: str = "sk",
    openrouter_key: str = "",
    *,
    dependencies: AnalysisPipelineDependencies,
) -> Iterator[str]:
    """Run one analysis inside a scoped telemetry run context."""
    analysis_run_id = new_analysis_run_id()
    started_at = time.perf_counter()
    try:
        with analysis_run_context(analysis_run_id):
            yield from _multi_model_analysis_events(
                slug,
                grok_key,
                gemini_keys,
                output_language,
                openrouter_key,
                dependencies=dependencies,
            )
    finally:
        try:
            dependencies.repository.job_dir(slug, require=True)
            dependencies.repository.write_json(
                slug,
                "ai_usage_summary.json",
                default_tracker.summarize_run(
                    analysis_run_id,
                    duration_ms=round((time.perf_counter() - started_at) * 1000),
                ),
            )
        except Exception as exc:
            # Usage telemetry must never turn a completed/failed analysis into
            # a different pipeline outcome.
            try:
                dependencies.log(f"AI usage summary warning: {exc}")
            except Exception:
                pass
