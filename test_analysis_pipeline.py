import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from scrapper_demo.services.analysis_pipeline import (
    AnalysisPipelineDependencies,
    _lock_report_evidence_claims,
    _lock_registration_age_claims,
    _merge_backend_evidence,
    _promote_selected_key,
    _research_parse_failed,
    _unavailable_vision_payload,
    _valid_vision_payload,
    multi_model_analysis_events,
)
from scrapper_demo.storage import ListingJobRepository


def _dependencies(repository, prompt_dir):
    empty_text = lambda *args, **kwargs: ""
    return AnalysisPipelineDependencies(
        repository=repository,
        prompt_dir=prompt_dir,
        build_final_synthesis_context=empty_text,
        build_text_research_context=empty_text,
        compact_json_for_prompt=lambda value: json.dumps(value),
        output_language=lambda value: value,
        inject_photo_vin=empty_text,
        listing_context_text=lambda value, **kwargs: value,
        model_display_name=lambda provider: provider.title(),
        no_photos_vision_result=lambda *args: "{}",
        normalize_report_headings=lambda value: value,
        public_analysis_markdown=lambda value, slug_dir: value,
        replace_photo_analysis_section=lambda value, *args: value,
        replace_quick_summary_scorecard=lambda value, *args: value,
        move_pros_cons_after_quick_summary=lambda value: value,
        save_kb_blocks=lambda blocks: [],
        safe_model_json=lambda value: json.loads(value),
        strip_kb_section=lambda value: value,
        prepare_images=lambda slug_dir: ([], {}),
        stream_text_model=lambda *args, **kwargs: iter(()),
        log=lambda message: None,
    )


