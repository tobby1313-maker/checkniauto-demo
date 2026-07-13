import unittest

from scrapper_demo.scorecard import build_buyer_scorecard


class BuyerScorecardTests(unittest.TestCase):
    def setUp(self):
        self.research = {
            "evidence_summary": {"overall_confidence": "MEDIUM"},
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
        self.assertLess(card["scores"]["transmission_profile"], card["scores"]["engine_profile"])
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


if __name__ == "__main__":
    unittest.main()
