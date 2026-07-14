import unittest

from scrapper_demo.scorecard import build_buyer_scorecard


class BuyerScorecardTests(unittest.TestCase):
    def setUp(self):
        self.research = {
            "evidence_summary": {"overall_confidence": "MEDIUM"},
            "component_identity": {
                "engine": {
                    "marketing_name": "2.0 TDI",
                    "resolution": "PROBABLE",
                },
                "transmission": {
                    "marketing_name": "7-speed DSG",
                    "resolution": "PROBABLE",
                },
            },
            "listing_facts": {
                "vin": "TMBJJ7NE0J0123456",
                "mileage": "125 000 km",
                "service_history": "Servisná knižka a faktúry",
                "origin_or_country": "Slovensko",
                "seller": "Súkromný predajca",
            },
            "vin_check": {"vin_present": True, "format_check": "ok"},
            "data_conflicts": [],
            "market_assessment": {"available": True, "price_view": "fair"},
            "market_comparables": [
                {"source_url": "https://example.test/car-1", "verified_url": True},
                {"source_url": "https://example.test/car-2", "verified_url": True},
            ],
            "technical_risks": [
                {
                    "component": "Automatická prevodovka",
                    "issue": "Výmena oleja v automatickej prevodovke",
                    "risk_level": "MEDIUM",
                    "evidence_category": "MODEL_LEVEL_RISK",
                },
                {
                    "component": "Motor",
                    "issue": "Kontrola rozvodov podľa servisnej histórie",
                    "risk_level": "CHECK",
                    "evidence_category": "MODEL_LEVEL_RISK",
                }
            ],
            "expected_costs": [
                {
                    "item": "Vstupná diagnostika",
                    "cost_type": "diagnostic",
                    "estimated_cost_eur_low": 80,
                    "estimated_cost_eur_high": 120,
                    "urgency": "medium",
                }
            ],
        }
        self.vision = {
            "photos_provided": True,
            "exterior_observations": [
                {"assessment": "reassuring", "severity": "none"}
            ],
            "interior_observations": [
                {"assessment": "reassuring", "severity": "none"}
            ],
            "dashboard_or_warning_lights": [],
            "visible_red_flags": [],
            "view_coverage": {
                "exterior": "visible_detail",
                "interior": "visible_detail",
                "dashboard": "visible_detail",
                "tires": "visible_detail",
            },
        }
        self.risk = {
            "decision_status": "INSPECT_WITH_RESERVATIONS",
            "screening_score": 75,
            "evidence_quality": "MEDIUM",
        }

    def test_builds_complete_higher_is_better_scorecard(self):
        card = build_buyer_scorecard(self.research, self.vision, self.risk)

        self.assertEqual(card["schema_version"], 1)
        self.assertEqual(card["scale_direction"], "higher_is_better")
        self.assertEqual(
            set(card["scores"]),
            {
                "listing_transparency",
                "market_position",
                "engine_profile",
                "transmission_profile",
                "visual_condition",
                "service_readiness",
            },
        )
        self.assertTrue(all(value is None or 0 <= value <= 100 for value in card["scores"].values()))
        self.assertEqual(card["scores"]["engine_profile"], 88)
        self.assertEqual(card["scores"]["transmission_profile"], 88)
        self.assertLessEqual(card["overall_score"], 75)
        self.assertEqual(card["confidence"], "MEDIUM")

    def test_missing_evidence_reduces_transparency_and_confidence(self):
        complete = build_buyer_scorecard(self.research, self.vision, self.risk)
        incomplete = build_buyer_scorecard(
            {
                "listing_facts": {},
                "vin_check": {"vin_present": False},
                "market_assessment": {"available": False},
            },
            {"photos_provided": False},
            self.risk,
        )

        self.assertLess(
            incomplete["scores"]["listing_transparency"],
            complete["scores"]["listing_transparency"],
        )
        self.assertEqual(incomplete["confidence"], "LOW")
        self.assertIsNone(incomplete["scores"]["market_position"])
        self.assertIsNone(incomplete["scores"]["visual_condition"])
        self.assertIsNone(incomplete["scores"]["service_readiness"])

    def test_verified_comparables_improve_market_score(self):
        verified = build_buyer_scorecard(self.research, self.vision, self.risk)
        unavailable_research = dict(self.research)
        unavailable_research["market_assessment"] = {"available": False}
        unavailable_research["market_comparables"] = []
        unavailable = build_buyer_scorecard(unavailable_research, self.vision, self.risk)

        self.assertGreater(
            verified["scores"]["market_position"],
            0,
        )
        self.assertIsNone(unavailable["scores"]["market_position"])

    def test_thin_market_sample_is_unscored_not_negative(self):
        research = dict(self.research)
        research["market_assessment"] = {
            "available": True,
            "benchmark_available": False,
            "benchmark_comparable_count": 2,
            "price_view": "requires_manual_verification",
        }

        card = build_buyer_scorecard(research, self.vision, self.risk)

        self.assertIsNone(card["scores"]["market_position"])

    def test_verified_european_background_benchmark_is_scored_without_public_links(self):
        research = dict(self.research)
        research["market_comparables"] = []
        research["market_assessment"] = {
            "available": True,
            "benchmark_available": True,
            "benchmark_comparable_count": 4,
            "benchmark_median_eur": 18000,
            "price_view": "fair",
        }

        card = build_buyer_scorecard(research, self.vision, self.risk)

        self.assertIsNotNone(card["scores"]["market_position"])

    def test_only_vehicle_specific_component_evidence_reduces_profile(self):
        baseline = build_buyer_scorecard(self.research, self.vision, self.risk)
        research = dict(self.research)
        research["technical_risks"] = list(self.research["technical_risks"]) + [
            {
                "component": "Automatic transmission",
                "issue": "Jerks during the test drive",
                "risk_level": "HIGH",
                "evidence_category": "VISUAL_INDICATION",
                "specific_vehicle_evidence": "Seller video shows repeated jerking.",
            }
        ]

        concerned = build_buyer_scorecard(research, self.vision, self.risk)

        self.assertEqual(baseline["scores"]["transmission_profile"], 88)
        self.assertLess(concerned["scores"]["transmission_profile"], 88)

    def test_missing_vin_and_service_history_are_unknown_not_score_penalties(self):
        baseline = build_buyer_scorecard(self.research, self.vision, self.risk)
        research = dict(self.research)
        research["listing_facts"] = dict(self.research["listing_facts"])
        research["listing_facts"].pop("vin")
        research["listing_facts"].pop("service_history")
        research["vin_check"] = {"vin_present": False}

        unknown = build_buyer_scorecard(research, self.vision, self.risk)

        self.assertEqual(
            unknown["scores"]["listing_transparency"],
            baseline["scores"]["listing_transparency"],
        )
        self.assertIsNone(unknown["scores"]["service_readiness"])

    def test_reassuring_legacy_severity_does_not_reduce_visual_score(self):
        vision = dict(self.vision)
        vision["exterior_observations"] = [
            {"assessment": "reassuring", "severity": "minor"}
        ]

        card = build_buyer_scorecard(self.research, vision, self.risk)

        self.assertGreaterEqual(card["scores"]["visual_condition"], 82)


if __name__ == "__main__":
    unittest.main()
