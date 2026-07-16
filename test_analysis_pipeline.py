import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from scrapper_demo.services.analysis_pipeline import (
    AnalysisPipelineDependencies,
    _lock_report_evidence_claims,
    _lock_registration_age_claims,
    _canonical_research_from_v2,
    _merge_backend_evidence,
    _promote_selected_key,
    _research_parse_failed,
    _research_v2_response_schema,
    _unavailable_research_model_output,
    _unavailable_vision_payload,
    _valid_vision_payload,
    _valid_research_model_output,
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
        direct_market_search=lambda listing: [
            {
                "pass_id": "sk_cz",
                "portal": "Bazos SK/CZ",
                "language": "sk/cs",
                "market_scope": "PUBLIC_SK_CZ",
                "search_method": "DIRECT_PORTAL_HTML",
                "status": "NOTHING_FOUND",
                "citation_count": 0,
                "candidate_count": 0,
                "verified_detail_count": 0,
                "verified_background_count": 0,
                "url_unverified_count": 0,
                "candidates": [],
                "source_attempts": [],
            }
        ],
    )


class AnalysisPipelineBoundaryTests(unittest.TestCase):
    def test_research_v2_gemini_schema_uses_supported_json_schema_subset(self):
        schema = _research_v2_response_schema(Path("prompts"))
        serialized = json.dumps(schema)

        self.assertNotIn('"$schema"', serialized)
        self.assertNotIn('"const"', serialized)
        self.assertEqual(schema["properties"]["schema_version"]["enum"], [2])
        self.assertEqual(
            schema["properties"]["source_role"]["enum"],
            ["research_model_output"],
        )

    def test_research_v2_merges_only_model_owned_fields_with_backend_facts(self):
        packet = _unavailable_research_model_output("fixture")
        packet["evidence_summary"]["data_completeness_score"] = 70
        packet["web_research_findings"] = [{"claim": "Supported finding"}]
        packet["technical_risks"] = [{"component": "engine", "issue": "Inspection point"}]

        merged = _canonical_research_from_v2(
            packet,
            {
                "title": "Backend title",
                "price": "11800 EUR",
                "year": 2014,
                "mileage_km": 205350,
                "engine": "2.0 TSI",
                "transmission": "DSG",
                "drive": "4x4",
            },
            {"schema_version": 1, "identification_status": "PROBABLE"},
        )

        self.assertEqual(merged["source_role"], "text_research")
        self.assertEqual(merged["listing_facts"]["price"], "11800 EUR")
        self.assertEqual(merged["listing_facts"]["advertised_mileage_km"], 205350)
        self.assertEqual(merged["component_identity"]["identification_status"], "PROBABLE")
        self.assertEqual(merged["technical_risks"][0]["issue"], "Inspection point")
        self.assertEqual(merged["market_comparables"], [])

    def test_research_v2_rejects_array_overflow_and_has_safe_double_failure_fallback(self):
        packet = _unavailable_research_model_output("Both attempts failed")
        self.assertTrue(_valid_research_model_output(packet))
        packet["seller_claims"] = [{} for _ in range(5)]
        self.assertFalse(_valid_research_model_output(packet))

        fallback = _canonical_research_from_v2(
            _unavailable_research_model_output("Both attempts failed"),
            {"title": "Known listing", "year": 2014},
            {"schema_version": 1, "identification_status": "UNKNOWN"},
        )
        self.assertEqual(fallback["research_status"], "unavailable")
        self.assertEqual(fallback["technical_risks"], [])
        self.assertIn("Both attempts failed", fallback["missing_or_uncertain_data"][0]["why_it_matters"])

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

    def test_public_report_links_only_strict_recommendations(self):
        report = """# VW Tiguan

## Rýchle zhrnutie
- **Cena:** nejasná.

## 💰 Cena a vyjednávanie
Voľný text z modelu.
"""
        selected_url = "https://auto.bazos.cz/inzerat/1/tiguan.php"
        rejected_url = "https://auto.bazos.sk/inzerat/2/tiguan.php"
        locked = _lock_report_evidence_claims(
            report,
            {
                "market_assessment": {
                    "benchmark_available": True,
                    "benchmark_comparable_count": 3,
                    "benchmark_median_eur": 12000,
                    "advertised_price_eur": 11800,
                    "price_delta_percent": -1.7,
                    "price_view": "fair",
                },
                "market_comparables": [
                    {
                        "description": "Exact configuration within band",
                        "source_url": selected_url,
                        "verified_url": True,
                        "display_in_report": True,
                        "market_scope": "PUBLIC_SK_CZ",
                    },
                    {
                        "description": "Verified but rejected fallback",
                        "source_url": rejected_url,
                        "verified_url": True,
                        "display_in_report": False,
                        "market_scope": "PUBLIC_SK_CZ",
                    },
                ],
            },
            {},
            output_language="sk",
        )

        self.assertIn(selected_url, locked)
        self.assertNotIn(rejected_url, locked)

    def test_public_comparable_prices_are_shown_as_approximate_eur(self):
        report = """# VW Tiguan

## \U0001f4b0 Cena a vyjedn\u00e1vanie
Vo\u013En\u00fd text z modelu.
"""
        url = "https://www.sauto.cz/osobni/detail/volkswagen/tiguan/210123456"
        locked = _lock_report_evidence_claims(
            report,
            {
                "market_assessment": {
                    "benchmark_available": False,
                    "benchmark_comparable_count": 0,
                    "advertised_price_eur": 11800,
                    "summary": "N\u00e1jden\u00e9 ponuky nezostavili overen\u00fa vzorku presnej konfigur\u00e1cie vozidla.",
                },
                "market_comparables": [
                    {
                        "description": "Volkswagen Tiguan 2.0 TSI DSG 4x4",
                        "source_url": url,
                        "verified_url": True,
                        "display_in_report": True,
                        "market_scope": "PUBLIC_SK_CZ",
                        "normalized_price_eur": 10204,
                        "original_currency": "CZK",
                        "price_display": "249 990 CZK",
                        "material_difference": "same visible engine/transmission/drivetrain attributes",
                    }
                ],
            },
            {},
            output_language="sk",
        )

        self.assertIn("pribli\u017ene 10 204 EUR", locked)
        self.assertNotIn("249 990 CZK", locked)
        self.assertNotIn("same visible engine/transmission/drivetrain attributes", locked)
        self.assertIn(url, locked)

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
                "research_v2_system.md",
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
                if phase == "text/research analysis":
                    return '{"schema_version":2', entries[0]
                if phase == "text/research JSON recovery":
                    return json.dumps(
                        {
                            "schema_version": 2,
                            "source_role": "research_model_output",
                            "evidence_summary": {
                                "data_completeness_score": 50,
                                "overall_confidence": "MEDIUM",
                                "strongest_evidence": [],
                                "weakest_evidence": [],
                            },
                            "seller_claims": [],
                            "missing_or_uncertain_data": [],
                            "data_conflicts": [],
                            "consistency_checks": [],
                            "safety_and_recall": {},
                            "web_research_findings": [],
                            "technical_risks": [],
                            "expected_costs": [],
                            "text_research_risk_flags": [],
                            "sources_used": [],
                        }
                    ), entries[0]
                raise AssertionError(f"Unexpected collected phase: {phase}")

            dependencies = replace(
                _dependencies(repository, prompt_dir),
                normalize_gemini_keys=lambda keys: [
                    {"key": key, "label": f"key-{index}"}
                    for index, key in enumerate(keys)
                ],
                collect_gemini=collect_gemini,
                direct_market_search=lambda listing: [
                    {
                        "pass_id": "sk_cz",
                        "portal": "Bazos SK/CZ",
                        "language": "sk/cs",
                        "market_scope": "PUBLIC_SK_CZ",
                        "search_method": "DIRECT_PORTAL_HTML",
                        "search_query": "Sample car",
                        "status": "FOUND",
                        "citation_count": 1,
                        "candidate_count": 1,
                        "verified_detail_count": 1,
                        "verified_background_count": 0,
                        "url_unverified_count": 0,
                        "source_attempts": [],
                        "candidates": [
                            {
                                "candidate_id": "BAZOS-SK-123456",
                                "description": "Sample car",
                                "year": 2015,
                                "mileage_km": 150000,
                                "price_eur": 8900,
                                "price_basis": "gross_asking",
                                "source_country": "SK",
                                "similarity_tier": "A",
                                "search_pass": "sk_cz",
                                "search_language": "sk",
                                "market_scope": "PUBLIC_SK_CZ",
                                "source_url": "https://auto.bazos.sk/inzerat/123456/sample.php",
                                "claimed_source_url": "https://auto.bazos.sk/inzerat/123456/sample.php",
                                "evidence_url": "https://auto.bazos.sk/inzeraty/sample-car/",
                                "verified_url": True,
                                "url_verification_status": "VERIFIED_DETAIL",
                                "background_evidence_verified": False,
                                "display_in_report": True,
                                "data_provenance": "DIRECT_PORTAL_SEARCH",
                            }
                        ],
                    }
                ],
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

            fake_text_provider_entries = [
                {
                    "id": "failed-call",
                    "model": "gemini-2.5-flash",
                    "phase": "text_research",
                    "status": "rate_limited",
                    "attempt": 1,
                    "retry_reason": "model_fallback",
                    "input_tokens": 9281,
                    "output_tokens": 0,
                    "actual_input_tokens": None,
                    "actual_output_tokens": None,
                    "actual_thinking_tokens": None,
                    "cached_input_tokens": None,
                    "actual_total_tokens": None,
                    "visible_output_tokens": 0,
                    "thinking_tokens": 0,
                    "total_tokens": 9281,
                    "usage_source": "estimated",
                    "estimated_cost": 0.0,
                    "duration_ms": 306,
                    "error": "quota",
                },
                {
                    "id": "successful-fallback",
                    "model": "gemini-3.5-flash",
                    "phase": "text_research",
                    "status": "success",
                    "attempt": 2,
                    "retry_reason": "model_fallback",
                    "input_tokens": 11443,
                    "output_tokens": 6760,
                    "actual_input_tokens": 11443,
                    "actual_output_tokens": 6760,
                    "actual_thinking_tokens": None,
                    "cached_input_tokens": None,
                    "actual_total_tokens": 18203,
                    "visible_output_tokens": 6760,
                    "thinking_tokens": 0,
                    "total_tokens": 18203,
                    "usage_source": "provider",
                    "estimated_cost": 0.078005,
                    "duration_ms": 32507,
                    "finish_reason": "STOP",
                    "output_chars": 20162,
                },
            ]

            def fake_requests_for_run(run_id, *, phase=None):
                return fake_text_provider_entries if phase == "text_research" else []

            with patch(
                "scrapper_demo.services.analysis_pipeline.default_tracker.get_requests_for_run",
                side_effect=fake_requests_for_run,
            ):
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
            self.assertIn(
                "https://auto.bazos.sk/inzerat/123456/sample.php",
                repository.read_text("sample", "market_search_results.json"),
            )
            self.assertEqual(
                collected_phases[:3],
                [
                    "component identity research",
                    "web research",
                    "text/research analysis",
                ],
            )
            self.assertEqual(
                collected_phases.count("text/research JSON recovery"),
                1,
            )
            self.assertTrue(repository.read_text("sample", "listing_facts.json"))
            self.assertTrue(repository.read_text("sample", "component_identity_research.md"))
            self.assertTrue(repository.read_text("sample", "component_identity.json"))
            self.assertTrue(repository.read_text("sample", "reliability_research.md"))
            self.assertTrue(repository.read_text("sample", "market_research.md"))
            self.assertTrue(repository.read_text("sample", "market_search_results.json"))
            self.assertTrue(repository.read_text("sample", "grok_research.json"))
            self.assertTrue(repository.read_text("sample", "research_model_output.json"))
            self.assertTrue(repository.read_text("sample", "gemini_vision.json"))
            self.assertTrue(repository.read_text("sample", "vision_provider_attempts.json"))
            self.assertTrue(repository.read_text("sample", "text_research_provider_attempts.json"))
            diagnostics = json.loads(repository.read_text("sample", "analysis_diagnostics.json"))
            text_diagnostics = diagnostics["phases"]["text_research"]
            self.assertEqual(text_diagnostics["status"], "completed")
            self.assertTrue(text_diagnostics["recovery_attempted"])
            self.assertTrue(text_diagnostics["recovered"])
            self.assertIn("policy", text_diagnostics)
            self.assertIn("input_budget", text_diagnostics)
            self.assertIn("recovery_input_budget", text_diagnostics)
            text_attempts = json.loads(
                repository.read_text("sample", "text_research_provider_attempts.json")
            )
            self.assertEqual(
                text_attempts["attempts"][0]["usage"]["estimated_input_tokens"],
                20724,
            )
            self.assertEqual(
                text_attempts["attempts"][0]["provider_calls"][0]["estimated_input_tokens"],
                9281,
            )
            self.assertEqual(text_attempts["attempt_count"], 2)
            self.assertTrue(text_attempts["attempts"][1]["schema_valid"])
            usage_summary = json.loads(repository.read_text("sample", "ai_usage_summary.json"))
            self.assertIn("analysis_run_id", usage_summary)
            self.assertEqual(usage_summary["call_count"], 0)
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
