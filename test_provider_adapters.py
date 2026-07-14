import os
import unittest
from unittest.mock import patch

import llm_client
from scrapper_demo.providers import gemini, grok, openrouter
from scrapper_demo.providers.errors import (
    ApiKeyError,
    GrokApiKeyError,
    ModelOutputLimitError,
    OpenRouterApiKeyError,
    RateLimitError,
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
    def test_grounding_redirect_is_preserved_until_it_can_be_resolved(self):
        redirect_url = (
            "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc123"
        )
        direct_url = "https://www.sauto.cz/osobni/detail/dacia/duster/210093410"
        payload = {
            "steps": [
                {
                    "type": "model_output",
                    "content": [
                        {
                            "text": "## Priamo overitelne porovnatelne inzeraty",
                            "annotations": [
                                {
                                    "title": "Dacia Duster 1.3 TCe 4x4",
                                    "url": redirect_url,
                                }
                            ],
                        }
                    ],
                }
            ]
        }

        extracted = gemini._extract_interaction_text_and_citations(payload)
        with patch.object(gemini.requests, "head") as head:
            head.return_value.status_code = 200
            head.return_value.url = direct_url
            resolved = gemini._resolve_annotation_redirects(extracted)

        self.assertIn(f"]({redirect_url})", extracted)
        self.assertIn(f"]({direct_url})", resolved)
        self.assertNotIn(gemini.GROUNDING_REDIRECT_HOST, resolved)

    def test_unresolved_grounding_redirect_becomes_plain_unverified_source(self):
        redirect_url = (
            "https://vertexaisearch.cloud.google.com/grounding-api-redirect/failed"
        )
        research = f"- [Dacia Duster]({redirect_url})"

        with patch.object(gemini.requests, "head", side_effect=TimeoutError):
            resolved = gemini._resolve_annotation_redirects(research)

        self.assertNotIn(redirect_url, resolved)
        self.assertIn("Dacia Duster (URL citacia nie je overitelna)", resolved)

    def test_grounding_redirect_is_replaced_in_json_evidence_and_citation(self):
        redirect_url = (
            "https://vertexaisearch.cloud.google.com/grounding-api-redirect/card123"
        )
        results_url = "https://www.otomoto.pl/osobowe/volkswagen/t-roc"
        research = (
            '{"candidates":[{"evidence_url":"' + redirect_url + '"}]}\n\n'
            "### Citacie z Google Search\n"
            f"- [Otomoto]({redirect_url})"
        )

        with patch.object(gemini.requests, "head") as head:
            head.return_value.status_code = 200
            head.return_value.url = results_url
            resolved = gemini._resolve_annotation_redirects(research)

        self.assertEqual(resolved.count(results_url), 2)
        self.assertNotIn(redirect_url, resolved)

    def test_grounding_rate_limit_stops_model_chain_for_outer_key_fallback(self):
        response = FakeResponse(429, text="Resource exhausted: quota")
        with (
            patch.object(gemini.requests, "post", return_value=response) as post,
            patch.object(gemini.default_tracker, "record_request"),
            patch.object(gemini.time, "sleep"),
        ):
            with self.assertRaises(RateLimitError):
                gemini.run_grounded_web_research(
                    "limited-key",
                    "VW T-Roc 2023 1.5 TSI DSG FWD 159000 km",
                    model="gemini-2.5-flash",
                    research_mode="market_sk_cz",
                )

        self.assertEqual(post.call_count, 1)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["response_format"]["mime_type"], "application/json")

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
                b'data: {"candidates":[{"content":{"parts":[{"text":"Gemini output"}]},"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":11,"candidatesTokenCount":3,"thoughtsTokenCount":5,"totalTokenCount":19}}',
                b"data: [DONE]",
            ],
        )
        requested_urls = []
        payloads = []

        def fake_post(url, **kwargs):
            requested_urls.append(url)
            payloads.append(kwargs["json"])
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
                    phase="text_research",
                )
            )

        self.assertEqual(chunks, ["Gemini output"])
        self.assertIn("/model/primary:streamGenerateContent", requested_urls[0])
        self.assertIn("/model/backup:streamGenerateContent", requested_urls[1])
        self.assertEqual(record_request.call_args.kwargs["actual_input_tokens"], 11)
        self.assertEqual(record_request.call_args.kwargs["actual_output_tokens"], 3)
        self.assertEqual(record_request.call_args.kwargs["actual_thinking_tokens"], 5)
        self.assertEqual(record_request.call_args.kwargs["actual_total_tokens"], 19)
        self.assertEqual(payloads[0]["generationConfig"]["maxOutputTokens"], 12000)
        self.assertEqual(payloads[0]["generationConfig"]["temperature"], 0.2)
        self.assertEqual(
            payloads[0]["generationConfig"]["thinkingConfig"],
            {"thinkingBudget": 0},
        )
        self.assertEqual(
            payloads[0]["generationConfig"]["responseMimeType"],
            "application/json",
        )

    def test_gemini_exposes_sanitized_model_finish_and_usage_diagnostics(self):
        response = FakeResponse(
            200,
            lines=[
                b'data: {"candidates":[{"content":{"parts":[{"text":"complete"}]},"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":12,"candidatesTokenCount":4,"thoughtsTokenCount":2,"totalTokenCount":18}}',
                b"data: [DONE]",
            ],
        )
        events = []
        with (
            patch.object(gemini.requests, "post", return_value=response),
            patch.object(gemini.default_tracker, "record_request"),
            patch.object(gemini, "safe_log"),
        ):
            chunks = list(
                gemini.stream_generate(
                    "secret-key",
                    "system",
                    "user",
                    model="model/vision",
                    phase="vision",
                    diagnostics_callback=events.append,
                )
            )

        self.assertEqual(chunks, ["complete"])
        self.assertEqual(events[-1]["model"], "model/vision")
        self.assertEqual(events[-1]["finish_reason"], "STOP")
        self.assertEqual(events[-1]["actual_total_tokens"], 18)
        self.assertNotIn("api_key", events[-1])

    def test_generation_profile_sets_phase_specific_limits_and_legacy_rollback(self):
        with patch.dict(os.environ, {"DEMO_ANALYSIS_PROFILE": "quality_optimized"}, clear=False):
            self.assertEqual(
                gemini._generation_settings("text_research"),
                {"max_output_tokens": 12000, "temperature": 0.2},
            )
            self.assertEqual(
                gemini._generation_settings("vision"),
                {"max_output_tokens": 8000, "temperature": 0.2},
            )
            self.assertEqual(
                gemini._generation_settings("final_synthesis"),
                {"max_output_tokens": 12000, "temperature": 0.5},
            )
        with patch.dict(os.environ, {"DEMO_ANALYSIS_PROFILE": "legacy"}, clear=False):
            self.assertEqual(
                gemini._generation_settings("final_synthesis"),
                {"max_output_tokens": 65536, "temperature": 0.7},
            )

    def test_thinking_profile_disables_hidden_reasoning_for_structured_phases(self):
        with patch.dict(os.environ, {"DEMO_ANALYSIS_PROFILE": "quality_optimized"}, clear=False):
            self.assertEqual(gemini._thinking_config("text_research"), {"thinkingBudget": 0})
            self.assertEqual(gemini._thinking_config("vision"), {"thinkingBudget": 0})
            self.assertEqual(gemini._thinking_config("final_synthesis"), {"thinkingBudget": 1024})
            self.assertEqual(
                gemini._thinking_config("text_research", "gemini-3.5-flash"),
                {"thinkingLevel": "minimal"},
            )
            self.assertEqual(
                gemini._thinking_config("final_synthesis", "gemini-3.5-flash"),
                {"thinkingLevel": "low"},
            )
        with patch.dict(os.environ, {"DEMO_ANALYSIS_PROFILE": "legacy"}, clear=False):
            self.assertEqual(gemini._thinking_config("text_research"), {})

    def test_final_synthesis_uses_bounded_thinking_without_json_response_mode(self):
        response = FakeResponse(
            200,
            lines=[
                b'data: {"candidates":[{"content":{"parts":[{"text":"report"}]},"finishReason":"STOP"}]}',
                b"data: [DONE]",
            ],
        )
        payloads = []

        def fake_post(_url, **kwargs):
            payloads.append(kwargs["json"])
            return response

        with (
            patch.object(gemini.requests, "post", side_effect=fake_post),
            patch.object(gemini.default_tracker, "record_request"),
            patch.object(gemini, "safe_log"),
        ):
            self.assertEqual(
                list(
                    gemini.stream_generate(
                        "key",
                        "system",
                        "user",
                        model="gemini-3.5-flash",
                        phase="final_synthesis",
                    )
                ),
                ["report"],
            )

        config = payloads[0]["generationConfig"]
        self.assertEqual(config["maxOutputTokens"], 12000)
        self.assertEqual(config["thinkingConfig"], {"thinkingLevel": "low"})
        self.assertNotIn("responseMimeType", config)

    def test_gemini_does_not_append_limit_warning_to_structured_output(self):
        response = FakeResponse(
            200,
            lines=[
                b'data: {"candidates":[{"content":{"parts":[{"text":"{\\"partial\\": true"}]},"finishReason":"MAX_TOKENS"}]}',
                b"data: [DONE]",
            ],
        )
        events = []
        with (
            patch.object(gemini.requests, "post", return_value=response),
            patch.object(gemini.default_tracker, "record_request") as record_request,
            patch.object(gemini, "safe_log"),
        ):
            with self.assertRaises(ModelOutputLimitError):
                list(
                    gemini.stream_generate(
                        "key",
                        "system",
                        "user",
                        phase="vision",
                        diagnostics_callback=events.append,
                    )
                )

        self.assertEqual(record_request.call_args.kwargs["status"], "truncated")
        self.assertEqual(events[-1]["status"], "truncated")
        self.assertEqual(events[-1]["finish_reason"], "MAX_TOKENS")
        self.assertEqual(events[-1]["output"], '{"partial": true')

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
