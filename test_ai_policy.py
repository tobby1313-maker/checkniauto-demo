import json
import unittest

from scrapper_demo.ai_policy import (
    check_and_compact_input,
    get_phase_policy,
)


class AiPolicyTests(unittest.TestCase):
    def test_phase_policies_match_phase_two_safety_ceilings(self):
        research = get_phase_policy("text_research")
        recovery = get_phase_policy("text_recovery")
        final = get_phase_policy("final_synthesis")

        self.assertEqual((research.max_input_tokens, research.max_output_tokens), (10_000, 5_000))
        self.assertEqual((recovery.max_input_tokens, recovery.max_output_tokens), (8_000, 5_000))
        self.assertEqual((final.max_input_tokens, final.max_output_tokens), (9_000, 6_000))
        self.assertEqual(final.visible_target_tokens, 3_500)
        self.assertEqual(get_phase_policy("grounding").phase, "reliability_grounding")

    def test_legacy_profile_preserves_previous_generation_ceiling(self):
        policy = get_phase_policy("text_research", profile="legacy")
        self.assertIsNone(policy.max_input_tokens)
        self.assertEqual(policy.max_output_tokens, 65_536)
        self.assertEqual(policy.thinking_mode, "default")

    def test_budget_compacts_in_priority_order_and_preserves_critical_values(self):
        payload = {
            "listing": {"vin": "WVWZZZ5NZDW123456", "price": "11800 EUR", "year": 2014},
            "grounded_research": "\n".join(["source prose"] * 4_000),
            "seller_claims": [{"claim": f"claim-{index}"} for index in range(10)],
            "technical_risks": [{"issue": f"risk-{index}"} for index in range(12)],
            "sources_used": [{"used_for": "x" * 500} for _ in range(12)],
        }
        content = "Use structured context.\n\n" + json.dumps(payload)
        calls = []

        def count_tokens(system, user):
            calls.append(len(user))
            return (len(system) + len(user)) // 4, "fake_provider_count"

        result = check_and_compact_input(
            "system",
            content,
            get_phase_policy("text_recovery"),
            count_tokens=count_tokens,
            protected_values=("WVWZZZ5NZDW123456", "11800 EUR", "2014"),
        )

        self.assertTrue(result.within_budget)
        self.assertLess(result.post_tokens, result.pre_tokens)
        self.assertEqual(result.counting_method, "fake_provider_count")
        self.assertIn("raw_web_after_normalized_findings", result.applied_compactions)
        self.assertIn("WVWZZZ5NZDW123456", result.user_content)
        self.assertIn("11800 EUR", result.user_content)
        self.assertGreaterEqual(len(calls), 2)

    def test_count_failure_uses_local_estimate_and_warning(self):
        def broken_counter(system, user):
            raise TimeoutError("countTokens unavailable")

        result = check_and_compact_input(
            "system",
            "small input",
            get_phase_policy("text_research"),
            count_tokens=broken_counter,
        )

        self.assertTrue(result.within_budget)
        self.assertEqual(result.counting_method, "local_estimate_fallback")
        self.assertTrue(any("Provider token count unavailable" in item for item in result.warnings))


if __name__ == "__main__":
    unittest.main()
