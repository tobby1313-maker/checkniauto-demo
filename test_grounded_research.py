import json
import unittest
from pathlib import Path

import llm_client
import web_server


class GroundedResearchTest(unittest.TestCase):
    def test_grounding_prompt_requires_component_by_component_search(self):
        prompt = llm_client._build_grounded_search_prompt(
            "Mazda CX-5 2.5 AWD automatic, 2014, 182734 km"
        )

        self.assertIn("Samostatne vyhladaj motor", prompt)
        self.assertIn("Samostatne vyhladaj prevodovku", prompt)
        self.assertIn("Generacia, karoseria a podvozok", prompt)
        self.assertIn("Zvolavacie a servisne kampane", prompt)
        self.assertIn("3-5 co najblizsich aktualnych porovnatelnych ponuk", prompt)
        self.assertIn("podmienene opravy nikdy", prompt)

    def test_text_research_context_is_stateless_and_uses_web_results(self):
        context = web_server._build_text_research_context(
            "Mazda CX-5 listing",
            "sk",
            "### Motor\nSource-backed engine finding.",
        )

        self.assertIn("Provided web research results", context)
        self.assertIn("Source-backed engine finding", context)
        self.assertNotIn("Knowledge base matches", context)

    def test_final_compaction_preserves_expanded_research_limits(self):
        payload = {
            "knowledge_base_findings": [{"component": "must be omitted"}],
            "web_research_findings": [{"claim": f"web-{i}"} for i in range(13)],
            "technical_risks": [{"component": f"risk-{i}"} for i in range(9)],
            "expected_costs": [{"item": f"cost-{i}"} for i in range(11)],
            "text_research_risk_flags": [{"risk": f"flag-{i}"} for i in range(11)],
            "sources_used": [{"source_name": f"source-{i}"} for i in range(21)],
        }

        compact = web_server._compact_text_research_for_final(json.dumps(payload))

        self.assertEqual(compact["knowledge_base_findings"], [])
        self.assertEqual(len(compact["web_research_findings"]), 12)
        self.assertEqual(len(compact["technical_risks"]), 8)
        self.assertEqual(len(compact["expected_costs"]), 10)
        self.assertEqual(len(compact["text_research_risk_flags"]), 10)
        self.assertEqual(len(compact["sources_used"]), 20)

    def test_vision_and_synthesis_prompts_require_detailed_buyer_sections(self):
        root = Path(__file__).resolve().parent
        vision_prompt = (root / "prompts" / "gemini_vision_system.md").read_text(encoding="utf-8")
        final_prompt = (root / "prompts" / "grok_final_synthesis_system.md").read_text(encoding="utf-8")

        self.assertIn("target 4-8 exterior observations and 3-6 interior observations", vision_prompt)
        self.assertIn("visible seller documents or service-book/facture photos", vision_prompt)
        self.assertIn("pros 4-6, cons 5-8", final_prompt)
        self.assertIn("### Červené vlajky a limity fotografií", final_prompt)
        self.assertIn("5-7 concrete questions or actions", final_prompt)


if __name__ == "__main__":
    unittest.main()
