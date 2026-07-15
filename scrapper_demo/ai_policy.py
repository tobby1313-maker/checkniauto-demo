"""Central AI phase policies and input-budget compaction helpers."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class PhasePolicy:
    """Immutable generation and retry policy for one logical AI phase."""

    phase: str
    max_input_tokens: int | None
    max_output_tokens: int | None
    visible_target_tokens: int
    temperature: float
    thinking_mode: str
    max_attempts: int


@dataclass(frozen=True, slots=True)
class BudgetResult:
    """Auditable result of checking and, when needed, compacting one input."""

    system_prompt: str
    user_content: str
    pre_tokens: int
    post_tokens: int
    max_input_tokens: int | None
    counting_method: str
    applied_compactions: tuple[str, ...]
    warnings: tuple[str, ...]
    within_budget: bool


_OPTIMIZED_POLICIES: Mapping[str, PhasePolicy] = MappingProxyType({
    "component_identity_grounding": PhasePolicy(
        "component_identity_grounding", 5_000, None, 600, 0.1, "off", 2
    ),
    "reliability_grounding": PhasePolicy(
        "reliability_grounding", 8_000, None, 2_500, 0.2, "off", 2
    ),
    "text_research": PhasePolicy(
        "text_research", 10_000, 4_000, 2_500, 0.1, "off", 1
    ),
    "text_recovery": PhasePolicy(
        "text_recovery", 8_000, 3_200, 2_200, 0.0, "off", 1
    ),
    "vision": PhasePolicy("vision", 8_000, 4_000, 1_800, 0.1, "off", 1),
    "vision_recovery": PhasePolicy(
        "vision_recovery", 8_000, 3_500, 1_600, 0.0, "off", 1
    ),
    "final_synthesis": PhasePolicy(
        "final_synthesis", 9_000, 6_000, 2_400, 0.3, "low", 2
    ),
})

_LEGACY_POLICIES: Mapping[str, PhasePolicy] = {
    name: PhasePolicy(name, None, 65_536, policy.visible_target_tokens, 0.7, "default", policy.max_attempts)
    for name, policy in _OPTIMIZED_POLICIES.items()
}
_LEGACY_POLICIES = MappingProxyType({
    **_LEGACY_POLICIES,
    "vision_recovery": PhasePolicy(
        "vision_recovery", None, 6_000, 1_600, 0.0, "default", 1
    ),
})

_PHASE_ALIASES = {
    "identity_grounding": "component_identity_grounding",
    "grounding": "reliability_grounding",
    "market_grounding_mobile_de": "reliability_grounding",
    "research": "text_research",
    "recovery": "text_recovery",
    "final": "final_synthesis",
}


def analysis_profile(value: str | None = None) -> str:
    """Return the active supported profile without leaking arbitrary env values."""
    profile = str(value or os.environ.get("DEMO_ANALYSIS_PROFILE", "quality_optimized"))
    profile = profile.strip().lower()
    return profile if profile in {"legacy", "quality_optimized", "cost_optimized"} else "quality_optimized"


def get_phase_policy(phase: str, *, profile: str | None = None) -> PhasePolicy:
    """Resolve one phase policy, including stable aliases used by telemetry."""
    normalized = _PHASE_ALIASES.get(str(phase or "").strip().lower(), str(phase or "").strip().lower())
    policies = _LEGACY_POLICIES if analysis_profile(profile) == "legacy" else _OPTIMIZED_POLICIES
    if normalized not in policies:
        raise KeyError(f"Unknown AI phase policy: {phase!r}")
    return policies[normalized]


def _local_token_estimate(system_prompt: str, user_content: str) -> int:
    # Conservative enough for budget fallback while keeping this module provider-neutral.
    return max(1, (len(system_prompt) + len(user_content) + 2) // 4)


def _count_tokens(
    system_prompt: str,
    user_content: str,
    counter: Callable[[str, str], int | tuple[int, str]] | None,
) -> tuple[int, str, str | None]:
    if counter is not None:
        try:
            result = counter(system_prompt, user_content)
            if isinstance(result, tuple):
                tokens, method = result
            else:
                tokens, method = result, "provider_count_tokens"
            return max(0, int(tokens)), str(method or "provider_count_tokens"), None
        except Exception as exc:  # counting must not stop a customer analysis
            warning = f"Provider token count unavailable; used local estimate ({type(exc).__name__})."
            return _local_token_estimate(system_prompt, user_content), "local_estimate_fallback", warning
    return _local_token_estimate(system_prompt, user_content), "local_estimate", None


def _deduplicate_lines(value: str) -> str:
    seen: set[str] = set()
    output: list[str] = []
    for line in value.splitlines():
        key = re.sub(r"\s+", " ", line).strip().casefold()
        if key and len(key) >= 24 and key in seen:
            continue
        if key:
            seen.add(key)
        output.append(line)
    return "\n".join(output)


def _embedded_json(value: str) -> tuple[str, Any] | None:
    start = value.find("{")
    if start < 0:
        return None
    try:
        return value[:start], json.loads(value[start:])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _json_text(prefix: str, payload: Any) -> str:
    return prefix + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _walk_mutable(value: Any, callback: Callable[[dict[str, Any]], None]) -> None:
    if isinstance(value, dict):
        callback(value)
        for item in value.values():
            _walk_mutable(item, callback)
    elif isinstance(value, list):
        for item in value:
            _walk_mutable(item, callback)


def _compact_json_step(value: str, step: str) -> str:
    parsed = _embedded_json(value)
    if parsed is None:
        return value
    prefix, payload = parsed
    if not isinstance(payload, dict):
        return value

    def mutate(mapping: dict[str, Any]) -> None:
        if step == "raw_web_after_normalized_findings":
            if mapping.get("web_research_findings") and "web_research" in mapping:
                mapping["web_research"] = {}
            if isinstance(mapping.get("grounded_research"), str):
                # Before normalization we cannot safely know which narrative
                # sentence is high impact. Remove only exact duplicate lines
                # and excess citation rows; never blind-truncate the evidence.
                lines = mapping["grounded_research"].splitlines()
                compacted: list[str] = []
                seen: set[str] = set()
                citation_rows = 0
                in_citations = False
                for line in lines:
                    normalized = re.sub(r"\s+", " ", line).strip().casefold()
                    if normalized.startswith("###") and "cit" in normalized:
                        in_citations = True
                    elif normalized.startswith("###"):
                        in_citations = False
                    if in_citations and normalized.startswith(("- http", "- [")):
                        citation_rows += 1
                        if citation_rows > 8:
                            continue
                    if normalized and normalized in seen:
                        continue
                    if normalized:
                        seen.add(normalized)
                    compacted.append(line)
                mapping["grounded_research"] = "\n".join(compacted)
        elif step == "duplicate_listing_identity_data":
            nested = mapping.get("text_research")
            if isinstance(nested, dict) and isinstance(mapping.get("listing"), dict):
                nested.pop("listing_facts", None)
        elif step == "repeated_claims_and_risks":
            caps = {
                "seller_claims": 4,
                "missing_or_uncertain_data": 4,
                "data_conflicts": 3,
                "consistency_checks": 4,
                "web_research_findings": 5,
                "technical_risks": 6,
                "expected_costs": 6,
                "text_research_risk_flags": 4,
            }
            for key, cap in caps.items():
                if isinstance(mapping.get(key), list):
                    mapping[key] = mapping[key][:cap]
        elif step == "low_priority_source_prose":
            if isinstance(mapping.get("sources_used"), list):
                mapping["sources_used"] = mapping["sources_used"][:8]
            for key in ("used_for", "notes", "buyer_impact"):
                if isinstance(mapping.get(key), str) and len(mapping[key]) > 280:
                    mapping[key] = mapping[key][:277].rstrip() + "..."
        elif step == "seller_description":
            for key in ("seller_description", "description", "listing_description"):
                if isinstance(mapping.get(key), str) and len(mapping[key]) > 1_200:
                    mapping[key] = mapping[key][:1_197].rstrip() + "..."
        elif step == "optional_low_impact_items":
            for key in list(mapping):
                if mapping[key] in (None, "", [], {}):
                    mapping.pop(key, None)
            for key in ("equipment", "supported_observations", "missing_views", "photo_limitations"):
                if isinstance(mapping.get(key), list):
                    mapping[key] = mapping[key][:4]

    _walk_mutable(payload, mutate)
    return _json_text(prefix, payload)


def _aggressive_json_shrink(value: str, *, string_limit: int, list_limit: int) -> str:
    parsed = _embedded_json(value)
    if parsed is None:
        return value
    prefix, payload = parsed

    def shrink(item: Any, key: str = "") -> Any:
        if isinstance(item, dict):
            return {name: shrink(child, name) for name, child in item.items() if child not in (None, "", [], {})}
        if isinstance(item, list):
            return [shrink(child, key) for child in item[:list_limit]]
        if isinstance(item, str):
            if key == "grounded_research":
                return item
            limit = string_limit
            return item if len(item) <= limit else item[: max(1, limit - 3)].rstrip() + "..."
        return item

    return _json_text(prefix, shrink(payload))


def _ensure_protected_values(value: str, protected_values: Iterable[Any]) -> tuple[str, bool]:
    required = [str(item).strip() for item in protected_values if str(item).strip()]
    missing = [item for item in required if item not in value]
    if not missing:
        return value, False
    parsed = _embedded_json(value)
    if parsed is not None and isinstance(parsed[1], dict):
        prefix, payload = parsed
        payload["protected_backend_values"] = missing
        return _json_text(prefix, payload), True
    appendix = "\n\n## Protected backend values\n" + "\n".join(f"- {item}" for item in missing)
    return value + appendix, True


def check_and_compact_input(
    system_prompt: str,
    user_content: str,
    policy: PhasePolicy,
    *,
    count_tokens: Callable[[str, str], int | tuple[int, str]] | None = None,
    protected_values: Sequence[Any] = (),
) -> BudgetResult:
    """Count an input and apply ordered, auditable, safety-preserving compaction."""
    pre_tokens, method, warning = _count_tokens(system_prompt, user_content, count_tokens)
    warnings = [warning] if warning else []
    max_input = policy.max_input_tokens
    if max_input is None or pre_tokens <= max_input:
        return BudgetResult(
            system_prompt, user_content, pre_tokens, pre_tokens, max_input, method, (), tuple(warnings), True
        )

    current = user_content
    applied: list[str] = []
    steps = (
        "duplicate_instructions",
        "raw_web_after_normalized_findings",
        "duplicate_listing_identity_data",
        "repeated_claims_and_risks",
        "low_priority_source_prose",
        "seller_description",
        "optional_low_impact_items",
    )
    # Provider count is deliberately used only before and after compaction;
    # intermediate decisions use the same local ratio to avoid several API calls.
    local_pre = _local_token_estimate(system_prompt, user_content)
    provider_ratio = pre_tokens / max(1, local_pre)
    for step in steps:
        candidate = _deduplicate_lines(current) if step == "duplicate_instructions" else _compact_json_step(current, step)
        candidate, restored = _ensure_protected_values(candidate, protected_values)
        if candidate != current:
            current = candidate
            applied.append(step)
            if restored:
                warnings.append(f"Protected values were restored after {step}.")
        estimated = round(_local_token_estimate(system_prompt, current) * provider_ratio)
        if estimated <= max_input:
            break

    if round(_local_token_estimate(system_prompt, current) * provider_ratio) > max_input:
        for string_limit, list_limit in ((600, 4), (320, 3), (180, 2)):
            candidate = _aggressive_json_shrink(current, string_limit=string_limit, list_limit=list_limit)
            candidate, restored = _ensure_protected_values(candidate, protected_values)
            if candidate != current:
                current = candidate
                applied.append(f"bounded_structured_compaction_{string_limit}_{list_limit}")
                if restored:
                    warnings.append("Protected values were restored after bounded structured compaction.")
            estimated = round(_local_token_estimate(system_prompt, current) * provider_ratio)
            if estimated <= max_input:
                break

    post_tokens, post_method, post_warning = _count_tokens(system_prompt, current, count_tokens)
    if post_warning:
        warnings.append(post_warning)
    if post_method != method:
        method = f"{method}->{post_method}"
    within_budget = post_tokens <= max_input
    if not within_budget:
        warnings.append(
            f"Input remains above the {max_input}-token safety ceiling after safe compaction; analysis continues with a warning."
        )
    return BudgetResult(
        system_prompt,
        current,
        pre_tokens,
        post_tokens,
        max_input,
        method,
        tuple(applied),
        tuple(dict.fromkeys(warnings)),
        within_budget,
    )
