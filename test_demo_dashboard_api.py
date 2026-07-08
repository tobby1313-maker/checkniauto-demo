import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import web_server


def _write_text(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class DemoDashboardApiTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.original_auta_dir = web_server.AUTA_DIR
        web_server.AUTA_DIR = os.path.join(self.temp_dir.name, "Auta")
        os.makedirs(web_server.AUTA_DIR, exist_ok=True)
        web_server.app.testing = True
        self.client = web_server.app.test_client()
        self.addCleanup(self._restore_auta_dir)

    def _restore_auta_dir(self):
        web_server.AUTA_DIR = self.original_auta_dir

    def _create_listing(
        self,
        slug,
        *,
        title=None,
        scraped_at="2026-07-01 10:00",
        car_info_mtime=None,
        has_analysis=True,
        images=None,
        year="2020",
        mileage="150 000 km",
    ):
        listing_dir = Path(web_server.AUTA_DIR) / slug
        images_dir = listing_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        images = images or ["01_front.jpg", "02_rear.jpg"]

        for filename in images:
            (images_dir / filename).write_bytes(b"fake-image-bytes")

        car_info_lines = [
            f"# {title or slug.replace('-', ' ').title()}",
            "",
            f"**Source:** https://example.com/{slug}",
            "",
        ]
        if scraped_at:
            car_info_lines.extend([
                f"**Scraped:** {scraped_at}",
                "",
            ])
        car_info_lines.extend([
            "## Price",
            "- **Price:** 12 500 EUR",
            "",
            "## Specifications",
            f"- **Year:** {year}",
            f"- **Mileage:** {mileage}",
            "- **VIN:** TESTVIN123456789",
            "",
            "## Photos",
            f"- **Downloaded:** {len(images)}",
            "",
        ])

        car_info_path = listing_dir / "car_info.md"
        _write_text(car_info_path, "\n".join(car_info_lines))

        if has_analysis:
            _write_text(listing_dir / "analysis_result.md", "# Saved analysis\n\nEverything looks fine.")

        if car_info_mtime is not None:
            os.utime(car_info_path, (car_info_mtime, car_info_mtime))

        return listing_dir

    def test_demo_listings_returns_only_saved_analyses(self):
        self._create_listing("with-analysis", has_analysis=True)
        self._create_listing("without-analysis", has_analysis=False)

        response = self.client.get("/api/demo/listings")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual([item["slug"] for item in payload], ["with-analysis"])
        self.assertTrue(payload[0]["has_analysis"])
        self.assertEqual(
            payload[0]["first_image_url"],
            "/api/demo/listings/with-analysis/image/01_front.jpg",
        )

    def test_demo_listings_order_uses_scraped_at_then_mtime_fallback(self):
        fallback_mtime = datetime(2026, 7, 2, 12, 0, 0).timestamp()
        self._create_listing(
            "older-scraped",
            scraped_at="2026-07-02 09:00",
            car_info_mtime=100,
        )
        self._create_listing(
            "mtime-fallback",
            scraped_at="",
            car_info_mtime=fallback_mtime,
        )
        self._create_listing(
            "newer-scraped",
            scraped_at="2026-07-03 09:00",
            car_info_mtime=50,
        )

        response = self.client.get("/api/demo/listings")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(
            [item["slug"] for item in payload],
            ["newer-scraped", "mtime-fallback", "older-scraped"],
        )

    def test_demo_detail_returns_404_for_missing_or_unsaved_listing(self):
        self._create_listing("no-analysis", has_analysis=False)

        missing = self.client.get("/api/demo/listings/missing-slug")
        unsaved = self.client.get("/api/demo/listings/no-analysis")

        self.assertEqual(missing.status_code, 404)
        self.assertEqual(unsaved.status_code, 404)

    def test_demo_detail_returns_saved_analysis_and_ordered_images(self):
        self._create_listing(
            "detail-check",
            images=["01_front.jpg", "02_dash.jpg", "03_rear.jpg"],
        )

        response = self.client.get("/api/demo/listings/detail-check")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["slug"], "detail-check")
        self.assertEqual(
            [image["filename"] for image in payload["images"]],
            ["01_front.jpg", "02_dash.jpg", "03_rear.jpg"],
        )
        self.assertIn("Saved analysis", payload["analysis_content"])
        self.assertEqual(payload["source_url"], "https://example.com/detail-check")

    def test_demo_image_route_rejects_traversal_and_serves_listing_image(self):
        self._create_listing("image-check", has_analysis=True, images=["01_front.jpg"])

        good = self.client.get("/api/demo/listings/image-check/image/01_front.jpg")
        bad = self.client.get("/api/demo/listings/image-check/image/..%5Csecret.jpg")

        self.assertEqual(good.status_code, 200)
        self.assertEqual(good.data, b"fake-image-bytes")
        self.assertEqual(bad.status_code, 400)
        good.close()
        bad.close()

    def test_representative_image_selection_includes_last_photo(self):
        indices = web_server._select_representative_indices(42, limit=20)

        self.assertEqual(len(indices), 20)
        self.assertEqual(indices[-1], 41)


if __name__ == "__main__":
    unittest.main()
