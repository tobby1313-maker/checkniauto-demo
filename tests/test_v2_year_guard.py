import tempfile
import unittest
from pathlib import Path

from v2_normalize_guard import normalize_listing


class VehicleYearGuardTests(unittest.TestCase):
    def test_scraped_timestamp_is_not_used_as_vehicle_year(self):
        markdown = """# Škoda Octavia 2.0 TDI

**Source:** https://www.autobazar.eu/detail/test/id/
**Scraped:** 2026-09-03 10:00
**Created:** 2025-12-10

## Price
- **Price:** 12 900 EUR
"""
        with tempfile.TemporaryDirectory() as tmp:
            listing_dir = Path(tmp)
            (listing_dir / "car_info.md").write_text(markdown, encoding="utf-8")
            listing = normalize_listing(listing_dir)

        self.assertEqual(listing["year"], 0)

    def test_explicit_vehicle_year_is_preserved(self):
        markdown = """# Škoda Octavia 2.0 TDI

**Scraped:** 2026-09-03 10:00

## Specifications
| Parameter | Value |
|---|---|
| Year | 2019 |
"""
        with tempfile.TemporaryDirectory() as tmp:
            listing_dir = Path(tmp)
            (listing_dir / "car_info.md").write_text(markdown, encoding="utf-8")
            listing = normalize_listing(listing_dir)

        self.assertEqual(listing["year"], 2019)


if __name__ == "__main__":
    unittest.main()
