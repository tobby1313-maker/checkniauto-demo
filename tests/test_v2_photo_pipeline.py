import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from v2_images import prepare_gallery
from v2_photo_pipeline import analyze_photos


def _write_photo(path: Path, color: tuple[int, int, int], offset: int) -> None:
    image = Image.new("RGB", (1000, 700), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((80 + offset, 100, 650 + offset, 560), fill=color)
    draw.line((20, 620 - offset // 4, 980, 90 + offset // 5), fill="black", width=9)
    image.save(path, quality=90)


class TwoTierPhotoPipelineTests(unittest.TestCase):
    def test_all_gallery_photos_remain_but_detail_is_selective(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image_dir = root / "listing" / "images"
            image_dir.mkdir(parents=True)
            for index in range(1, 13):
                _write_photo(
                    image_dir / f"{index:02d}.jpg",
                    ((index * 29) % 255, (index * 53) % 255, (index * 79) % 255),
                    index * 19,
                )

            package = prepare_gallery(root / "listing", root / "job")
            overview = {
                "available": True,
                "summary": "Prehľad galérie je použiteľný.",
                "findings": [
                    {
                        "title": "Možné poškodenie disku",
                        "severity": "risk",
                        "confidence": "medium",
                        "photo_refs": ["Foto 07"],
                        "observation": "Na disku je viditeľná nepravidelnosť.",
                        "interpretation": "Môže ísť o oder alebo odlesk.",
                        "action": "Skontrolovať disk zblízka.",
                        "cost_min_eur": 0,
                        "cost_max_eur": 250,
                    }
                ],
                "positive_signals": [],
                "coverage_gaps": [],
                "detail_candidates": [
                    {
                        "photo_ref": "Foto 07",
                        "priority": "high",
                        "reason": "Jemný detail na disku.",
                    }
                ],
                "limitations": [],
            }
            detail = {
                "available": True,
                "images_reviewed": 4,
                "summary": "Detailná kontrola potvrdila viditeľný oder.",
                "findings": [
                    {
                        "title": "Oder disku",
                        "severity": "watch",
                        "confidence": "high",
                        "photo_refs": ["Foto 07"],
                        "observation": "Na hrane disku je viditeľný oder.",
                        "interpretation": "Ide o kozmetické poškodenie.",
                        "action": "Overiť, či pneumatika nebola poškodená nárazom.",
                        "cost_min_eur": 60,
                        "cost_max_eur": 160,
                    }
                ],
                "positive_signals": [],
                "coverage_gaps": [],
                "limitations": [],
            }

            with patch(
                "v2_photo_pipeline.call_generate_content_json",
                side_effect=[overview, detail],
            ) as mocked:
                result = analyze_photos(
                    {"title": "Test", "year": 2020, "mileage_km": 90000},
                    package,
                    "sk",
                )

            self.assertEqual(mocked.call_count, 2)
            self.assertEqual(result["gallery_total"], 12)
            self.assertEqual(len(result["gallery"]), 12)
            self.assertEqual(result["visual_coverage_percent"], 100)
            self.assertGreaterEqual(result["detail_count"], 4)
            self.assertLess(result["detail_count"], result["gallery_total"])
            self.assertTrue(
                any(
                    item["label"] == "Foto 07" and item["review_level"] == "detail"
                    for item in result["gallery"]
                )
            )
            self.assertEqual(result["findings"][0]["inspection_level"], "detail")


if __name__ == "__main__":
    unittest.main()
