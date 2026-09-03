import tempfile
import unittest
from pathlib import Path

from PIL import Image

import v2_entry


class V2GalleryRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = v2_entry.app.test_client()

    def test_index_loads_gallery_assets(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"/v2-static/gallery.css", response.data)
        self.assertIn(b"/v2-static/gallery.js", response.data)

    def test_serves_only_photos_declared_by_completed_report(self):
        job = v2_entry.v2_app._create_job("manual", "", "sk")
        job_id = job["id"]
        gallery_dir = v2_entry.v2_app._job_dir(job_id) / "gallery"
        gallery_dir.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (320, 240), (80, 110, 150)).save(
            gallery_dir / "photo-001.jpg",
            format="JPEG",
        )
        Image.new("RGB", (320, 240), (180, 80, 80)).save(
            gallery_dir / "photo-002.jpg",
            format="JPEG",
        )

        report = {
            "photo_analysis": {
                "gallery_total": 1,
                "gallery": [
                    {
                        "id": "photo-001",
                        "label": "Foto 01",
                        "review_level": "overview",
                    }
                ],
            }
        }
        v2_entry.v2_app._update_job(
            job_id,
            status="done",
            stage="complete",
            progress=100,
            report=report,
        )

        allowed = self.client.get(f"/api/v2/jobs/{job_id}/photos/photo-001")
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.mimetype, "image/jpeg")
        self.assertIn("private", allowed.headers.get("Cache-Control", ""))
        self.assertIn("inline", allowed.headers.get("Content-Disposition", ""))

        unlisted = self.client.get(f"/api/v2/jobs/{job_id}/photos/photo-002")
        invalid = self.client.get(f"/api/v2/jobs/{job_id}/photos/../../job.json")
        self.assertEqual(unlisted.status_code, 404)
        self.assertIn(invalid.status_code, {404, 308})

    def test_does_not_serve_photo_before_report_completion(self):
        job = v2_entry.v2_app._create_job("manual", "", "sk")
        job_id = job["id"]
        gallery_dir = v2_entry.v2_app._job_dir(job_id) / "gallery"
        gallery_dir.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (100, 100), "white").save(gallery_dir / "photo-001.jpg")

        response = self.client.get(f"/api/v2/jobs/{job_id}/photos/photo-001")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
