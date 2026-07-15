import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch

from token_tracker import TokenTracker, analysis_run_context


class TokenTrackerThinkingTests(unittest.TestCase):
    def test_stats_include_thinking_and_provider_total_tokens(self):
        with tempfile.TemporaryDirectory() as temp:
            tracker = TokenTracker(str(Path(temp) / "tokens.json"))
            tracker.record_request(
                model="gemini-test",
                phase="final_synthesis",
                request_type="stream_generate_content",
                input_tokens=10,
                output_tokens=4,
                actual_input_tokens=100,
                actual_output_tokens=40,
                actual_thinking_tokens=60,
                actual_total_tokens=200,
                status="success",
                duration_ms=123,
            )

            stats = tracker.get_stats()

            self.assertEqual(stats["totals"]["input_tokens"], 100)
            self.assertEqual(stats["totals"]["output_tokens"], 40)
            self.assertEqual(stats["totals"]["thinking_tokens"], 60)
            self.assertEqual(stats["totals"]["total_tokens"], 200)
            self.assertEqual(stats["recent_requests"][0]["phase"], "final_synthesis")
            self.assertEqual(stats["recent_requests"][0]["thinking_tokens"], 60)

    def test_explicit_provider_zero_is_not_replaced_by_estimate(self):
        with tempfile.TemporaryDirectory() as temp:
            tracker = TokenTracker(str(Path(temp) / "tokens.json"))
            tracker.record_request(
                model="gemini-test",
                input_tokens=100,
                output_tokens=80,
                actual_input_tokens=0,
                actual_output_tokens=0,
                actual_thinking_tokens=0,
                actual_total_tokens=0,
                status="success",
                duration_ms=1,
            )

            stats = tracker.get_stats()

            self.assertEqual(stats["totals"]["input_tokens"], 0)
            self.assertEqual(stats["totals"]["output_tokens"], 0)
            self.assertEqual(stats["totals"]["thinking_tokens"], 0)
            self.assertEqual(stats["totals"]["total_tokens"], 0)

    def test_run_summary_tracks_cached_tokens_without_double_counting(self):
        with tempfile.TemporaryDirectory() as temp:
            tracker = TokenTracker(str(Path(temp) / "tokens.json"))
            with analysis_run_context("run-123"):
                tracker.record_request(
                    model="gemini-test",
                    phase="text_research",
                    input_tokens=1000,
                    output_tokens=100,
                    actual_input_tokens=1000,
                    actual_output_tokens=100,
                    actual_thinking_tokens=20,
                    cached_input_tokens=700,
                    actual_total_tokens=1120,
                    status="success",
                    duration_ms=10,
                    attempt=2,
                    retry_reason="json_recovery",
                )

            summary = tracker.summarize_run("run-123")
            phase = summary["usage_by_phase"]["text_research"]

            self.assertEqual(summary["call_count"], 1)
            self.assertEqual(summary["retry_count"], 1)
            self.assertEqual(summary["recovery_count"], 1)
            self.assertEqual(phase["input_tokens"], 1000)
            self.assertEqual(phase["cached_input_tokens"], 700)
            self.assertEqual(phase["total_tokens"], 1120)
            self.assertEqual(phase["actual_coverage"]["total"], 1.0)

    def test_failed_request_without_provider_usage_has_no_confirmed_cost(self):
        with tempfile.TemporaryDirectory() as temp:
            tracker = TokenTracker(str(Path(temp) / "tokens.json"))
            tracker.record_request(
                model="unknown-model",
                input_tokens=1000,
                output_tokens=500,
                status="http_503",
                duration_ms=1,
            )

            entry = tracker.get_recent_requests(1)[0]

            self.assertEqual(entry["estimated_cost"], 0.0)
            self.assertEqual(entry["usage_source"], "estimated")

    def test_model_rate_configuration_is_recorded_on_entry(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ,
            {
                "SCRAPPER_TOKEN_MODEL_RATES_JSON":
                '{"gemini-test":{"input_per_1m":0.3,"output_per_1m":2.5}}'
            },
            clear=False,
        ):
            tracker = TokenTracker(str(Path(temp) / "tokens.json"))
            tracker.record_request(
                model="gemini-test",
                input_tokens=1000,
                output_tokens=1000,
                status="success",
                duration_ms=1,
            )

            entry = tracker.get_recent_requests(1)[0]

            self.assertEqual(entry["cost_rate_source"], "model_configured")
            self.assertEqual(entry["cost_rates"]["input_per_1m"], 0.3)


if __name__ == "__main__":
    unittest.main()
