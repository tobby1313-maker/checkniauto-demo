import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from scrapper_demo.providers.errors import ModelOutputLimitError, RateLimitError
from scrapper_demo.services.analysis_pipeline import (
    AnalysisPipelineDependencies,
    _apply_research_delivery_gate,
    _build_grounded_source_registry,
    _contains_fixed_service_interval,
    _redact_fixed_service_interval,
    _complete_supported_research_sections,
    _lock_report_evidence_claims,
    _lock_registration_age_claims,
    _limited_research_model_output,
    _canonical_research_from_v2,
    _enforce_research_source_policy,
    _ensure_final_recommendation_body,
    _filter_research_transmission_conflicts,
    _generalize_candidate_component_codes,
    _merge_backend_evidence,
    _market_search_listing_context,
    _promote_selected_key,
    _select_limited_research_candidate,
    _research_parse_failed,
    _research_v2_response_schema,
    _normalize_research_model_output,
    _sanitize_vision_claims,
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
    def test_normalization_removes_unverified_seller_claim_from_strongest_evidence(self):
        packet = {
            "evidence_summary": {
                "overall_confidence": "LOW",
                "strongest_evidence": [
                    "Záznamy potvrdzujú pravidelnú údržbu DSG a Haldexu.",
                    "Technický zdroj opisuje riziko rozvodovej reťaze.",
                ],
            },
            "seller_claims": [{
                "claim": "Úplná servisná história a servisná knižka.",
                "verification_status": "UNVERIFIED",
                "evidence_category": "NEEDS_VERIFICATION",
            }],
        }

        normalized = _normalize_research_model_output(packet)

        self.assertEqual(
            normalized["evidence_summary"]["strongest_evidence"],
            ["Technický zdroj opisuje riziko rozvodovej reťaze."],
        )

    def test_grounded_source_registry_is_stable_deduplicated_and_public(self):
        registry = _build_grounded_source_registry(
            "https://workshop.test/dq500/ https://workshop.test/dq500 "
            "https://vertexaisearch.cloud.google.com/redirect "
            "https://official.test/recall"
        )

        self.assertEqual(registry, {
            "gsrc_001": "https://official.test/recall",
            "gsrc_002": "https://workshop.test/dq500",
        })

    def test_research_v2_gemini_schema_uses_supported_json_schema_subset(self):
        schema = _research_v2_response_schema(Path("prompts"))
        serialized = json.dumps(schema, separators=(",", ":"))

        self.assertNotIn('"$schema"', serialized)
        self.assertNotIn('"const"', serialized)
        self.assertNotIn('"maxItems"', serialized)
        self.assertNotIn('"$defs"', serialized)
        self.assertLess(len(serialized), 5_000)
        self.assertEqual(schema["properties"]["schema_version"]["enum"], [2])
        self.assertEqual(
            schema["properties"]["source_role"]["enum"],
            ["research_model_output"],
        )
        self.assertIn(
            "verification_action",
            schema["properties"]["technical_risks"]["items"]["required"],
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
        self.assertIn("Both attempts failed", fallback["evidence_summary"]["weakest_evidence"][0])
        self.assertNotIn("Both attempts failed", fallback["missing_or_uncertain_data"][0]["why_it_matters"])

    def test_unavailable_research_fallback_is_customer_safe_in_all_languages(self):
        for language, expected in (
            ("sk", "Technické overenie modelu"),
            ("cs", "Technické ověření modelu"),
            ("en", "Model-specific technical research"),
        ):
            packet = _unavailable_research_model_output(
                "Research V2 returned invalid JSON twice.",
                output_language=language,
            )
            customer_item = packet["missing_or_uncertain_data"][0]
            self.assertEqual(customer_item["item"], expected)
            self.assertNotIn("invalid JSON", customer_item["why_it_matters"])
            self.assertIn("invalid JSON", packet["evidence_summary"]["weakest_evidence"][0])

    def test_limited_research_fallback_keeps_content_but_removes_links_and_prices(self):
        packet = _unavailable_research_model_output("fixture")
        packet["evidence_summary"].update({
            "data_completeness_score": 70,
            "overall_confidence": "MEDIUM",
        })
        packet["technical_risks"] = [{
            "component": "EA888",
            "issue": "Timing-chain wear can require a 1 200–2 000 EUR repair.",
            "risk_level": "HIGH",
            "evidence_category": "MODEL_LEVEL_RISK",
            "buyer_impact": "Possible engine damage.",
            "specific_vehicle_evidence": "",
            "verification_action": "Check every 60 000 km.",
            "estimated_cost_eur_low": 1200,
            "estimated_cost_eur_high": 2000,
            "confidence": "Stredna",
            "source_ids": ["made-up"],
        }]
        packet["sources_used"] = [{
            "source_id": "made-up",
            "source_name": "Rewritten source",
            "source_type": "TECHNICAL_PUBLICATION",
            "reliability": "MEDIUM",
            "source_url": "https://rewritten.invalid/article",
            "verified_url": False,
            "used_for": "timing chain",
        }]

        limited = _limited_research_model_output(packet, output_language="sk")

        self.assertTrue(_valid_research_model_output(limited))
        self.assertEqual(limited["sources_used"], [])
        self.assertEqual(limited["technical_risks"][0]["source_ids"], [])
        self.assertIsNone(limited["technical_risks"][0]["estimated_cost_eur_low"])
        self.assertNotIn("1 200", limited["technical_risks"][0]["issue"])
        self.assertNotIn("60 000", limited["technical_risks"][0]["verification_action"])
        self.assertEqual(limited["evidence_summary"]["overall_confidence"], "LOW")

    def test_research_v2_rejects_malformed_nested_items_after_serving_validation(self):
        packet = _unavailable_research_model_output("fixture")
        packet["technical_risks"] = [{"issue": "Incomplete object"}]

        self.assertFalse(_valid_research_model_output(packet))

    def test_research_v2_without_supported_technical_evidence_is_limited(self):
        packet = _unavailable_research_model_output("fixture")
        packet["evidence_summary"]["data_completeness_score"] = 40

        merged = _canonical_research_from_v2(packet, {"title": "Known listing"}, {})

        self.assertEqual(merged["research_status"], "limited")

    def test_research_v2_normalizes_aliases_and_rejects_unknown_enums(self):
        packet = _unavailable_research_model_output("fixture")
        packet["evidence_summary"]["data_completeness_score"] = 50
        packet["evidence_summary"]["overall_confidence"] = "MODERATE"
        packet["seller_claims"] = [
            {
                "claim": "Full history", "evidence_category": "UNVERIFIED_CLAIM",
                "verification_status": "unverified", "buyer_relevance": "Check invoices",
            },
            {
                "claim": "Recent service", "evidence_category": "OTHER",
                "verification_status": "unverified", "buyer_relevance": "Check invoice",
            },
        ]
        packet["missing_or_uncertain_data"][0]["severity"] = "critical"
        packet["expected_costs"] = [{
            "item": "Initial oil service", "why": "Service history is unknown",
            "estimated_cost_eur_low": 100, "estimated_cost_eur_high": 150,
            "cost_type": "MAINTENANCE", "urgency": "medium",
            "basis": "Grounded workshop estimate", "source_ids": [],
        }]
        packet["consistency_checks"] = [{
            "check": "Mileage", "result": "NEEDS_VERIFICATION", "explanation": "Check records",
        }]
        packet["safety_and_recall"].update({
            "status": "RECALLS_IDENTIFIED_PENDING_VIN_CHECK",
            "evidence_category": "REGULATORY",
        })
        packet["web_research_findings"] = [{
            "claim": "Published model-level issue",
            "evidence_category": "TECHNICAL_PUBLICATION",
            "buyer_impact": "Inspect the component",
            "confidence": "MEDIUM",
            "source_ids": [],
        }]
        normalized = _normalize_research_model_output(packet)

        self.assertEqual(normalized["evidence_summary"]["overall_confidence"], "MEDIUM")
        self.assertEqual(normalized["seller_claims"][0]["evidence_category"], "LISTING_CLAIM")
        self.assertEqual(normalized["seller_claims"][1]["evidence_category"], "NEEDS_VERIFICATION")
        self.assertEqual(normalized["missing_or_uncertain_data"][0]["severity"], "high")
        self.assertEqual(normalized["expected_costs"][0]["cost_type"], "initial_service")
        self.assertEqual(normalized["consistency_checks"][0]["result"], "unknown")
        self.assertEqual(
            normalized["safety_and_recall"]["status"],
            "POSSIBLE_CAMPAIGN_NEEDS_VIN_CHECK",
        )
        self.assertEqual(
            normalized["safety_and_recall"]["evidence_category"],
            "NEEDS_VERIFICATION",
        )
        self.assertEqual(
            normalized["web_research_findings"][0]["evidence_category"],
            "MODEL_LEVEL_RISK",
        )
        self.assertTrue(_valid_research_model_output(normalized))
        normalized["seller_claims"][0]["evidence_category"] = "MADE_UP"
        self.assertFalse(_valid_research_model_output(normalized))

    def test_research_v2_normalizes_aliases_seen_in_tiguan_recovery(self):
        packet = _unavailable_research_model_output("fixture")
        packet["consistency_checks"] = [
            {"check": "Identity", "result": "passed", "explanation": "Matches"},
            {
                "check": "Seller claim support",
                "result": "consistent_with_claim",
                "explanation": "Available data agrees.",
            },
            {
                "check": "Imported history",
                "result": "inconsistent_with_claim",
                "explanation": "The claim conflicts with available data.",
            },
        ]
        packet["web_research_findings"] = [{
            "claim": "Published component specification",
            "evidence_category": "TECHNICAL_SPECIFICATION",
            "buyer_impact": "Verify the exact component",
            "confidence": "LOW",
            "source_ids": [],
        }]
        packet["expected_costs"] = [{
            "item": "Diagnostic scan",
            "why": "Check stored faults",
            "estimated_cost_eur_low": None,
            "estimated_cost_eur_high": None,
            "cost_type": "DIAGNOSTIC",
            "urgency": "medium",
            "basis": "Inspection scope",
            "source_ids": [],
        }]

        normalized = _normalize_research_model_output(packet)

        self.assertEqual(normalized["consistency_checks"][0]["result"], "ok")
        self.assertEqual(normalized["consistency_checks"][1]["result"], "ok")
        self.assertEqual(normalized["consistency_checks"][2]["result"], "concern")
        self.assertEqual(
            normalized["web_research_findings"][0]["evidence_category"],
            "NEEDS_VERIFICATION",
        )
        self.assertEqual(normalized["expected_costs"][0]["cost_type"], "diagnostic")
        self.assertTrue(_valid_research_model_output(normalized))

    def test_research_v2_normalizes_recommended_cost_urgency(self):
        packet = _unavailable_research_model_output("fixture")
        packet["expected_costs"] = [{
            "item": "Predkúpna diagnostika",
            "why": "Overenie chybových kódov.",
            "estimated_cost_eur_low": None,
            "estimated_cost_eur_high": None,
            "cost_type": "SCHEDULED_MAINTENANCE",
            "urgency": "recommended",
            "basis": "Kontrolný úkon.",
            "source_ids": [],
        }]

        normalized = _normalize_research_model_output(packet)

        self.assertEqual(normalized["expected_costs"][0]["urgency"], "medium")
        self.assertEqual(normalized["expected_costs"][0]["cost_type"], "initial_service")
        self.assertTrue(_valid_research_model_output(normalized))

    def test_research_v2_normalizes_cx60_provider_aliases(self):
        packet = _unavailable_research_model_output("fixture")
        packet["seller_claims"] = [{
            "claim": "Mazda CX-60 D200",
            "evidence_category": "LISTING_SPECIFICATION",
            "verification_status": "UNVERIFIED",
            "buyer_relevance": "Overiť doklady.",
        }]
        packet["web_research_findings"] = [{
            "claim": "Official maintenance item",
            "evidence_category": "OFFICIAL_MAINTENANCE_SCHEDULE",
            "buyer_impact": "Check records.",
            "confidence": "MEDIUM",
            "source_ids": [],
        }]
        packet["technical_risks"] = [{
            "component": "Chassis",
            "issue": "Firm ride",
            "risk_level": "CHECK",
            "evidence_category": "MODEL_SPECIFIC_CHARACTERISTIC",
            "buyer_impact": "Comfort",
            "specific_vehicle_evidence": "",
            "verification_action": "Test drive",
            "estimated_cost_eur_low": None,
            "estimated_cost_eur_high": None,
            "confidence": "MEDIUM",
            "source_ids": [],
        }]
        packet["expected_costs"] = [{
            "item": "Service",
            "why": "If records are missing.",
            "estimated_cost_eur_low": None,
            "estimated_cost_eur_high": None,
            "cost_type": "initial_service",
            "urgency": "immediate_if_not_done",
            "basis": "Quote required.",
            "source_ids": [],
        }]

        normalized = _normalize_research_model_output(packet)

        self.assertEqual(normalized["seller_claims"][0]["evidence_category"], "LISTING_CLAIM")
        self.assertEqual(normalized["web_research_findings"][0]["evidence_category"], "MODEL_LEVEL_RISK")
        self.assertEqual(normalized["technical_risks"][0]["evidence_category"], "MODEL_LEVEL_RISK")
        self.assertEqual(normalized["expected_costs"][0]["urgency"], "high")
        self.assertTrue(_valid_research_model_output(normalized))

    def test_market_search_context_uses_grounded_generation_for_make_less_title(self):
        augmented = _market_search_listing_context(
            {"title": "CX-60 3.3 e-Skyactive D200"},
            {"generation": {"name": "Mazda CX-60 (KH)"}},
        )

        self.assertTrue(augmented["title"].startswith("Mazda CX-60 (KH)"))
        self.assertTrue(augmented["market_identity_augmented"])

    def test_research_v2_normalizes_aliases_seen_in_honda_recovery(self):
        packet = _unavailable_research_model_output("fixture")
        packet["safety_and_recall"].update({
            "status": "ACTION_REQUIRED",
            "evidence_category": "RECALL_INFORMATION",
        })
        packet["seller_claims"] = [{
            "claim": "Servisná história",
            "evidence_category": "LISTING_DATA",
            "verification_status": "UNVERIFIED",
            "buyer_relevance": "Overiť doklady.",
        }]
        packet["consistency_checks"] = [{
            "check": "Modelové problémy",
            "result": "konzistentné s hlásenými problémami.",
            "explanation": "Vyžaduje kontrolu.",
        }]
        packet["web_research_findings"] = [{
            "claim": "Hybrid-system inspection is recommended.",
            "evidence_category": "MODEL_GENERAL_ISSUE",
            "buyer_impact": "Check the system before purchase.",
            "confidence": "MODERATE",
            "source_ids": [],
        }]
        packet["technical_risks"] = [{
            "component": "Hybrid system",
            "issue": "Inspection point",
            "risk_level": "CHECK",
            "evidence_category": "MODEL_ISSUE",
            "buyer_impact": "Check before purchase.",
            "specific_vehicle_evidence": "",
            "verification_action": "Run diagnostics.",
            "estimated_cost_eur_low": None,
            "estimated_cost_eur_high": None,
            "confidence": "MODERATE",
            "source_ids": [],
        }]
        packet["expected_costs"] = [{
            "item": "Oil service",
            "why": "Routine maintenance.",
            "estimated_cost_eur_low": None,
            "estimated_cost_eur_high": None,
            "cost_type": "REQUIRED_MAINTENANCE",
            "urgency": "medium",
            "basis": "Workshop quote required.",
            "source_ids": [],
        }]
        packet["text_research_risk_flags"] = [{
            "risk": "Unknown service scope",
            "why_it_matters_to_buyer": "Maintenance may be due.",
            "evidence": "Listing claim only.",
            "confidence": "MODERATE",
        }]

        normalized = _normalize_research_model_output(packet)

        self.assertEqual(
            normalized["web_research_findings"][0]["evidence_category"],
            "MODEL_LEVEL_RISK",
        )
        self.assertEqual(
            normalized["safety_and_recall"]["status"],
            "POSSIBLE_CAMPAIGN_NEEDS_VIN_CHECK",
        )
        self.assertEqual(normalized["seller_claims"][0]["evidence_category"], "LISTING_CLAIM")
        self.assertEqual(normalized["consistency_checks"][0]["result"], "concern")
        self.assertEqual(normalized["technical_risks"][0]["evidence_category"], "MODEL_LEVEL_RISK")
        self.assertEqual(normalized["expected_costs"][0]["cost_type"], "initial_service")
        self.assertEqual(normalized["web_research_findings"][0]["confidence"], "Stredna")
        self.assertEqual(normalized["text_research_risk_flags"][0]["confidence"], "Stredna")
        self.assertTrue(_valid_research_model_output(normalized))

    def test_limited_research_salvages_unknown_enums_with_safe_defaults(self):
        packet = _unavailable_research_model_output("fixture")
        packet["evidence_summary"]["data_completeness_score"] = 70
        packet["web_research_findings"] = [{
            "claim": "Model-level inspection point",
            "evidence_category": "NEW_PROVIDER_ENUM",
            "buyer_impact": "Inspect before purchase.",
            "confidence": "UNCERTAIN",
            "source_ids": [],
        }]
        packet["technical_risks"] = [{
            "component": "Hybrid system",
            "issue": "Inspect system operation",
            "risk_level": "HIGH",
            "evidence_category": "NEW_PROVIDER_ENUM",
            "buyer_impact": "A workshop check is prudent.",
            "specific_vehicle_evidence": "",
            "verification_action": "Verify NHTSA Campaign Number 23V858000 by VIN.",
            "estimated_cost_eur_low": None,
            "estimated_cost_eur_high": None,
            "confidence": "UNCERTAIN",
            "source_ids": [],
        }]
        packet["expected_costs"] = [{
            "item": "Pre-purchase diagnostics",
            "why": "Check the hybrid system.",
            "estimated_cost_eur_low": None,
            "estimated_cost_eur_high": None,
            "cost_type": "CHECKUP",
            "urgency": "SOON",
            "basis": "Request a workshop quote.",
            "source_ids": [],
        }]

        limited = _limited_research_model_output(packet, output_language="sk")

        self.assertTrue(_valid_research_model_output(limited))
        self.assertEqual(limited["web_research_findings"][0]["confidence"], "Nizka")
        self.assertEqual(limited["technical_risks"][0]["risk_level"], "CHECK")
        self.assertNotIn("23V858000", limited["technical_risks"][0]["verification_action"])
        self.assertEqual(
            limited["technical_risks"][0]["verification_action"],
            "Overte otvorené zvolávacie akcie podľa VIN v autorizovanom servise.",
        )
        self.assertEqual(limited["expected_costs"][0]["cost_type"], "diagnostic")
        self.assertEqual(limited["expected_costs"][0]["urgency"], "medium")

    def test_research_v2_normalizes_aliases_seen_in_bmw_x3_run(self):
        packet = _unavailable_research_model_output("fixture")
        packet["consistency_checks"] = [{
            "check": "Transmission",
            "result": "neoverené",
            "explanation": "Requires inspection.",
        }]
        packet["web_research_findings"] = [{
            "claim": "Model-level issue",
            "evidence_category": "MODEL_COMMON_ISSUE",
            "buyer_impact": "Inspect before purchase.",
            "confidence": "LOW",
            "source_ids": [],
        }]
        packet["technical_risks"] = [{
            "component": "Drivetrain",
            "issue": "Maintenance point",
            "risk_level": "CHECK",
            "evidence_category": "MODEL_MAINTENANCE_REQUIREMENT",
            "buyer_impact": "Check service history.",
            "specific_vehicle_evidence": "",
            "verification_action": "Inspect it.",
            "estimated_cost_eur_low": None,
            "estimated_cost_eur_high": None,
            "confidence": "LOW",
            "source_ids": [],
        }]
        packet["expected_costs"] = [{
            "item": "Conditional repair",
            "why": "Only if inspection confirms a fault.",
            "estimated_cost_eur_low": None,
            "estimated_cost_eur_high": None,
            "cost_type": "REPAIR",
            "urgency": "medium",
            "basis": "Workshop quote required.",
            "source_ids": [],
        }]

        normalized = _normalize_research_model_output(packet)

        self.assertEqual(normalized["consistency_checks"][0]["result"], "unknown")
        self.assertEqual(
            normalized["web_research_findings"][0]["evidence_category"],
            "MODEL_LEVEL_RISK",
        )
        self.assertEqual(
            normalized["technical_risks"][0]["evidence_category"],
            "MODEL_LEVEL_RISK",
        )
        self.assertEqual(normalized["expected_costs"][0]["cost_type"], "conditional_repair")
        self.assertTrue(_valid_research_model_output(normalized))

    def test_research_v2_normalizes_aliases_seen_in_kodiaq_run(self):
        packet = _unavailable_research_model_output("fixture")
        packet["consistency_checks"] = [{
            "check": "Configuration",
            "result": "konzistentné",
            "explanation": "Visible facts agree.",
        }]
        packet["web_research_findings"] = [{
            "claim": "General model reliability",
            "evidence_category": "MODEL_GENERAL_RELIABILITY",
            "buyer_impact": "Useful background.",
            "confidence": "LOW",
            "source_ids": [],
        }]
        packet["technical_risks"] = [{
            "component": "Engine",
            "issue": "Inspection point",
            "risk_level": "LOW",
            "evidence_category": "MODEL_KNOWLEDGE",
            "buyer_impact": "Inspect before purchase.",
            "specific_vehicle_evidence": "",
            "verification_action": "Run diagnostics.",
            "estimated_cost_eur_low": None,
            "estimated_cost_eur_high": None,
            "confidence": "LOW",
            "source_ids": [],
        }]

        normalized = _normalize_research_model_output(packet)

        self.assertEqual(normalized["consistency_checks"][0]["result"], "ok")
        self.assertEqual(
            normalized["web_research_findings"][0]["evidence_category"],
            "MODEL_LEVEL_RISK",
        )
        self.assertEqual(normalized["technical_risks"][0]["risk_level"], "CHECK")
        self.assertEqual(
            normalized["technical_risks"][0]["evidence_category"],
            "MODEL_LEVEL_RISK",
        )
        self.assertTrue(_valid_research_model_output(normalized))

    def test_limited_research_prefers_useful_initial_attempt_over_broken_recovery(self):
        initial = _unavailable_research_model_output("fixture")
        initial["evidence_summary"]["data_completeness_score"] = 65
        initial["web_research_findings"] = [{
            "claim": "Published model inspection point",
            "evidence_category": "MODEL_SPECIFIC_ISSUE",
            "buyer_impact": "Inspect before purchase.",
            "confidence": "MEDIUM",
            "source_ids": [],
        }]
        initial["technical_risks"] = [{
            "component": "Emissions system",
            "issue": "Inspection point",
            "risk_level": "CHECK",
            "evidence_category": "MODEL_SPECIFIC_ISSUE",
            "buyer_impact": "Diagnostics are prudent.",
            "specific_vehicle_evidence": "",
            "verification_action": "Run diagnostics.",
            "estimated_cost_eur_low": None,
            "estimated_cost_eur_high": None,
            "confidence": "MEDIUM",
            "source_ids": [],
        }]
        initial["expected_costs"] = [{
            "item": "Diagnostics",
            "why": "Inspect the emissions system.",
            "estimated_cost_eur_low": None,
            "estimated_cost_eur_high": None,
            "cost_type": "diagnostic",
            "urgency": "medium",
            "basis": "Workshop quote required.",
            "source_ids": [],
        }]

        attempt, selected = _select_limited_research_candidate(
            initial,
            {"_parse_error": True},
        )
        limited = _limited_research_model_output(selected, output_language="sk")

        self.assertEqual(attempt, "initial")
        self.assertTrue(_valid_research_model_output(limited))
        self.assertEqual(len(limited["web_research_findings"]), 1)
        self.assertEqual(len(limited["technical_risks"]), 1)
        self.assertEqual(len(limited["expected_costs"]), 1)

    def test_explicit_manual_listing_removes_automatic_research_items(self):
        packet = _unavailable_research_model_output("fixture")
        packet["web_research_findings"] = [
            {"claim": "ZF 8HP automatic transmission oil service", "buyer_impact": "Cost"},
            {"claim": "N20 timing-chain inspection", "buyer_impact": "Engine risk"},
        ]
        packet["technical_risks"] = [
            {"component": "Automatic transmission ZF 8HP", "issue": "Oil service"},
            {"component": "Engine N20", "issue": "Timing chain"},
        ]
        packet["expected_costs"] = [
            {"item": "ZF 8HP automatic transmission service", "why": "Maintenance"},
            {"item": "Engine diagnostics", "why": "Inspection"},
        ]

        filtered, counts = _filter_research_transmission_conflicts(
            packet,
            {"transmission": "Manuálna 6-st."},
            {"transmission": {"marketing_name": "6-speed Manual", "family": "Manual"}},
        )

        self.assertEqual(counts, {
            "web_research_findings": 1,
            "technical_risks": 1,
            "expected_costs": 1,
        })
        self.assertEqual(filtered["web_research_findings"][0]["claim"], "N20 timing-chain inspection")
        self.assertEqual(filtered["technical_risks"][0]["component"], "Engine N20")
        self.assertEqual(filtered["expected_costs"][0]["item"], "Engine diagnostics")

    def test_candidate_only_component_codes_are_not_rendered_as_selected_facts(self):
        packet = _unavailable_research_model_output("fixture")
        packet["web_research_findings"] = [{
            "claim": "Motor EA288 (DFGA) is reliable and DQ381 is robust.",
            "buyer_impact": "Inspect both components.",
        }]
        packet["technical_risks"] = [{
            "component": "Prevodovka DQ381/DQ500 (7-st. DSG)",
            "issue": "DQ381 or DQ500 mechatronic inspection",
        }]
        packet["expected_costs"] = [{
            "item": "DFGA diagnostics",
            "why": "Check the engine.",
        }]
        identity = {
            "engine": {"marketing_name": "2.0 TDI", "family": "EA288", "code": ""},
            "transmission": {"marketing_name": "7-st. DSG", "family": "DQ", "code": ""},
            "candidate_variants": [
                {"engine_code": "DFGA", "transmission_code": ""},
                {"engine_code": "", "transmission_code": "DQ381"},
                {
                    "engine_code": "",
                    "transmission_code": "",
                    "reason": "Possible DQ250 or DQ500 transmission; exact variant is unknown.",
                },
            ],
        }

        generalized, counts = _generalize_candidate_component_codes(packet, identity)
        serialized = json.dumps(generalized)

        self.assertNotIn("DFGA", serialized)
        self.assertNotIn("DQ381", serialized)
        self.assertNotIn("DQ500", serialized)
        self.assertEqual(
            generalized["technical_risks"][0]["component"],
            "Prevodovka (7-st. DSG)",
        )
        self.assertGreaterEqual(counts["engine"], 2)
        self.assertGreaterEqual(counts["transmission"], 3)

    def test_report_renders_limited_evidence_and_non_numeric_cost_actions(self):
        report = (
            "# Report\n\n## 🌐 Webové overenie\n\nplaceholder\n\n"
            "## 🔧 Technické riziká modelu a komponentov\n\nplaceholder\n\n"
            "## 🛠️ Očakávané náklady na najbližších 30 000 km\n\nplaceholder\n"
        )
        research = {
            "web_research_findings": [{
                "claim": "Skontrolovať funkciu hybridného systému",
                "buyer_impact": "Kontrola môže odhaliť uložené chyby.",
                "confidence": "Nizka",
                "source_ids": [],
            }],
            "technical_risks": [{
                "component": "Hybridný systém",
                "issue": "Orientačný kontrolný bod",
                "risk_level": "CHECK",
                "buyer_impact": "Overiť diagnostikou.",
                "verification_action": "Vykonať predkúpnu diagnostiku.",
                "confidence": "Nizka",
                "source_ids": [],
            }],
            "expected_costs": [{
                "item": "Predkúpna diagnostika",
                "why": "Kontrola hybridného systému",
                "estimated_cost_eur_low": None,
                "estimated_cost_eur_high": None,
                "urgency": "medium",
                "source_ids": [],
            }],
            "sources_used": [],
            "listing_facts": {},
            "vin_check": {},
            "market_assessment": {
                "benchmark_available": True,
                "benchmark_comparable_count": 3,
                "benchmark_median_eur": 20_000,
            },
            "market_comparables": [],
        }

        locked = _lock_report_evidence_claims(report, research, {}, output_language="sk")

        self.assertIn("Orientačný modelový kontrolný bod", locked)
        self.assertIn("nejde o dôkaz vady konkrétneho vozidla", locked)
        self.assertIn("Predkúpna diagnostika", locked)
        self.assertIn("Cena na overenie", locked)
        self.assertIn("stredná", locked)

    def test_research_v2_source_policy_requires_topic_match_and_official_intervals(self):
        packet = _unavailable_research_model_output("fixture")
        packet["evidence_summary"]["data_completeness_score"] = 60
        packet["web_research_findings"] = [
            {
                "claim": "DSG oil service interval is every 60000 km.",
                "evidence_category": "MODEL_LEVEL_MAINTENANCE",
                "buyer_impact": "Verify service history.",
                "confidence": "HIGH",
                "source_ids": ["official"],
            },
            {
                "claim": "Haldex oil service interval is every 60000 km.",
                "evidence_category": "MODEL_LEVEL_MAINTENANCE",
                "buyer_impact": "Verify service history.",
                "confidence": "HIGH",
                "source_ids": ["blog"],
            },
        ]
        packet["technical_risks"] = [{
            "component": "Engine", "issue": "Timing chain tensioner failure",
            "risk_level": "HIGH", "evidence_category": "MODEL_LEVEL_ISSUE",
            "buyer_impact": "Possible engine damage", "specific_vehicle_evidence": "",
            "verification_action": "Listen during cold start",
            "estimated_cost_eur_low": 500, "estimated_cost_eur_high": 1000,
            "confidence": "HIGH", "source_ids": ["blog", "catalog"],
        }]
        packet["sources_used"] = [
            {
                "source_id": "official", "source_name": "VW manual", "source_type": "OFFICIAL",
                "reliability": "HIGH", "source_url": "https://vw.example/manual",
                "verified_url": True, "used_for": "DSG oil service interval 60000 km",
            },
            {
                "source_id": "blog", "source_name": "Technical article",
                "source_type": "TECHNICAL_PUBLICATION", "reliability": "MEDIUM",
                "source_url": "https://example.test/article", "verified_url": True,
                "used_for": "Timing chain tensioner failure and Haldex oil service interval",
            },
            {
                "source_id": "catalog", "source_name": "Parts catalog",
                "source_type": "PARTS_CATALOG", "reliability": "HIGH",
                "source_url": "https://parts.example/item", "verified_url": True,
                "used_for": "Timing chain tensioner failure",
            },
        ]

        filtered = _enforce_research_source_policy(_normalize_research_model_output(packet))

        self.assertTrue(_valid_research_model_output(filtered))
        self.assertEqual(len(filtered["web_research_findings"]), 1)
        self.assertEqual(filtered["web_research_findings"][0]["source_ids"], ["official"])
        self.assertEqual(filtered["technical_risks"][0]["source_ids"], ["blog"])
        self.assertEqual(filtered["technical_risks"][0]["confidence"], "Stredna")
        self.assertNotIn("catalog", {item["source_id"] for item in filtered["sources_used"]})

    def test_source_policy_matches_issue_topics_but_rejects_wrong_component_scope(self):
        packet = _unavailable_research_model_output("fixture")
        packet["evidence_summary"]["data_completeness_score"] = 60
        packet["technical_risks"] = [{
            "component": "EA888 Gen 2", "issue": "Excessive oil consumption",
            "risk_level": "HIGH", "evidence_category": "MODEL_LEVEL_RISK",
            "buyer_impact": "Possible engine wear", "specific_vehicle_evidence": "",
            "verification_action": "Check oil level and exhaust smoke",
            "estimated_cost_eur_low": None, "estimated_cost_eur_high": None,
            "confidence": "MEDIUM", "source_ids": ["engine", "gearbox"],
        }]
        packet["sources_used"] = [
            {
                "source_id": "engine", "source_name": "EA888 technical article",
                "source_type": "TECHNICAL_PUBLICATION", "reliability": "MEDIUM",
                "source_url": "https://example.test/ea888", "verified_url": True,
                "used_for": "EA888 piston ring wear",
            },
            {
                "source_id": "gearbox", "source_name": "DQ500 workshop article",
                "source_type": "REPAIR_SOURCE", "reliability": "MEDIUM",
                "source_url": "https://example.test/dq500", "verified_url": True,
                "used_for": "DQ500 oil filter change",
            },
        ]

        filtered = _enforce_research_source_policy(
            _normalize_research_model_output(packet)
        )

        self.assertEqual(filtered["technical_risks"][0]["source_ids"], ["engine"])

    def test_source_policy_verifies_urls_against_grounded_input_not_model_boolean(self):
        packet = _unavailable_research_model_output("fixture")
        packet["evidence_summary"]["data_completeness_score"] = 60
        packet["technical_risks"] = [{
            "component": "EA888", "issue": "Excessive oil consumption",
            "risk_level": "HIGH", "evidence_category": "MODEL_LEVEL_RISK",
            "buyer_impact": "Possible engine wear", "specific_vehicle_evidence": "",
            "verification_action": "Check oil level and exhaust smoke",
            "estimated_cost_eur_low": None, "estimated_cost_eur_high": None,
            "confidence": "MEDIUM", "source_ids": ["grounded", "invented"],
        }]
        packet["sources_used"] = [
            {
                "source_id": "grounded", "source_name": "EA888 article",
                "source_type": "TECHNICAL_PUBLICATION", "reliability": "MEDIUM",
                "source_url": "https://example.test/ea888/", "verified_url": False,
                "used_for": "EA888 piston ring wear and oil consumption",
            },
            {
                "source_id": "invented", "source_name": "Invented article",
                "source_type": "TECHNICAL_PUBLICATION", "reliability": "MEDIUM",
                "source_url": "https://example.test/invented", "verified_url": True,
                "used_for": "EA888 oil consumption",
            },
        ]
        diagnostics = {}

        filtered = _enforce_research_source_policy(
            _normalize_research_model_output(packet),
            verified_source_urls={"https://example.test/ea888"},
            diagnostics=diagnostics,
        )

        self.assertEqual(filtered["technical_risks"][0]["source_ids"], ["grounded"])
        self.assertEqual(diagnostics["backend_verified_source_count"], 1)
        self.assertEqual(diagnostics["source_rejection_counts"]["unverified_url"], 1)

    def test_source_policy_restores_registered_url_but_rejects_unknown_registry_id(self):
        packet = _unavailable_research_model_output("fixture")
        packet["evidence_summary"]["data_completeness_score"] = 60
        packet["technical_risks"] = [{
            "component": "EA888", "issue": "Excessive oil consumption",
            "risk_level": "HIGH", "evidence_category": "MODEL_LEVEL_RISK",
            "buyer_impact": "Possible engine wear", "specific_vehicle_evidence": "",
            "verification_action": "Check oil level and exhaust smoke",
            "estimated_cost_eur_low": None, "estimated_cost_eur_high": None,
            "confidence": "MEDIUM", "source_ids": ["gsrc_001", "gsrc_999"],
        }]
        packet["sources_used"] = [
            {
                "source_id": "gsrc_001", "source_name": "EA888 article",
                "source_type": "TECHNICAL_PUBLICATION", "reliability": "MEDIUM",
                "source_url": "https://model-rewrote.test/wrong-path",
                "verified_url": False,
                "used_for": "EA888 piston ring wear and oil consumption",
            },
            {
                "source_id": "gsrc_999", "source_name": "Invented article",
                "source_type": "TECHNICAL_PUBLICATION", "reliability": "MEDIUM",
                "source_url": "https://invented.test/ea888",
                "verified_url": True,
                "used_for": "EA888 oil consumption",
            },
        ]
        grounded_url = "https://workshop.test/ea888-oil-consumption"
        diagnostics = {}

        filtered = _enforce_research_source_policy(
            _normalize_research_model_output(packet),
            verified_source_urls={grounded_url},
            verified_source_registry={"gsrc_001": grounded_url},
            diagnostics=diagnostics,
        )

        self.assertEqual(filtered["technical_risks"][0]["source_ids"], ["gsrc_001"])
        self.assertEqual(filtered["sources_used"][0]["source_url"], grounded_url)
        self.assertEqual(diagnostics["registry_resolved_source_count"], 1)
        self.assertEqual(diagnostics["unknown_registry_id_count"], 1)
        self.assertEqual(diagnostics["backend_verified_source_count"], 1)
        self.assertEqual(
            diagnostics["rejected_url_samples"],
            [{"source_id": "gsrc_999", "source_url": "https://invented.test/ea888"}],
        )

    def test_source_policy_keeps_grounded_other_sources_at_low_confidence(self):
        packet = _unavailable_research_model_output("fixture")
        packet["evidence_summary"]["data_completeness_score"] = 60
        packet["technical_risks"] = [{
            "component": "DQ500", "issue": "Mechatronic faults",
            "risk_level": "CHECK", "evidence_category": "MODEL_LEVEL_RISK",
            "buyer_impact": "Possible shifting problems", "specific_vehicle_evidence": "",
            "verification_action": "Run diagnostics and a road test",
            "estimated_cost_eur_low": None, "estimated_cost_eur_high": None,
            "confidence": "HIGH", "source_ids": ["secondary"],
        }]
        packet["sources_used"] = [{
            "source_id": "secondary", "source_name": "Workshop article",
            "source_type": "OTHER", "reliability": "MEDIUM",
            "source_url": "https://workshop.test/dq500", "verified_url": True,
            "used_for": "DQ500 mechatronic faults and diagnostics",
        }]

        filtered = _enforce_research_source_policy(
            _normalize_research_model_output(packet),
            verified_source_urls={"https://workshop.test/dq500"},
        )

        self.assertEqual(len(filtered["technical_risks"]), 1)
        self.assertEqual(filtered["technical_risks"][0]["confidence"], "Nizka")

    def test_source_policy_redacts_unofficial_interval_but_keeps_supported_cost(self):
        packet = _unavailable_research_model_output("fixture")
        packet["evidence_summary"]["data_completeness_score"] = 60
        packet["expected_costs"] = [{
            "item": "DQ500 oil and filter service every 60000 km",
            "why": "Preventive service is due every 60000 km when history is unknown.",
            "estimated_cost_eur_low": 185,
            "estimated_cost_eur_high": 228,
            "cost_type": "initial_service",
            "urgency": "medium",
            "basis": "Workshop price for DQ500 oil service at 60000 km",
            "source_ids": ["workshop"],
        }]
        packet["sources_used"] = [{
            "source_id": "workshop", "source_name": "DQ500 workshop",
            "source_type": "REPAIR_SOURCE", "reliability": "MEDIUM",
            "source_url": "https://workshop.test/dq500-service", "verified_url": True,
            "used_for": "DQ500 oil change cost 185-228 EUR and filter service",
        }]
        diagnostics = {}

        filtered = _enforce_research_source_policy(
            _normalize_research_model_output(packet),
            verified_source_urls={"https://workshop.test/dq500-service"},
            diagnostics=diagnostics,
        )

        self.assertEqual(len(filtered["expected_costs"]), 1)
        self.assertFalse(_contains_fixed_service_interval(filtered["expected_costs"][0]["item"]))
        self.assertEqual(diagnostics["source_rejection_counts"]["redacted_fixed_interval"], 1)

    def test_source_policy_requires_exact_price_evidence_for_numeric_costs(self):
        packet = _unavailable_research_model_output("fixture")
        packet["evidence_summary"]["data_completeness_score"] = 60
        packet["technical_risks"] = [{
            "component": "DQ500",
            "issue": "Mechatronic failure",
            "risk_level": "HIGH",
            "evidence_category": "MODEL_LEVEL_RISK",
            "buyer_impact": "Possible shifting failure",
            "specific_vehicle_evidence": "",
            "verification_action": "Run diagnostics",
            "estimated_cost_eur_low": 1000,
            "estimated_cost_eur_high": 2000,
            "confidence": "Stredna",
            "source_ids": ["article"],
        }]
        packet["expected_costs"] = [{
            "item": "DQ500 oil service",
            "why": "Preventive maintenance",
            "estimated_cost_eur_low": 250,
            "estimated_cost_eur_high": 400,
            "cost_type": "initial_service",
            "urgency": "high",
            "basis": "Workshop estimate",
            "source_ids": ["article"],
        }]
        packet["sources_used"] = [{
            "source_id": "article",
            "source_name": "DQ500 reliability article",
            "source_type": "TECHNICAL_PUBLICATION",
            "reliability": "MEDIUM",
            "source_url": "https://example.test/dq500",
            "verified_url": True,
            "used_for": "DQ500 mechatronic failure and oil service interval",
        }]
        diagnostics = {}

        filtered = _enforce_research_source_policy(
            _normalize_research_model_output(packet),
            verified_source_urls={"https://example.test/dq500"},
            diagnostics=diagnostics,
        )

        self.assertEqual(len(filtered["technical_risks"]), 1)
        self.assertIsNone(filtered["technical_risks"][0]["estimated_cost_eur_low"])
        self.assertIsNone(filtered["technical_risks"][0]["estimated_cost_eur_high"])
        self.assertEqual(filtered["expected_costs"], [])
        self.assertEqual(
            diagnostics["source_rejection_counts"]["cost_without_price_source"],
            2,
        )

    def test_supported_risk_fills_missing_sections_without_inventing_price(self):
        packet = _unavailable_research_model_output("fixture")
        packet["evidence_summary"]["data_completeness_score"] = 55
        packet["technical_risks"] = [{
            "component": "EA888",
            "issue": "Nadmerná spotreba oleja",
            "risk_level": "HIGH",
            "evidence_category": "MODEL_LEVEL_RISK",
            "buyer_impact": "Môže viesť k drahej oprave motora.",
            "specific_vehicle_evidence": "",
            "verification_action": "Zmerať spotrebu oleja a skontrolovať dymivosť.",
            "estimated_cost_eur_low": None,
            "estimated_cost_eur_high": None,
            "confidence": "Stredna",
            "source_ids": ["gsrc_001"],
        }]
        packet["sources_used"] = [{
            "source_id": "gsrc_001",
            "source_name": "EA888 technical article",
            "source_type": "TECHNICAL_PUBLICATION",
            "reliability": "MEDIUM",
            "source_url": "https://workshop.test/ea888-oil-consumption",
            "verified_url": True,
            "used_for": "EA888 piston-ring wear and oil consumption",
        }]
        diagnostics = {}

        completed = _complete_supported_research_sections(
            packet,
            output_language="sk",
            diagnostics=diagnostics,
        )

        self.assertTrue(_valid_research_model_output(completed))
        self.assertEqual(len(completed["web_research_findings"]), 1)
        self.assertEqual(len(completed["technical_risks"]), 1)
        self.assertEqual(len(completed["expected_costs"]), 1)
        self.assertIsNone(completed["expected_costs"][0]["estimated_cost_eur_low"])
        self.assertIsNone(completed["expected_costs"][0]["estimated_cost_eur_high"])
        self.assertEqual(completed["expected_costs"][0]["source_ids"], ["gsrc_001"])
        self.assertEqual(
            diagnostics["generated_sections"],
            ["web_research_findings", "expected_costs"],
        )

    def test_supported_finding_can_fill_risk_and_non_numeric_diagnostic(self):
        packet = _unavailable_research_model_output("fixture")
        packet["evidence_summary"]["data_completeness_score"] = 45
        packet["web_research_findings"] = [{
            "claim": "Haldex pump screen can become restricted.",
            "evidence_category": "MODEL_LEVEL_RISK",
            "buyer_impact": "AWD operation can be reduced.",
            "confidence": "Stredna",
            "source_ids": ["gsrc_002"],
        }]
        packet["sources_used"] = [{
            "source_id": "gsrc_002",
            "source_name": "Haldex workshop article",
            "source_type": "REPAIR_SOURCE",
            "reliability": "MEDIUM",
            "source_url": "https://workshop.test/haldex-pump",
            "verified_url": True,
            "used_for": "Haldex pump screen restriction and diagnostics",
        }]

        completed = _complete_supported_research_sections(packet, output_language="en")

        self.assertTrue(_valid_research_model_output(completed))
        self.assertEqual(completed["technical_risks"][0]["risk_level"], "CHECK")
        self.assertEqual(completed["expected_costs"][0]["cost_type"], "diagnostic")
        self.assertIn("workshop must confirm", completed["expected_costs"][0]["basis"])

    def test_section_completion_does_not_mask_invalid_or_unverified_research(self):
        invalid = _unavailable_research_model_output("fixture")
        invalid["technical_risks"] = [{"issue": "Malformed"}]
        invalid_diagnostics = {}

        self.assertIs(
            _complete_supported_research_sections(
                invalid,
                diagnostics=invalid_diagnostics,
            ),
            invalid,
        )
        self.assertEqual(invalid_diagnostics["reason"], "invalid_contract")

        unsupported = _unavailable_research_model_output("fixture")
        completed = _complete_supported_research_sections(unsupported)
        self.assertEqual(completed["web_research_findings"], [])
        self.assertEqual(completed["technical_risks"], [])
        self.assertEqual(completed["expected_costs"], [])

    def test_report_lock_removes_unofficial_interval_and_invented_cost_range(self):
        report = (
            "# Report\n\n## 🔧 Technické riziká modelu a komponentov\n\n"
            "- **Kedy sa prejavuje:** Servis je každých 60 000 km.\n"
            "- **Odhadovaný náklad:** 800 – 2 500 EUR.\n\n"
            "## 🛠️ Očakávané náklady na najbližších 30 000 km\n\n"
            "| Položka | Odhad EUR |\n|---|---:|\n| DSG servis | 185 – 228 |\n\n"
            "## Otázky pre predajcu\n\n"
            "1. Bol olej v DSG menený každých 60 000 km?\n"
        )
        research = {
            "web_research_findings": [{"claim": "DQ500 needs regular oil service"}],
            "technical_risks": [{
                "issue": "Mechatronic failure",
                "estimated_cost_eur_low": None,
                "estimated_cost_eur_high": None,
                "source_ids": ["workshop"],
            }],
            "expected_costs": [{
                "item": "DQ500 oil service",
                "estimated_cost_eur_low": 185,
                "estimated_cost_eur_high": 228,
                "source_ids": ["workshop"],
            }],
            "sources_used": [{
                "source_id": "workshop", "source_type": "REPAIR_SOURCE",
                "verified_url": True,
            }],
            "listing_facts": {},
            "vin_check": {},
            "market_assessment": {"benchmark_available": False},
            "market_comparables": [],
        }

        locked = _lock_report_evidence_claims(report, research, {}, output_language="sk")

        self.assertNotIn("60 000 km", locked)
        self.assertIn("podľa servisného plánu výrobcu", locked)
        self.assertNotIn("800 – 2 500", locked)
        self.assertIn("185 – 228", locked)

    def test_interval_redaction_does_not_leave_repeated_broken_phrases(self):
        redacted = _redact_fixed_service_interval(
            "Vozidlo má 107 296 km, čo je blízko odporúčaného "
            "servisného intervalu 150 000 km."
        )

        self.assertEqual(
            redacted,
            "Konkrétny interval a aktuálnu potrebu úkonu overte podľa "
            "servisnej histórie a servisného plánu výrobcu.",
        )
        self.assertNotIn("107 296", redacted)
        self.assertNotIn("150 000", redacted)

    def test_interval_redaction_catches_unofficial_inspection_mileage(self):
        original = "Potreba kontroly a vymedzenia ventilovej vôle po 100 000 km."

        self.assertTrue(_contains_fixed_service_interval(original))
        redacted = _redact_fixed_service_interval(original)
        self.assertNotIn("100 000", redacted)
        self.assertIn("servisného plánu výrobcu", redacted)

    def test_source_policy_keeps_risk_after_redacting_unofficial_interval(self):
        packet = _unavailable_research_model_output("fixture")
        packet["technical_risks"] = [{
            "component": "Valve train",
            "issue": "Kontrola ventilovej vôle po 100 000 km.",
            "risk_level": "MEDIUM",
            "evidence_category": "MODEL_LEVEL_MAINTENANCE",
            "buyer_impact": "Pri zanedbaní môže vzniknúť hlučnosť.",
            "specific_vehicle_evidence": "",
            "verification_action": "Overiť servisné záznamy.",
            "estimated_cost_eur_low": None,
            "estimated_cost_eur_high": None,
            "confidence": "MEDIUM",
            "source_ids": ["article"],
        }]
        packet["sources_used"] = [{
            "source_id": "article",
            "source_name": "Valve train technical article",
            "source_type": "TECHNICAL_PUBLICATION",
            "reliability": "MEDIUM",
            "source_url": "https://example.test/valve-train",
            "verified_url": True,
            "used_for": "Valve train clearance inspection and noise",
        }]
        diagnostics = {}

        filtered = _enforce_research_source_policy(
            _normalize_research_model_output(packet),
            verified_source_urls={"https://example.test/valve-train"},
            diagnostics=diagnostics,
        )

        self.assertEqual(len(filtered["technical_risks"]), 1)
        self.assertNotIn("100 000", filtered["technical_risks"][0]["issue"])
        self.assertEqual(
            diagnostics["source_rejection_counts"]["redacted_fixed_interval"],
            1,
        )

    def test_limited_fallback_removes_exact_service_interval_consistency_check(self):
        packet = _unavailable_research_model_output("fixture")
        packet["consistency_checks"] = [{
            "check": "Servisné intervaly a najazdené kilometre",
            "result": "ok",
            "explanation": "Prehliadka je každých 20 000 km alebo 1 rok.",
        }]

        limited = _limited_research_model_output(packet, output_language="sk")

        self.assertEqual(limited["consistency_checks"], [])

    def test_report_lock_keeps_sources_internal_and_attributes_service_claim(self):
        report = (
            "# Report\n\n## 🌐 Webové overenie\n\n- Modelové riziko.\n\n"
            "## 🔧 Technické riziká modelu a komponentov\n\n- Riziko.\n\n"
            "## 🛠️ Očakávané náklady na najbližších 30 000 km\n\n- Náklad.\n\n"
            "## ✅ Klady\n\n"
            "- **Pravidelný servis:** Záznamy v servisnej knižke potvrdzujú pravidelnú údržbu DSG.\n"
        )
        research = {
            "seller_claims": [{
                "claim": "Úplná servisná história a servisná knižka.",
                "verification_status": "UNVERIFIED",
            }],
            "web_research_findings": [{
                "claim": "Modelové riziko",
                "buyer_impact": "Preveriť vozidlo",
                "source_ids": ["source"],
            }],
            "technical_risks": [{
                "component": "Motor",
                "issue": "Modelové riziko",
                "verification_action": "Diagnostika",
                "source_ids": ["source"],
            }],
            "expected_costs": [{
                "item": "Diagnostika",
                "why": "Vstupná kontrola",
                "estimated_cost_eur_low": 30,
                "estimated_cost_eur_high": 80,
                "source_ids": ["source"],
            }],
            "sources_used": [{
                "source_id": "source",
                "source_name": "Workshop",
                "source_url": "https://workshop.test/source",
                "verified_url": True,
            }],
            "listing_facts": {},
            "vin_check": {},
            "market_assessment": {"benchmark_available": False},
            "market_comparables": [],
        }

        locked = _lock_report_evidence_claims(report, research, {}, output_language="sk")

        self.assertNotIn("https://", locked)
        self.assertNotIn("potvrdzujú pravidelnú údržbu", locked)
        self.assertIn("Predajca uvádza servisnú knižku", locked)

    def test_report_lock_preserves_first_check_and_limits_visual_claims(self):
        report = (
            "# Report\n\n## Rýchle zhrnutie\n\n"
            "- **Čo overiť ako prvé:** Vyžiadajte si VIN a preverte, či boli pravidelne menené oleje v DSG a Haldexe.\n\n"
            "## 📸 Analýza fotografií\n\n"
            "- **Foto 01-10:** Panelové medzery sú konzistentné, čo naznačuje, že vozidlo nebolo vážne poškodené. Naznačuje dobrú integritu karosérie.\n\n"
            "## ✅ Klady\n\n"
            "- **Nové pneumatiky:** Viditeľný dezén potvrdzuje tvrdenie o nových pneumatikách.\n"
        )
        research = {
            "seller_claims": [
                {
                    "claim": "Úplná servisná história a pravidelný servis DSG a Haldex.",
                    "verification_status": "UNVERIFIED",
                },
                {
                    "claim": "Nové pneumatiky Continental.",
                    "verification_status": "UNVERIFIED",
                },
            ],
            "listing_facts": {"vin": ""},
            "vin_check": {"vin_present": False},
            "market_assessment": {"benchmark_available": False},
            "market_comparables": [],
            "web_research_findings": [],
            "technical_risks": [],
            "expected_costs": [],
            "sources_used": [],
        }

        locked = _lock_report_evidence_claims(report, research, {}, output_language="sk")

        self.assertIn("**Čo overiť ako prvé:**", locked)
        self.assertIn("konkrétne úkony na DSG a Haldexe overte podľa dokumentácie", locked)
        self.assertNotIn("nebolo vážne poškodené", locked)
        self.assertNotIn("dobrú integritu karosérie", locked)
        self.assertIn("fotografie však nepotvrdzujú nehodovú ani opravárenskú históriu", locked.lower())
        self.assertNotIn("potvrdzuje tvrdenie o nových pneumatikách", locked)
        self.assertIn("DOT a merania dezénu", locked)

    def test_report_lock_removes_unsupported_table_costs_and_technical_quantities(self):
        report = (
            "# Report\n\n## Web verification\n\n"
            "- Oil consumption is documented. It can exceed 1 litre per 1 000 km.\n"
            "- The issue often appears above 150 000 km.\n\n"
            "- The critical period is between 120 000 and 180 000 km.\n\n"
            "## Technical risks\n\n### Invented Haldex failure\n"
            "- **Estimated cost:** 800 - 2 500 EUR.\n\n"
            "## Expected costs over the next 30 000 km\n\n"
            "| Item | Estimated EUR |\n|---|---:|\n"
            "| Supported service | 185 - 228 |\n"
            "| Invented repair | 1 500 - 2 500 |\n"
        )
        research = {
            "web_research_findings": [{"claim": "Oil consumption is documented"}],
            "technical_risks": [{
                "issue": "Mechatronic failure",
                "estimated_cost_eur_low": None,
                "estimated_cost_eur_high": None,
            }],
            "expected_costs": [{
                "item": "Supported service",
                "estimated_cost_eur_low": 185,
                "estimated_cost_eur_high": 228,
            }],
            "sources_used": [],
            "listing_facts": {},
            "market_assessment": {"benchmark_available": False},
            "market_comparables": [],
        }

        locked = _lock_report_evidence_claims(report, research, {}, output_language="en")

        self.assertIn("Oil consumption is documented", locked)
        self.assertNotIn("1 litre per 1 000 km", locked)
        self.assertNotIn("150 000 km", locked)
        self.assertNotIn("120 000", locked)
        self.assertNotIn("180 000 km", locked)
        self.assertIn("185 – 228", locked)
        self.assertNotIn("1 500 - 2 500", locked)
        self.assertNotIn("800 - 2 500", locked)
        self.assertNotIn("Invented Haldex failure", locked)

    def test_incomplete_research_delivery_gate_caps_green_verdict(self):
        risk_score = {
            "decision_status": "WORTH_INSPECTING",
            "allowed_final_verdict": "🟢 STOJÍ ZA OBHLIADKU",
        }

        gate = _apply_research_delivery_gate(
            risk_score,
            {
                "research_status": "limited",
                "web_research_findings": [],
                "technical_risks": [],
                "expected_costs": [],
            },
            output_language="sk",
        )

        self.assertEqual(gate["status"], "INCOMPLETE")
        self.assertTrue(gate["verdict_capped"])
        self.assertEqual(risk_score["decision_status"], "INSPECT_WITH_RESERVATIONS")
        self.assertIn("NAJPRV PREVERIŤ", risk_score["allowed_final_verdict"])

    def test_empty_final_recommendation_gets_safe_delivery_explanation(self):
        report = (
            "# Report\n\n## 🏁 Záverečné odporúčanie\n\n"
            "**🟡 NAJPRV PREVERIŤ**\n\n<!-- END_ANALYSIS -->\n"
        )
        risk = {
            "allowed_final_verdict": "🟡 NAJPRV PREVERIŤ",
            "research_delivery_gate": {"status": "INCOMPLETE"},
        }

        repaired = _ensure_final_recommendation_body(report, risk, output_language="sk")

        self.assertIn("Technické webové overenie nebolo dokončené", repaired)
        self.assertIn("analýzu zopakujte", repaired)
        self.assertIn("<!-- END_ANALYSIS -->", repaired)

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

    def test_vision_policy_downgrades_authenticity_function_and_tread_claims(self):
        payload = {
            "view_coverage": {"underbody": "missing", "engine_bay": "visible_detail"},
            "missing_views": [],
            "supported_observations": [
                {"observation": "Výrobný štítok je originálny a nepoškodený.", "confidence": "HIGH", "evidence_category": "CONFIRMED"},
                {"observation": "Displej zobrazuje plne funkčný 360-stupňový kamerový systém.", "confidence": "HIGH"},
                {"observation": "Na displeji je viditeľná funkčná parkovacia kamera.", "confidence": "HIGH"},
                {"observation": "Pneumatiky majú dostatočný dezén.", "confidence": "HIGH"},
            ]
        }

        sanitized, counts = _sanitize_vision_claims(payload, output_language="sk")

        observations = sanitized["supported_observations"]
        self.assertIn("pravosť nemožno potvrdiť", observations[0]["observation"])
        self.assertEqual(observations[0]["evidence_category"], "VISUAL_INDICATION")
        self.assertIn("treba vyskúšať", observations[1]["observation"])
        self.assertIn("treba vyskúšať", observations[2]["observation"])
        self.assertIn("bez merania", observations[3]["observation"])
        self.assertEqual(sanitized["missing_views"], ["Podvozok"])
        self.assertEqual(counts, {
            "plate_authenticity": 1,
            "system_functionality": 2,
            "tread_depth": 1,
            "odometer_conflict": 0,
            "odometer_unverified": 0,
            "repair_history": 0,
            "enum_normalization": 0,
            "service_document": 0,
        })

    def test_vision_policy_normalizes_known_nested_enum_aliases(self):
        payload = {
            "exterior_observations": [{
                "observation": "Vozidlo má ťažné zariadenie.",
                "buyer_impact": "equipment",
            }],
            "mileage_wear_consistency": {
                "assessment": "better_than_expected",
                "explanation": "Opotrebovanie je nižšie, než sa očakávalo.",
                "confidence": "HIGH",
            },
        }

        sanitized, counts = _sanitize_vision_claims(payload, output_language="sk")

        self.assertEqual(sanitized["exterior_observations"][0]["buyer_impact"], "value")
        self.assertEqual(sanitized["mileage_wear_consistency"]["assessment"], "consistent")
        self.assertEqual(sanitized["mileage_wear_consistency"]["confidence"], "Vysoká")
        self.assertEqual(counts["enum_normalization"], 3)

    def test_vision_policy_limits_service_book_and_deduplicates_missing_views(self):
        payload = {
            "view_coverage": {"engine_bay": "missing", "underbody": "missing"},
            "missing_views": ["Motorový priestor", "Podvozok vozidla", "Podvozok"],
            "supported_observations": [{
                "type": "documents",
                "observation": "Zobrazená servisná knižka s pečiatkami autorizovaného servisu Audi.",
                "evidence_category": "CONFIRMED",
                "confidence": "HIGH",
            }],
            "mileage_wear_consistency": {
                "assessment": "consistent",
                "explanation": "Záznamy v servisnej knižke chronologicky podporujú deklarovaný nájazd.",
                "confidence": "Vysoká",
            },
        }

        sanitized, counts = _sanitize_vision_claims(payload, output_language="sk")

        document = sanitized["supported_observations"][0]
        self.assertEqual(document["evidence_category"], "VISUAL_INDICATION")
        self.assertEqual(document["confidence"], "MEDIUM")
        self.assertIn("overiť podľa VIN", document["observation"])
        self.assertEqual(sanitized["missing_views"], ["Motorový priestor", "Podvozok vozidla"])
        self.assertIn("overiť nezávisle", sanitized["mileage_wear_consistency"]["explanation"])
        self.assertEqual(counts["service_document"], 2)

    def test_vision_policy_does_not_publish_unverified_photo_only_odometer(self):
        payload = {
            "odometer": {
                "visible": True,
                "reading_km": 195178,
                "confidence": "HIGH",
                "notes": "Čitateľné.",
            },
            "supported_observations": [{
                "type": "odometer",
                "observation": "Počítadlo ukazuje 195 178 km.",
                "evidence_category": "CONFIRMED",
                "confidence": "HIGH",
            }],
            "exterior_observations": [{
                "observation": "Ťažné zariadenie vyzerá funkčne.",
                "confidence": "HIGH",
            }],
            "mileage_wear_consistency": {
                "assessment": "consistent",
                "explanation": "Opotrebovanie zodpovedá odpočtu.",
                "confidence": "Vysoká",
            },
        }

        sanitized, counts = _sanitize_vision_claims(payload, output_language="sk")

        self.assertIsNone(sanitized["odometer"]["reading_km"])
        self.assertEqual(sanitized["supported_observations"][0]["confidence"], "LOW")
        self.assertEqual(sanitized["mileage_wear_consistency"]["assessment"], "cannot_assess")
        self.assertIn("homologizáciu", sanitized["exterior_observations"][0]["observation"])
        self.assertEqual(counts["odometer_unverified"], 1)
        self.assertEqual(counts["system_functionality"], 1)

    def test_vision_policy_downgrades_odometer_conflict_and_repair_history(self):
        payload = {
            "odometer": {
                "visible": True,
                "reading_km": 284,
                "confidence": "HIGH",
                "notes": "Jasne viditeľné.",
            },
            "supported_observations": [{
                "type": "odometer",
                "observation": "Digitálny odpočet ukazuje 284 km.",
                "evidence_category": "CONFIRMED",
                "confidence": "HIGH",
            }],
            "exterior_observations": [{
                "observation": "Panely majú konzistentné medzery.",
                "buyer_relevance": "Žiadne známky predchádzajúceho poškodenia alebo opravy karosérie.",
            }],
            "mileage_wear_consistency": {
                "assessment": "consistent",
                "explanation": "Opotrebovanie zodpovedá 284 km.",
                "confidence": "Vysoká",
            },
        }

        sanitized, counts = _sanitize_vision_claims(
            payload,
            output_language="sk",
            listing_mileage_km=284000,
        )

        self.assertIsNone(sanitized["odometer"]["reading_km"])
        self.assertEqual(sanitized["mileage_wear_consistency"]["assessment"], "cannot_assess")
        self.assertEqual(sanitized["supported_observations"][0]["confidence"], "LOW")
        self.assertIn("históriu", sanitized["exterior_observations"][0]["buyer_relevance"])
        self.assertEqual(counts["odometer_conflict"], 1)
        self.assertEqual(counts["repair_history"], 1)

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

    def test_report_replaces_unsupported_technical_sections_and_fixed_intervals(self):
        report = """# Report

## Webové overenie
- EA888 oil consumption and piston rings.

## Technické riziká modelu a komponentov
- Timing chain tensioner failure.

## Očakávané náklady
- Timing chain replacement: 1 000 EUR.

## Otázky
- DSG oil must be changed every 60000 km.
"""
        locked = _lock_report_evidence_claims(
            report,
            {
                "research_status": "limited",
                "web_research_findings": [],
                "technical_risks": [],
                "expected_costs": [],
                "market_assessment": {
                    "benchmark_available": True,
                    "benchmark_comparable_count": 3,
                    "benchmark_median_eur": 10000,
                },
            },
            {},
            output_language="sk",
        )

        self.assertIn("Backendová validácia nepotvrdila", locked)
        self.assertIn("Žiadne modelové technické riziko", locked)
        self.assertIn("Nie je dostupný dostatočne podložený odhad", locked)
        self.assertNotIn("Timing chain", locked)

    def test_report_repairs_ordered_list_gaps_after_evidence_filtering(self):
        report = """# Report

## OtÃ¡zky
1. Request the VIN.
2. Change DSG oil every 60000 km.
3. Test drive the car.
"""
        locked = _lock_report_evidence_claims(
            report,
            {
                "web_research_findings": [],
                "technical_risks": [],
                "expected_costs": [],
                "market_assessment": {
                    "benchmark_available": True,
                    "benchmark_comparable_count": 3,
                    "benchmark_median_eur": 10000,
                },
            },
            {},
            output_language="sk",
        )

        self.assertIn("1. Request the VIN.", locked)
        self.assertIn("2. Change DSG oil according to the manufacturer service schedule.", locked)
        self.assertIn("3. Test drive the car.", locked)
        self.assertNotIn("60000", locked)

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

    def test_missing_grounded_sources_stops_before_downstream_synthesis(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repository = ListingJobRepository(root / "jobs")
            repository.job_dir("sample", create=True)
            repository.write_text("sample", "car_info.md", "# Sample listing")
            phases = []

            def collect_gemini(entries, phase, factory, **kwargs):
                yield from ()
                phases.append(phase)
                if phase == "component identity research":
                    return (
                        '{"schema_version":1,"identification_status":"UNKNOWN",'
                        '"generation":{"resolution":"UNKNOWN"},'
                        '"engine":{"resolution":"UNKNOWN"},'
                        '"transmission":{"resolution":"UNKNOWN"},'
                        '"drivetrain":{"resolution":"UNKNOWN"},'
                        '"candidate_variants":[],"sources":[],"notes":[]}',
                        entries[0],
                    )
                if phase == "web research":
                    raise RateLimitError("quota")
                raise AssertionError(f"Unexpected collected phase: {phase}")

            dependencies = replace(
                _dependencies(repository, root / "prompts"),
                normalize_gemini_keys=lambda keys: [{"key": keys[0], "label": "primary"}],
                collect_gemini=collect_gemini,
                direct_market_search=lambda listing: (_ for _ in ()).throw(
                    AssertionError("market search must not run")
                ),
            )

            events = list(
                multi_model_analysis_events(
                    "sample",
                    "",
                    ["gemini-key"],
                    dependencies=dependencies,
                )
            )
            diagnostics = repository.read_json("sample", "analysis_diagnostics.json")

        self.assertEqual(phases, ["component identity research", "web research"])
        self.assertTrue(any("analysis cannot continue" in event for event in events))
        self.assertTrue(any("customer credit was not charged" in event for event in events))
        self.assertEqual(diagnostics["delivery"]["status"], "RETRY_REQUIRED")
        self.assertFalse(diagnostics["delivery"]["chargeable"])
        self.assertEqual(diagnostics["phases"]["final_synthesis"]["status"], "skipped")

    def test_identity_grounding_exhaustion_stops_before_reliability_grounding(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repository = ListingJobRepository(root / "jobs")
            repository.job_dir("sample", create=True)
            repository.write_text("sample", "car_info.md", "# Sample listing")
            phases = []

            def collect_gemini(entries, phase, factory, **kwargs):
                yield from ()
                phases.append(phase)
                raise RateLimitError("all identity models and keys exhausted")

            dependencies = replace(
                _dependencies(repository, root / "prompts"),
                normalize_gemini_keys=lambda keys: [{"key": keys[0], "label": "primary"}],
                collect_gemini=collect_gemini,
            )

            events = list(
                multi_model_analysis_events(
                    "sample",
                    "",
                    ["gemini-key"],
                    dependencies=dependencies,
                )
            )
            diagnostics = repository.read_json("sample", "analysis_diagnostics.json")

        self.assertEqual(phases, ["component identity research"])
        self.assertTrue(any("customer credit was not charged" in event for event in events))
        self.assertEqual(
            diagnostics["delivery"]["reason"],
            "component_identity_grounding_unavailable",
        )
        self.assertEqual(diagnostics["phases"]["grounded_research"]["status"], "skipped")

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
                    return (
                        "### Technical source\n"
                        "- [Workshop](https://workshop.test/sample) - supported inspection point.",
                        entries[0],
                    )
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
                            "safety_and_recall": {
                                "status": "INSUFFICIENT_DATA",
                                "summary": "No supported recall conclusion.",
                                "required_action": "Verify with VIN.",
                                "evidence_category": "NEEDS_VERIFICATION",
                                "source_ids": [],
                            },
                            "web_research_findings": [{
                                "claim": "Sample issue",
                                "evidence_category": "MODEL_LEVEL_RISK",
                                "buyer_impact": "Inspect before purchase",
                                "confidence": "Stredna",
                                "source_ids": ["src_sample"],
                            }],
                            "technical_risks": [{
                                "component": "Sample component",
                                "issue": "Sample issue",
                                "risk_level": "CHECK",
                                "evidence_category": "MODEL_LEVEL_RISK",
                                "buyer_impact": "Possible wear",
                                "specific_vehicle_evidence": "",
                                "verification_action": "Inspect before purchase",
                                "estimated_cost_eur_low": None,
                                "estimated_cost_eur_high": None,
                                "confidence": "Stredna",
                                "source_ids": ["src_sample"],
                            }],
                            "expected_costs": [{
                                "item": "Sample service",
                                "why": "Baseline maintenance",
                                "estimated_cost_eur_low": 100,
                                "estimated_cost_eur_high": 150,
                                "cost_type": "initial_service",
                                "urgency": "medium",
                                "basis": "Published workshop price",
                                "source_ids": ["src_sample"],
                            }],
                            "text_research_risk_flags": [],
                            "sources_used": [{
                                "source_id": "src_sample",
                                "source_name": "Sample workshop",
                                "source_type": "REPAIR_SOURCE",
                                "reliability": "MEDIUM",
                                "source_url": "https://workshop.test/sample",
                                "verified_url": True,
                                "used_for": "Sample issue and sample service cost 100-150 EUR",
                            }],
                        }
                    ), entries[0]
                raise AssertionError(f"Unexpected collected phase: {phase}")

            final_calls = []

            def call_gemini(*args, **kwargs):
                final_calls.append(kwargs)
                if len(final_calls) == 1:
                    yield "# Partial report"
                    raise ModelOutputLimitError("MAX_TOKENS")
                yield "# Final recovered report"

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
                call_gemini=call_gemini,
                calculate_risk_score=lambda *args, **kwargs: {
                    "risk_score": 0,
                    "allowed_final_verdict": "ZVAZIT",
                },
                prepare_images=lambda slug_dir: ([], {
                    "coverage_mode": "detail_all",
                    "original_count": 20,
                    "unique_count": 18,
                    "duplicate_count": 2,
                    "selected_count": 18,
                    "attachment_count": 5,
                    "attachment_limit": 5,
                    "full_gallery_included": True,
                    "selection_reason": "all_unique_photos_in_detail_collages_after_perceptual_deduplication",
                }),
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
            self.assertEqual(text_diagnostics["research_status"], "completed")
            self.assertTrue(text_diagnostics["recovery_attempted"])
            self.assertTrue(text_diagnostics["recovered"])
            self.assertIn("policy", text_diagnostics)
            self.assertIn("input_budget", text_diagnostics)
            self.assertIn("recovery_input_budget", text_diagnostics)
            self.assertEqual(
                diagnostics["phases"]["vision"]["image_payload"]["duplicate_count"],
                2,
            )
            self.assertEqual(
                diagnostics["phases"]["vision"]["image_payload"]["attachment_count"],
                5,
            )
            self.assertEqual(
                text_diagnostics["contract_enforcement"]["attempt"],
                "recovery",
            )
            final_diagnostics = diagnostics["phases"]["final_synthesis"]
            self.assertTrue(final_diagnostics["recovery_attempted"])
            self.assertTrue(final_diagnostics["recovered"])
            self.assertEqual(len(final_calls), 2)
            self.assertEqual(final_calls[1]["temperature"], 0.1)
            self.assertNotIn(
                "Partial report",
                repository.read_text("sample", "analysis_result_raw.md"),
            )
            self.assertIn(
                "Final recovered report",
                repository.read_text("sample", "analysis_result_raw.md"),
            )
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
            self.assertEqual(
                repository.read_text("sample", "analysis_result_raw.md"),
                "# Final recovered report",
            )
            self.assertIn("<!-- END_ANALYSIS -->", repository.read_text("sample", "analysis_result.md"))
            self.assertIn('"done": true', events[-1])


if __name__ == "__main__":
    unittest.main()
