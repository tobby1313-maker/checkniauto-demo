import json
import unittest

from risk_scorer import calculate_risk_score


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

    def test_serious_visual_red_flag_caps_verdict(self):
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

        self.assertIn("visible_serious_damage_or_red_flags", _rules(result))
        self.assertIn("serious visible red flags", _overrides(result))

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

    def test_listing_contradiction_caps_verdict_as_risky(self):
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

        self.assertIn("obvious_listing_conflict", _rules(result))
        self.assertIn("major listing contradiction", _overrides(result))

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
        self.assertIn("price_suspiciously_low_or_high", _rules(result))
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
        self.assertIn("price_suspicious_and_other_risks_exist", _rules(result))
        self.assertIn("suspiciously low price + invalid VIN", _overrides(result))

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

        self.assertIn("good_documentation_clear_origin_vin_good_photos", _rules(result))
        self.assertIn("excellent_documentation_and_low_risk_profile", _rules(result))
        self.assertEqual(result["risk_score"], 0)

    def test_source_backed_expensive_technical_risk_affects_score_without_kb(self):
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

        self.assertIn("high_confidence_expensive_known_risk", _rules(result))


if __name__ == "__main__":
    unittest.main()
