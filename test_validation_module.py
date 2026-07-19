import json
import tempfile
import unittest
from pathlib import Path

from scrapper_demo import validation


class ValidationModuleTest(unittest.TestCase):
    def test_schema_validation_loads_project_schema_without_flask(self):
        warnings = validation.soft_validate_json_contract(
            "risk_score.json",
            json.dumps({"risk_score": 2}),
            "risk_score.schema.json",
        )

        self.assertEqual(warnings[0]["type"], "schema_required")
        self.assertIn("allowed_final_verdict", warnings[0]["fields"])

    def test_schema_validation_reports_nested_enum_and_type_violations(self):
        payload = {
            "source_role": "vision",
            "photos_provided": True,
            "photo_limitations": [],
            "exterior_observations": [{
                "buyer_impact": "equipment",
                "confidence": "HIGH",
            }],
            "interior_observations": [],
            "dashboard_or_warning_lights": [],
            "visible_red_flags": [],
            "mileage_wear_consistency": {
                "assessment": "better_than_expected",
                "confidence": "Vysoká",
            },
            "visual_verdict": "OK",
            "must_not_infer": [],
        }

        warnings = validation.soft_validate_json_contract(
            "gemini_vision.json",
            json.dumps(payload),
            "gemini_vision.schema.json",
        )

        contract = next(item for item in warnings if item["type"] == "schema_contract")
        self.assertTrue(any("buyer_impact" in error for error in contract["errors"]))
        self.assertTrue(any("mileage_wear_consistency.assessment" in error for error in contract["errors"]))

    def test_markdown_parser_preserves_parentheses_inside_url(self):
        links = validation.markdown_links(
            "[Source](https://example.invalid/article_(detail))"
        )

        self.assertEqual(
            links,
            [("Source", "https://example.invalid/article_(detail)")],
        )

    def test_final_report_validation_is_non_blocking_and_structured(self):
        warnings = validation.soft_validate_final_report(
            "# Report\n\nThe VIN was verified online.",
            "SAFE",
        )

        warning_types = {warning["type"] for warning in warnings}
        self.assertIn("verdict_lock", warning_types)
        self.assertIn("missing_end_marker", warning_types)
        self.assertIn("missing_required_sections", warning_types)
        self.assertIn("forbidden_claim", warning_types)

    def test_warning_persistence_is_atomic_and_logger_is_injected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            logged = []
            path = validation.write_validation_warnings(
                temp_dir,
                [{"artifact": "analysis_result.md", "message": "fixture warning"}],
                log=logged.append,
            )
            payload = json.loads(Path(path).read_text(encoding="utf-8"))

        self.assertEqual(payload["warnings"][0]["message"], "fixture warning")
        self.assertEqual(logged, ["Validation warning: fixture warning"])

    def test_empty_warning_list_still_creates_debug_artifact(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = validation.write_validation_warnings(temp_dir, [])
            payload = json.loads(Path(path).read_text(encoding="utf-8"))

        self.assertEqual(payload["warnings"], [])

    def test_end_marker_is_idempotent_and_placeholder_hosts_are_rejected(self):
        report = validation.ensure_end_analysis_marker("# Report")

        self.assertEqual(
            validation.ensure_end_analysis_marker(report).count("<!-- END_ANALYSIS -->"),
            1,
        )
        self.assertFalse(validation.is_verified_public_url("https://example.com/test"))
        self.assertTrue(validation.is_verified_public_url("https://www.nhtsa.gov/recalls"))


if __name__ == "__main__":
    unittest.main()
