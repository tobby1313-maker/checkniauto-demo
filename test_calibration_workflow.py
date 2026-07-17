import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import web_server
from scrapper_demo.calibration import (
    _manifest_metadata,
    create_calibration_bundle,
    create_debugging_bundle,
    safe_extract_bundle,
)
from scrapper_demo.calibration_cli import evaluate_dataset, validate_label


class CalibrationWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.original_auta = web_server.AUTA_DIR
        self.original_token = web_server.app.config.get("ADMIN_DASHBOARD_TOKEN", "")
        self.original_secret = web_server.app.config.get("SECRET_KEY")
        web_server.AUTA_DIR = str(Path(self.temp.name) / "Auta")
        Path(web_server.AUTA_DIR).mkdir()
        web_server.app.config["ADMIN_DASHBOARD_TOKEN"] = "secret-token"
        web_server.app.config["SECRET_KEY"] = "test-secret-key"
        web_server.app.testing = True
        self.client = web_server.app.test_client()
        self.addCleanup(self._restore)

    def _restore(self):
        web_server.AUTA_DIR = self.original_auta
        web_server.app.config["ADMIN_DASHBOARD_TOKEN"] = self.original_token
        web_server.app.config["SECRET_KEY"] = self.original_secret

    def _job(self, slug="case-one"):
        job = Path(web_server.AUTA_DIR) / slug
        (job / "images").mkdir(parents=True)
        (job / ".analysis_images").mkdir()
        (job / "images" / "01.jpg").write_bytes(b"original")
        (job / ".analysis_images" / "overview.jpg").write_bytes(b"overview")
        (job / "car_info.md").write_text("# Car\n\n**Source:** https://example.test/car\n", encoding="utf-8")
        (job / "raw_data.json").write_text(json.dumps({"source_url": "https://example.test/car"}), encoding="utf-8")
        listing_facts = {"year": "2015", "vin": "TESTVIN", "service_history": "full"}
        component_identity = {
            "schema_version": 1,
            "identification_status": "PROBABLE",
            "generation": {"name": "Test generation", "resolution": "PROBABLE"},
            "engine": {"code": "TEST-ENGINE", "resolution": "PROBABLE"},
            "transmission": {"code": "TEST-GEARBOX", "resolution": "PROBABLE"},
            "drivetrain": {"type": "FWD", "resolution": "PROBABLE"},
        }
        (job / "listing_facts.json").write_text(json.dumps(listing_facts), encoding="utf-8")
        (job / "component_identity_research.md").write_text("# Identity grounding\n", encoding="utf-8")
        (job / "component_identity.json").write_text(json.dumps(component_identity), encoding="utf-8")
        (job / "reliability_research.md").write_text("# Reliability\n", encoding="utf-8")
        (job / "market_research.md").write_text("# Market\n", encoding="utf-8")
        (job / "market_research_sk_cz.md").write_text("{}", encoding="utf-8")
        (job / "market_research_mobile_de.md").write_text("{}", encoding="utf-8")
        (job / "market_research_otomoto_pl.md").write_text("{}", encoding="utf-8")
        (job / "market_research_autoscout.md").write_text("{}", encoding="utf-8")
        (job / "market_search_results.json").write_text(
            json.dumps({"schema_version": 1, "passes": [], "candidates": [], "summary": {}}),
            encoding="utf-8",
        )
        (job / "grok_research.json").write_text(json.dumps({"listing_facts": listing_facts, "component_identity": component_identity, "vin_check": {"vin_present": True, "format_check": "ok"}}), encoding="utf-8")
        (job / "gemini_vision.json").write_text(json.dumps({"photos_provided": True, "photo_limitations": []}), encoding="utf-8")
        (job / "analysis_result.md").write_text("# Hidden verdict", encoding="utf-8")
        (job / "analysis_result_raw.md").write_text("# Raw hidden verdict", encoding="utf-8")
        (job / "risk_score.json").write_text(json.dumps({"allowed_final_verdict": "hidden"}), encoding="utf-8")
        (job / "analysis_request.md").write_text("legacy prompt", encoding="utf-8")
        (job / "analysis_diagnostics.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "risk_scorer_v2_active": True,
                    "build_commit": "test-commit",
                    "analysis_profile": "quality_optimized",
                    "models": {"vision": "test-model"},
                    "phases": {
                        "text_research": {
                            "status": "completed",
                            "provider_schema_valid": True,
                        },
                        "vision": {
                            "status": "completed",
                            "parse_error": False,
                        },
                        "risk_scoring": {
                            "status": "completed",
                            "research_delivery_gate": {
                                "section_counts": {
                                    "web_research_findings": 1,
                                    "technical_risks": 1,
                                    "expected_costs": 1,
                                }
                            },
                        },
                        "final_synthesis": {"status": "completed"},
                    },
                    "validation": {"warning_count": 0, "warning_types": []},
                }
            ),
            encoding="utf-8",
        )
        (job / "ai_usage_summary.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "call_count": 6,
                    "successful_calls": 5,
                    "failed_calls": 1,
                    "retry_count": 1,
                    "recovery_count": 0,
                    "grounding_call_count": 2,
                    "duration_ms": 120000,
                    "actual_usage_coverage": {"total": 1.0},
                    "usage_by_phase": {
                        "text_research": {
                            "input_tokens": 2000,
                            "visible_output_tokens": 1000,
                            "thinking_tokens": 0,
                            "cached_input_tokens": 0,
                            "total_tokens": 3000,
                            "actual_usage": {"total": 3000},
                        },
                        "final_synthesis": {
                            "input_tokens": 3000,
                            "visible_output_tokens": 1500,
                            "thinking_tokens": 0,
                            "cached_input_tokens": 0,
                            "total_tokens": 4500,
                            "actual_usage": {"total": 4500},
                        },
                    },
                    "usage_by_model": {
                        "test-model": {
                            "calls": 6,
                            "estimated_cost": 0.12,
                        }
                    },
                    "estimated_cost": 0.12,
                    "cost_currency": "EUR",
                }
            ),
            encoding="utf-8",
        )
        (job / "vision_provider_attempts.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "attempt_count": 2,
                    "recovery_attempted": True,
                    "provider_events": [
                        {"model": "test-model", "finish_reason": "MAX_TOKENS"}
                    ],
                    "attempts": [],
                }
            ),
            encoding="utf-8",
        )
        return job

    def _login(self):
        response = self.client.post("/admin/login", data={"token": "secret-token"})
        self.assertEqual(response.status_code, 302)

    def test_dashboard_and_diagnostics_require_admin_session(self):
        dashboard = self.client.get("/token-dashboard.html")
        telemetry = self.client.get("/api/token-usage")
        self.assertEqual(dashboard.status_code, 302)
        self.assertIn("/admin/login", dashboard.headers["Location"])
        self.assertEqual(telemetry.status_code, 401)

        self._login()
        dashboard = self.client.get("/token-dashboard.html")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b"Download debugging bundle", dashboard.data)
        self.assertIn(b"fetch('/api/admin/listings'", dashboard.data)
        self.assertNotIn(b"fetch('/api/listings')", dashboard.data)
        self.assertEqual(dashboard.headers["Cache-Control"], "no-store, max-age=0")
        self.assertEqual(dashboard.headers["Pragma"], "no-cache")
        dashboard.close()
        self.assertEqual(self.client.get("/api/token-usage").status_code, 200)

    def test_dashboard_admin_artifact_endpoints_work_in_demo_mode(self):
        self._job("dashboard-case")
        self.assertEqual(self.client.get("/api/admin/listings").status_code, 401)
        self._login()

        listings = self.client.get("/api/admin/listings")
        artifacts = self.client.get(
            "/api/admin/listings/dashboard-case/artifacts"
        )
        car_info = self.client.get(
            "/api/admin/listings/dashboard-case/artifacts/car_info.md?raw=1"
        )

        self.assertEqual(listings.status_code, 200)
        self.assertIn("dashboard-case", {item["slug"] for item in listings.get_json()})
        self.assertEqual(artifacts.status_code, 200)
        self.assertIn(
            "analysis_result.md",
            {item["filename"] for item in artifacts.get_json()["artifacts"]},
        )
        self.assertEqual(car_info.status_code, 200)
        self.assertIn(b"# Car", car_info.data)

    def test_development_secret_cannot_enable_admin_api(self):
        web_server.app.config["SECRET_KEY"] = "dev-demo-secret-change-me"
        fresh_client = web_server.app.test_client()
        login = fresh_client.post("/admin/login", data={"token": "secret-token"})
        telemetry = fresh_client.get("/api/token-usage")
        self.assertEqual(login.status_code, 200)
        self.assertEqual(telemetry.status_code, 503)

    def test_calibration_bundle_contains_evidence_and_excludes_verdicts(self):
        self._job()
        self._login()
        response = self.client.get("/api/admin/calibration-bundles/case-one")
        self.assertEqual(response.status_code, 200)
        archive_path = Path(self.temp.name) / "download.zip"
        archive_path.write_bytes(response.data)
        response.close()
        with zipfile.ZipFile(archive_path) as archive:
            names = set(archive.namelist())
            self.assertIn("manifest.json", names)
            self.assertIn("expert_label.json", names)
            self.assertIn("images/01.jpg", names)
            self.assertIn("analysis_images/overview.jpg", names)
            self.assertIn("listing_facts.json", names)
            self.assertIn("component_identity_research.md", names)
            self.assertIn("component_identity.json", names)
            self.assertIn("reliability_research.md", names)
            self.assertIn("market_research.md", names)
            self.assertIn("market_search_results.json", names)
            self.assertIn("analysis_diagnostics.json", names)
            self.assertIn("ai_usage_summary.json", names)
            self.assertIn("validation_warnings.json", names)
            self.assertNotIn("vision_provider_attempts.json", names)
            self.assertNotIn("text_research_provider_attempts.json", names)
            self.assertIn("reproducibility/risk_policy_v2.json", names)
            self.assertIn(
                "reproducibility/prompts/grok_final_synthesis_system.md", names
            )
            self.assertNotIn("risk_score.json", names)
            self.assertNotIn("analysis_result.md", names)
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["bundle_schema_version"], 2)
            self.assertEqual(manifest["bundle_type"], "calibration")
            self.assertEqual(manifest["build_commit"], "test-commit")

    def test_debugging_bundle_contains_complete_outputs_and_no_expert_label(self):
        job = self._job("debug-case")
        (job / "text_research_provider_attempts.json").write_text("{}", encoding="utf-8")
        (job / "ai_usage_summary.json").write_text("{}", encoding="utf-8")

        bundle = create_debugging_bundle(job, "debug-case")
        self.addCleanup(bundle.unlink, missing_ok=True)

        with zipfile.ZipFile(bundle) as archive:
            names = set(archive.namelist())
            self.assertIn("risk_score.json", names)
            self.assertIn("analysis_result_raw.md", names)
            self.assertIn("analysis_result.md", names)
            self.assertIn("analysis_request.md", names)
            self.assertIn("analysis_diagnostics.json", names)
            self.assertIn("validation_warnings.json", names)
            self.assertIn("vision_provider_attempts.json", names)
            self.assertIn("text_research_provider_attempts.json", names)
            self.assertIn("ai_usage_summary.json", names)
            self.assertIn("market_research_mobile_de.md", names)
            self.assertNotIn("expert_label.json", names)
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["bundle_type"], "debugging")

    def test_debugging_bundle_endpoint_requires_admin_and_streams_zip(self):
        self._job("debug-route")
        self.assertEqual(
            self.client.get("/api/admin/debugging-bundles/debug-route").status_code,
            401,
        )
        self._login()

        response = self.client.get("/api/admin/debugging-bundles/debug-route")

        self.assertEqual(response.status_code, 200)
        archive_path = Path(self.temp.name) / "debug-route.zip"
        archive_path.write_bytes(response.data)
        response.close()
        with zipfile.ZipFile(archive_path) as archive:
            self.assertIn("risk_score.json", archive.namelist())

    def test_failed_analysis_attempt_can_download_debugging_bundle(self):
        job = self._job("failed-debug-route")
        (job / "analysis_result.md").unlink()
        (job / "analysis_result_raw.md").unlink()
        (job / "risk_score.json").unlink()
        (job / "analysis_diagnostics.json").write_text(
            json.dumps({
                "schema_version": 1,
                "delivery": {
                    "status": "RETRY_REQUIRED",
                    "chargeable": False,
                    "reason": "text_research_unavailable",
                },
            }),
            encoding="utf-8",
        )
        self._login()

        response = self.client.get(
            "/api/admin/debugging-bundles/failed-debug-route"
        )

        self.assertEqual(response.status_code, 200)
        archive_path = Path(self.temp.name) / "failed-debug-route.zip"
        archive_path.write_bytes(response.data)
        response.close()
        with zipfile.ZipFile(archive_path) as archive:
            names = set(archive.namelist())
            self.assertIn("analysis_diagnostics.json", names)
            self.assertNotIn("analysis_result.md", names)
            diagnostics = json.loads(archive.read("analysis_diagnostics.json"))
            self.assertEqual(diagnostics["delivery"]["status"], "RETRY_REQUIRED")

    def test_manifest_metadata_parses_fenced_research_and_car_info_fallbacks(self):
        job = self._job("metadata-case")
        (job / "raw_data.json").write_text(json.dumps({"yearValue": ""}), encoding="utf-8")
        (job / "car_info.md").write_text(
            "# Kia\n\n**Source:** https://example.test/kia\n"
            "\n- **Year:** 2013\n- **Mileage:** 118 510 km\n",
            encoding="utf-8",
        )
        (job / "grok_research.json").write_text(
            "```json\n" + json.dumps({"listing_facts": {"year": "2013", "mileage": "118510 km"}}) + "\n```",
            encoding="utf-8",
        )

        metadata = _manifest_metadata(job, "metadata-case")

        self.assertEqual(metadata["source_url"], "https://example.test/kia")
        self.assertEqual(metadata["vehicle_year"], "2013")
        self.assertEqual(metadata["vehicle_mileage"], "118510 km")

    def test_export_temporary_archive_is_removed_when_response_closes(self):
        self._job("cleanup-case")
        self._login()
        archive = Path(self.temp.name) / "temporary-export.zip"
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("manifest.json", "{}")
        with patch.object(web_server, "create_calibration_bundle", return_value=archive):
            response = self.client.get("/api/admin/calibration-bundles/cleanup-case")
            self.assertEqual(response.status_code, 200)
            _ = response.data
            response.close()
        self.assertFalse(archive.exists())

    def test_bundle_import_label_validation_and_offline_evaluation(self):
        job = self._job("offline-case")
        bundle = create_calibration_bundle(job, "offline-case")
        dataset = Path(self.temp.name) / "dataset"
        case = safe_extract_bundle(bundle, dataset)
        bundle.unlink(missing_ok=True)
        label_path = case / "expert_label.json"
        label = json.loads(label_path.read_text(encoding="utf-8"))
        label.update({
            "expected_component_identity": {
                "generation": "Test generation",
                "engine_code": "TEST-ENGINE",
                "transmission_code": "TEST-GEARBOX",
                "drivetrain": "FWD",
                "identity_confidence": "HIGH",
                "verification_source": "reviewed listing evidence",
            },
            "expected_status": "WORTH_INSPECTING",
            "proceed_to_inspection": True,
            "reviewer_confidence": "HIGH",
            "reviewer_role": "mechanic",
            "dataset_split": "holdout",
        })
        label_path.write_text(json.dumps(label, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(validate_label(case), [])
        result = evaluate_dataset(dataset, split="holdout")
        self.assertEqual(result["case_count"], 1)
        self.assertEqual(result["metrics"]["exact_agreement"], 1.0)
        self.assertEqual(result["component_identity"]["engine_code_exact_agreement"], 1.0)
        self.assertEqual(result["component_identity"]["false_verified_count"], 0)
        self.assertEqual(result["operational"]["telemetry_case_count"], 1)
        self.assertEqual(result["operational"]["call_count"]["median"], 6)
        self.assertEqual(result["operational"]["estimated_cost"]["median"], 0.12)
        self.assertEqual(result["operational"]["schema_valid_case_count"], 1)
        self.assertEqual(result["operational"]["research_complete_case_count"], 1)
        self.assertEqual(result["operational"]["by_model"]["test-model"]["calls"], 6)

    def test_legacy_visible_verdict_label_remains_importable(self):
        job = self._job("legacy-label-case")
        bundle = create_calibration_bundle(job, "legacy-label-case")
        dataset = Path(self.temp.name) / "legacy-dataset"
        case = safe_extract_bundle(bundle, dataset)
        bundle.unlink(missing_ok=True)
        label_path = case / "expert_label.json"
        label = json.loads(label_path.read_text(encoding="utf-8"))
        label.pop("expected_status", None)
        label.update({
            "expected_verdict": "🟠 ZVÁŽIŤ",
            "proceed_to_inspection": True,
            "reviewer_confidence": "MEDIUM",
            "reviewer_role": "legacy reviewer",
            "dataset_split": "tuning",
        })
        label_path.write_text(json.dumps(label, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(validate_label(case), [])

    def test_phase_four_evaluation_compares_profile_costs(self):
        dataset = Path(self.temp.name) / "profile-dataset"
        for slug, profile, cost in (
            ("paired-legacy", "legacy", 0.20),
            ("paired-optimized", "cost_optimized", 0.10),
        ):
            job = self._job(slug)
            diagnostics_path = job / "analysis_diagnostics.json"
            diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
            diagnostics["analysis_profile"] = profile
            diagnostics_path.write_text(json.dumps(diagnostics), encoding="utf-8")
            usage_path = job / "ai_usage_summary.json"
            usage = json.loads(usage_path.read_text(encoding="utf-8"))
            usage["estimated_cost"] = cost
            usage["usage_by_model"]["test-model"]["estimated_cost"] = cost
            usage_path.write_text(json.dumps(usage), encoding="utf-8")
            bundle = create_calibration_bundle(job, slug)
            case = safe_extract_bundle(bundle, dataset)
            bundle.unlink(missing_ok=True)
            label_path = case / "expert_label.json"
            label = json.loads(label_path.read_text(encoding="utf-8"))
            label.update({
                "comparison_group": "same-car-run",
                "expected_status": "WORTH_INSPECTING",
                "proceed_to_inspection": True,
                "reviewer_confidence": "HIGH",
                "reviewer_role": "mechanic",
                "dataset_split": "tuning",
            })
            label_path.write_text(json.dumps(label), encoding="utf-8")

        result = evaluate_dataset(dataset, split="tuning")

        self.assertEqual(result["by_profile"]["legacy"]["estimated_cost"]["median"], 0.2)
        self.assertEqual(
            result["by_profile"]["cost_optimized"]["estimated_cost"]["median"],
            0.1,
        )
        self.assertEqual(len(result["profile_comparisons"]), 1)
        self.assertEqual(
            result["profile_comparisons"][0]["cost_reduction_percent"],
            50.0,
        )
        self.assertTrue(result["quality_gates"]["cost_reduction_at_least_35_percent"])

    def test_pipeline_config_activates_v2_and_failure_falls_back_to_yellow(self):
        research = json.dumps({"listing_facts": {"vin": "TESTVIN", "service_history": "full"}, "vin_check": {"vin_present": True, "format_check": "ok"}})
        vision = json.dumps({"photos_provided": True, "photo_limitations": []})
        with web_server.app.test_request_context("/"):
            previous = web_server.app.config.get("RISK_SCORER_V2_ACTIVE")
            web_server.app.config["RISK_SCORER_V2_ACTIVE"] = True
            try:
                active = web_server._pipeline_calculate_risk_score(research, vision, "")
                self.assertEqual(active["schema_version"], 2)
                with patch("risk_scorer_v2.calculate_risk_score_v2", side_effect=RuntimeError("boom")):
                    fallback = web_server._pipeline_calculate_risk_score(research, vision, "")
                self.assertEqual(fallback["allowed_final_verdict"], "🟡 NAJPRV PREVERIŤ")
                self.assertEqual(fallback["evidence_quality"], "LOW")
            finally:
                web_server.app.config["RISK_SCORER_V2_ACTIVE"] = previous


if __name__ == "__main__":
    unittest.main()
