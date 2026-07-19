import json
import unittest
from pathlib import Path

import llm_client
import web_server
from scrapper_demo.services.analysis_pipeline import _has_linked_market_comparable


class GroundedResearchTest(unittest.TestCase):
    def test_component_identity_prompt_is_short_strict_and_non_confirming(self):
        prompt = llm_client._build_grounded_component_identity_prompt(
            "Hyundai Tucson, 09/2016, 1.6 T-GDi, 130 kW, 7-speed automatic, 4x4"
        )

        self.assertIn("VERIFIED pouzi iba", prompt)
        self.assertIn("PROBABLE pouzi", prompt)
        self.assertIn("Marketingovy nazov ako 7DCT", prompt)
        self.assertIn('"candidate_variants"', prompt)
        self.assertIn("Nevytvaraj hodnotenie kupy", prompt)

    def test_grounding_prompt_requires_component_by_component_search(self):
        prompt = llm_client._build_grounded_search_prompt(
            "Mazda CX-5 2.5 AWD automatic, 2014, 182734 km"
        )

        self.assertIn("Samostatne vyhladaj motor", prompt)
        self.assertIn("Samostatne vyhladaj prevodovku", prompt)
        self.assertIn("Generacia, karoseria a podvozok", prompt)
        self.assertIn("Zvolavacie a servisne kampane", prompt)
        self.assertNotIn("najblizsich aktualnych porovnatelnych ponuk", prompt)
        self.assertNotIn("Orientacna cena / trh", prompt)
        self.assertIn("podmienene opravy nikdy", prompt)
        self.assertIn("lahku samostatnu kontrolu presneho VIN", prompt)
        self.assertIn("cely retazec v uvodzovkach", prompt)

    def test_market_fallback_prompt_is_portal_specific_and_provenance_locked(self):
        prompt = llm_client._build_grounded_market_prompt(
            "Suzuki Vitara 1.6 VVT Elegance 2WD automatic, 2015, 240513 km"
        )

        self.assertIn("samostatny search pass SK_CZ", prompt)
        self.assertIn("A: rovnaka generacia, motor, prevodovka a pohon", prompt)
        self.assertIn("site-specific dopyt", prompt)
        self.assertIn("URL nevypisuj", prompt)
        self.assertIn("inline grounding citaciu", prompt)
        self.assertIn("analyzovany inzerat nepouzivaj", prompt)
        self.assertIn("CANDIDATE | description=", prompt)
        self.assertIn("Nepouzivaj JSON", prompt)

    def test_each_foreign_market_pass_uses_its_own_portal_and_language(self):
        mobile = llm_client._build_grounded_market_prompt("Toyota RAV4", "mobile_de")
        otomoto = llm_client._build_grounded_market_prompt("Toyota RAV4", "otomoto_pl")
        autoscout = llm_client._build_grounded_market_prompt("Toyota RAV4", "autoscout")

        self.assertIn("samostatny search pass MOBILE_DE", mobile)
        self.assertIn("nemcine", mobile)
        self.assertIn("iba mobile.de", mobile)
        self.assertNotIn("iba otomoto.pl", mobile)
        self.assertIn("samostatny search pass OTOMOTO_PL", otomoto)
        self.assertIn("polstine", otomoto)
        self.assertIn("iba otomoto.pl", otomoto)
        self.assertNotIn("iba mobile.de", otomoto)
        self.assertIn("samostatny search pass AUTOSCOUT", autoscout)
        self.assertIn("relevantneho trhu", autoscout)
        self.assertIn("iba AutoScout24", autoscout)

    def test_market_fallback_accepts_direct_ads_from_citation_block_only(self):
        citation_output = (
            "### Citacie z Google Search\n"
            "- [Dacia Duster 1.3 TCe 4x4]"
            "(https://www.sauto.cz/osobni/detail/dacia/duster/210093410)\n"
        )
        category_output = (
            "## Priamo overitelne porovnatelne inzeraty\n"
            "- [Dacia Duster results]"
            "(https://www.autobazar.eu/vysledky/suv-terenne-vozidla/dacia/duster/2020/)\n"
        )

        self.assertTrue(_has_linked_market_comparable(citation_output, market_only=True))
        self.assertFalse(_has_linked_market_comparable(category_output, market_only=True))

    def test_supported_rescue_requires_direct_grounding_citation_not_stale_narrative(self):
        stale_narrative = (
            "### Orientacna cena / trh\n"
            "- [Old Tucson](https://auto.bazos.cz/inzerat/111111/old-tucson.php)\n\n"
            "### Citacie z Google Search\n"
            "- [Tucson category](https://auto.bazos.cz/inzeraty/hyundai-tucson/)\n"
        )
        current_citation = (
            stale_narrative
            + "- [Current Tucson](https://auto.bazos.cz/inzerat/222222/current-tucson.php)\n"
        )

        self.assertFalse(
            _has_linked_market_comparable(stale_narrative, customer_facing_only=True)
        )
        self.assertTrue(
            _has_linked_market_comparable(current_citation, customer_facing_only=True)
        )

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

    def test_research_v2_context_compacts_grounded_prose_and_includes_source_registry(self):
        grounded = "\n\n".join((
            "### Identifikacia komponentov\n" + ("duplicate identity " * 800),
            "### Zdroje\n" + ("verbose source bibliography " * 900),
            "### Motor\n" + ("Oil consumption evidence.\n" * 300),
            "### Prevodovka a pohon\n" + ("Mechatronic evidence.\n" * 300),
            "### Zvolavacie a servisne kampane\nVIN recall check required.",
            "### Naklady\nA direct workshop price is required.",
            "### Najdolezitejsie webove zistenia\nInspect the engine and gearbox.",
            "### Citacie z Google Search\nhttps://workshop.test/ea888",
        ))
        registry = {"gsrc_001": "https://workshop.test/ea888"}

        context = web_server._build_text_research_context(
            "ignored legacy listing",
            "sk",
            grounded,
            {"identification_status": "PROBABLE", "sources": [{"large": "value"}]},
            research_v2=True,
            listing_context={"title": "VW Tiguan"},
            verified_source_registry=registry,
        )
        payload = json.loads(context.split("\n\n", 1)[1])
        compacted = payload["grounded_research"]

        self.assertEqual(payload["verified_source_registry"], registry)
        self.assertEqual(payload["component_identity"], {"identification_status": "PROBABLE"})
        self.assertLessEqual(len(compacted), web_server.RESEARCH_V2_GROUNDED_MAX_CHARS)
        self.assertIn("### Motor", compacted)
        self.assertIn("### Prevodovka a pohon", compacted)
        self.assertNotIn("verbose source bibliography", compacted)
        self.assertNotIn("duplicate identity", compacted)

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

    def test_listing_context_extracts_bazos_label_before_value_facts(self):
        context = web_server._listing_context_object(
            "# Hyundai Tucson 1.6 T-GDi Premium 4x4 DTC\n\n"
            "## Specifications\n\n"
            "| Parameter | Value |\n"
            "|-----------|-------|\n"
            "| Engine Power | 130 kW |\n"
            "| Fuel | Benzín |\n"
            "| Drivetrain | 4x4 |\n\n"
            "## Seller Note (Poznamka)\n\n"
            "Mesiac/Rok: 09/2016\n"
            "Prevodovka: 7-st. automatická\n"
            "Najazdené km: 161949\n"
        )

        self.assertEqual(context["mileage"], "161949 km")
        self.assertEqual(context["mileage_km"], 161949)
        self.assertEqual(context["year"], "2016")
        self.assertEqual(context["transmission"], "7-st. automatická")
        self.assertEqual(context["power"], "130 kW")

    def test_listing_context_extracts_unlabelled_bazos_toyota_facts(self):
        context = web_server._listing_context_object(
            "# TOYOTA RAV4 122 000km\n\n"
            "**Source:** https://auto.bazos.sk/inzerat/191944776/example.php\n"
            "**Scraped:** 2026-07-14 15:24\n\n"
            "## Specifications\n\n"
            "| Parameter | Value |\n|---|---|\n"
            "| Engine Power | 112 kW |\n| Fuel | Benzín |\n\n"
            "## Seller Note (Poznamka)\n\n"
            "Predám Toyotu RAV4 2008 2.0l benzin 112 kw s automatickou "
            "prevodovkou a štvorkolkou, najazdené len 122000 km"
        )

        self.assertEqual(context["year"], "2008")
        self.assertEqual(context["mileage_km"], 122000)
        self.assertEqual(context["engine"], "2.0l")
        self.assertIn("automat", context["transmission"].lower())
        self.assertEqual(context["drive"], "4x4")

    def test_listing_context_engine_label_ignores_inflected_motor_word(self):
        context = web_server._listing_context_object(
            "# Audi A5 Coupe 3.0 TDI Quattro\n\n"
            "## Seller Note (Poznamka)\n\n"
            "Vozidlo s nesmrteľným 3.0 TDI V6 motorom a manuálnou prevodovkou. "
            "• Motor: 3.0 TDI V6 / 176 kW (240 PS) / Palivo Diesel"
        )

        self.assertEqual(context["engine"], "3.0 TDI V6")

    def test_listing_context_extracts_czech_manual_transmission(self):
        context = web_server._listing_context_object(
            "# BMW X3 xDrive20i F25\n\n"
            "## Seller Note (Poznamka)\n\n"
            "manuální převodovka, 6 rychlostních stupňů, pohon 4x4"
        )

        self.assertEqual(context["transmission"], "Manuálna 6-st.")

    def test_listing_context_extracts_slovak_front_wheel_nahon(self):
        context = web_server._listing_context_object(
            "# Škoda Kodiaq 2.0 TDI DSG\n\n"
            "## Seller Note (Poznamka)\n\n"
            "Pohon: predný náhon"
        )

        self.assertEqual(context["drive"], "Predný")

    def test_listing_context_repairs_gear_only_transmission_and_extracts_ps_power(self):
        context = web_server._listing_context_object(
            "# Mazda CX-5 2.0 benzín 4x4\n\n"
            "## Specifications\n\n"
            "| Parameter | Value |\n|---|---|\n"
            "| Transmission | 6 stupnova |\n| Drivetrain | 4x4 |\n\n"
            "## Seller Note (Poznamka)\n\n"
            "2,0L, 160 PS, pohon 4x4, manuálna prevodovka 6 stupnova"
        )

        self.assertEqual(context["transmission"], "Manuálna 6-st.")
        self.assertEqual(context["power"], "118 kW (160 PS)")

    def test_listing_context_extracts_title_engine_and_at_abbreviation(self):
        context = web_server._listing_context_object(
            "# HYUNDAI SANTA FE 2.2 CRDI PREMIUM 4X4 A/T\n\n"
            "## Seller Note (Poznamka)\n\n"
            "R.V.: 3/2019, 2199 CM3, 147KW, A/T, NAFTA, 183 270 KM"
        )

        self.assertEqual(context["engine"], "2.2 CRDI")
        self.assertEqual(context["transmission"], "Automatická")

    def test_listing_context_repairs_cx60_heading_engine_and_reversed_drive_label(self):
        context = web_server._listing_context_object(
            "# CX-60 3.3 e-Skyactive D200 mHEV Exclusive Line A/T\n\n"
            "## Specifications\n\n"
            "| Parameter | Value |\n|---|---|\n"
            "| Engine | a prevodovka : |\n| Transmission | automat 8st |\n\n"
            "## Seller Note (Poznamka)\n\n"
            "Dátum výroby: 01/2023 Pohon kolies: zadný Objem valcov: 3 283cm3 "
            "Motor a prevodovka : -3,3-litrový 6-valcový dieselový motor"
        )

        self.assertIn("E-SKYACTIV D200", context["engine"])
        self.assertEqual(context["drive"], "Zadný")

    def test_listing_context_repairs_stale_troc_mileage_and_inventory_alternatives(self):
        context = web_server._listing_context_object(
            "# VW T-Roc 2023, 1.5TSi, DSG\n\n"
            "## Specifications\n\n"
            "| Parameter | Value |\n|---|---|\n"
            "| Mileage | 159 km |\n| Drivetrain | 4x4 |\n"
            "| Transmission | DSG 7-stupňov (máme aj manuál) |\n\n"
            "## Seller Note (Poznamka)\n\n"
            "Predám Volkswagen T-Roc s pohonom FWD (máme aj 4x4), "
            "prevodovka DSG 7-stupňov (máme aj manuál), najazdené 159tis.km. "
            "Cena 15.439 EUR + DPH = 18.990 EUR s DPH."
        )

        self.assertEqual(context["mileage_km"], 159000)
        self.assertEqual(context["drive"], "Predný")
        self.assertEqual(context["transmission"], "DSG 7-stupňov")
        self.assertEqual(context["asking_price_gross_eur"], 18990)

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
        self.assertEqual(len(compact["web_research_findings"]), 8)
        self.assertEqual(len(compact["technical_risks"]), 8)
        self.assertEqual(len(compact["expected_costs"]), 10)
        self.assertEqual(compact["text_research_risk_flags"], [])
        self.assertEqual(compact["sources_used"], [])

    def test_final_compaction_drops_unlinked_market_estimates(self):
        payload = {
            "market_assessment": {
                "available": True,
                "advertised_price_eur": 9500,
                "observed_market_low_eur": 8990,
                "observed_market_high_eur": 10500,
                "comparable_count": 3,
                "price_view": "fair",
            },
            "market_comparables": [
                {
                    "description": "Suzuki Grand Vitara 2.4i 4x4 A/T, 2009, 137000 km",
                    "price_eur": 8990,
                    "mileage_km": 137000,
                    "source_url": "",
                    "verified_url": False,
                },
                {
                    "description": "Suzuki Grand Vitara 2.4 VVT Automat 4x4, 2009, 158000 km",
                    "price_eur": 9490,
                    "mileage_km": 158000,
                    "source_url": "",
                    "verified_url": False,
                },
            ],
        }

        compact = web_server._compact_text_research_for_final(json.dumps(payload))

        self.assertEqual(compact["market_comparables"], [])
        self.assertFalse(compact["market_assessment"]["available"])
        self.assertEqual(compact["market_assessment"]["comparable_count"], 0)
        self.assertIsNone(compact["market_assessment"]["observed_market_low_eur"])
        self.assertIsNone(compact["market_assessment"]["observed_market_high_eur"])
        self.assertEqual(
            compact["market_assessment"]["price_view"],
            "requires_manual_verification",
        )

    def test_final_compaction_keeps_verified_foreign_ads_as_aggregate_only(self):
        payload = {
            "market_assessment": {
                "available": True,
                "observed_market_low_eur": 11000,
                "observed_market_high_eur": 13000,
                "observed_market_average_eur": 12000,
                "comparable_count": 2,
                "price_view": "fair",
            },
            "market_comparables": [
                {
                    "description": "Dacia Duster 1.3 TCe 4x4",
                    "price_eur": 12000,
                    "source_country": "DE",
                    "source_url": "https://www.mobile.de/auto-inserat/duster/1.html",
                    "verified_url": True,
                }
            ],
        }

        compact = web_server._compact_text_research_for_final(json.dumps(payload))

        self.assertEqual(compact["market_comparables"], [])
        self.assertTrue(compact["market_assessment"]["available"])
        self.assertEqual(compact["market_assessment"]["comparable_count"], 2)
        self.assertEqual(compact["market_assessment"]["public_comparable_count"], 0)
        self.assertEqual(compact["market_assessment"]["observed_market_average_eur"], 12000)

    def test_final_context_has_global_budget_and_keeps_quality_fields(self):
        payload = {
            "listing_facts": {"title": "Kia Sportage", "vin": "U5YPC811BDL362988"},
            "web_research_findings": [{"claim": "web finding " + ("x" * 500)} for _ in range(12)],
            "technical_risks": [{"component": "risk", "issue": "x" * 500} for _ in range(8)],
            "expected_costs": [{"item": "cost", "why": "x" * 500} for _ in range(10)],
            "sources_used": [{"source_name": "source", "used_for": "x" * 500} for _ in range(20)],
        }
        context = web_server._build_final_synthesis_context(
            "sk",
            "# Kia Sportage\n\n## Specifications\n- **VIN:** U5YPC811BDL362988",
            json.dumps(payload),
            json.dumps({"photos_provided": False, "visual_verdict": "clear"}),
            json.dumps({"allowed_final_verdict": "ZVAZIT"}),
            "- grounded source line " + ("y" * 5000),
        )
        user_payload = context.split("\n\n", 1)[1]
        parsed = json.loads(user_payload)
        self.assertLessEqual(len(user_payload), web_server.FINAL_CONTEXT_MAX_CHARS)
        self.assertEqual(parsed["text_research"]["sources_used"], [])
        self.assertEqual(parsed["listing"]["vin"], "U5YPC811BDL362988")

    def test_unavailable_research_excludes_raw_web_and_blocks_technical_inference(self):
        context = web_server._build_final_synthesis_context(
            "sk",
            "# Volkswagen Tiguan\n\n- **Year:** 2014",
            json.dumps({
                "research_status": "unavailable",
                "web_research_findings": [],
                "technical_risks": [],
                "expected_costs": [],
            }),
            json.dumps({"photos_provided": False}),
            json.dumps({"allowed_final_verdict": "ZVAZIT"}),
            "EA888 DQ500 invented-risk bait",
        )

        instruction, user_payload = context.split("\n\n", 1)
        parsed = json.loads(user_payload)
        self.assertIn("Technical research is unavailable", instruction)
        self.assertNotIn("EA888", context)
        self.assertEqual(parsed["web_research"]["evidence_excerpt"], "")
        self.assertFalse(parsed["text_research"]["technical_research_available"])

    def test_limited_research_cannot_restore_rejected_raw_web(self):
        context = web_server._build_final_synthesis_context(
            "sk",
            "# Volkswagen Tiguan\n\n- **Year:** 2014",
            json.dumps({
                "research_packet_schema_version": 2,
                "research_status": "limited",
                "component_identity": {
                    "engine": {"family": "EA888", "resolution": "AMBIGUOUS"},
                    "sources": [{"source_name": "Rejected parts catalog AUTODOC"}],
                },
                "web_research_findings": [],
                "technical_risks": [],
                "expected_costs": [],
            }),
            json.dumps({"photos_provided": False}),
            json.dumps({"allowed_final_verdict": "ZVAZIT"}),
            "Rejected exact 60000 km interval and unsupported repair claim",
        )

        instruction, user_payload = context.split("\n\n", 1)
        parsed = json.loads(user_payload)
        self.assertIn("Technical research is unavailable", instruction)
        self.assertNotIn("60000", context)
        self.assertNotIn("AUTODOC", context)
        self.assertEqual(parsed["text_research"]["component_identity"]["engine"]["family"], "EA888")
        self.assertEqual(parsed["web_research"]["evidence_excerpt"], "")
        self.assertFalse(parsed["text_research"]["technical_research_available"])

    def test_final_context_carries_vin_light_decode_metadata(self):
        context = web_server._build_final_synthesis_context(
            "sk",
            "# Volkswagen Touareg\n\n## Specifications\n- **VIN:** WVGFF9BP4CD005167\n- **Year:** 2012",
            json.dumps({"listing_facts": {"vin": "WVGFF9BP4CD005167"}, "vin_check": {"vin_present": True}}),
            json.dumps({"photos_provided": False}),
            json.dumps({"allowed_final_verdict": "ZVAZIT"}),
            "",
            {},
            {
                "vin": "WVGFF9BP4CD005167",
                "valid": True,
                "wmi": "WVG",
                "manufacturer": "Volkswagen",
                "model_year_code": "C",
                "model_year_candidates": [1982, 2012],
                "plant_hint": "Bratislava, Slovakia",
            },
        )
        payload = json.loads(context.split("\n\n", 1)[1])

        self.assertEqual(payload["vin_light_check"]["manufacturer"], "Volkswagen")
        self.assertEqual(payload["vin_light_check"]["plant_hint"], "Bratislava, Slovakia")
        self.assertIn(2012, payload["vin_light_check"]["model_year_candidates"])

    def test_vision_and_synthesis_prompts_require_detailed_buyer_sections(self):
        root = Path(__file__).resolve().parent
        vision_prompt = (root / "prompts" / "gemini_vision_system.md").read_text(encoding="utf-8")
        final_prompt = (root / "prompts" / "grok_final_synthesis_system.md").read_text(encoding="utf-8")

        self.assertLess(len(final_prompt), 9_000)
        self.assertIn("target 4-8 exterior observations and 3-6 interior observations", vision_prompt)
        self.assertIn("visible seller documents or service-book/facture photos", vision_prompt)
        self.assertIn("pros 4-6, cons 5-8", final_prompt)
        self.assertIn("### Červené vlajky a limity fotografií", final_prompt)
        self.assertIn("5-7 sentence-style items", final_prompt)
        self.assertIn("| Palivo |", final_prompt)
        self.assertIn("| Farba |", final_prompt)
        self.assertIn("Benzín + LPG", final_prompt)
        self.assertIn("podľa fotiek", final_prompt)

    def test_vat_prompts_omit_unlisted_tax_commentary(self):
        root = Path(__file__).resolve().parent
        research_prompt = (root / "prompts" / "grok_text_research_system.md").read_text(encoding="utf-8")
        final_prompt = (root / "prompts" / "grok_final_synthesis_system.md").read_text(encoding="utf-8")

        self.assertIn("VAT/DPH is opt-in evidence", research_prompt)
        self.assertIn("leave `vat_context` empty", research_prompt)
        self.assertIn("If that field is empty, do not mention DPH/VAT", final_prompt)
        self.assertIn("verified comparable-ad links", final_prompt)
        self.assertIn("Do not link `web_research_findings`", final_prompt)
        self.assertIn("vin_light_check", final_prompt)
        self.assertIn("neutral no-result sentence", final_prompt)
        self.assertIn("**Dekódovanie:**", final_prompt)
        self.assertNotIn("Ľahké dekódovanie", final_prompt)

    def test_browser_renderer_allows_links_only_for_price_comparables(self):
        root = Path(__file__).resolve().parent
        technical_js = (root / "web" / "assets" / "redesign-technical.js").read_text(encoding="utf-8")
        presentation = (root / "scrapper_demo" / "presentation.py").read_text(encoding="utf-8")

        self.assertIn("market.comparables", technical_js)
        self.assertIn('target=\"_blank\"', technical_js)
        self.assertIn('item.get("verified_url") is not True', presentation)
        self.assertIn('item.get("display_in_report") is True', presentation)
        self.assertNotIn("screening_score", technical_js)

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
