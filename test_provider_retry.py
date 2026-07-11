import json
import unittest

from scrapper_demo.providers.errors import ApiKeyError, GroundingTransientError
from scrapper_demo.providers.retry import (
    collect_gemini_with_key_fallback,
    normalize_gemini_key_entries,
)


def exhaust_generator(generator):
    yielded = []
    while True:
        try:
            yielded.append(next(generator))
        except StopIteration as stop:
            return yielded, stop.value


class ProviderRetryTest(unittest.TestCase):
    def test_key_entries_are_trimmed_deduplicated_and_labeled(self):
        entries = normalize_gemini_key_entries(
            [" primary ", "", "primary", "backup", "third"]
        )

        self.assertEqual(
            entries,
            [
                {"key": "primary", "label": "primary"},
                {"key": "backup", "label": "backup"},
                {"key": "third", "label": "backup 2"},
            ],
        )

    def test_same_key_retry_then_backup_key_success(self):
        entries = normalize_gemini_key_entries(["primary", "backup"])
        calls = []

        def stream(key):
            calls.append(key)
            if calls == ["primary"]:
                raise GroundingTransientError("temporary")
            if calls == ["primary", "primary"]:
                raise ApiKeyError("primary exhausted")
            yield "backup output"

        events, result = exhaust_generator(
            collect_gemini_with_key_fallback(
                entries,
                "grounding",
                stream,
                retry_exceptions=(GroundingTransientError, ApiKeyError),
                same_key_retries=1,
                same_key_retry_exceptions=(GroundingTransientError,),
                log=lambda _message: None,
                sleep=lambda _seconds: None,
            )
        )

        statuses = [json.loads(event[6:])["status"] for event in events]
        self.assertEqual(calls, ["primary", "primary", "backup"])
        self.assertIn("Retrying the same Gemini key", statuses[0])
        self.assertIn("Trying backup Gemini key", statuses[1])
        self.assertEqual(result, ("backup output", entries[1]))

    def test_partial_output_prevents_retry_on_another_key(self):
        entries = normalize_gemini_key_entries(["primary", "backup"])
        calls = []

        def stream(key):
            calls.append(key)
            yield "partial"
            raise ApiKeyError("failed after output")

        generator = collect_gemini_with_key_fallback(
            entries,
            "vision",
            stream,
            log=lambda _message: None,
            sleep=lambda _seconds: None,
        )

        with self.assertRaises(ApiKeyError):
            list(generator)
        self.assertEqual(calls, ["primary"])


if __name__ == "__main__":
    unittest.main()
