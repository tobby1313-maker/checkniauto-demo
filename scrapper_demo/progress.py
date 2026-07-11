"""Thread-safe, process-local runtime state for one Flask app instance."""

from __future__ import annotations

import json
import threading
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any


RUNTIME_STATE_KEY = "scrapper_demo.runtime_state"


class ProgressState:
    def __init__(self, *, max_log_lines: int = 120):
        self._max_log_lines = max(1, int(max_log_lines))
        self._lock = threading.Lock()
        self._status = ""
        self._log_lines: list[str] = []
        self._done = False

    def update(self, *, status=None, line=None, done=False, reset=False) -> None:
        with self._lock:
            if reset:
                self._status = ""
                self._log_lines = []
                self._done = False
            if status is not None:
                self._status = str(status)
            if line:
                self._log_lines.append(str(line))
                self._log_lines = self._log_lines[-self._max_log_lines :]
            self._done = bool(done)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "status": self._status,
                "log_lines": list(self._log_lines),
                "done": self._done,
            }

    def track_sse(self, event_text) -> None:
        if not event_text:
            return
        for raw_line in str(event_text).splitlines():
            if not raw_line.startswith("data: "):
                continue
            payload = raw_line[6:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                continue

            if data.get("error"):
                message = "ERROR: " + str(data["error"])
                self.update(status=message, line=message, done=True)
                continue

            if data.get("status"):
                status = str(data["status"])
                self.update(status=status, line=status, done=bool(data.get("done")))

            for key in ("log", "line"):
                if data.get(key):
                    self.update(line=str(data[key]), done=bool(data.get("done")))

            if data.get("token_usage"):
                usage = data["token_usage"] or {}
                token_line = "Tokens sent: ~{0}, received: ~{1}".format(
                    usage.get("input_tokens", 0),
                    usage.get("output_tokens", 0),
                )
                self.update(line=token_line, done=False)

            if data.get("done"):
                self.update(status="Done", line="Done", done=True)


class DailyRateLimiter:
    def __init__(self):
        self._counts: defaultdict[str, int] = defaultdict(int)
        self._lock = threading.Lock()

    @staticmethod
    def bucket_key(client_id: str, *, now: datetime | None = None) -> str:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return f"{current.astimezone(timezone.utc).strftime('%Y-%m-%d')}:{client_id}"

    def allow(self, client_id: str, limit: int, *, now: datetime | None = None) -> bool:
        if int(limit) <= 0:
            return True
        key = self.bucket_key(client_id, now=now)
        with self._lock:
            if self._counts[key] >= int(limit):
                return False
            self._counts[key] += 1
            return True

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts)

    def clear(self) -> None:
        with self._lock:
            self._counts.clear()


class JobConcurrency:
    def __init__(self, max_concurrent_jobs: int):
        self.max_concurrent_jobs = max(1, int(max_concurrent_jobs))
        self._semaphore = threading.BoundedSemaphore(self.max_concurrent_jobs)

    def acquire(self, *, blocking: bool = False) -> bool:
        return self._semaphore.acquire(blocking=blocking)

    def release(self) -> None:
        self._semaphore.release()


class DemoRuntimeState:
    def __init__(self, max_concurrent_jobs: int):
        self.progress = ProgressState()
        self.rate_limiter = DailyRateLimiter()
        self.jobs = JobConcurrency(max_concurrent_jobs)
