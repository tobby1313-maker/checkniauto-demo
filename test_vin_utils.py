import unittest
import tempfile
from pathlib import Path

from scrapper_demo.services.analysis_pipeline import _read_vin_light_decode
from scrapper_demo.storage import ListingJobRepository
from vin_utils import validate_vin


class VinLightDecodeTests(unittest.TestCase):
    def test_missing_vin_sentinels_are_not_invalid_identity_evidence(self):
        for sentinel in ("N/A", "unknown", "none", ""):
            with self.subTest(sentinel=sentinel):
                decoded = validate_vin(sentinel)
                self.assertEqual(decoded["vin"], "")
                self.assertFalse(decoded["valid"])
                self.assertEqual(decoded["validation_message"], "No VIN provided.")

    def test_wvg_touareg_light_decode_exposes_wmi_year_code_and_plant(self):
        decoded = validate_vin("WVGFF9BP4CD005167")

        self.assertTrue(decoded["valid"])
        self.assertEqual(decoded["manufacturer"], "Volkswagen")
        self.assertEqual(decoded["model_year_code"], "C")
        self.assertIn(2012, decoded["model_year_candidates"])
        self.assertEqual(decoded["plant_hint"], "Bratislava, Slovakia")

    def test_light_decode_falls_back_to_vin_in_car_info_for_legacy_jobs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = ListingJobRepository(Path(temp_dir) / "Auta")
            repository.job_dir("legacy-vin", create=True)
            repository.write_text(
                "legacy-vin",
                "car_info.md",
                "# Volkswagen Touareg\n\n- **VIN:** WVGFF9BP4CD005167",
            )

            decoded = _read_vin_light_decode(repository, "legacy-vin")

        self.assertEqual(decoded["manufacturer"], "Volkswagen")
        self.assertEqual(decoded["plant_hint"], "Bratislava, Slovakia")

    def test_tma_hyundai_uses_czech_wmi_and_optional_row_check_digit(self):
        decoded = validate_vin("TMAJ3812HHJ268187")

        self.assertTrue(decoded["valid"])
        self.assertEqual(decoded["manufacturer"], "Hyundai Motor Manufacturing Czech")
        self.assertEqual(decoded["region"], "Czech Republic")
        self.assertIsNone(decoded["model_year_hint"])
        self.assertEqual(decoded["check_digit_policy"], "optional_row")
        self.assertEqual(decoded["check_digit_severity"], "info")


if __name__ == "__main__":
    unittest.main()
