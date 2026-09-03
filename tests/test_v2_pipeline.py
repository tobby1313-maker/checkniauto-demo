import tempfile
import unittest
from pathlib import Path

from v2_pipeline import (
    build_fallback_report,
    calculate_data_quality,
    is_supported_url,
    normalize_listing,
    sanitize_report,
    unavailable_photo,
    unavailable_research,
)


class UrlValidationTests(unittest.TestCase):
    def test_supported_marketplaces(self):
        self.assertTrue(is_supported_url("https://www.autobazar.eu/detail/test/id/"))
        self.assertTrue(is_supported_url("https://auto.bazos.sk/inzerat/123/test.php"))
        self.assertTrue(is_supported_url("https://auto.bazos.cz/inzerat/123/test.php"))

    def test_rejects_ssrf_and_unrelated_hosts(self):
        self.assertFalse(is_supported_url("http://127.0.0.1:5000/secret"))
        self.assertFalse(is_supported_url("https://autobazar.eu.example.com/fake"))
        self.assertFalse(is_supported_url("file:///etc/passwd"))


class NormalizationTests(unittest.TestCase):
    def test_normalizes_markdown_listing(self):
        markdown = """# Škoda Octavia Combi 2.0 TDI DSG

**Source:** https://www.autobazar.eu/detail/test/id/

## Price
- **Current Price:** 12 900 EUR

## Specifications
| Parameter | Value |
|---|---|
| Year | 2019 |
| Mileage | 168 400 km |
| Fuel | Diesel |
| Transmission | Automatická DSG |
| Engine Power | 110 kW |
| VIN | TMBJG7NE0K0123456 |

## Seller Note (Poznamka)
Pravidelne servisované vozidlo, servisná knižka a faktúry. Predný náhon.

## Photos
- **Downloaded:** 12
"""
        with tempfile.TemporaryDirectory() as tmp:
            listing_dir = Path(tmp)
            (listing_dir / "car_info.md").write_text(markdown, encoding="utf-8")
            (listing_dir / "images").mkdir()
            listing = normalize_listing(listing_dir)

        self.assertEqual(listing["price"]["amount"], 12900)
        self.assertEqual(listing["price"]["currency"], "EUR")
        self.assertEqual(listing["year"], 2019)
        self.assertEqual(listing["mileage_km"], 168400)
        self.assertEqual(listing["power_kw"], 110)
        self.assertEqual(listing["vin"], "TMBJG7NE0K0123456")
        self.assertTrue(listing["service_history_claimed"])
        self.assertGreaterEqual(listing["data_quality"]["score"], 70)

    def test_bazos_cz_source_forces_czk_currency(self):
        markdown = """# Škoda Octavia 2018

**Source:** https://auto.bazos.cz/inzerat/123/test.php

## Price
- **Price:** 249 000 EUR
"""
        with tempfile.TemporaryDirectory() as tmp:
            listing_dir = Path(tmp)
            (listing_dir / "car_info.md").write_text(markdown, encoding="utf-8")
            listing = normalize_listing(listing_dir)

        self.assertEqual(listing["price"]["amount"], 249000)
        self.assertEqual(listing["price"]["currency"], "CZK")
        self.assertEqual(listing["source_host"], "auto.bazos.cz")

    def test_quality_flags_critical_missing_fields(self):
        quality = calculate_data_quality(
            {
                "title": "Auto",
                "price": {"amount": 0},
                "year": 0,
                "mileage_km": 0,
                "engine": "",
                "fuel": "",
                "transmission": "",
                "drivetrain": "",
                "vin": "",
                "service_history_claimed": False,
                "description": "",
                "photos_count": 0,
            }
        )
        self.assertIn("VIN", quality["missing_critical"])
        self.assertIn("cena", quality["missing_critical"])
        self.assertLess(quality["score"], 30)


class ReportSafetyTests(unittest.TestCase):
    def test_sanitizer_caps_confidence_and_removes_unverified_market_numbers(self):
        listing = {
            "title": "Test auto",
            "source_url": "",
            "source_host": "",
            "price": {"amount": 10000, "currency": "EUR"},
            "year": 2018,
            "mileage_km": 150000,
            "engine": "",
            "power_kw": 0,
            "fuel": "Diesel",
            "transmission": "",
            "drivetrain": "",
            "vin": "",
            "seller": "",
            "location": "",
            "photos_count": 0,
            "service_history_claimed": False,
        }
        listing["data_quality"] = calculate_data_quality(listing)
        photo = unavailable_photo("No images")
        research = unavailable_research("No search")
        report = build_fallback_report(listing, photo, research, "sk")
        report["verdict"]["confidence"] = 100
        report["price_assessment"].update(
            {"evidence_quality": "high", "market_min": 9000, "market_max": 12000}
        )

        sanitized = sanitize_report(
            report,
            listing,
            photo,
            research,
            "sk",
            "a" * 32,
            0.0,
        )
        self.assertLess(sanitized["verdict"]["confidence"], 100)
        self.assertEqual(sanitized["price_assessment"]["evidence_quality"], "unavailable")
        self.assertEqual(sanitized["price_assessment"]["market_min"], 0)
        self.assertEqual(sanitized["schema_version"], "2.0")


if __name__ == "__main__":
    unittest.main()
