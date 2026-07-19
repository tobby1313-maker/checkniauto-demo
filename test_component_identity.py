import unittest

from scrapper_demo.component_identity import (
    component_is_identified,
    normalize_component_identity,
    parse_first_json_object,
    reconcile_component_identity_with_listing,
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
                    "evidence_refs": ["src_spec"],
                },
                "transmission": {
                    "marketing_name": "7-speed dry DCT",
                    "code": "D7UF1",
                    "resolution": "PROBABLE",
                    "confidence": "MEDIUM",
                    "evidence_refs": ["src_spec"],
                },
                "drivetrain": {
                    "type": "AWD",
                    "resolution": "PROBABLE",
                    "confidence": "HIGH",
                },
                "candidate_variants": [],
                "sources": [
                    {
                        "source_id": "src_spec",
                        "source_name": "OEM application catalog",
                        "source_url": "https://example.test/oem-catalog",
                        "source_type": "OEM_CATALOG",
                        "used_for": "engine, transmission, drivetrain",
                    }
                ],
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
                "engine": {"code": "G4FJ", "resolution": "VERIFIED", "evidence_refs": ["src_spec"]},
                "transmission": {"code": "D7UF1", "resolution": "VERIFIED", "evidence_refs": ["src_spec"]},
                "drivetrain": {"type": "AWD", "resolution": "VERIFIED"},
                "sources": [
                    {
                        "source_id": "src_spec",
                        "source_name": "Application catalog",
                        "source_url": "https://example.test/catalog",
                        "source_type": "TECHNICAL_PUBLICATION",
                    }
                ],
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

    def test_generic_marketing_labels_are_not_kept_as_exact_codes(self):
        identity = normalize_component_identity(
            {
                "identification_status": "PROBABLE",
                "generation": {"name": "Tucson TL", "resolution": "PROBABLE"},
                "engine": {"code": "G4FJ", "resolution": "PROBABLE", "evidence_refs": ["src_engine"]},
                "transmission": {
                    "marketing_name": "7-speed DCT",
                    "code": "7DCT",
                    "resolution": "PROBABLE",
                },
                "drivetrain": {
                    "type": "AWD",
                    "code": "HTRAC",
                    "resolution": "PROBABLE",
                },
                "sources": [
                    {
                        "source_id": "src_engine",
                        "source_name": "OEM engine catalog",
                        "source_url": "https://example.test/engine",
                        "source_type": "OEM_CATALOG",
                    }
                ],
            }
        )

        self.assertEqual(identity["engine"]["code"], "G4FJ")
        self.assertEqual(identity["transmission"]["code"], "")
        self.assertEqual(identity["drivetrain"]["code"], "")

    def test_unreferenced_exact_4wd_transmission_code_becomes_candidate(self):
        identity = normalize_component_identity(
            {
                "identification_status": "PROBABLE",
                "generation": {"name": "RAV4 XA30", "resolution": "PROBABLE"},
                "engine": {"marketing_name": "2.0 VVT-i", "resolution": "PROBABLE"},
                "transmission": {
                    "marketing_name": "4-speed automatic",
                    "code": "U241E",
                    "resolution": "PROBABLE",
                    "confidence": "HIGH",
                    "evidence_refs": [],
                },
                "drivetrain": {"type": "4WD", "resolution": "PROBABLE"},
            }
        )

        self.assertEqual(identity["transmission"]["code"], "")
        self.assertEqual(identity["transmission"]["confidence"], "MEDIUM")
        self.assertTrue(
            any(
                item["transmission_code"] == "U241E"
                for item in identity["candidate_variants"]
            )
        )

    def test_dangling_reference_cannot_support_an_exact_component_code(self):
        identity = normalize_component_identity(
            {
                "identification_status": "PROBABLE",
                "generation": {"name": "RAV4 XA30", "resolution": "PROBABLE"},
                "engine": {"marketing_name": "2.0 VVT-i", "resolution": "PROBABLE"},
                "transmission": {
                    "marketing_name": "4-speed automatic",
                    "code": "U241E",
                    "resolution": "PROBABLE",
                    "evidence_refs": ["source_that_does_not_exist"],
                },
                "drivetrain": {"type": "4WD", "resolution": "PROBABLE"},
                "sources": [],
            }
        )

        self.assertEqual(identity["transmission"]["evidence_refs"], [])
        self.assertEqual(identity["transmission"]["code"], "")
        self.assertTrue(
            any(item["transmission_code"] == "U241E" for item in identity["candidate_variants"])
        )

    def test_grounding_citations_restore_source_urls_and_refs_do_not_dangle(self):
        identity = normalize_component_identity(
            '{"identification_status":"PROBABLE",'
            '"generation":{"name":"Tucson TL","resolution":"PROBABLE","evidence_refs":["src_20"]},'
            '"engine":{"code":"G4FJ","resolution":"PROBABLE","evidence_refs":["src_20"]},'
            '"transmission":{"marketing_name":"7DCT","resolution":"PROBABLE"},'
            '"sources":[{"source_id":"src_20","source_name":"Official Hyundai Tucson technical bulletin","source_url":"https://vertexaisearch.cloud.google.com/redirect/x"}]}'
            '\n\n### Citacie z Google Search\n'
            '- [Official Hyundai Tucson technical bulletin](https://example.test/hyundai-tsb)'
        )

        self.assertEqual(identity["sources"][0]["source_id"], "src_20")
        self.assertEqual(
            identity["sources"][0]["source_url"],
            "https://example.test/hyundai-tsb",
        )
        preserved = {item["source_id"] for item in identity["sources"]}
        self.assertTrue(set(identity["engine"]["evidence_refs"]) <= preserved)

    def test_explicit_manual_listing_replaces_incompatible_automatic_guess(self):
        identity = normalize_component_identity({
            "identification_status": "PROBABLE",
            "generation": {"name": "BMW X3 F25", "resolution": "PROBABLE"},
            "engine": {"marketing_name": "2.0 TwinPower Turbo", "resolution": "PROBABLE"},
            "transmission": {
                "marketing_name": "8-speed Automatic",
                "family": "ZF 8HP",
                "resolution": "PROBABLE",
                "confidence": "HIGH",
                "verification_basis": "MULTIPLE_CANDIDATES",
            },
            "candidate_variants": [{
                "engine_code": "N20B20A",
                "transmission_code": "GA8HP45Z",
                "reason": "Common automatic candidate.",
            }],
        })

        reconciled = reconcile_component_identity_with_listing(
            identity,
            {"transmission": "Manuálna 6-st."},
        )

        self.assertEqual(reconciled["transmission"]["marketing_name"], "6-speed Manual")
        self.assertEqual(reconciled["transmission"]["family"], "Manual")
        self.assertEqual(reconciled["transmission"]["code"], "")
        self.assertEqual(len(reconciled["candidate_variants"]), 1)
        self.assertEqual(reconciled["candidate_variants"][0]["transmission_code"], "")
        self.assertEqual(reconciled["identification_status"], "PROBABLE")

    def test_generic_kodiaq_sources_cannot_select_exact_component_codes(self):
        identity = normalize_component_identity({
            "identification_status": "PROBABLE",
            "generation": {"name": "Kodiaq NS7", "resolution": "PROBABLE"},
            "engine": {
                "marketing_name": "2.0 TDI",
                "code": "DFGA",
                "family": "EA288",
                "resolution": "PROBABLE",
                "evidence_refs": ["engine_codes"],
            },
            "transmission": {
                "marketing_name": "7-speed DSG",
                "code": "DQ381",
                "family": "DQ",
                "resolution": "PROBABLE",
                "evidence_refs": ["repair"],
            },
            "sources": [
                {
                    "source_id": "engine_codes",
                    "source_name": "Skoda Kodiaq diesel engine codes",
                    "source_url": "https://example.test/kodiaq-engine-codes",
                    "source_type": "PARTS_CATALOG",
                },
                {
                    "source_id": "repair",
                    "source_name": "Skoda Kodiaq DQ381 mechatronic fault",
                    "source_url": "https://example.test/kodiaq-dq381-fault",
                    "source_type": "REPAIR_SOURCE",
                },
            ],
        })

        reconciled = reconcile_component_identity_with_listing(identity, {
            "title": "Škoda Kodiaq 2.0 TDI 110 kW DSG 2023",
            "engine": "2.0 TDI",
            "power": "110 kW",
            "transmission": "automatická DSG (7-st.)",
            "drive": "Predný",
            "year": "2023",
        })

        self.assertEqual(reconciled["engine"]["code"], "")
        self.assertEqual(reconciled["transmission"]["code"], "")
        self.assertTrue(any(
            item["engine_code"] == "DFGA" for item in reconciled["candidate_variants"]
        ))
        self.assertTrue(any(
            item["transmission_code"] == "DQ381" for item in reconciled["candidate_variants"]
        ))

    def test_application_specific_source_keeps_exact_transmission_code(self):
        identity = normalize_component_identity({
            "identification_status": "PROBABLE",
            "generation": {"name": "Audi A5 8T", "resolution": "PROBABLE"},
            "engine": {"marketing_name": "3.0 TDI", "resolution": "PROBABLE"},
            "transmission": {
                "marketing_name": "6-speed Manual",
                "code": "0B4",
                "resolution": "PROBABLE",
                "evidence_refs": ["application"],
            },
            "sources": [{
                "source_id": "application",
                "source_name": "Audi A5 3.0 TDI Quattro manual gearbox 0B4",
                "source_url": "https://example.test/audi-a5-30tdi-quattro-0b4",
                "source_type": "PARTS_CATALOG",
            }],
        })

        reconciled = reconcile_component_identity_with_listing(identity, {
            "title": "Audi A5 Coupe 3.0 TDI Quattro",
            "engine": "3.0 TDI",
            "transmission": "Manuálna 6-st.",
            "drive": "4x4",
            "year": "2008",
        })

        self.assertEqual(reconciled["transmission"]["code"], "0B4")

    def test_unverified_exact_codes_cannot_hide_in_transmission_family(self):
        identity = normalize_component_identity({
            "identification_status": "PROBABLE",
            "generation": {"name": "Santa Fe TM", "resolution": "PROBABLE"},
            "engine": {"marketing_name": "2.2 CRDi", "family": "R-series", "resolution": "PROBABLE"},
            "transmission": {
                "marketing_name": "8-speed automatic",
                "family": "A8LF1 / A8Fxx",
                "resolution": "PROBABLE",
                "confidence": "HIGH",
                "evidence_refs": ["spec"],
            },
            "sources": [{
                "source_id": "spec",
                "source_name": "Hyundai Santa Fe 2.2 CRDi 8-speed automatic specification",
                "source_url": "https://example.test/santa-fe-automatic",
                "source_type": "OFFICIAL",
            }],
        })

        reconciled = reconcile_component_identity_with_listing(identity, {
            "title": "Hyundai Santa Fe 2.2 CRDi 147 kW 4x4 A/T",
            "engine": "2.2 CRDI",
            "power": "147 kW",
            "transmission": "Automatická",
            "drive": "4x4",
            "year": "2019",
        })

        self.assertEqual(reconciled["transmission"]["family"], "8-speed automatic")
        self.assertEqual(reconciled["transmission"]["confidence"], "MEDIUM")
        self.assertTrue(any(
            item["transmission_code"] == "A8LF1"
            for item in reconciled["candidate_variants"]
        ))

    def test_unadvertised_mercedes_variant_is_generalized(self):
        identity = normalize_component_identity({
            "identification_status": "PROBABLE",
            "generation": {"name": "GLK-Class X204", "resolution": "PROBABLE"},
            "engine": {
                "marketing_name": "GLK 300 V6",
                "family": "GLK 300 V6",
                "resolution": "PROBABLE",
                "confidence": "MEDIUM",
            },
            "transmission": {
                "marketing_name": "7G-TRONIC",
                "resolution": "PROBABLE",
            },
        })

        reconciled = reconcile_component_identity_with_listing(identity, {
            "title": "Mercedes-Benz GLK",
            "description_excerpt": "Predám GLK 3,0 lit Benzín + LPG, ročník 2009.",
            "engine": "3,0 lit",
            "fuel": "Benzín",
        })

        self.assertEqual(reconciled["identification_status"], "AMBIGUOUS")
        self.assertEqual(reconciled["engine"]["marketing_name"], "3.0 L V6 Benzín")
        self.assertEqual(reconciled["engine"]["resolution"], "AMBIGUOUS")
        self.assertTrue(any("not advertised" in note for note in reconciled["notes"]))


if __name__ == "__main__":
    unittest.main()
