import os
import unittest
from unittest.mock import patch

import llm_client
from scrapper_demo.providers import gemini, grok, openrouter
from scrapper_demo.providers.errors import (
    ApiKeyError,
    GrokApiKeyError,
    OpenRouterApiKeyError,
)


class FakeResponse:
    def __init__(self, status_code, *, text="", lines=None):
        self.status_code = status_code
        self.text = text
        self._lines = list(lines or [])
        self.encoding = None

    def iter_lines(self):
        return iter(self._lines)


class ProviderAdapterTest(unittest.TestCase):
    def test_facade_reexports_provider_implementations_and_shared_errors(self):
        self.assertIs(llm_client.analyze_with_llm, gemini.analyze_with_llm)
        self.assertIs(llm_client.analyze_with_grok, grok.analyze_with_grok)
        self.assertIs(llm_client.analyze_with_openrouter, openrouter.analyze_with_openrouter)
        self.assertIs(llm_client.ApiKeyError, ApiKeyError)
        self.assertIs(llm_client.GrokApiKeyError, GrokApiKeyError)
        self.assertIs(llm_client.OpenRouterApiKeyError, OpenRouterApiKeyError)

    def test_missing_provider_keys_use_provider_specific_exception_types(self):
        with self.assertRaises(ApiKeyError):
            list(gemini.analyze_with_llm("", "system", "user"))
        with self.assertRaises(GrokApiKeyError):
            list(grok.analyze_with_grok("", "system", "user"))
        with self.assertRaises(OpenRouterApiKeyError):
            list(openrouter.analyze_with_openrouter("", "system", "user"))

    def test_grok_stream_parsing_and_token_tracking(self):
        response = FakeResponse(
            200,
            lines=[
                b'data: {"choices":[{"delta":{"content":"Hello "}}]}',
                b'data: {"choices":[{"delta":{"content":"world"}}]}',
                b"data: [DONE]",
            ],
        )
        with (
            patch.object(grok.requests, "post", return_value=response),
            patch.object(grok.default_tracker, "record_request") as record_request,
            patch.object(grok, "safe_log"),
        ):
            chunks = list(grok.analyze_with_grok("key", "system", "user"))

        self.assertEqual(chunks, ["Hello ", "world"])
        self.assertEqual(response.encoding, "utf-8")
        self.assertEqual(record_request.call_args.kwargs["status"], "success")
        self.assertEqual(record_request.call_args.kwargs["request_type"], "grok_stream")

    def test_gemini_advances_model_and_preserves_usage_metadata(self):
        unavailable = FakeResponse(503, text="high demand")
        success = FakeResponse(
            200,
            lines=[
                b'data: {"candidates":[{"content":{"parts":[{"text":"Gemini output"}]},"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":11,"candidatesTokenCount":3}}',
                b"data: [DONE]",
            ],
        )
        requested_urls = []

        def fake_post(url, **_kwargs):
            requested_urls.append(url)
            return unavailable if len(requested_urls) == 1 else success

        with (
            patch.object(gemini.requests, "post", side_effect=fake_post),
            patch.object(gemini.time, "sleep"),
            patch.object(gemini.default_tracker, "record_request") as record_request,
            patch.object(gemini, "safe_log"),
        ):
            chunks = list(
                gemini.stream_generate(
                    "key",
                    "system",
                    "user",
                    model="model/primary",
                    fallback_models=["model/backup"],
                )
            )

        self.assertEqual(chunks, ["Gemini output"])
        self.assertIn("/model/primary:streamGenerateContent", requested_urls[0])
        self.assertIn("/model/backup:streamGenerateContent", requested_urls[1])
        self.assertEqual(record_request.call_args.kwargs["actual_input_tokens"], 11)
        self.assertEqual(record_request.call_args.kwargs["actual_output_tokens"], 3)

    def test_grok_auth_error_is_not_reported_as_generic_api_error(self):
        response = FakeResponse(401, text="invalid key")
        with (
            patch.object(grok.requests, "post", return_value=response),
            patch.object(grok.default_tracker, "record_request"),
            patch.object(grok, "safe_log"),
        ):
            with self.assertRaises(GrokApiKeyError):
                list(grok.analyze_with_grok("bad", "system", "user"))

    def test_openrouter_model_candidates_are_deduplicated_from_environment(self):
        with patch.dict(
            os.environ,
            {
                "OPENROUTER_MODEL": "model/primary",
                "OPENROUTER_FALLBACK_MODELS": "model/backup, model/primary, model/backup",
            },
            clear=False,
        ):
            candidates = openrouter._openrouter_model_candidates()

        self.assertEqual(candidates, ["model/primary", "model/backup"])

    def test_openrouter_advances_to_next_model_after_rate_limit(self):
        limited = FakeResponse(429, text="rate limited")
        success = FakeResponse(
            200,
            lines=[
                b'data: {"choices":[{"delta":{"content":"fallback output"}}]}',
                b"data: [DONE]",
            ],
        )
        requested_models = []

        def fake_post(*_args, **kwargs):
            requested_models.append(kwargs["json"]["model"])
            return limited if len(requested_models) == 1 else success

        with (
            patch.dict(
                os.environ,
                {"OPENROUTER_FALLBACK_MODELS": "model/backup"},
                clear=False,
            ),
            patch.object(openrouter.requests, "post", side_effect=fake_post) as post,
            patch.object(openrouter.time, "sleep"),
            patch.object(openrouter.default_tracker, "record_request"),
            patch.object(openrouter, "safe_log"),
        ):
            chunks = list(
                openrouter.analyze_with_openrouter(
                    "key",
                    "system",
                    "user",
                    model="model/primary",
                )
            )

        self.assertEqual(chunks, ["fallback output"])
        self.assertEqual(post.call_count, 2)
        self.assertEqual(requested_models, ["model/primary", "model/backup"])


if __name__ == "__main__":
    unittest.main()
