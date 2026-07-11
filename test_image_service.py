import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from scrapper_demo.services import image_service


class ImageServiceTest(unittest.TestCase):
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
            for index in range(3):
                Image.new("RGB", (100, 100), "navy").save(
                    images_dir / f"{index:02d}.jpg",
                    format="JPEG",
                )

            _image_data, metadata = image_service.prepare_llm_images(listing_dir)

        self.assertEqual(metadata["selected_count"], 1)
        self.assertEqual(metadata["coverage_mode"], "detail_limited")


if __name__ == "__main__":
    unittest.main()
