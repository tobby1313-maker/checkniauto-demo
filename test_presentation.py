import os
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import web_server
from scrapper_demo.presentation import build_presentation_payload
from scrapper_demo.storage import ListingJobRepository


class PresentationPayloadTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repository = ListingJobRepository(Path(self.temp_dir.name) / "Auta")
        self.slug = "presentation-car"
        self.repository.job_dir(self.slug, create=True)

    def test_builds_safe_customer_model_from_canonical_artifacts(self):
        self.repository.write_json(
            self.slug,
            "listing_facts.json",
            {
                "title": "Example Car 2.0",
                "price": "18 900 EUR",
                "year": "2020",
                "advertised_mileage_km": 84200,
                "vin": "TESTVIN123456789",
                "fuel": "Petrol",
                "transmission": "Automatic",
            },
        )
        self.repository.write_json(
            self.slug,
            "analysis_metadata.json",
            {"schema_version": 1, "output_language": "en"},
        )
        self.repository.write_json(
            self.slug,
            "risk_score.json",
            {
                "decision_status": "INSPECT_WITH_RESERVATIONS",
                "screening_score": 87,
                "buyer_actions": ["Verify the VIN", "Request service invoices"],
            },
        )
        self.repository.write_json(
            self.slug,
            "grok_research.json",
            {
                "evidence_summary": {
                    "overall_confidence": "MEDIUM",
                    "strongest_evidence": ["Service documentation"],
                },
                "missing_or_uncertain_data": [
                    {"item": "Accident history", "why_it_matters": "VIN check needed", "severity": "high"}
                ],
                "technical_risks": [
                    {
                        "component": "Transmission",
                        "issue": "Wear",
                        "risk_level": "MEDIUM",
                        "buyer_impact": "Rough shifting",
                        "verification_action": "Cold test drive",
                        "estimated_cost_eur_low": 300,
                        "estimated_cost_eur_high": 800,
                    }
                ],
                "expected_costs": [
                    {
                        "item": "Initial service",
                        "why": "Baseline maintenance",
                        "estimated_cost_eur_low": 150,
                        "estimated_cost_eur_high": 300,
                        "cost_type": "initial_service",
                        "urgency": "high",
                    }
                ],
                "sources_used": [
                    {
                        "source_name": "Official source",
                        "source_url": "https://example.com/official",
                        "verified_url": True,
                        "source_type": "OFFICIAL",
                        "reliability": "HIGH",
                        "used_for": "Service interval",
                    },
                    {
                        "source_name": "Unverified source",
                        "source_url": "https://example.com/unverified",
                        "verified_url": False,
                    },
                ],
            },
        )
        self.repository.write_json(
            self.slug,
            "market_benchmark.json",
            {
                "available": True,
                "confidence": "MEDIUM",
                "advertised_price_eur": 18900,
                "median_eur": 18500,
                "price_delta_percent": 2.2,
                "price_view": "fair",
                "accepted_comparables": [
                    {
                        "title": "Public comparable",
                        "detail_url": "https://example.com/car",
                        "display_in_report": True,
                        "price_eur": 18500,
                    },
                    {
                        "title": "Background only",
                        "detail_url": "https://example.com/background",
                        "display_in_report": False,
                    },
                ],
                "limitations": ["Asking prices are not transaction prices."],
            },
        )

        payload = build_presentation_payload(
            self.repository,
            self.slug,
            parsed={
                "title": "Fallback title",
                "price": "0",
                "vin": "",
                "specs": {},
                "source_url": "https://example.com/listing",
                "scraped_at": "2026-07-16 12:00",
            },
            images=[{"filename": "01.jpg", "url": "/image/01.jpg"}],
            report_markdown=(
                "# Public report\n\n"
                "### Analysis score\n\n"
                "| Area | Score |\n|---|---:|\n| Overall | 87/100 |\n\n"
                "### Buyer notes\n\nVerify the VIN."
            ),
        )

        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["language"], "en")
        self.assertEqual(payload["listing"]["price_eur"], 18900)
        self.assertEqual(payload["verdict"]["status"], "INSPECT_WITH_RESERVATIONS")
        self.assertNotIn("screening_score", payload["verdict"])
        self.assertNotIn("risk_score", payload)
        self.assertEqual(payload["costs"]["initial_service"]["high_eur"], 300)
        self.assertEqual(len(payload["sources"]), 1)
        self.assertEqual(len(payload["market"]["comparables"]), 1)
        self.assertIn("Verify the VIN", payload["seller_message"])
        self.assertNotIn("87/100", payload["report_markdown"])
        self.assertIn("Buyer notes", payload["report_markdown"])

    def test_partial_artifacts_return_honest_unavailable_states(self):
        payload = build_presentation_payload(
            self.repository,
            self.slug,
            parsed={"title": "Sparse Car", "specs": {}, "source_url": "", "scraped_at": ""},
            images=[],
            report_markdown="# Sparse report",
        )

        self.assertEqual(payload["listing"]["title"], "Sparse Car")
        self.assertIsNone(payload["listing"]["price_eur"])
        self.assertFalse(payload["market"]["available"])
        self.assertFalse(payload["costs"]["initial_service"]["available"])
        self.assertEqual(payload["sources"], [])

    def test_czech_metadata_localizes_customer_owned_presentation_text(self):
        self.repository.write_json(
            self.slug,
            "analysis_metadata.json",
            {"schema_version": 1, "output_language": "cs"},
        )
        self.repository.write_json(
            self.slug,
            "risk_score.json",
            {
                "decision_status": "INSPECT_WITH_RESERVATIONS",
                "buyer_actions": ["Ověřte VIN", "Vyžádejte si servisní dokumentaci"],
            },
        )

        payload = build_presentation_payload(
            self.repository,
            self.slug,
            parsed={"title": "Testovací vůz", "specs": {}, "source_url": "", "scraped_at": ""},
            images=[],
            report_markdown="# Český report",
        )

        self.assertEqual(payload["language"], "cs")
        self.assertEqual(payload["verdict"]["label"], "Nejprve prověřit")
        self.assertIn("Dobrý den", payload["seller_message"])
        self.assertIn("Ověřte VIN", payload["seller_message"])

    def test_supports_legacy_artifacts_and_current_vision_fields(self):
        self.repository.write_json(
            self.slug,
            "grok_research.json",
            {
                "listing_facts": {"title": "Legacy Car", "price": "18.900,00 EUR", "mileage": "84.200 km"},
                "component_identity": {
                    "identification_status": "AMBIGUOUS",
                    "generation": {"name": "Generation X", "confidence": "MEDIUM"},
                    "engine": {"marketing_name": "2.0 TDI", "code": "EA288"},
                    "transmission": {"marketing_name": "DSG"},
                    "drivetrain": {"type": "FWD"},
                    "candidate_variants": [
                        {"engine_code": "A", "transmission_code": "B", "reason": "VIN is required"}
                    ],
                    "notes": ["Specification matching is not exact identification."],
                },
                "market_assessment": {
                    "benchmark_available": True,
                    "benchmark_confidence": "MEDIUM",
                    "benchmark_median_eur": 18500,
                    "price_view": "fair",
                },
                "market_comparables": [
                    {
                        "description": "Verified legacy comparable",
                        "source_url": "https://example.com/legacy-car",
                        "verified_url": True,
                        "display_in_report": True,
                        "price_eur": 18500,
                    },
                    {
                        "description": "Unverified comparable",
                        "source_url": "https://example.com/not-public",
                        "verified_url": False,
                        "display_in_report": True,
                    },
                ],
                "web_research_findings": [
                    {
                        "claim": "Official interval",
                        "buyer_impact": "Check the service record",
                        "source_name": "Official",
                        "source_url": "https://example.com/official-source",
                        "verified_url": True,
                        "source_type": "official",
                        "confidence": "HIGH",
                    }
                ],
            },
        )
        self.repository.write_json(
            self.slug,
            "gemini_vision.json",
            {
                "photos_provided": True,
                "visible_red_flags": [
                    {"red_flag": "Warning lamp is visible", "severity": "serious"}
                ],
                "supported_observations": [
                    {"observation": "Front bumper has a visible scratch"}
                ],
                "missing_views": ["underbody"],
            },
        )

        payload = build_presentation_payload(
            self.repository,
            self.slug,
            parsed={"title": "Legacy fallback", "specs": {}, "source_url": "", "scraped_at": ""},
            images=[],
            report_markdown="# Legacy report",
        )

        self.assertEqual(payload["listing"]["price_eur"], 18900)
        self.assertEqual(payload["listing"]["mileage_km"], 84200)
        self.assertEqual(payload["identity"]["status"], "AMBIGUOUS")
        self.assertEqual(payload["identity"]["candidate_variants"][0]["engine_code"], "A")
        self.assertEqual(payload["priority_findings"][0]["title"], "Warning lamp is visible")
        self.assertEqual(payload["vision"]["supported_observations"], ["Front bumper has a visible scratch"])
        self.assertEqual(payload["vision"]["missing_views"], ["underbody"])
        self.assertEqual(len(payload["market"]["comparables"]), 1)
        self.assertEqual(len(payload["sources"]), 1)

    def test_successful_pipeline_fixture_maps_without_private_score_fields(self):
        fixture_dir = Path(__file__).resolve().parent / "test_fixtures" / "successful_pipeline"
        job_dir = self.repository.job_dir(self.slug)
        for source in fixture_dir.iterdir():
            if source.is_file():
                shutil.copy2(source, job_dir / source.name)

        payload = build_presentation_payload(
            self.repository,
            self.slug,
            parsed={"title": "Fixture", "specs": {}, "source_url": "", "scraped_at": ""},
            images=[],
            report_markdown=(job_dir / "analysis_result.md").read_text(encoding="utf-8"),
        )

        serialized = json.dumps(payload)
        self.assertEqual(payload["listing"]["title"], "Representative Vehicle 2.0 TSI")
        self.assertEqual(payload["identity"]["engine"]["code"], "ENG-TEST")
        self.assertEqual(payload["verdict"]["status"], "RESOLVE_BEFORE_PROCEEDING")
        self.assertNotIn("screening_score", serialized)
        self.assertNotIn("score_breakdown", serialized)


class PresentationRouteTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.original_auta_dir = web_server.AUTA_DIR
        web_server.AUTA_DIR = os.path.join(self.temp_dir.name, "Auta")
        Path(web_server.AUTA_DIR).mkdir(parents=True, exist_ok=True)
        self.addCleanup(self._restore)
        web_server.app.testing = True
        self.client = web_server.app.test_client()

    def _restore(self):
        web_server.AUTA_DIR = self.original_auta_dir

    def test_redesign_pages_and_missing_presentation_route(self):
        summary = self.client.get("/analysis/example-car")
        technical = self.client.get("/analysis/example-car/technical")
        missing = self.client.get("/api/demo/listings/example-car/presentation")

        self.assertEqual(summary.status_code, 200)
        self.assertEqual(technical.status_code, 200)
        self.assertEqual(missing.status_code, 404)
        self.assertIn("Checkni Auto", summary.get_data(as_text=True))
        self.assertIn("technical", technical.get_data(as_text=True).lower())
        summary.close()
        technical.close()
        missing.close()


if __name__ == "__main__":
    unittest.main()
