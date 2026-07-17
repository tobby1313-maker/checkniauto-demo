import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from scrapper_demo.services import image_service


class ImageServiceTest(unittest.TestCase):
    @staticmethod
    def _pattern_image(index, size=(120, 80)):
        digest = hashlib.sha256(str(index).encode("ascii")).digest()
        pixels = []
        for value in digest[:8]:
            pixels.extend(255 if value & (1 << bit) else 0 for bit in range(8))
        image = Image.new("L", (8, 8))
        image.putdata(pixels)
        return image.resize(size, Image.Resampling.NEAREST).convert("RGB")

    def test_missing_image_directory_returns_none_coverage(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_data, metadata = image_service.prepare_llm_images(temp_dir)

        self.assertEqual(image_data, [])
        self.assertEqual(metadata["coverage_mode"], "none")
        self.assertEqual(metadata["original_count"], 0)
        self.assertFalse(metadata["full_gallery_included"])

    def test_representative_selection_includes_gallery_boundaries(self):
        indices = image_service.select_representative_indices(42, limit=20)

        self.assertEqual(len(indices), 20)
        self.assertEqual(indices[0], 0)
        self.assertEqual(indices[-1], 41)
        self.assertEqual(indices, sorted(set(indices)))

    def test_small_gallery_creates_detail_collage_without_modifying_originals(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            listing_dir = Path(temp_dir)
            images_dir = listing_dir / "images"
            images_dir.mkdir()
            original_bytes = {}
            rectangles = (
                (0, 0, 59, 79),
                (60, 0, 119, 79),
                (0, 0, 119, 39),
                (0, 40, 119, 79),
            )
            for index, rectangle in enumerate(rectangles, start=1):
                path = images_dir / f"{index:02d}.jpg"
                image = Image.new("RGB", (120, 80), "white")
                ImageDraw.Draw(image).rectangle(rectangle, fill="black")
                image.save(path, format="JPEG")
                original_bytes[path.name] = path.read_bytes()

            image_data, metadata = image_service.prepare_llm_images(listing_dir)

            self.assertEqual(len(image_data), 1)
            self.assertEqual(metadata["coverage_mode"], "detail_all")
            self.assertEqual(metadata["selected_count"], 4)
            self.assertEqual(metadata["unique_count"], 4)
            self.assertEqual(metadata["duplicate_count"], 0)
            self.assertEqual(metadata["attachment_count"], 1)
            self.assertTrue(metadata["full_gallery_included"])
            self.assertTrue((listing_dir / ".analysis_images" / "collage_01_llm.jpg").is_file())
            self.assertEqual(
                {path.name: path.read_bytes() for path in images_dir.iterdir()},
                original_bytes,
            )

    def test_duplicate_photos_are_filtered_from_detail_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            listing_dir = Path(temp_dir)
            images_dir = listing_dir / "images"
            images_dir.mkdir()
            for index in range(24):
                Image.new("RGB", (100, 100), "navy").save(
                    images_dir / f"{index:02d}.jpg",
                    format="JPEG",
                )

            _image_data, metadata = image_service.prepare_llm_images(listing_dir)

        self.assertEqual(metadata["selected_count"], 1)
        self.assertEqual(metadata["unique_count"], 1)
        self.assertEqual(metadata["duplicate_count"], 23)
        self.assertEqual(metadata["coverage_mode"], "detail_all")
        self.assertEqual(metadata["selection_reason"], "all_unique_photos_in_detail_collages_after_perceptual_deduplication")

    def test_attachment_cap_limits_small_gallery_payload_and_reports_reason(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            listing_dir = Path(temp_dir)
            images_dir = listing_dir / "images"
            images_dir.mkdir()
            for index in range(8):
                self._pattern_image(index).save(images_dir / f"{index:02d}.jpg", format="JPEG")

            with patch.object(image_service, "AI_MAX_VISION_ATTACHMENTS", 1):
                image_data, metadata = image_service.prepare_llm_images(listing_dir)

        self.assertEqual(len(image_data), 1)
        self.assertEqual(metadata["attachment_count"], 1)
        self.assertEqual(metadata["attachment_limit"], 1)
        self.assertEqual(metadata["selected_count"], 4)
        self.assertEqual(metadata["unique_count"], 8)
        self.assertEqual(metadata["coverage_mode"], "detail_limited")
        self.assertEqual(metadata["selection_reason"], "representative_unique_photos_selected_within_attachment_limit")


if __name__ == "__main__":
    unittest.main()
