import json
import unittest
from pathlib import Path

from risk_scorer import calculate_risk_score
from risk_scorer_v2 import calculate_risk_score_v2


def _json(data):
    return json.dumps(data, ensure_ascii=False)


def _rules(result):
    return {item["rule"] for item in result["applied_rules"]}


def _overrides(result):
    return {item["rule"] for item in result["override_rules_applied"]}


class RiskScorerTest(unittest.TestCase):
    def test_missing_vin_is_verification_task_not_vin_risk_cap(self):
        result = calculate_risk_score(
            _json(
                {
                    "listing_facts": {
                        "year": "2016",
                        "mileage": "180 000 km",
                        "service_history": "",
                        "origin_or_country": "",
                        "seller": "",
                    },
                    "vin_check": {"vin_present": False, "format_check": "skipped"},
                    "market_assessment": {"price_view": "unclear"},
                    "consistency_checks": [],
                    "knowledge_base_findings": [],
                }
            ),
            _json({"photos_provided": False, "photo_limitations": [], "visual_verdict": "Nedostatocne fotografie"}),
        )

        self.assertIn("vin_missing_request_before_viewing", _rules(result))
        self.assertIn("missing_or_weak_photos", _rules(result))
        self.assertNotIn("invalid VIN + weak photos", _overrides(result))
        self.assertIn("no photos", _overrides(result))
        self.assertIn("VIN", result["missing_data_flags"])
        self.assertIn("photos", result["missing_data_flags"])

    def test_legacy_visual_severity_without_polarity_is_not_scored(self):
        result = calculate_risk_score(
            _json(
                {
                    "listing_facts": {
                        "vin": "JMBXNGA1WBZ019167",
                        "service_history": "full",
                        "origin_or_country": "SK",
                        "seller": "dealer",
                    },
                    "vin_check": {"vin_present": True, "format_check": "ok"},
                    "market_assessment": {"price_view": "fair"},
                    "consistency_checks": [],
                    "knowledge_base_findings": [],
                }
            ),
            _json(
                {
                    "photos_provided": True,
                    "photo_limitations": [],
                    "visual_verdict": "Viditelne rizika",
                    "exterior_observations": [{"severity": "serious"}],
                    "visible_red_flags": [],
                }
            ),
        )

        self.assertNotIn("visible_serious_damage_or_red_flags", _rules(result))
        self.assertNotIn("serious visible red flags", _overrides(result))

    def test_overview_only_photo_limitation_is_not_weak_photos(self):
        result = calculate_risk_score(
            _json(
                {
                    "listing_facts": {
                        "vin": "JMBXNGA1WBZ019167",
                        "year": "2022",
                        "mileage": "40 000 km",
                        "service_history": "full",
                        "origin_or_country": "SK",
                        "seller": "dealer",
                    },
                    "vin_check": {"vin_present": True, "format_check": "ok"},
                    "market_assessment": {"price_view": "fair"},
                    "consistency_checks": [],
                    "knowledge_base_findings": [],
                }
            ),
            _json(
                {
                    "photos_provided": True,
                    "photo_coverage": {
                        "coverage_mode": "full_gallery_overview",
                        "original_count": 104,
                        "analyzed_count": 104,
                        "full_gallery_overview": True,
                    },
                    "view_coverage": {"engine_bay": "visible_overview_only"},
                    "photo_limitations": [
                        "Engine bay is visible only in overview; not assessable in detail."
                    ],
                    "visual_verdict": "VyzerÃ¡ vizuÃ¡lne dobre",
                }
            ),
        )

        self.assertNotIn("missing_or_weak_photos", _rules(result))
        self.assertNotIn("photos", result["missing_data_flags"])

    def test_unsourced_consistency_concern_is_an_action_not_a_penalty(self):
        result = calculate_risk_score(
            _json(
                {
                    "listing_facts": {
                        "vin": "JMBXNGA1WBZ019167",
                        "service_history": "full",
                        "origin_or_country": "SK",
                        "seller": "dealer",
                    },
                    "vin_check": {"vin_present": True, "format_check": "ok"},
                    "market_assessment": {"price_view": "fair"},
                    "consistency_checks": [{"result": "concern", "explanation": "Mileage conflict"}],
                    "knowledge_base_findings": [],
                }
            ),
            _json({"photos_provided": True, "photo_limitations": [], "visual_verdict": "Vyzerá vizuálne dobre"}),
        )

        self.assertNotIn("obvious_listing_conflict", _rules(result))
        self.assertNotIn("major listing contradiction", _overrides(result))
        self.assertEqual(result["decision_status"], "WORTH_INSPECTING")
        self.assertIn("listing_consistency", result["missing_data_flags"])

    def test_sourced_material_conflict_can_lower_verdict(self):
        result = calculate_risk_score(
            _json(
                {
                    "listing_facts": {
                        "vin": "JMBXNGA1WBZ019167",
                        "service_history": "full",
                        "origin_or_country": "SK",
                        "seller": "dealer",
                    },
                    "vin_check": {"vin_present": True, "format_check": "ok"},
                    "market_assessment": {"price_view": "fair"},
                    "consistency_checks": [],
                    "data_conflicts": [
                        {
                            "issue": "Listing mileage differs from photographed odometer",
                            "importance": "HIGH",
                            "source_a": "listing mileage field",
                            "source_b": "photo 4 odometer",
                        }
                    ],
                    "knowledge_base_findings": [],
                }
            ),
            _json({"photos_provided": True, "photo_limitations": []}),
        )

        self.assertIn("sourced_listing_conflict_high", _rules(result))
        self.assertIn("sourced material listing conflict", _overrides(result))
        self.assertEqual(result["decision_status"], "RESOLVE_BEFORE_PROCEEDING")

    def test_two_claims_from_same_listing_are_not_independent_sources(self):
        research = {
            "listing_facts": {
                "vin": "",
                "year": "2023",
                "mileage": "159000 km",
                "service_history": "",
                "origin_or_country": "",
                "seller": "",
            },
            "vin_check": {"vin_present": False, "format_check": "skipped"},
            "market_assessment": {"price_view": "requires_manual_verification"},
            "consistency_checks": [{"result": "concern"}],
            "data_conflicts": [
                {
                    "issue": "Mileage versus seller's advertised condition",
                    "importance": "HIGH",
                    "source_a": "Inzerát: 159 000 km, rok 2023",
                    "source_b": "Inzerát: stav nového auta",
                }
            ],
            "knowledge_base_findings": [],
        }
        vision = {
            "photos_provided": True,
            "photo_limitations": [
                "Rozlíšenie fotografií neumožňuje detailné posúdenie drobných škrabancov."
            ],
            "visual_verdict": "Vyzerá vizuálne dobre",
        }

        hotfixed = calculate_risk_score(_json(research), _json(vision))
        v2 = calculate_risk_score_v2(_json(research), _json(vision))

        self.assertNotIn("sourced_listing_conflict_high", _rules(hotfixed))
        self.assertNotIn("sourced material listing conflict", _overrides(hotfixed))
        self.assertEqual(hotfixed["decision_status"], "WORTH_INSPECTING")
        self.assertFalse(any(item["code"] == "SOURCED_CONFLICT" for item in v2["gate_triggers"]))

    def test_suspicious_cheap_price_with_missing_vin_does_not_force_risky_verdict(self):
        result = calculate_risk_score(
            _json(
                {
                    "listing_facts": {
                        "service_history": "full",
                        "origin_or_country": "SK",
                        "seller": "private",
                    },
                    "vin_check": {"vin_present": False, "format_check": "skipped"},
                    "market_assessment": {"price_view": "rather_cheap"},
                    "consistency_checks": [],
                    "knowledge_base_findings": [],
                }
            ),
            _json({"photos_provided": True, "photo_limitations": [], "visual_verdict": "Vyzerá vizuálne dobre"}),
        )

        self.assertIn("vin_missing_request_before_viewing", _rules(result))
        self.assertNotIn("price_suspiciously_low_or_high", _rules(result))
        self.assertNotIn("suspiciously low price + invalid VIN", _overrides(result))
        self.assertIn("market_comparison", result["missing_data_flags"])

    def test_invalid_vin_with_suspicious_cheap_price_is_risky(self):
        result = calculate_risk_score(
            _json(
                {
                    "listing_facts": {
                        "vin": "INVALIDVINVALUE",
                        "service_history": "full",
                        "origin_or_country": "SK",
                        "seller": "private",
                    },
                    "vin_check": {"vin_present": True, "format_check": "problem"},
                    "market_assessment": {"price_view": "rather_cheap"},
                    "consistency_checks": [],
                    "knowledge_base_findings": [],
                }
            ),
            _json({"photos_provided": True, "photo_limitations": [], "visual_verdict": "Looks visually ok"}),
        )

        self.assertIn("vin_invalid_or_conflicting", _rules(result))
        self.assertNotIn("price_suspicious_and_other_risks_exist", _rules(result))
        self.assertNotIn("suspiciously low price + invalid VIN", _overrides(result))

    def test_spz_only_concern_is_verification_not_major_contradiction(self):
        result = calculate_risk_score(
            _json(
                {
                    "listing_facts": {
                        "vin": "JMBXNGA1WBZ019167",
                        "service_history": "full",
                        "origin_or_country": "SK",
                        "seller": "dealer",
                    },
                    "vin_check": {"vin_present": True, "format_check": "ok"},
                    "market_assessment": {"price_view": "fair"},
                    "consistency_checks": [{"result": "concern", "explanation": "SPZ on one photo looks different."}],
                    "knowledge_base_findings": [],
                }
            ),
            _json({"photos_provided": True, "photo_limitations": [], "visual_verdict": "Looks visually ok"}),
        )

        self.assertIn("registration_plate_needs_verification", _rules(result))
        self.assertNotIn("obvious_listing_conflict", _rules(result))
        self.assertNotIn("major listing contradiction", _overrides(result))
        self.assertIn("registration_plate", result["missing_data_flags"])

    def test_good_documentation_reduces_score_without_negative_result(self):
        result = calculate_risk_score(
            _json(
                {
                    "listing_facts": {
                        "vin": "JMBXNGA1WBZ019167",
                        "year": "2022",
                        "mileage": "40 000 km",
                        "service_history": "full",
                        "origin_or_country": "SK",
                        "seller": "dealer",
                    },
                    "vin_check": {"vin_present": True, "format_check": "ok"},
                    "market_assessment": {"price_view": "fair"},
                    "consistency_checks": [],
                    "knowledge_base_findings": [],
                }
            ),
            _json({"photos_provided": True, "photo_limitations": [], "visual_verdict": "Vyzerá vizuálne dobre"}),
        )

        self.assertNotIn("good_documentation_clear_origin_vin_good_photos", _rules(result))
        self.assertNotIn("excellent_documentation_and_low_risk_profile", _rules(result))
        self.assertEqual(result["risk_score"], 0)

    def test_generic_expensive_technical_risk_is_inspection_action_only(self):
        result = calculate_risk_score(
            _json(
                {
                    "listing_facts": {
                        "vin": "JM3KE4DYXE0325836",
                        "year": "2014",
                        "mileage": "182 734 km",
                        "service_history": "regular service claimed",
                        "origin_or_country": "SK",
                        "seller": "dealer",
                    },
                    "vin_check": {"vin_present": True, "format_check": "ok"},
                    "market_assessment": {"price_view": "fair"},
                    "consistency_checks": [],
                    "knowledge_base_findings": [],
                    "technical_risks": [
                        {
                            "component": "automatic transmission",
                            "issue": "Valve-body repair is a conditional high-mileage risk.",
                            "evidence_category": "MODEL_LEVEL_RISK",
                            "estimated_cost_eur_low": 300,
                            "estimated_cost_eur_high": 1500,
                            "confidence": "Vysoka",
                        }
                    ],
                }
            ),
            _json({"photos_provided": True, "photo_limitations": [], "visual_verdict": "Looks visually ok"}),
        )

        self.assertNotIn("high_confidence_expensive_known_risk", _rules(result))
        self.assertTrue(any("modelove rizika" in item for item in result["buyer_priority_checks"]))

    def test_hyundai_optional_vin_checks_do_not_create_false_red(self):
        result = calculate_risk_score(
            _json(
                {
                    "listing_facts": {
                        "vin": "TMAJ3812HHJ268187",
                        "year": "2016",
                        "mileage": "161949 km",
                        "service_history": "full service history claimed",
                        "origin_or_country": "Germany claimed",
                        "seller": "dealer",
                    },
                    "vin_check": {"vin_present": True, "format_check": "ok"},
                    "market_assessment": {"price_view": "requires_manual_verification"},
                    "consistency_checks": [
                        {
                            "result": "concern",
                            "check": "VIN check digit validity",
                            "explanation": "Optional ROW check digit does not match.",
                        },
                        {
                            "result": "concern",
                            "check": "VIN model year consistency",
                            "explanation": "Code H can be 1987 or 2017 for a 09/2016 registration.",
                        },
                    ],
                    "data_conflicts": [],
                    "technical_risks": [
                        {
                            "component": "7DCT",
                            "issue": "Clutch judder is a model-level inspection point.",
                            "evidence_category": "MODEL_LEVEL_RISK",
                        }
                    ],
                }
            ),
            _json(
                {
                    "photos_provided": True,
                    "photo_limitations": [],
                    "exterior_observations": [
                        {"assessment": "reassuring", "severity": "none"}
                    ],
                    "visible_red_flags": [],
                }
            ),
        )

        self.assertEqual(result["decision_status"], "WORTH_INSPECTING")
        self.assertEqual(result["risk_score"], 0)
        self.assertNotIn("major listing contradiction", _overrides(result))

    def test_uncertain_or_legacy_red_flag_is_not_scored_as_serious_damage(self):
        result = calculate_risk_score(
            _json(
                {
                    "listing_facts": {
                        "vin": "TMAJ3812HHJ268187",
                        "service_history": "full",
                    },
                    "vin_check": {"vin_present": True, "format_check": "ok"},
                }
            ),
            _json(
                {
                    "photos_provided": True,
                    "photo_limitations": [],
                    "visible_red_flags": [
                        {"assessment": "uncertain", "severity": "unknown"}
                    ],
                }
            ),
        )

        self.assertNotIn("visible_serious_damage_or_red_flags", _rules(result))

    def test_v2_missing_vin_is_action_only(self):
        result = calculate_risk_score_v2(
            _json({
                "listing_facts": {"year": "2018", "service_history": "full"},
                "vin_check": {"vin_present": False, "format_check": "skipped"},
                "technical_risks": [],
            }),
            _json({"photos_provided": True, "photo_limitations": []}),
        )
        self.assertEqual(result["decision_status"], "WORTH_INSPECTING")
        self.assertEqual(result["evidence_quality"], "MEDIUM")
        self.assertFalse(any(item.get("code") == "VIN_INVALID" for item in result["gate_triggers"]))
        self.assertTrue(any("Cebia" in action for action in result["buyer_actions"]))

    def test_v2_suzuki_positive_minor_legacy_observations_do_not_escalate(self):
        fixture = json.loads(
            (Path(__file__).parent / "test_fixtures" / "risk_calibration" / "suzuki_positive_legacy.json").read_text(encoding="utf-8")
        )
        result = calculate_risk_score_v2(
            _json(fixture["text_research"]),
            _json(fixture["vision"]),
        )
        self.assertEqual(result["decision_status"], fixture["expected"]["decision_status"])
        self.assertEqual(len(result["vehicle_specific_findings"]), fixture["expected"]["vehicle_specific_findings"])

    def test_v2_same_minor_cosmetic_wear_is_age_adjusted_but_stays_green(self):
        def score(year):
            return calculate_risk_score_v2(
                _json({"listing_facts": {"year": str(year), "vin": "JSA123", "service_history": "full"}, "vin_check": {"vin_present": True, "format_check": "ok"}}),
                _json({"photos_provided": True, "photo_limitations": [], "exterior_observations": [{"assessment": "concern", "severity": "minor", "buyer_impact": "cosmetic", "age_context": "expected", "confidence": "HIGH", "photo_label": "Foto 01", "observation": "Small scratch"}]}),
            )
        new_car = score(2025)
        old_car = score(2010)
        self.assertEqual(new_car["decision_status"], "WORTH_INSPECTING")
        self.assertEqual(old_car["decision_status"], "WORTH_INSPECTING")
        self.assertLess(new_car["screening_score"], old_car["screening_score"])

    def test_v2_low_evidence_caps_green_at_yellow(self):
        result = calculate_risk_score_v2("not json", _json({"photos_provided": False}), "")
        self.assertEqual(result["decision_status"], "INSPECT_WITH_RESERVATIONS")
        self.assertEqual(result["evidence_quality"], "LOW")
        self.assertLessEqual(result["screening_score"], 89)

    def test_truncated_vision_with_listing_photos_is_provider_failure_not_missing_photos(self):
        research = _json(
            {
                "listing_facts": {"vin": "TESTVIN123456789", "service_history": "full"},
                "vin_check": {"vin_present": True, "format_check": "ok"},
            }
        )

        result = calculate_risk_score(
            research,
            '{"photos_provided": true, "confidence": "HIGH',
            "Downloaded images: 7",
        )

        self.assertIn("vision_analysis_unavailable", _rules(result))
        self.assertNotIn("missing_or_weak_photos", _rules(result))
        self.assertNotIn("photos", result["missing_data_flags"])
        self.assertIn("vision_analysis", result["missing_data_flags"])
        self.assertEqual(result["decision_status"], "INSPECT_WITH_RESERVATIONS")
        self.assertTrue(any("manualne" in action for action in result["buyer_priority_checks"]))
        self.assertFalse(any("Doplnit fotky" in action for action in result["buyer_priority_checks"]))

    def test_v2_unavailable_vision_does_not_claim_photos_are_missing(self):
        result = calculate_risk_score_v2(
            _json(
                {
                    "listing_facts": {"vin": "TESTVIN123456789", "service_history": "full"},
                    "vin_check": {"vin_present": True, "format_check": "ok"},
                }
            ),
            _json(
                {
                    "analysis_status": "unavailable",
                    "photos_provided": True,
                    "photo_limitations": ["Provider output was truncated."],
                }
            ),
        )

        codes = {item["code"] for item in result["missing_information"]}
        self.assertIn("VISION_ANALYSIS_UNAVAILABLE", codes)
        self.assertNotIn("NO_USABLE_PHOTOS", codes)
        self.assertEqual(result["evidence_quality"], "LOW")
        self.assertEqual(result["decision_status"], "INSPECT_WITH_RESERVATIONS")

    def test_v2_model_level_risks_do_not_escalate(self):
        result = calculate_risk_score_v2(
            _json({
                "listing_facts": {"year": "2015", "vin": "JSA123", "service_history": "full"},
                "vin_check": {"vin_present": True, "format_check": "ok"},
                "technical_risks": [{"component": "turbo", "issue": "expensive", "risk_level": "HIGH", "evidence_category": "MODEL_LEVEL_RISK", "confidence": "HIGH"}] * 5,
            }),
            _json({"photos_provided": True, "photo_limitations": []}),
        )
        self.assertEqual(result["decision_status"], "WORTH_INSPECTING")
        self.assertEqual(len(result["model_level_inspection_points"]), 1)

    def test_v2_localizes_customer_verdict_without_changing_status(self):
        research = _json({"listing_facts": {"vin": "JSA123", "service_history": "full"}, "vin_check": {"vin_present": True, "format_check": "ok"}})
        vision = _json({"photos_provided": True, "photo_limitations": []})
        slovak = calculate_risk_score_v2(research, vision, output_language="sk")
        english = calculate_risk_score_v2(research, vision, output_language="en")
        czech = calculate_risk_score_v2(research, vision, output_language="cs")
        self.assertEqual(slovak["decision_status"], english["decision_status"])
        self.assertEqual(slovak["decision_status"], czech["decision_status"])
        self.assertEqual(slovak["allowed_final_verdict"], "🟢 STOJÍ ZA OBHLIADKU")
        self.assertEqual(czech["allowed_final_verdict"], "🟢 STOJÍ ZA PROHLÍDKU")
        self.assertEqual(english["allowed_final_verdict"], "🟢 WORTH CHECKING OUT")


if __name__ == "__main__":
    unittest.main()
