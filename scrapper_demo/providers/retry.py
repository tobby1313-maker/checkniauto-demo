"""Provider-independent Gemini key fallback orchestration."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable, Sequence
from typing import Generator

from scrapper_demo.contracts import GeminiKeyEntry
from scrapper_demo.logging import safe_log
from .errors import ApiKeyError, RateLimitError


def normalize_gemini_key_entries(
    gemini_keys: str | Sequence[str] | None,
) -> list[GeminiKeyEntry]:
    """Normalize one or more Gemini keys into labeled, de-duplicated entries."""
    if isinstance(gemini_keys, str):
        raw_keys = [gemini_keys]
    else:
        raw_keys = list(gemini_keys or [])

    entries: list[GeminiKeyEntry] = []
    seen: set[str] = set()
    for key in raw_keys:
        key = (key or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        index = len(entries)
        label = "primary" if index == 0 else "backup" if index == 1 else f"backup {index}"
        entries.append({"key": key, "label": label})
    return entries


def gemini_retry_status(
    failed_entry: GeminiKeyEntry,
    next_entry: GeminiKeyEntry,
    phase_name: str,
    exc: BaseException,
    *,
    log: Callable[[object], None] = safe_log,
) -> str:
    log(
        f"Gemini {failed_entry['label']} key failed during {phase_name}: {exc}. "
        f"Trying {next_entry['label']} key."
    )
    return (
        f"Gemini {failed_entry['label']} key failed during {phase_name}. "
        f"Trying {next_entry['label']} Gemini key..."
    )


def collect_gemini_with_key_fallback(
    key_entries: Sequence[GeminiKeyEntry],
    phase_name: str,
    stream_factory: Callable[[str], Iterable[str]],
    retry_exceptions: tuple[type[BaseException], ...] = (ApiKeyError, RateLimitError),
    same_key_retries: int = 0,
    same_key_retry_exceptions: tuple[type[BaseException], ...] | None = None,
    *,
    log: Callable[[object], None] = safe_log,
    sleep: Callable[[float], None] = time.sleep,
) -> Generator[str, None, tuple[str, GeminiKeyEntry | None]]:
    """Collect a hidden Gemini stream with same-key retry and key fallback."""
    if same_key_retry_exceptions is None:
        same_key_retry_exceptions = retry_exceptions

    last_exc = None
    for index, entry in enumerate(key_entries):
        for attempt in range(same_key_retries + 1):
            chunks = []
            try:
                for chunk in stream_factory(entry["key"]):
                    chunks.append(chunk)
                return "".join(chunks), entry
            except retry_exceptions as exc:
                if chunks:
                    raise
                last_exc = exc
                if attempt < same_key_retries and isinstance(
                    exc, same_key_retry_exceptions
                ):
                    log(
                        f"Gemini {entry['label']} key failed during {phase_name}: {exc}. "
                        "Retrying same key."
                    )
                    status = (
                        f"Gemini {entry['label']} key failed during {phase_name}. "
                        "Retrying the same Gemini key..."
                    )
                    yield f"data: {json.dumps({'status': status})}\n\n"
                    sleep(1)
                    continue
                if index >= len(key_entries) - 1:
                    raise
                status = gemini_retry_status(
                    entry,
                    key_entries[index + 1],
                    phase_name,
                    exc,
                    log=log,
                )
                yield f"data: {json.dumps({'status': status})}\n\n"
                break

    if last_exc:
        raise last_exc
    return "", None