class AnalysisPipelineBoundaryTests(unittest.TestCase):
    def test_successful_backup_key_is_reused_first_for_later_phases(self):
        entries = [
            {"key": "limited", "label": "primary"},
            {"key": "working", "label": "backup"},
        ]

        _promote_selected_key(entries, entries[1])

        self.assertEqual([entry["key"] for entry in entries], ["working", "limited"])

    def test_unavailable_vision_preserves_that_photos_exist(self):
        payload = _unavailable_vision_payload(
            {
                "coverage_mode": "detail_all",
                "original_count": 7,
                "selected_count": 7,
                "full_gallery_included": True,
            },
            output_language="sk",
            reason="JSONDecodeError",
        )

        self.assertTrue(payload["photos_provided"])
        self.assertEqual(payload["analysis_status"], "unavailable")
        self.assertEqual(payload["photo_coverage"]["original_count"], 7)
        self.assertTrue(_valid_vision_payload(payload))

    def test_unverified_market_and_missing_vin_claims_are_locked(self):
        report = """# Toyota RAV4

## 📋 Rýchle zhrnutie
- **Cena:** podozrivo nízka - môže skrývať poruchu.
- **Najväčšie riziko:** Chýbajúci VIN.

### Skóre analýzy
| Oblasť | Skóre |
|---|---:|
| Celkové skóre | 75/100 |

## 💰 Cena a vyjednávanie
Cena 6 900 EUR je podozrivo lacná a na spodnej hranici trhu.

## ❌ Zápory / riziká
- **Podozrivo nízka cena:** Môže indikovať skrytú vadu.
"""
        locked = _lock_report_evidence_claims(
            report,
            {
                "listing_facts": {"price": "6900", "vin": ""},
                "vin_check": {"vin_present": False},
                "market_assessment": {
                    "benchmark_available": False,
                    "benchmark_comparable_count": 0,
                    "advertised_price_eur": 6900,
                },
                "market_comparables": [],
            },
            {"applied_rules": [{"rule": "vin_missing_request_before_viewing", "points": 0}]},
            output_language="sk",
        )

        self.assertNotIn("75/100", locked)
        self.assertNotIn("podozrivo nízka", locked.lower())
        self.assertNotIn("chýbajúci vin", locked.lower())
        self.assertIn("nemožno označiť za lacnú", locked)

    def test_backend_verdict_replaces_model_verdict(self):
        report = """# VW T-Roc

## Rýchle zhrnutie
- **Hodnotenie:** 🟠 RIEŠIŤ LEN S VÝHRADAMI
"""

        locked = _lock_report_evidence_claims(
            report,
            {"listing_facts": {}, "market_assessment": {"benchmark_available": False}},
            {"allowed_final_verdict": "🟢 STOJÍ ZA OBHLIADKU"},
            output_language="sk",
        )

        self.assertIn("- **Hodnotenie:** 🟢 STOJÍ ZA OBHLIADKU", locked)
        self.assertNotIn("🟠 RIEŠIŤ LEN S VÝHRADAMI", locked)

    def test_missing_vin_is_not_biggest_risk_when_minor_concern_exists(self):
        report = """# VW T-Roc

## Rýchle zhrnutie
- **Hodnotenie:** 🟢 STOJÍ ZA OBHLIADKU
- **Najväčšie riziko:** Úplná absencia VIN znemožňuje overenie histórie.
"""

        locked = _lock_report_evidence_claims(
            report,
            {
                "listing_facts": {"vin": ""},
                "vin_check": {"vin_present": False},
                "market_assessment": {"benchmark_available": False},
            },
            {
                "allowed_final_verdict": "🟢 STOJÍ ZA OBHLIADKU",
                "applied_rules": [
                    {"rule": "visible_minor_damage", "points": 1}
                ],
            },
            output_language="sk",
        )

        self.assertIn("nie je potvrdená konkrétna zásadná vada", locked)
        self.assertNotIn("Úplná absencia VIN", locked)

    def test_registration_age_claim_is_calculated_deterministically(self):
        report = "- **Riziko:** 159 000 km za necelé 2 roky prevádzky."

        locked = _lock_registration_age_claims(
            report,
            {"registration_date": "2/2023"},
            language="sk",
            as_of=datetime(2026, 7, 14),
        )

        self.assertEqual(
            locked,
            "- **Riziko:** 159 000 km za približne 3 roky a 5 mesiacov prevádzky.",
        )
        self.assertIn(
            "za približne 3 roky a 5 mesiacov prevádzky",
            _lock_registration_age_claims(
                "Nájazd za necelé dva roky.",
                {"registration_date": "2/2023"},
                language="sk",
                as_of=datetime(2026, 7, 14),
            ),
        )

    def test_market_lock_preserves_url_unverified_search_message(self):
        report = """# Toyota RAV4

## 📋 Rýchle zhrnutie
- **Cena:** výhodná.

## 💰 Cena a vyjednávanie
Nenašli sa porovnateľné vozidlá.
"""
        message = "Boli nájdené ponuky, ale nepodarilo sa overiť ich detailné URL."

        locked = _lock_report_evidence_claims(
            report,
            {
                "listing_facts": {"price": "6900"},
                "market_assessment": {
                    "benchmark_available": False,
                    "benchmark_comparable_count": 0,
                    "advertised_price_eur": 6900,
                    "summary": message,
                },
                "market_comparables": [],
            },
            {},
            output_language="sk",
        )

        self.assertIn(message, locked)
        self.assertNotIn("Nenašli sa porovnateľné vozidlá", locked)

    def test_usable_background_benchmark_restores_deterministic_price_table(self):
        report = """# VW T-Roc

## 📋 Rýchle zhrnutie
- **Cena:** nejasná.

## 💰 Cena a vyjednávanie
Bez porovnania.
"""
        locked = _lock_report_evidence_claims(
            report,
            {
                "market_assessment": {
                    "available": True,
                    "benchmark_available": True,
                    "benchmark_comparable_count": 4,
                    "benchmark_median_eur": 18100,
                    "advertised_price_eur": 18990,
                    "price_delta_percent": 4.9,
                    "price_view": "fair",
                },
                "market_comparables": [],
            },
            {},
            output_language="sk",
        )

        self.assertIn("| Vážený medián trhu | 18 100 EUR |", locked)
        self.assertIn("| Vzorka benchmarku | 4 ponúk |", locked)
        self.assertIn("**Cena:** v rámci trhu", locked)

    def test_public_report_drops_stray_numeric_or_calibration_score_text(self):
        locked = _lock_report_evidence_claims(
            "# Report\n\nOverall score: 74/100.\n\nThe scorer is UNCALIBRATED.\n",
            {
                "market_assessment": {
                    "benchmark_available": True,
                    "benchmark_comparable_count": 3,
                    "benchmark_median_eur": 10000,
                }
            },
            {},
            output_language="en",
        )

        self.assertNotIn("74/100", locked)
        self.assertNotIn("UNCALIBRATED", locked)

    def test_unsourced_model_risk_cannot_claim_this_car_has_the_defect(self):
        merged = _merge_backend_evidence(
            {
                "listing_facts": {"year": "2008", "mileage": "122000 km"},
                "technical_risks": [
                    {
                        "component": "1AZ-FE",
                        "issue": "Oil consumption",
                        "evidence_category": "MODEL_LEVEL_RISK",
                        "specific_vehicle_evidence": "Age and mileage make failure likely.",
                        "confidence": "HIGH",
                        "source_url": "https://example.test/owner-blog",
                        "typical_trigger_or_interval": "Usually fails after 130000 km",
                    }
                ],
                "sources_used": [
                    {
                        "source_url": "https://example.test/owner-blog",
                        "source_type": "OWNER_FORUM",
                    }
                ],
            },
            {"year": "2008", "mileage": "122000 km"},
            {},
            {},
        )

        risk = merged["technical_risks"][0]
        self.assertEqual(risk["specific_vehicle_evidence"], "")
        self.assertEqual(risk["confidence"], "Stredna")
        self.assertNotIn("130000", risk["typical_trigger_or_interval"])

    def test_missing_vin_overrides_model_invalid_vin_hallucination(self):
        merged = _merge_backend_evidence(
            {
                "listing_facts": {},
                "vin_check": {
                    "vin_present": True,
                    "format_check": "problem",
                    "local_validation": {"vin": "N/A", "valid": False},
                },
                "consistency_checks": [
                    {
                        "check": "VIN format validation",
                        "result": "concern",
                        "explanation": "N/A has invalid VIN length.",
                    }
                ],
            },
            {"vin": ""},
            {},
            {},
        )

        self.assertFalse(merged["vin_check"]["vin_present"])
        self.assertEqual(merged["vin_check"]["format_check"], "skipped")
        self.assertNotIn("local_validation", merged["vin_check"])
        self.assertEqual(merged["consistency_checks"], [])

    def test_local_european_vin_metadata_removes_refuted_model_concerns(self):
        merged = _merge_backend_evidence(
            {
                "listing_facts": {"vin": "TMAJ3812HHJ268187"},
                "vin_check": {
                    "vin_present": True,
                    "format_check": "ok",
                    "decoded_information": "Model claimed invalid check digit.",
                },
                "consistency_checks": [
                    {
                        "check": "VIN check digit validity",
                        "result": "concern",
                        "explanation": "The check digit is invalid.",
                    },
                    {
                        "check": "VIN model year consistency",
                        "result": "concern",
                        "explanation": "Model year code may mean 1987 or 2017.",
                    },
                ],
                "data_conflicts": [
                    {
                        "issue": "Conflicting VINs between listing and photographed document",
                        "importance": "HIGH",
                        "source_a": "listing VIN TMAJ3812HHJ268187",
                        "source_b": "document VIN TMAJ3812HHJ268188",
                    }
                ],
            },
            {"vin": "TMAJ3812HHJ268187"},
            {},
            {
                "vin": "TMAJ3812HHJ268187",
                "valid": True,
                "validation_message": "Valid ROW VIN; checksum is optional.",
                "model_year_hint": None,
                "check_digit_policy": "optional_row",
                "check_digit_severity": "info",
            },
        )

        self.assertEqual(merged["vin_check"]["format_check"], "ok")
        self.assertEqual(len(merged["consistency_checks"]), 1)
        self.assertEqual(merged["consistency_checks"][0]["result"], "ok")
        self.assertEqual(len(merged["data_conflicts"]), 1)

    def test_incomplete_structured_research_is_detected_before_final_synthesis(self):
        self.assertTrue(_research_parse_failed({"_parse_error": True, "raw_preview": "{"}))
        self.assertTrue(_research_parse_failed({"raw_preview": "partial"}))
        self.assertFalse(_research_parse_failed({"source_role": "text_research"}))

    def test_missing_job_is_reported_without_flask_context(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = ListingJobRepository(Path(temp_dir) / "jobs")
            dependencies = _dependencies(repository, Path(temp_dir) / "prompts")

            events = list(
                multi_model_analysis_events(
                    "missing",
                    "",
                    ["gemini-key"],
                    dependencies=dependencies,
                )
            )

        self.assertEqual(events, ['data: {"error": "Listing job not found."}\n\n'])

    def test_missing_keys_is_reported_before_provider_or_prompt_access(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = ListingJobRepository(Path(temp_dir) / "jobs")
            repository.job_dir("sample", create=True)
            repository.write_text("sample", "car_info.md", "# Sample")
            dependencies = _dependencies(repository, Path(temp_dir) / "missing-prompts")

            events = list(
                multi_model_analysis_events(
                    "sample",
                    "",
                    [],
                    dependencies=dependencies,
                )
            )

        self.assertEqual(
            events,
            ['data: {"error": "Gemini API keys are not configured on the server."}\n\n'],
        )

    def test_injected_pipeline_preserves_phase_order_and_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repository = ListingJobRepository(root / "jobs")
            repository.job_dir("sample", create=True)
            repository.write_text("sample", "car_info.md", "# Sample listing")
            prompt_dir = root / "prompts"
            prompt_dir.mkdir()
            for filename in (
                "grok_text_research_system.md",
                "gemini_vision_system.md",
                "grok_final_synthesis_system.md",
            ):
                (prompt_dir / filename).write_text("system", encoding="utf-8")

            collected_phases = []

            def collect_gemini(entries, phase, factory, **kwargs):
                yield from ()
                collected_phases.append(phase)
                if phase == "component identity research":
                    return (
                        '{"schema_version":1,"identification_status":"PROBABLE",'
                        '"generation":{"name":"Sample generation","resolution":"PROBABLE"},'
                        '"engine":{"marketing_name":"2.0 TDI","resolution":"PROBABLE"},'
                        '"transmission":{"marketing_name":"7DCT","resolution":"PROBABLE"},'
                        '"drivetrain":{"type":"FWD","resolution":"PROBABLE"},'
                        '"candidate_variants":[],"sources":[],"notes":[]}',
                        entries[0],
                    )
                if phase == "web research":
                    return "### Orientacna cena / trh\n- Bez priamych URL.", entries[0]
                if phase == "market research sk_cz":
                    return (
                        '{"search_pass":"sk_cz","candidates":[{'
                        '"description":"Sample car","year":2015,"mileage_km":150000,'
                        '"price_eur":8900,"price_basis":"gross_asking",'
                        '"source_country":"SK","similarity_tier":"A",'
                        '"detail_url":"https://auto.bazos.sk/inzerat/123456/sample.php",'
                        '"evidence_url":"https://auto.bazos.sk/inzerat/123456/sample.php"}]}'
                        "\n\n### Citacie z Google Search\n"
                        "- [Sample car](https://auto.bazos.sk/inzerat/123456/sample.php)",
                        entries[0],
                    )
                if phase in {
                    "market research mobile_de",
                    "market research otomoto_pl",
                    "market research autoscout",
                }:
                    return '{"candidates":[]}', entries[0]
                if phase == "text/research analysis":
                    return '{"listing_facts": {}, "vin_check": {}}', entries[0]
                raise AssertionError(f"Unexpected collected phase: {phase}")

            dependencies = replace(
                _dependencies(repository, prompt_dir),
                normalize_gemini_keys=lambda keys: [
                    {"key": key, "label": f"key-{index}"}
                    for index, key in enumerate(keys)
                ],
                collect_gemini=collect_gemini,
                call_gemini=lambda *args, **kwargs: iter(("# Final report",)),
                calculate_risk_score=lambda *args, **kwargs: {
                    "risk_score": 0,
                    "allowed_final_verdict": "ZVAZIT",
                },
                estimate_request_tokens=lambda *args: 10,
                estimate_output_tokens=lambda chunk: 3,
                validate_json_contract=lambda *args: [],
                validate_final_report=lambda *args: [],
                ensure_end_analysis_marker=lambda value: value + "\n\n<!-- END_ANALYSIS -->",
                write_validation_warnings=lambda *args, **kwargs: None,
                extract_kb_blocks=lambda value: [],
            )

            events = list(
                multi_model_analysis_events(
                    "sample",
                    "",
                    ["gemini-key"],
                    dependencies=dependencies,
                )
            )

            phase_positions = [
                next(index for index, event in enumerate(events) if marker in event)
                for marker in ("Phase 1/4", "Phase 2/4", "Phase 3/4", "Phase 4/4")
            ]
            self.assertEqual(phase_positions, sorted(phase_positions))
            self.assertIn("https://auto.bazos.sk/inzerat/123456/sample.php", repository.read_text("sample", "web_research.md"))
            self.assertEqual(
                collected_phases[:7],
                [
                    "component identity research",
                    "web research",
                    "market research sk_cz",
                    "market research mobile_de",
                    "market research otomoto_pl",
                    "market research autoscout",
                    "text/research analysis",
                ],
            )
            self.assertTrue(repository.read_text("sample", "listing_facts.json"))
            self.assertTrue(repository.read_text("sample", "component_identity_research.md"))
            self.assertTrue(repository.read_text("sample", "component_identity.json"))
            self.assertTrue(repository.read_text("sample", "reliability_research.md"))
            self.assertTrue(repository.read_text("sample", "market_research.md"))
            self.assertTrue(repository.read_text("sample", "market_search_results.json"))
            self.assertTrue(repository.read_text("sample", "grok_research.json"))
            self.assertTrue(repository.read_text("sample", "gemini_vision.json"))
            self.assertTrue(repository.read_text("sample", "vision_provider_attempts.json"))
            self.assertTrue(repository.read_text("sample", "risk_score.json"))
            self.assertIn(
                "buyer_scorecard",
                json.loads(repository.read_text("sample", "risk_score.json")),
            )
            self.assertEqual(repository.read_text("sample", "analysis_result_raw.md"), "# Final report")
            self.assertIn("<!-- END_ANALYSIS -->", repository.read_text("sample", "analysis_result.md"))
            self.assertIn('"done": true', events[-1])


if __name__ == "__main__":
    unittest.main()
