"""Typed contracts shared across stable Scrapper demo boundaries."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, TypedDict, TypeAlias


JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


class ParsedListingData(TypedDict, total=False):
    title: str
    price: int | float
    currency: str
    vin: str
    specs: dict[str, str]
    equipment: dict[str, list[str]]
    seller: dict[str, str]
    location: str
    description: str
    photos_count: int
    source_url: str
    scraped_at: str


class GeminiKeyEntry(TypedDict):
    key: str
    label: str


class ProviderFailure(TypedDict, total=False):
    provider: Literal["gemini", "grok", "openrouter"]
    phase: str
    error_type: str
    message: str
    retryable: bool


class TextProvider(Protocol):
    def __call__(
        self,
        api_key: str,
        system_prompt: str,
        user_content: str,
        *,
        listing_slug: str | None = None,
    ) -> Iterator[str]: ...


class RiskRule(TypedDict):
    rule: str
    points: int
    reason: str


class RiskOverride(TypedDict):
    rule: str
    effect: str


class RiskScoreResult(TypedDict, total=False):
    schema_version: int
    policy_version: int
    calibration_status: str
    decision_status: str
    allowed_final_verdict: str
    screening_score: int
    evidence_quality: str
    vehicle_specific_findings: list[dict[str, Any]]
    normal_wear_observations: list[dict[str, Any]]
    model_level_inspection_points: list[dict[str, Any]]
    missing_information: list[dict[str, Any]]
    buyer_actions: list[str]
    gate_triggers: list[dict[str, Any]]
    score_breakdown: dict[str, Any]
    risk_score: int
    applied_rules: list[RiskRule]
    override_rules_applied: list[RiskOverride]
    missing_data_flags: list[str]
    buyer_priority_checks: list[str]
    buyer_scorecard: dict[str, Any]


class SSETokenUsage(TypedDict):
    input_tokens: int
    output_tokens: int


class SSEPayload(TypedDict, total=False):
    status: str
    error: str
    text: str
    token_usage: SSETokenUsage
    done: bool
    slug: str
    has_kb_blocks: bool
    saved_kb: list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class AnalysisPipelineInput:
    slug: str
    grok_key: str
    gemini_keys: Sequence[str]
    output_language: Literal["sk", "en"] = "sk"
    openrouter_key: str = ""


@dataclass(frozen=True, slots=True)
class AnalysisArtifacts:
    car_info: str = "car_info.md"
    listing_facts: str = "listing_facts.json"
    component_identity: str = "component_identity.json"
    reliability_research: str = "reliability_research.md"
    market_research: str = "market_research.md"
    web_research: str = "web_research.md"
    text_research: str = "grok_research.json"
    vision: str = "gemini_vision.json"
    risk_score: str = "risk_score.json"
    raw_report: str = "analysis_result_raw.md"
    public_report: str = "analysis_result.md"


class ListingJobRepositoryProtocol(Protocol):
    def job_dir(
        self,
        slug: str,
        *,
        create: bool = False,
        require: bool = False,
    ) -> Path: ...

    def artifact_path(
        self,
        slug: str,
        filename: str,
        *,
        public_only: bool = False,
    ) -> Path: ...

    def read_text(
        self,
        slug: str,
        filename: str,
        *,
        public_only: bool = False,
        default: str | None = None,
    ) -> str | None: ...

    def write_text(self, slug: str, filename: str, content: str) -> Path: ...

    def write_json(
        self,
        slug: str,
        filename: str,
        value: Any,
        *,
        indent: int = 2,
        ensure_ascii: bool = False,
    ) -> Path: ...
