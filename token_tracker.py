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
from datetime import datetime, timezone
from pathlib import Path

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
INPUT_COST_PER_1M = float(os.environ.get("SCRAPPER_TOKEN_INPUT_COST_PER_1M", "0") or 0)
OUTPUT_COST_PER_1M = float(os.environ.get("SCRAPPER_TOKEN_OUTPUT_COST_PER_1M", "0") or 0)
TOKEN_COST_CURRENCY = os.environ.get("SCRAPPER_TOKEN_COST_CURRENCY", "EUR")

_process_lock = threading.RLock()


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


def _estimate_cost(input_tokens: int, output_tokens: int) -> float:
    return round(
        (input_tokens / 1_000_000 * INPUT_COST_PER_1M)
        + (output_tokens / 1_000_000 * OUTPUT_COST_PER_1M),
        6,
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
        error: str | None = None,
        actual_input_tokens: int | None = None,
        actual_output_tokens: int | None = None,
    ) -> dict:
        entry = {
            "id": uuid.uuid4().hex,
            "timestamp": _utc_now_iso(),
            "model": model,
            "request_type": request_type,
            "listing_slug": listing_slug,
            "input_tokens": int(input_tokens or 0),
            "output_tokens": int(output_tokens or 0),
            "actual_input_tokens": actual_input_tokens,
            "actual_output_tokens": actual_output_tokens,
            "status": status,
            "duration_ms": int(duration_ms or 0),
            "error": str(error)[:500] if error else None,
        }

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
            "total_tokens": 0,
            "estimated_cost": 0.0,
            "successful_requests": 0,
            "failed_requests": 0,
        }
        today_totals = totals.copy()
        by_listing = defaultdict(lambda: {"requests": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
        by_model = defaultdict(lambda: {"requests": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0})

        for entry in requests:
            input_tokens = int(entry.get("actual_input_tokens") or entry.get("input_tokens") or 0)
            output_tokens = int(entry.get("actual_output_tokens") or entry.get("output_tokens") or 0)
            total_tokens = input_tokens + output_tokens
            status = entry.get("status") or "unknown"

            totals["requests"] += 1
            totals["input_tokens"] += input_tokens
            totals["output_tokens"] += output_tokens
            totals["total_tokens"] += total_tokens
            totals["estimated_cost"] += _estimate_cost(input_tokens, output_tokens)
            if status == "success":
                totals["successful_requests"] += 1
            else:
                totals["failed_requests"] += 1

            if str(entry.get("timestamp", "")).startswith(today):
                today_totals["requests"] += 1
                today_totals["input_tokens"] += input_tokens
                today_totals["output_tokens"] += output_tokens
                today_totals["total_tokens"] += total_tokens
                today_totals["estimated_cost"] += _estimate_cost(input_tokens, output_tokens)
                if status == "success":
                    today_totals["successful_requests"] += 1
                else:
                    today_totals["failed_requests"] += 1

            listing = entry.get("listing_slug") or "unattributed"
            by_listing[listing]["requests"] += 1
            by_listing[listing]["input_tokens"] += input_tokens
            by_listing[listing]["output_tokens"] += output_tokens
            by_listing[listing]["total_tokens"] += total_tokens
            by_listing[listing]["estimated_cost"] = by_listing[listing].get("estimated_cost", 0.0) + _estimate_cost(input_tokens, output_tokens)

            model = entry.get("model") or "unknown"
            by_model[model]["requests"] += 1
            by_model[model]["input_tokens"] += input_tokens
            by_model[model]["output_tokens"] += output_tokens
            by_model[model]["total_tokens"] += total_tokens
            by_model[model]["estimated_cost"] = by_model[model].get("estimated_cost", 0.0) + _estimate_cost(input_tokens, output_tokens)

        for bucket in (totals, today_totals):
            bucket["estimated_cost"] = round(bucket["estimated_cost"], 6)
        for group in (by_listing, by_model):
            for item in group.values():
                item["estimated_cost"] = round(item.get("estimated_cost", 0.0), 6)

        recent_requests = []
        for entry in self.get_recent_requests(recent_limit):
            input_tokens = int(entry.get("actual_input_tokens") or entry.get("input_tokens") or 0)
            output_tokens = int(entry.get("actual_output_tokens") or entry.get("output_tokens") or 0)
            recent_entry = dict(entry)
            recent_entry["estimated_cost"] = _estimate_cost(input_tokens, output_tokens)
            recent_requests.append(recent_entry)

        return {
            "updated_at": data.get("updated_at") or data.get("created_at"),
            "storage_path": self.path,
            "cost_currency": TOKEN_COST_CURRENCY,
            "cost_rates": {
                "input_per_1m": INPUT_COST_PER_1M,
                "output_per_1m": OUTPUT_COST_PER_1M,
            },
            "totals": totals,
            "today": today_totals,
            "by_listing": dict(sorted(by_listing.items(), key=lambda item: item[1]["total_tokens"], reverse=True)),
            "by_model": dict(sorted(by_model.items(), key=lambda item: item[1]["total_tokens"], reverse=True)),
            "recent_requests": recent_requests,
            "cost_note": "Set SCRAPPER_TOKEN_INPUT_COST_PER_1M and SCRAPPER_TOKEN_OUTPUT_COST_PER_1M to current provider prices for non-zero estimates.",
        }


default_tracker = TokenTracker()
