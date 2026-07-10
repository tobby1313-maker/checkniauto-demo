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

    def test_default_gemini_model_order_matches_demo_routing(self):
        standard_chain = llm_client._ordered_unique_models(
            llm_client.GEMINI_TEXT_RESEARCH_MODEL,
            llm_client.GEMINI_FALLBACK_MODELS,
        )
        final_chain = llm_client._ordered_unique_models(
            llm_client.GEMINI_FINAL_MODEL,
            llm_client.GEMINI_FINAL_FALLBACK_MODELS,
        )

        self.assertEqual(
            standard_chain,
            ["gemini-2.5-flash", "gemini-3.5-flash", "gemini-3.1-flash-lite"],
        )
        self.assertEqual(
            final_chain,
            ["gemini-3.5-flash", "gemini-2.5-flash", "gemini-3.1-flash-lite"],
        )

    def test_gemini_rate_limit_can_advance_model_chain(self):
        self.assertTrue(llm_client._is_gemini_rate_limit_error(429, "Resource exhausted: quota"))
        self.assertTrue(llm_client._is_gemini_rate_limit_error(429, "rate limit exceeded"))
        self.assertFalse(llm_client._is_gemini_rate_limit_error(429, "permission denied"))

    def test_text_research_context_is_stateless_and_uses_web_results(self):
        context = web_server._build_text_research_context(
            "Mazda CX-5 listing",
            "sk",
            "### Motor\nSource-backed engine finding.",
        )

        self.assertIn("Provided web research results", context)
        self.assertIn("Source-backed engine finding", context)
        self.assertNotIn("Knowledge base matches", context)

    def test_markdown_link_parser_keeps_parentheses_inside_urls(self):
        links = web_server._markdown_links(
            "[Engine Finders](https://enginefinder.co.uk/blog/example-(test)) "
            "[Autobazar](https://www.autobazar.eu/detail/test/)"
        )

        self.assertEqual(
            links,
            [
                ("Engine Finders", "https://enginefinder.co.uk/blog/example-(test)"),
                ("Autobazar", "https://www.autobazar.eu/detail/test/"),
            ],
        )

    def test_listing_context_promotes_mileage_to_canonical_fields(self):
        context = web_server._listing_context_object(
            "# VW Tiguan\n\n"
            "## Specifications\n"
            "- **Year:** 2019\n"
            "- **Mileage:** 148 200 km\n"
            "- **Engine:** 2.0 TDI\n"
            "- **Fuel:** Diesel\n"
            "- **Color:** Biela\n"
        )

        self.assertEqual(context["mileage"], "148 200 km")
        self.assertEqual(context["mileage_km"], 148200)
        self.assertEqual(context["year"], "2019")
        self.assertEqual(context["engine"], "2.0 TDI")
        self.assertEqual(context["fuel"], "Diesel")
        self.assertEqual(context["color"], "Biela")

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
        self.assertIn("5-7 sentence-style items", final_prompt)
        self.assertIn("| Palivo |", final_prompt)
        self.assertIn("| Farba |", final_prompt)
        self.assertIn("Benzín + LPG", final_prompt)
        self.assertIn("podľa fotiek", final_prompt)

    def test_final_prompt_formats_technical_risks_as_severity_blocks(self):
        root = Path(__file__).resolve().parent
        final_prompt = (root / "prompts" / "grok_final_synthesis_system.md").read_text(encoding="utf-8")

        self.assertIn("compact risk blocks sorted from most critical to least critical", final_prompt)
        self.assertIn("🔴 **{komponent alebo problém}**", final_prompt)
        self.assertIn("**Dopad pre kupujúceho:**", final_prompt)
        self.assertIn("Keep `### Ďalšie modelové kontroly` as simple note-style bullets", final_prompt)

    def test_final_prompt_uses_numbered_seller_inspection_checklist(self):
        root = Path(__file__).resolve().parent
        final_prompt = (root / "prompts" / "grok_final_synthesis_system.md").read_text(encoding="utf-8")
        section = final_prompt.rsplit("## Otázky pre predajcu a kontrola pri obhliadke", 1)[1]
        section = section.split("## Záverečné odporúčanie", 1)[0]

        self.assertIn("do not use a table", final_prompt)
        self.assertIn("1. **VIN:**", section)
        self.assertIn("2. **Servisná história:**", section)
        self.assertNotIn("| Otázka / úkon |", section)
        self.assertNotIn("|---|---|", section)


if __name__ == "__main__":
    unittest.main()
