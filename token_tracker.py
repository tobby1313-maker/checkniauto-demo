"""Token usage tracking for Gemini and Grok API calls."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

try:
    import msvcrt
except ImportError:  # pragma: no cover - Windows only module
    msvcrt = None

try:
    import fcntl
except ImportError:  # pragma: no cover - Unix only module
    fcntl = None


DATA_DIR = os.environ.get("SCRAPPER_DATA_DIR") or os.path.join(tempfile.gettempdir(), "scrapper-demo")
AUTA_DIR = os.environ.get("SCRAPPER_AUTA_DIR") or os.path.join(DATA_DIR, "Auta")
TOKEN_USAGE_PATH = os.environ.get("SCRAPPER_TOKEN_USAGE_PATH") or os.path.join(AUTA_DIR, "token_usage.json")
MAX_RECENT_REQUESTS = 500
IMAGE_INPUT_TOKENS_PER_ATTACHMENT = int(os.environ.get("SCRAPPER_IMAGE_INPUT_TOKENS_PER_ATTACHMENT", "1290") or 1290)
INPUT_COST_PER_1M = float(os.environ.get("SCRAPPER_TOKEN_INPUT_COST_PER_1M", "1.5") or 1.5)
OUTPUT_COST_PER_1M = float(os.environ.get("SCRAPPER_TOKEN_OUTPUT_COST_PER_1M", "9.00") or 9.00)
TOKEN_COST_CURRENCY = os.environ.get("SCRAPPER_TOKEN_COST_CURRENCY", "EUR")

_process_lock = threading.RLock()
_tracking_context: ContextVar[dict[str, Any] | None] = ContextVar(
    "scrapper_tracking_context", default=None
)


def new_analysis_run_id() -> str:
    """Return a non-sensitive identifier shared by all calls in one analysis."""
    return uuid.uuid4().hex


@contextmanager
def tracking_context(**values: Any) -> Iterator[None]:
    """Temporarily attach phase/retry metadata to provider telemetry.

    Providers remain usable outside the pipeline.  In that case the context is
    simply absent and the tracker keeps writing the legacy-compatible record.
    """
    previous = dict(_tracking_context.get() or {})
    previous.update(values)
    token = _tracking_context.set(previous)
    try:
        yield
    finally:
        _tracking_context.reset(token)


def analysis_run_context(run_id: str) -> Any:
    return tracking_context(analysis_run_id=run_id)


def current_tracking_value(name: str, default: Any = None) -> Any:
    return (_tracking_context.get() or {}).get(name, default)


def estimate_text_tokens(text: str | None) -> int:
    """Approximate text tokens with the common 4 chars ~= 1 token heuristic."""
    if not text:
        return 0
    return max(1, round(len(str(text)) / 4))


def estimate_image_tokens(image_data_list: list | None) -> int:
    """Estimate multimodal input tokens without treating base64 as prompt text."""
    if not image_data_list:
        return 0
    attachment_count = 0
    for image in image_data_list:
        try:
            _filename, _image_base64, _mime_type = image
        except (TypeError, ValueError):
            continue
        attachment_count += 1
    return max(0, attachment_count * IMAGE_INPUT_TOKENS_PER_ATTACHMENT)


def estimate_request_tokens(system_prompt: str | None, user_content: str | None, image_data_list: list | None = None) -> int:
    return (
        estimate_text_tokens(system_prompt)
        + estimate_text_tokens(user_content)
        + estimate_image_tokens(image_data_list)
    )


def estimate_output_tokens(text: str | None) -> int:
    return estimate_text_tokens(text)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _empty_store() -> dict:
    return {
        "version": 1,
        "created_at": _utc_now_iso(),
        "requests": [],
    }


def _model_cost_rates(model: str | None) -> tuple[dict[str, float], str]:
    """Resolve a model rate without making an unlabelled pricing claim.

    The legacy input/output environment variables remain the fallback.  A
    deployment can provide exact per-model rates as JSON, for example
    ``{"gemini-2.5-flash": {"input_per_1m": 0.3, "output_per_1m": 2.5}}``.
    """
    fallback = {
        "input_per_1m": INPUT_COST_PER_1M,
        "output_per_1m": OUTPUT_COST_PER_1M,
    }
    raw_rates = os.environ.get("SCRAPPER_TOKEN_MODEL_RATES_JSON", "").strip()
    if not raw_rates:
        return fallback, "configured_fallback"
    try:
        parsed = json.loads(raw_rates)
    except json.JSONDecodeError:
        return fallback, "invalid_configured_fallback"
    if not isinstance(parsed, dict):
        return fallback, "invalid_configured_fallback"

    model_name = str(model or "").strip().lower()
    candidates = [model_name]
    if "/" in model_name:
        candidates.append(model_name.rsplit("/", 1)[-1])
    for candidate in candidates:
        value = parsed.get(candidate)
        if not isinstance(value, dict):
            continue
        try:
            input_rate = float(value["input_per_1m"])
            output_rate = float(value["output_per_1m"])
        except (KeyError, TypeError, ValueError):
            continue
        if input_rate < 0 or output_rate < 0:
            continue
        return {
            "input_per_1m": input_rate,
            "output_per_1m": output_rate,
        }, "model_configured"
    return fallback, "unknown_model_fallback" if model_name else "configured_fallback"


def _estimate_cost(
    input_tokens: int,
    output_tokens: int,
    *,
    model: str | None = None,
) -> float:
    rates, _source = _model_cost_rates(model)
    return round(
        (max(0, int(input_tokens or 0)) / 1_000_000 * rates["input_per_1m"])
        + (max(0, int(output_tokens or 0)) / 1_000_000 * rates["output_per_1m"]),
        6,
    )


def _effective_tokens(entry: dict[str, Any]) -> dict[str, int]:
    """Use provider zeroes as real values; fall back only when absent."""
    actual_input = entry.get("actual_input_tokens")
    actual_output = entry.get("actual_output_tokens")
    actual_thinking = entry.get("actual_thinking_tokens")
    actual_total = entry.get("actual_total_tokens")
    input_tokens = int(actual_input) if actual_input is not None else int(entry.get("input_tokens") or 0)
    visible_output = int(actual_output) if actual_output is not None else int(entry.get("output_tokens") or 0)
    thinking = int(actual_thinking) if actual_thinking is not None else 0
    total = (
        int(actual_total)
        if actual_total is not None
        else input_tokens + visible_output + thinking
    )
    return {
        "input_tokens": max(0, input_tokens),
        "visible_output_tokens": max(0, visible_output),
        "thinking_tokens": max(0, thinking),
        "cached_input_tokens": max(0, int(entry.get("cached_input_tokens") or 0)),
        "total_tokens": max(0, total),
    }


def _entry_cost(entry: dict[str, Any]) -> float:
    has_provider_usage = any(
        entry.get(key) is not None
        for key in (
            "actual_input_tokens",
            "actual_output_tokens",
            "actual_thinking_tokens",
            "cached_input_tokens",
            "actual_total_tokens",
        )
    )
    if entry.get("status") not in {None, "success", "truncated"} and not has_provider_usage:
        # A failed HTTP attempt without provider usage is not confirmed billable.
        return 0.0
    effective = _effective_tokens(entry)
    return _estimate_cost(
        effective["input_tokens"],
        effective["visible_output_tokens"] + effective["thinking_tokens"],
        model=str(entry.get("model") or ""),
    )


@contextmanager
def _locked_file(lock_path: str):
    Path(lock_path).parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+b") as lock_file:
        if msvcrt:
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        elif fcntl:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if msvcrt:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            elif fcntl:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


class TokenTracker:
    def __init__(self, path: str | None = None):
        self.path = path or TOKEN_USAGE_PATH
        self.lock_path = self.path + ".lock"

    def _read_unlocked(self) -> dict:
        if not os.path.exists(self.path):
            return _empty_store()
        try:
            with open(self.path, "r", encoding="utf-8") as usage_file:
                data = json.load(usage_file)
        except (OSError, json.JSONDecodeError):
            return _empty_store()
        if not isinstance(data, dict):
            return _empty_store()
        data.setdefault("version", 1)
        data.setdefault("created_at", _utc_now_iso())
        data.setdefault("requests", [])
        if not isinstance(data["requests"], list):
            data["requests"] = []
        return data

    def _write_unlocked(self, data: dict) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        temp_path = f"{self.path}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        with open(temp_path, "w", encoding="utf-8") as usage_file:
            json.dump(data, usage_file, indent=2, ensure_ascii=False)
            usage_file.write("\n")
        os.replace(temp_path, self.path)

    def _mutate(self, callback):
        with _process_lock:
            with _locked_file(self.lock_path):
                data = self._read_unlocked()
                result = callback(data)
                self._write_unlocked(data)
                return result

    def record_request(
        self,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        status: str,
        duration_ms: int,
        listing_slug: str | None = None,
        request_type: str = "generate_content",
        phase: str | None = None,
        error: str | None = None,
        actual_input_tokens: int | None = None,
        actual_output_tokens: int | None = None,
        actual_thinking_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        actual_total_tokens: int | None = None,
        analysis_run_id: str | None = None,
        attempt: int | None = None,
        retry_reason: str | None = None,
        visible_output_tokens: int | None = None,
        total_tokens: int | None = None,
        usage_source: str | None = None,
        thinking_mode: str | None = None,
        max_output_tokens: int | None = None,
        grounding_enabled: bool | None = None,
        provider_request_id: str | None = None,
        finish_reason: str | None = None,
        output_chars: int | None = None,
    ) -> dict:
        context_run_id = current_tracking_value("analysis_run_id")
        context_phase = current_tracking_value("phase")
        context_attempt = current_tracking_value("attempt")
        context_retry_reason = current_tracking_value("retry_reason")
        context_thinking_mode = current_tracking_value("thinking_mode")
        context_max_output = current_tracking_value("max_output_tokens")
        context_grounding = current_tracking_value("grounding_enabled")
        provider_usage_present = any(
            value is not None
            for value in (
                actual_input_tokens,
                actual_output_tokens,
                actual_thinking_tokens,
                cached_input_tokens,
                actual_total_tokens,
            )
        )
        if usage_source is None:
            usage_source = "provider" if provider_usage_present else "estimated"
        resolved_input = (
            int(actual_input_tokens)
            if actual_input_tokens is not None
            else int(input_tokens or 0)
        )
        resolved_visible_output = (
            int(actual_output_tokens)
            if actual_output_tokens is not None
            else int(output_tokens or 0)
        )
        resolved_thinking = (
            int(actual_thinking_tokens) if actual_thinking_tokens is not None else 0
        )
        resolved_total = (
            int(actual_total_tokens)
            if actual_total_tokens is not None
            else resolved_input + resolved_visible_output + resolved_thinking
        )
        rates, rate_source = _model_cost_rates(model)
        entry = {
            "id": uuid.uuid4().hex,
            "timestamp": _utc_now_iso(),
            "model": model,
            "request_type": request_type,
            "phase": phase if phase is not None else context_phase,
            "listing_slug": listing_slug,
            "input_tokens": int(input_tokens or 0),
            "output_tokens": int(output_tokens or 0),
            "actual_input_tokens": actual_input_tokens,
            "actual_output_tokens": actual_output_tokens,
            "actual_thinking_tokens": actual_thinking_tokens,
            "cached_input_tokens": cached_input_tokens,
            "actual_total_tokens": actual_total_tokens,
            "analysis_run_id": analysis_run_id or context_run_id,
            "attempt": attempt if attempt is not None else context_attempt,
            "retry_reason": retry_reason if retry_reason is not None else context_retry_reason,
            "visible_output_tokens": (
                int(visible_output_tokens)
                if visible_output_tokens is not None
                else resolved_visible_output
            ),
            "thinking_tokens": resolved_thinking,
            "total_tokens": (
                int(total_tokens) if total_tokens is not None else resolved_total
            ),
            "usage_source": usage_source,
            "thinking_mode": (
                thinking_mode if thinking_mode is not None else context_thinking_mode
            ),
            "max_output_tokens": (
                int(max_output_tokens)
                if max_output_tokens is not None
                else int(context_max_output)
                if context_max_output is not None
                else None
            ),
            "grounding_enabled": (
                grounding_enabled
                if grounding_enabled is not None
                else context_grounding
            ),
            "provider_request_id": str(provider_request_id)[:200]
            if provider_request_id
            else None,
            "finish_reason": str(finish_reason)[:100] if finish_reason else None,
            "output_chars": int(output_chars or 0) if output_chars is not None else None,
            "status": status,
            "duration_ms": int(duration_ms or 0),
            "error": str(error)[:500] if error else None,
        }
        entry["cost_rates"] = rates
        entry["cost_rate_source"] = rate_source
        entry["estimated_cost"] = _entry_cost(entry)

        def add_entry(data):
            data["requests"].append(entry)
            data["requests"] = data["requests"][-MAX_RECENT_REQUESTS:]
            data["updated_at"] = _utc_now_iso()
            return entry

        return self._mutate(add_entry)

    def get_recent_requests(self, limit: int = 50) -> list[dict]:
        limit = max(1, min(int(limit or 50), MAX_RECENT_REQUESTS))
        with _process_lock:
            with _locked_file(self.lock_path):
                data = self._read_unlocked()
        return list(reversed(data.get("requests", [])[-limit:]))

    def get_requests_for_run(
        self,
        analysis_run_id: str,
        *,
        phase: str | None = None,
    ) -> list[dict]:
        """Return chronological telemetry for one analysis run."""
        if not analysis_run_id:
            return []
        with _process_lock:
            with _locked_file(self.lock_path):
                data = self._read_unlocked()
        entries = [
            dict(entry)
            for entry in data.get("requests", [])
            if entry.get("analysis_run_id") == analysis_run_id
            and (phase is None or entry.get("phase") == phase)
        ]
        return entries

    def get_stats(self, recent_limit: int = 50) -> dict:
        with _process_lock:
            with _locked_file(self.lock_path):
                data = self._read_unlocked()

        requests = data.get("requests", [])
        today = datetime.now(timezone.utc).date().isoformat()
        totals = {
            "requests": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "thinking_tokens": 0,
            "total_tokens": 0,
            "estimated_cost": 0.0,
            "successful_requests": 0,
            "failed_requests": 0,
        }
        today_totals = totals.copy()
        by_listing = defaultdict(lambda: {"requests": 0, "input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0, "total_tokens": 0})
        by_model = defaultdict(lambda: {"requests": 0, "input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0, "total_tokens": 0})

        for entry in requests:
            effective = _effective_tokens(entry)
            input_tokens = effective["input_tokens"]
            output_tokens = effective["visible_output_tokens"]
            thinking_tokens = effective["thinking_tokens"]
            total_tokens = effective["total_tokens"]
            status = entry.get("status") or "unknown"

            totals["requests"] += 1
            totals["input_tokens"] += input_tokens
            totals["output_tokens"] += output_tokens
            totals["thinking_tokens"] += thinking_tokens
            totals["total_tokens"] += total_tokens
            totals["estimated_cost"] += float(entry.get("estimated_cost") or _entry_cost(entry))
            if status == "success":
                totals["successful_requests"] += 1
            else:
                totals["failed_requests"] += 1

            if str(entry.get("timestamp", "")).startswith(today):
                today_totals["requests"] += 1
                today_totals["input_tokens"] += input_tokens
                today_totals["output_tokens"] += output_tokens
                today_totals["thinking_tokens"] += thinking_tokens
                today_totals["total_tokens"] += total_tokens
                today_totals["estimated_cost"] += float(entry.get("estimated_cost") or _entry_cost(entry))
                if status == "success":
                    today_totals["successful_requests"] += 1
                else:
                    today_totals["failed_requests"] += 1

            listing = entry.get("listing_slug") or "unattributed"
            by_listing[listing]["requests"] += 1
            by_listing[listing]["input_tokens"] += input_tokens
            by_listing[listing]["output_tokens"] += output_tokens
            by_listing[listing]["thinking_tokens"] += thinking_tokens
            by_listing[listing]["total_tokens"] += total_tokens
            by_listing[listing]["estimated_cost"] = by_listing[listing].get("estimated_cost", 0.0) + float(entry.get("estimated_cost") or _entry_cost(entry))

            model = entry.get("model") or "unknown"
            by_model[model]["requests"] += 1
            by_model[model]["input_tokens"] += input_tokens
            by_model[model]["output_tokens"] += output_tokens
            by_model[model]["thinking_tokens"] += thinking_tokens
            by_model[model]["total_tokens"] += total_tokens
            by_model[model]["estimated_cost"] = by_model[model].get("estimated_cost", 0.0) + float(entry.get("estimated_cost") or _entry_cost(entry))

        for bucket in (totals, today_totals):
            bucket["estimated_cost"] = round(bucket["estimated_cost"], 6)
        for group in (by_listing, by_model):
            for item in group.values():
                item["estimated_cost"] = round(item.get("estimated_cost", 0.0), 6)

        recent_requests = []
        for entry in self.get_recent_requests(recent_limit):
            effective = _effective_tokens(entry)
            input_tokens = effective["input_tokens"]
            output_tokens = effective["visible_output_tokens"]
            thinking_tokens = effective["thinking_tokens"]
            total_tokens = effective["total_tokens"]
            recent_entry = dict(entry)
            recent_entry["input_tokens_effective"] = input_tokens
            recent_entry["visible_output_tokens"] = output_tokens
            recent_entry["thinking_tokens"] = thinking_tokens
            recent_entry["total_tokens"] = total_tokens
            recent_entry["estimated_cost"] = float(entry.get("estimated_cost") or _entry_cost(entry))
            recent_requests.append(recent_entry)

        return {
            "updated_at": data.get("updated_at") or data.get("created_at"),
            "storage_path": self.path,
            "cost_currency": TOKEN_COST_CURRENCY,
            "cost_rates": {
                "input_per_1m": INPUT_COST_PER_1M,
                "output_per_1m": OUTPUT_COST_PER_1M,
            },
            "cost_rates_source": "per-request model rates with configured fallback",
            "totals": totals,
            "today": today_totals,
            "by_listing": dict(sorted(by_listing.items(), key=lambda item: item[1]["total_tokens"], reverse=True)),
            "by_model": dict(sorted(by_model.items(), key=lambda item: item[1]["total_tokens"], reverse=True)),
            "recent_requests": recent_requests,
            "cost_note": "Set SCRAPPER_TOKEN_INPUT_COST_PER_1M and SCRAPPER_TOKEN_OUTPUT_COST_PER_1M to current provider prices for non-zero estimates.",
        }

    def summarize_run(
        self,
        analysis_run_id: str,
        *,
        duration_ms: int | None = None,
    ) -> dict[str, Any]:
        """Build a compact, admin-only usage summary for one analysis."""
        requests = self.get_requests_for_run(analysis_run_id)
        by_phase: dict[str, dict[str, Any]] = {}
        actual_fields = {
            "input": "actual_input_tokens",
            "visible_output": "actual_output_tokens",
            "thinking": "actual_thinking_tokens",
            "cached_input": "cached_input_tokens",
            "total": "actual_total_tokens",
        }

        def phase_bucket(name: str) -> dict[str, Any]:
            return by_phase.setdefault(
                name,
                {
                    "calls": 0,
                    "successful_calls": 0,
                    "failed_calls": 0,
                    "retries": 0,
                    "input_tokens": 0,
                    "visible_output_tokens": 0,
                    "thinking_tokens": 0,
                    "cached_input_tokens": 0,
                    "total_tokens": 0,
                    "estimated_cost": 0.0,
                    "actual_usage": {
                        "input": 0,
                        "visible_output": 0,
                        "thinking": 0,
                        "cached_input": 0,
                        "total": 0,
                    },
                    "actual_coverage": {
                        "input": 0.0,
                        "visible_output": 0.0,
                        "thinking": 0.0,
                        "cached_input": 0.0,
                        "total": 0.0,
                    },
                },
            )

        total_actual_counts = {key: 0 for key in actual_fields}
        total_estimated_cost = 0.0
        retry_count = 0
        recovery_count = 0
        grounding_count = 0
        successful_count = 0
        failed_count = 0
        for entry in requests:
            bucket = phase_bucket(str(entry.get("phase") or "unknown"))
            bucket["calls"] += 1
            if entry.get("status") == "success":
                bucket["successful_calls"] += 1
                successful_count += 1
            else:
                bucket["failed_calls"] += 1
                failed_count += 1
            attempt = entry.get("attempt")
            retry_reason = str(entry.get("retry_reason") or "")
            if (isinstance(attempt, int) and attempt > 1) or retry_reason:
                bucket["retries"] += 1
                retry_count += 1
            if "recovery" in str(entry.get("phase") or "").lower() or "recover" in retry_reason.lower():
                recovery_count += 1
            if entry.get("grounding_enabled") is True or "grounding" in str(entry.get("phase") or "").lower():
                grounding_count += 1

            effective = _effective_tokens(entry)
            for key in (
                "input_tokens",
                "visible_output_tokens",
                "thinking_tokens",
                "cached_input_tokens",
                "total_tokens",
            ):
                bucket[key] += effective[key]
            cost = float(entry.get("estimated_cost") or _entry_cost(entry))
            bucket["estimated_cost"] += cost
            total_estimated_cost += cost
            for summary_key, entry_key in actual_fields.items():
                if entry.get(entry_key) is not None:
                    total_actual_counts[summary_key] += 1
                    bucket["actual_usage"][summary_key] += int(entry.get(entry_key) or 0)

        coverage_fields = {
            "input": "actual_input_tokens",
            "visible_output": "actual_output_tokens",
            "thinking": "actual_thinking_tokens",
            "cached_input": "cached_input_tokens",
            "total": "actual_total_tokens",
        }
        for phase, bucket in by_phase.items():
            bucket["estimated_cost"] = round(bucket["estimated_cost"], 6)
            calls = max(1, int(bucket["calls"]))
            for key, entry_key in coverage_fields.items():
                bucket["actual_coverage"][key] = round(
                    sum(
                        1
                        for entry in requests
                        if str(entry.get("phase") or "unknown") == phase
                        and entry.get(entry_key) is not None
                    )
                    / calls,
                    3,
                )

        return {
            "schema_version": 1,
            "analysis_run_id": analysis_run_id,
            "call_count": len(requests),
            "successful_calls": successful_count,
            "failed_calls": failed_count,
            "retry_count": retry_count,
            "recovery_count": recovery_count,
            "grounding_call_count": grounding_count,
            "duration_ms": int(duration_ms or sum(int(item.get("duration_ms") or 0) for item in requests)),
            "actual_usage_coverage": {
                key: round(count / max(1, len(requests)), 3)
                for key, count in total_actual_counts.items()
            },
            "usage_by_phase": by_phase,
            "estimated_cost": round(total_estimated_cost, 6),
            "cost_currency": TOKEN_COST_CURRENCY,
            "cost_rates_source": "per-request model rates with configured fallback",
        }


default_tracker = TokenTracker()
