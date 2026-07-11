import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import web_server
from scrapper_demo import create_app
from scrapper_demo.progress import (
    RUNTIME_STATE_KEY,
    DailyRateLimiter,
    DemoRuntimeState,
    ProgressState,
)


class ProgressStateTest(unittest.TestCase):
    def test_sse_tracking_preserves_dashboard_contract(self):
        progress = ProgressState()
        progress.track_sse(
            "".join(
                [
                    f"data: {json.dumps({'status': 'Researching'})}\n\n",
                    f"data: {json.dumps({'log': 'Scraper line'})}\n\n",
                    f"data: {json.dumps({'token_usage': {'input_tokens': 10, 'output_tokens': 2}})}\n\n",
                    f"data: {json.dumps({'done': True})}\n\n",
                    "data: [DONE]\n\n",
                ]
            )
        )

        snapshot = progress.snapshot()
        self.assertEqual(snapshot["status"], "Done")
        self.assertTrue(snapshot["done"])
        self.assertIn("Scraper line", snapshot["log_lines"])
        self.assertIn("Tokens sent: ~10, received: ~2", snapshot["log_lines"])

    def test_progress_updates_are_synchronized_and_bounded(self):
        progress = ProgressState(max_log_lines=120)

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(lambda index: progress.update(line=f"line-{index}"), range(250)))

        snapshot = progress.snapshot()
        self.assertEqual(len(snapshot["log_lines"]), 120)
        self.assertIsInstance(snapshot["log_lines"], list)


class DailyRateLimiterTest(unittest.TestCase):
    def test_daily_bucket_is_utc_and_limit_is_atomic(self):
        limiter = DailyRateLimiter()
        now = datetime(2026, 7, 11, 23, 30, tzinfo=timezone.utc)

        with ThreadPoolExecutor(max_workers=8) as executor:
            allowed = list(
                executor.map(
                    lambda _index: limiter.allow("203.0.113.20", 5, now=now),
                    range(20),
                )
            )

        self.assertEqual(sum(allowed), 5)
        self.assertEqual(limiter.snapshot(), {"2026-07-11:203.0.113.20": 5})


class RuntimeStateFactoryTest(unittest.TestCase):
    def test_job_gate_uses_configured_capacity(self):
        state = DemoRuntimeState(2)

        self.assertTrue(state.jobs.acquire())
        self.assertTrue(state.jobs.acquire())
        self.assertFalse(state.jobs.acquire())
        state.jobs.release()
        state.jobs.release()

    def test_factory_apps_do_not_share_progress_rate_or_job_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            first = create_app(
                {
                    "TESTING": True,
                    "DEMO_RATE_LIMIT_PER_IP": "1/day",
                    "DEMO_MAX_CONCURRENT_JOBS": 1,
                    "SCRAPPER_AUTA_DIR": str(Path(temp_dir) / "first"),
                }
            )
            second = create_app(
                {
                    "TESTING": True,
                    "DEMO_RATE_LIMIT_PER_IP": "1/day",
                    "DEMO_MAX_CONCURRENT_JOBS": 1,
                    "SCRAPPER_AUTA_DIR": str(Path(temp_dir) / "second"),
                }
            )

            first_state = first.extensions[RUNTIME_STATE_KEY]
            second_state = second.extensions[RUNTIME_STATE_KEY]
            self.assertIsNot(first_state, second_state)

            with first.test_request_context(headers={"X-Forwarded-For": "203.0.113.30"}):
                web_server._set_current_progress(status="first", line="first")
                self.assertIsNone(web_server._check_demo_rate_limit())
                first_rate_error = web_server._check_demo_rate_limit()
            with second.test_request_context(headers={"X-Forwarded-For": "203.0.113.30"}):
                web_server._set_current_progress(status="second", line="second")
                self.assertIsNone(web_server._check_demo_rate_limit())

            self.assertEqual(first_rate_error[1], 429)
            self.assertEqual(first_state.progress.snapshot()["status"], "first")
            self.assertEqual(second_state.progress.snapshot()["status"], "second")
            self.assertEqual(len(first_state.rate_limiter.snapshot()), 1)
            self.assertEqual(len(second_state.rate_limiter.snapshot()), 1)


if __name__ == "__main__":
    unittest.main()
