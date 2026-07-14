import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main
from scrapper_demo.storage import ListingJobRepository


class ListingStorageIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repository = ListingJobRepository(Path(self.temp_dir.name) / "Auta")

    def test_main_helpers_preserve_job_artifact_names(self):
        slug = "integration-vehicle"
        job_dir = self.repository.job_dir(slug, create=True)
        self.repository.write_text(
            slug,
            "car_info.md",
            "# Integration Vehicle\n\n"
            "**Source:** https://example.com/integration\n\n"
            "## Specifications\n"
            "- **VIN:** 1HGCM82633A004352\n"
            "- **Mileage:** 100 000 km\n",
        )
        self.repository.write_json(slug, "raw_data.json", {"vin": "1HGCM82633A004352"})

        main.ensure_scraped_timestamp(str(job_dir))
        with patch.object(main, "safe_print"):
            main._run_vin_decoding(str(job_dir))
        request_path = main.build_analysis_request(
            str(Path(__file__).parent),
            str(job_dir),
            "https://example.com/integration",
        )

        self.assertEqual(Path(request_path).name, "analysis_request.md")
        self.assertTrue(self.repository.artifact_path(slug, "analysis_request.md").is_file())
        decoded = json.loads(
            self.repository.artifact_path(slug, "vin_decoded.json").read_text(encoding="utf-8")
        )
        self.assertEqual(decoded["vin"], "1HGCM82633A004352")
        car_info = self.repository.read_text(slug, "car_info.md")
        self.assertIn("**Scraped:**", car_info)

    def test_repository_reads_preexisting_non_atomic_artifacts(self):
        slug = "legacy-job"
        job_dir = self.repository.job_dir(slug, create=True)
        (job_dir / "car_info.md").write_text("# Legacy Vehicle", encoding="utf-8")
        (job_dir / "analysis_result.md").write_text("# Legacy Report", encoding="utf-8")

        completed = list(
            self.repository.iter_job_directories(require_artifact="analysis_result.md")
        )

        self.assertEqual(completed[0][0], slug)
        self.assertEqual(self.repository.read_text(slug, "car_info.md"), "# Legacy Vehicle")

    def test_missing_vin_sentinel_does_not_create_invalid_decode_artifact(self):
        slug = "missing-vin"
        job_dir = self.repository.job_dir(slug, create=True)
        self.repository.write_text(slug, "car_info.md", "# Vehicle without VIN")
        self.repository.write_json(slug, "raw_data.json", {"vin": "N/A"})
        self.repository.write_json(slug, "vin_decoded.json", {"vin": "N/A", "valid": False})

        main._run_vin_decoding(str(job_dir))

        self.assertFalse(self.repository.artifact_path(slug, "vin_decoded.json").exists())


if __name__ == "__main__":
    unittest.main()
