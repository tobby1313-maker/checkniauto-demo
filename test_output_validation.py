import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import web_server


class OutputValidationTest(unittest.TestCase):
    def test_final_report_with_matching_verdict_and_end_marker_has_no_warnings(self):
        warnings = web_server._soft_validate_final_report(
            """# Analyza: Test

## Rychle zhrnutie

**RISKY**

## Data z inzeratu

| Polozka | Hodnota | Poznamka |
|---|---:|---|
| Cena | 10 000 EUR | Test |

## VIN a transparentnost

VIN je uvedene.

## Webove overenie

- Manualne overit.

## Cena a vyjednavanie

Cena je orientacna.

## Ocakavane naklady na najblizsich 30 000 km

| Polozka | Preco | Odhad EUR | Urgentnost |
|---|---|---:|---|
| Kontrola | Test | 100 - 200 | Nizka |

## Analyza fotografii

- Fotografie skontrolovat pri obhliadke.

## Klady

- Test klad.

## Zapory / rizika

- Test riziko.

## Otazky pre predajcu a kontrola pri obhliadke

| Otazka / ukon | Preco |
|---|---|
| Overit doklady | Test |

## Zaverecne odporucanie

**RISKY**

<!-- END_ANALYSIS -->""",
            "RISKY",
        )

        self.assertEqual(warnings, [])

    def test_final_report_warns_when_backend_verdict_is_missing(self):
        warnings = web_server._soft_validate_final_report(
            "# Report\n\nLooks acceptable.\n\n<!-- END_ANALYSIS -->",
            "RISKY",
        )

        self.assertIn("verdict_lock", {warning["type"] for warning in warnings})

    def test_final_report_warns_when_end_marker_is_missing(self):
        warnings = web_server._soft_validate_final_report("# Report\n\n**RISKY**", "RISKY")

        self.assertIn("missing_end_marker", {warning["type"] for warning in warnings})

    def test_final_report_warns_on_forbidden_claim(self):
        warnings = web_server._soft_validate_final_report(
            "# Report\n\nVIN bol overeny online a auto je bez rizika.\n\n**RISKY**\n\n<!-- END_ANALYSIS -->",
            "RISKY",
        )

        self.assertIn("forbidden_claim", {warning["type"] for warning in warnings})

    def test_final_report_warns_on_internal_customer_labels(self):
        warnings = web_server._soft_validate_final_report(
            "# Report\n\n"
            "| Polozka | Dokaz | Istota |\n"
            "|---|---|---|\n"
            "| Servis | Inzerat | Stredna |\n\n"
            "- **Evidence:** Listing\n"
            "- **Confidence:** Medium\n\n"
            "**RISKY**\n\n"
            "<!-- END_ANALYSIS -->",
            "RISKY",
        )

        labels = {
            warning.get("label")
            for warning in warnings
            if warning["type"] == "internal_label"
        }
        self.assertEqual(labels, {"Dôkaz", "Istota", "Evidence", "Confidence"})

    def test_final_report_warns_on_unverified_public_link(self):
        warnings = web_server._soft_validate_final_report(
            "# Report\n\n"
            "[Google redirect](https://vertexaisearch.cloud.google.com/grounding-api-redirect/test)\n\n"
            "[Placeholder](https://example.com/source)\n\n"
            "**RISKY**\n\n"
            "<!-- END_ANALYSIS -->",
            "RISKY",
        )

        unverified_urls = {
            warning["url"]
            for warning in warnings
            if warning["type"] == "unverified_public_link"
        }
        self.assertEqual(
            unverified_urls,
            {
                "https://vertexaisearch.cloud.google.com/grounding-api-redirect/test",
                "https://example.com/source",
            },
        )

    def test_json_contract_validation_warns_without_failing(self):
        warnings = web_server._soft_validate_json_contract(
            "grok_research.json",
            json.dumps({"source_role": "text_research"}),
            "grok_research.schema.json",
        )

        self.assertIn("schema_required", {warning["type"] for warning in warnings})

    def test_grok_schema_contract_accepts_new_buyer_value_fields(self):
        payload = {
            "source_role": "text_research",
            "listing_facts": {"title": "Mazda CX-5", "price": "9999 EUR"},
            "missing_or_uncertain_data": [],
            "consistency_checks": [],
            "vin_check": {"vin_present": True, "format_check": "ok"},
            "knowledge_base_findings": [],
            "web_research_findings": [
                {
                    "claim": "Comparable listings cluster around 9000-12000 EUR.",
                    "source_name": "Autobazar.EU",
                    "source_url": "https://www.autobazar.eu/mazda/cx-5/",
                    "verified_url": True,
                    "source_type": "market",
                    "confidence": "Stredna",
                    "buyer_impact": "Price is plausible but still depends on service history.",
                    "notes": "Use as orientation only.",
                }
            ],
            "technical_risks": [
                {
                    "component": "AWD",
                    "issue": "Differential or transfer-case service may be due.",
                    "buyer_impact": "Neglected driveline service can become expensive.",
                    "typical_trigger_or_interval": "Older AWD car around 150000 km.",
                    "estimated_cost_eur_low": 250,
                    "estimated_cost_eur_high": 700,
                    "source_basis": "Odhad",
                    "confidence": "Stredna",
                }
            ],
            "market_assessment": {
                "available": True,
                "advertised_price_eur": 9999,
                "observed_market_low_eur": 9000,
                "observed_market_high_eur": 12000,
                "comparable_count": 4,
                "summary": "Price sits inside observed range.",
                "limitations": "Comparable cars differ by equipment and history.",
                "negotiation_anchor_eur": 9300,
                "negotiation_reason": "Service history is missing.",
                "price_view": "fair",
            },
            "expected_costs": [
                {
                    "item": "AWD fluids and inspection",
                    "why": "Age and mileage make driveline service worth checking.",
                    "estimated_cost_eur_low": 180,
                    "estimated_cost_eur_high": 450,
                    "urgency": "medium",
                    "basis": "Odhad",
                }
            ],
            "text_research_risk_flags": [],
        }

        warnings = web_server._soft_validate_json_contract(
            "grok_research.json",
            json.dumps(payload),
            "grok_research.schema.json",
        )

        self.assertNotIn("schema_required", {warning["type"] for warning in warnings})

    def test_final_context_passes_cost_market_and_verified_source_data(self):
        car_info_text = "\n".join(
            [
                "# Mazda CX-5 2.0 Skyactiv-G AWD",
                "",
                "## Price",
                "- **Price:** 9 999 EUR",
                "",
                "## Specifications",
                "- **Year:** 2013",
                "- **Mileage:** 158 303 km",
                "- **VIN:** JMZKEK97800158303",
            ]
        )
        grok_json = json.dumps(
            {
                "listing_facts": {"title": "Mazda CX-5", "price": "9999 EUR"},
                "missing_or_uncertain_data": [],
                "consistency_checks": [],
                "vin_check": {"vin_present": True, "format_check": "ok"},
                "knowledge_base_findings": [],
                "web_research_findings": [
                    {
                        "claim": "Common CX-5 market listings are available.",
                        "source_name": "Autobazar.EU",
                        "source_url": "https://www.autobazar.eu/mazda/cx-5/",
                        "verified_url": True,
                        "source_type": "market",
                    },
                    {
                        "claim": "Redirect-only source should not become public.",
                        "source_name": "Redirect Source",
                        "source_url": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/test",
                        "verified_url": True,
                        "source_type": "reliability",
                    },
                ],
                "technical_risks": [
                    {
                        "component": "AWD",
                        "issue": "Driveline service uncertainty",
                        "estimated_cost_eur_low": 250,
                        "estimated_cost_eur_high": 700,
                        "source_url": "https://www.autobazar.eu/mazda/cx-5/",
                    }
                ],
                "market_assessment": {
                    "available": True,
                    "advertised_price_eur": 9999,
                    "observed_market_low_eur": 9000,
                    "observed_market_high_eur": 12000,
                    "comparable_count": 4,
                    "limitations": "Comparable cars differ by history.",
                    "negotiation_anchor_eur": 9300,
                    "price_view": "fair",
                },
                "expected_costs": [
                    {
                        "item": "AWD fluids",
                        "why": "Mileage and age.",
                        "estimated_cost_eur_low": 180,
                        "estimated_cost_eur_high": 450,
                        "urgency": "medium",
                        "basis": "Odhad",
                    }
                ],
                "text_research_risk_flags": [],
            }
        )
        context = web_server._build_final_synthesis_context(
            "sk",
            car_info_text,
            grok_json,
            json.dumps({"photos_provided": False, "photo_limitations": []}),
            json.dumps({"risk_score": 4, "allowed_final_verdict": "RIZIKOVA KUPA"}),
            "\n".join(
                [
                    "- [Autobazar.EU](https://www.autobazar.eu/mazda/cx-5/) - market listings",
                    "- Redirect Source (URL citacia nie je overitelna)",
                    "- [Bad](https://vertexaisearch.cloud.google.com/grounding-api-redirect/test)",
                ]
            ),
        )
        payload = json.loads(context.split("\n\n", 1)[1])

        self.assertEqual(
            payload["text_research"]["expected_costs"][0]["estimated_cost_eur_high"],
            450,
        )
        self.assertEqual(payload["text_research"]["market_assessment"]["negotiation_anchor_eur"], 9300)
        self.assertTrue(payload["text_research"]["web_research_findings"][0]["verified_url"])
        self.assertFalse(payload["text_research"]["web_research_findings"][1]["verified_url"])
        self.assertEqual(payload["text_research"]["web_research_findings"][1]["source_url"], "")
        self.assertEqual(
            payload["web_research"]["verified_source_lines"],
            ["- [Autobazar.EU](https://www.autobazar.eu/mazda/cx-5/) - market listings"],
        )
        self.assertNotIn("vertexaisearch", payload["web_research"]["evidence_excerpt"])

    def test_fixture_style_report_regression_for_costs_links_market_and_verdict(self):
        report = """
# Analyza: Mazda CX-5 2.0 Skyactiv-G AWD

## Webove overenie

- Trhove porovnanie je orientacne podla [Autobazar.EU](https://www.autobazar.eu/mazda/cx-5/).
- Zdroj z Google Search: URL nie je priamo overitelna.

## Cena a vyjednavanie

Aktualne porovnanie trhu vyzaduje manualne online overenie, preto je vyjednavaci strop iba orientacny.

## Ocakavane naklady na najblizsich 30 000 km

| Polozka | Preco | Odhad EUR | Urgentnost |
|---|---|---:|---|
| AWD servis | Vek a najazd | 180 - 450 | Stredna |
| Kontrola korozie | Model a vek | 100 - 500 | Stredna |

**RIZIKOVA KUPA**

<!-- END_ANALYSIS -->
"""

        warnings = web_server._soft_validate_final_report(report, "RIZIKOVA KUPA")

        self.assertNotIn("unverified_public_link", {warning["type"] for warning in warnings})
        self.assertIn("180 - 450", report)
        self.assertIn("Aktualne porovnanie trhu vyzaduje manualne online overenie", report)
        self.assertIn("<!-- END_ANALYSIS -->", report)

    def test_write_validation_warnings_creates_artifact(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(web_server, "safe_log"):
                path = web_server._write_validation_warnings(
                    temp_dir,
                    [{"artifact": "analysis_result.md", "type": "missing_end_marker", "message": "missing"}],
                )

            self.assertTrue(Path(path).exists())
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            self.assertEqual(payload["warnings"][0]["type"], "missing_end_marker")


if __name__ == "__main__":
    unittest.main()
