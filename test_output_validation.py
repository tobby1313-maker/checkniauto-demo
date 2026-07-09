import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import web_server


class OutputValidationTest(unittest.TestCase):
    def test_final_report_with_matching_verdict_and_end_marker_has_no_warnings(self):
        warnings = web_server._soft_validate_final_report(
            "# Report\n\n**RISKY**\n\n<!-- END_ANALYSIS -->",
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
            "| Položka | Dôkaz | Istota |\n"
            "|---|---|---|\n"
            "| Servis | Inzerát | Stredná |\n\n"
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

    def test_json_contract_validation_warns_without_failing(self):
        warnings = web_server._soft_validate_json_contract(
            "grok_research.json",
            json.dumps({"source_role": "text_research"}),
            "grok_research.schema.json",
        )

        self.assertIn("schema_required", {warning["type"] for warning in warnings})

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
