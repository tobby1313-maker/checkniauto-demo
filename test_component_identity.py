import unittest

from scrapper_demo.component_identity import (
    component_is_identified,
    normalize_component_identity,
    parse_first_json_object,
)


class ComponentIdentityTests(unittest.TestCase):
    def test_parses_json_before_trailing_grounding_citations(self):
        parsed = parse_first_json_object(
            '{"schema_version":1,"identification_status":"PROBABLE"}'
            "\n\n### Citacie z Google Search\n- [Source](https://example.test)"
        )

        self.assertEqual(parsed["identification_status"], "PROBABLE")

    def test_normalizes_probable_codes_without_upgrading_them(self):
        identity = normalize_component_identity(
            {
                "schema_version": 1,
                "identification_status": "PROBABLE",
                "generation": {
                    "name": "Hyundai Tucson TL",
                    "resolution": "PROBABLE",
                    "confidence": "HIGH",
                },
                "engine": {
                    "marketing_name": "1.6 T-GDi 130 kW",
                    "code": "G4FJ",
                    "resolution": "PROBABLE",
                    "confidence": "HIGH",
                },
                "transmission": {
                    "marketing_name": "7-speed dry DCT",
                    "code": "D7UF1",
                    "resolution": "PROBABLE",
                    "confidence": "MEDIUM",
                },
                "drivetrain": {
                    "type": "AWD",
                    "resolution": "PROBABLE",
                    "confidence": "HIGH",
                },
                "candidate_variants": [],
                "sources": [],
                "notes": [],
            }
        )

        self.assertEqual(identity["engine"]["code"], "G4FJ")
        self.assertEqual(identity["engine"]["resolution"], "PROBABLE")
        self.assertEqual(identity["transmission"]["resolution"], "PROBABLE")
        self.assertTrue(component_is_identified(identity, "engine"))
        self.assertTrue(component_is_identified(identity, "transmission"))

    def test_invalid_output_becomes_unknown_instead_of_a_guess(self):
        identity = normalize_component_identity('{"engine":')

        self.assertEqual(identity["identification_status"], "UNKNOWN")
        self.assertFalse(component_is_identified(identity, "engine"))

    def test_verified_without_direct_vehicle_basis_is_downgraded(self):
        identity = normalize_component_identity(
            {
                "identification_status": "VERIFIED",
                "generation": {"name": "Tucson TL", "resolution": "VERIFIED"},
                "engine": {"code": "G4FJ", "resolution": "VERIFIED"},
                "transmission": {"code": "D7UF1", "resolution": "VERIFIED"},
                "drivetrain": {"type": "AWD", "resolution": "VERIFIED"},
            }
        )

        self.assertEqual(identity["identification_status"], "PROBABLE")
        self.assertEqual(identity["engine"]["resolution"], "PROBABLE")

    def test_direct_vin_record_can_remain_verified(self):
        direct = {"resolution": "VERIFIED", "verification_basis": "VIN_RECORD"}
        identity = normalize_component_identity(
            {
                "identification_status": "VERIFIED",
                "generation": {"name": "Tucson TL", **direct},
                "engine": {"code": "G4FJ", **direct},
                "transmission": {"code": "D7UF1", **direct},
                "drivetrain": {"type": "AWD", **direct},
            }
        )

        self.assertEqual(identity["identification_status"], "VERIFIED")

    def test_redirect_sources_are_not_preserved(self):
        identity = normalize_component_identity(
            {
                "identification_status": "PROBABLE",
                "generation": {"name": "Example", "resolution": "PROBABLE"},
                "sources": [
                    {
                        "source_name": "Redirect",
                        "source_url": "https://vertexaisearch.cloud.google.com/redirect/x",
                        "source_type": "OFFICIAL",
                    }
                ],
            }
        )

        self.assertEqual(identity["sources"][0]["source_url"], "")


if __name__ == "__main__":
    unittest.main()
