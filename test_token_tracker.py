import tempfile
import unittest
from pathlib import Path

from token_tracker import TokenTracker


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


if __name__ == "__main__":
    unittest.main()
