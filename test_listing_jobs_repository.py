import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from scrapper_demo.storage import PUBLIC_ARTIFACTS, ListingJobRepository, atomic_write_text


class ListingJobRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name) / "Auta"
        self.repository = ListingJobRepository(self.root)

    def test_root_and_job_paths_are_created_inside_configured_storage(self):
        job_dir = self.repository.job_dir("../../Vehicle Name", create=True)

        self.assertTrue(self.root.is_dir())
        self.assertEqual(job_dir, self.root.resolve() / "vehicle-name")
        self.assertEqual(os.path.commonpath([str(self.root.resolve()), str(job_dir)]), str(self.root.resolve()))

    def test_artifact_and_image_filenames_reject_traversal(self):
        for filename in ("../secret.txt", "..\\secret.txt", "folder/file.jpg", ".."):
            with self.subTest(filename=filename):
                with self.assertRaises(ValueError):
                    self.repository.image_path("vehicle", filename)
                with self.assertRaises(ValueError):
                    self.repository.artifact_path("vehicle", filename)

        with self.assertRaises(ValueError):
            self.repository.artifact_path("vehicle", "not-public.txt", public_only=True)

    def test_atomic_text_and_json_writes_replace_complete_artifacts(self):
        self.repository.write_text("vehicle", "car_info.md", "first")
        self.repository.write_text("vehicle", "car_info.md", "second")
        self.repository.write_json("vehicle", "raw_data.json", {"price": 12000})

        job_dir = self.repository.job_dir("vehicle")
        self.assertEqual((job_dir / "car_info.md").read_text(encoding="utf-8"), "second")
        self.assertEqual(json.loads((job_dir / "raw_data.json").read_text(encoding="utf-8")), {"price": 12000})
        self.assertEqual(list(job_dir.glob("*.tmp")), [])

    def test_failed_atomic_replace_cleans_temporary_file(self):
        destination = self.root / "vehicle" / "car_info.md"

        with patch("scrapper_demo.storage.listing_jobs.os.replace", side_effect=OSError("replace failed")):
            with self.assertRaisesRegex(OSError, "replace failed"):
                atomic_write_text(destination, "incomplete")

        self.assertFalse(destination.exists())
        self.assertEqual(list(destination.parent.glob("*.tmp")), [])

    def test_available_artifacts_preserve_public_compatibility_order(self):
        self.repository.write_text("vehicle", "analysis_result.md", "report")
        self.repository.write_json("vehicle", "raw_data.json", {"title": "Vehicle"})
        self.repository.write_text("vehicle", "car_info.md", "# Vehicle")

        available = [path.name for path in self.repository.available_artifacts("vehicle")]

        self.assertEqual(available, ["raw_data.json", "car_info.md", "analysis_result.md"])
        self.assertEqual(PUBLIC_ARTIFACTS[0:2], ("raw_data.json", "car_info.md"))

    def test_completed_job_discovery_ignores_files_and_partial_jobs(self):
        self.repository.write_text("complete", "analysis_result.md", "report")
        self.repository.write_text("partial", "car_info.md", "# Partial")
        (self.root / "token_usage.json").write_text("{}", encoding="utf-8")

        completed = list(
            self.repository.iter_job_directories(require_artifact="analysis_result.md")
        )

        self.assertEqual([(slug, path.name) for slug, path in completed], [("complete", "complete")])

    def test_unique_slug_does_not_overwrite_existing_job(self):
        self.repository.job_dir("vehicle", create=True)
        self.repository.job_dir("vehicle-20260711-120000", create=True)

        slug = self.repository.unique_slug("Vehicle", timestamp="20260711-120000")

        self.assertEqual(slug, "vehicle-20260711-120000-2")

    def test_cleanup_removes_only_expired_job_directories(self):
        old_job = self.repository.job_dir("old", create=True)
        current_job = self.repository.job_dir("current", create=True)
        now = time.time()
        os.utime(old_job, (now - 7200, now - 7200))

        removed = self.repository.cleanup_expired(60, now=now)

        self.assertEqual(removed, ["old"])
        self.assertFalse(old_job.exists())
        self.assertTrue(current_job.exists())


if __name__ == "__main__":
    unittest.main()
