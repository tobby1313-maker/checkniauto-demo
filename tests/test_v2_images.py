import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from v2_images import (
    mark_detail_reviewed,
    prepare_detail_images,
    prepare_gallery,
    public_gallery,
    reset_review_levels,
)


def _scene(path: Path, color: tuple[int, int, int], offset: int = 0, quality: int = 92) -> None:
    image = Image.new("RGB", (1200, 800), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((80 + offset, 110, 760 + offset, 610), fill=color)
    draw.line((0, 760 - offset // 3, 1200, 100 + offset // 4), fill="black", width=12)
    image.save(path, quality=quality)


class GalleryCoverageTests(unittest.TestCase):
    def test_keeps_every_photo_and_groups_only_near_duplicates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            images = root / "listing" / "images"
            images.mkdir(parents=True)

            _scene(images / "01-front.jpg", (195, 35, 35), 0, 96)
            with Image.open(images / "01-front.jpg") as original:
                original.resize((1000, 667)).save(images / "02-front-copy.jpg", quality=74)
            _scene(images / "03-rear.jpg", (195, 35, 35), 330, 94)
            _scene(images / "04-interior.jpg", (35, 80, 185), 90, 94)
            _scene(images / "05-dashboard.jpg", (45, 155, 80), 180, 94)
            _scene(images / "06-engine.jpg", (180, 150, 35), 250, 94)

            package = prepare_gallery(root / "listing", root / "job")
            manifest = public_gallery(package)

            self.assertEqual(manifest["gallery_total"], 6)
            self.assertEqual(len(manifest["gallery"]), 6)
            self.assertEqual(manifest["gallery_unique"], 5)
            self.assertEqual(manifest["duplicate_count"], 1)
            self.assertEqual(manifest["visual_coverage_percent"], 100)
            self.assertEqual(manifest["overview_sheet_count"], 2)

            copy = next(item for item in manifest["gallery"] if item["label"] == "Foto 02")
            rear = next(item for item in manifest["gallery"] if item["label"] == "Foto 03")
            self.assertEqual(copy["duplicate_of"], "Foto 01")
            self.assertNotEqual(rear["cluster_id"], copy["cluster_id"])

    def test_detail_pass_uses_flagged_photos_and_spread_sample(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            images = root / "listing" / "images"
            images.mkdir(parents=True)
            for index in range(1, 10):
                _scene(
                    images / f"{index:02d}.jpg",
                    ((index * 23) % 255, (index * 47) % 255, (index * 71) % 255),
                    index * 34,
                )

            package = prepare_gallery(root / "listing", root / "job")
            details = prepare_detail_images(package, ["Foto 07"])
            labels = [item["label"] for item in details]

            self.assertIn("Foto 07", labels)
            self.assertGreaterEqual(len(labels), 4)
            self.assertEqual(len(labels), len(set(labels)))

            mark_detail_reviewed(package, labels)
            manifest = public_gallery(package)
            self.assertEqual(manifest["detail_count"], len(labels))
            self.assertEqual(manifest["visual_coverage_percent"], 100)

            reset_review_levels(package)
            reset = public_gallery(package)
            self.assertEqual(reset["detail_count"], 0)
            self.assertEqual(reset["visual_coverage_percent"], 0)


if __name__ == "__main__":
    unittest.main()
