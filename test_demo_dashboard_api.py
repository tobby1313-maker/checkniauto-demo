import hashlib
import json
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
        self.original_admin_token = web_server.app.config.get("ADMIN_DASHBOARD_TOKEN", "")
        self.original_secret_key = web_server.app.config.get("SECRET_KEY")
        web_server.app.config["ADMIN_DASHBOARD_TOKEN"] = "test-admin-token"
        web_server.app.config["SECRET_KEY"] = "test-secret-key"
        self.client = web_server.app.test_client()
        self.client.post("/admin/login", data={"token": "test-admin-token"})
        self.addCleanup(self._restore_auta_dir)

    def _restore_auta_dir(self):
        web_server.AUTA_DIR = self.original_auta_dir
        web_server.app.config["ADMIN_DASHBOARD_TOKEN"] = self.original_admin_token
        web_server.app.config["SECRET_KEY"] = self.original_secret_key

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
        self.assertEqual(payload["parsed"]["specs"]["Mileage"], "150 000 km")
        self.assertEqual(
            [image["filename"] for image in payload["images"]],
            ["01_front.jpg", "02_dash.jpg", "03_rear.jpg"],
        )
        self.assertIn("Saved analysis", payload["analysis_content"])
        self.assertEqual(payload["source_url"], "https://example.com/detail-check")

    def test_demo_artifacts_include_raw_scraped_inputs_first(self):
        listing_dir = self._create_listing("artifact-check", has_analysis=True, images=["01_front.jpg"])
        _write_text(listing_dir / "raw_data.json", json.dumps({"mileage": "150 000 km"}))

        response = self.client.get("/api/demo/listings/artifact-check/artifacts")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        filenames = [artifact["filename"] for artifact in payload["artifacts"]]
        labels = {artifact["filename"]: artifact["label"] for artifact in payload["artifacts"]}
        self.assertEqual(filenames[:2], ["raw_data.json", "car_info.md"])
        self.assertEqual(labels["raw_data.json"], "Scraped raw JSON")
        self.assertEqual(labels["car_info.md"], "Scraped listing markdown")

    def test_markdown_artifact_preview_renders_clickable_links_and_keeps_raw_mode(self):
        listing_dir = self._create_listing("artifact-preview", has_analysis=True, images=["01_front.jpg"])
        _write_text(
            listing_dir / "web_research.md",
            "## Zdroje\n\n"
            "- [Engine Finders](https://enginefinder.co.uk/blog/example-(test)) - rozvody.\n"
            "- Plain https://www.autobazar.eu/detail/test/.\n",
        )

        preview = self.client.get("/api/demo/listings/artifact-preview/artifacts/web_research.md")
        raw = self.client.get("/api/demo/listings/artifact-preview/artifacts/web_research.md?raw=1")

        self.assertEqual(preview.status_code, 200)
        self.assertIn("text/html", preview.content_type)
        self.assertIn(
            'href="https://enginefinder.co.uk/blog/example-(test)"',
            preview.get_data(as_text=True),
        )
        self.assertIn('href="https://www.autobazar.eu/detail/test/"', preview.get_data(as_text=True))
        self.assertEqual(raw.status_code, 200)
        self.assertIn("text/plain", raw.content_type)
        self.assertIn("[Engine Finders](https://enginefinder.co.uk/blog/example-(test))", raw.get_data(as_text=True))

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

    def test_prepare_llm_images_uses_full_gallery_overview_for_large_gallery(self):
        from PIL import Image

        listing_dir = Path(self.temp_dir.name) / "large-gallery"
        images_dir = listing_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        for index in range(104):
            digest = hashlib.sha256(str(index).encode("ascii")).digest()
            pixels = []
            for value in digest[:8]:
                pixels.extend(255 if value & (1 << bit) else 0 for bit in range(8))
            image = Image.new("L", (8, 8))
            image.putdata(pixels)
            image = image.resize((80, 80), Image.Resampling.NEAREST).convert("RGB")
            image.save(images_dir / f"{index + 1:03d}_photo.jpg", format="JPEG")

        image_data, image_meta = web_server.prepare_llm_images(str(listing_dir))

        self.assertEqual(len(image_data), web_server.MAX_ANALYSIS_COLLAGES)
        self.assertEqual(image_meta["coverage_mode"], "full_gallery_overview")
        self.assertEqual(image_meta["original_count"], 104)
        self.assertEqual(image_meta["unique_count"], 104)
        self.assertEqual(image_meta["duplicate_count"], 0)
        self.assertEqual(image_meta["selected_count"], 104)
        self.assertEqual(image_meta["attachment_count"], web_server.MAX_ANALYSIS_COLLAGES)
        self.assertEqual(image_meta["overview_count"], web_server.LLM_OVERVIEW_ATTACHMENTS)
        self.assertEqual(image_meta["detail_count"], web_server.LLM_COLLAGE_COLUMNS * web_server.LLM_COLLAGE_ROWS)
        self.assertTrue(image_meta["overview_includes_all"])
        self.assertTrue(image_meta["full_gallery_included"])
        self.assertTrue(all(group["type"] == "overview" for group in image_meta["overview_groups"]))
        covered = [
            item["original_name"]
            for group in image_meta["overview_groups"]
            for item in group["items"]
        ]
        self.assertEqual(len(covered), 104)
        self.assertEqual(set(covered), {f"{index + 1:03d}_photo.jpg" for index in range(104)})

    def test_final_synthesis_context_includes_image_payload_metadata(self):
        image_meta = {
            "coverage_mode": "full_gallery_overview",
            "original_count": 104,
            "selected_count": 104,
            "overview_count": 4,
            "detail_count": 4,
            "overview_includes_all": True,
            "full_gallery_included": True,
        }

        context = web_server._build_final_synthesis_context(
            "sk",
            "# Mazda CX-5\n\n## Photos\n- **Downloaded:** 104",
            json.dumps({"listing_facts": {}, "market_assessment": {}, "consistency_checks": []}),
            json.dumps({
                "photos_provided": True,
                "photo_limitations": ["Engine bay visible only in overview; not assessable in detail."],
                "view_coverage": {"engine_bay": "visible_overview_only"},
                "visual_verdict": "Vyzerá vizuálne dobre",
            }),
            json.dumps({"risk_score": 1, "allowed_final_verdict": "ZVAZIT"}),
            "",
            image_meta,
        )
        payload = json.loads(context.split("\n\n", 1)[1])

        self.assertTrue(payload["image_payload"]["full_gallery_included"])
        self.assertEqual(payload["image_payload"]["coverage_mode"], "full_gallery_overview")
        self.assertEqual(payload["vision"]["view_coverage"]["engine_bay"], "visible_overview_only")

    def test_final_synthesis_context_keeps_photo_labels_and_positive_vision_note(self):
        context = web_server._build_final_synthesis_context(
            "sk",
            "# BMW X5\n\n## Photos\n- **Downloaded:** 20",
            json.dumps({"listing_facts": {}, "market_assessment": {}, "consistency_checks": []}),
            json.dumps({
                "photos_provided": True,
                "exterior_observations": [
                    {
                        "photo_label": "Foto 01",
                        "observation": "Predný nárazník má drobné škrabance.",
                        "severity": "minor",
                    }
                ],
                "interior_observations": [
                    {
                        "photo_label": "Foto 18",
                        "observation": "Zadné sedadlá vyzerajú v dobrom stave s bežnými záhybmi.",
                        "severity": "minor",
                    },
                    {
                        "photo_label": "Foto 08",
                        "observation": "Sedadlo vodiča vykazuje opotrebenie na bočnici.",
                        "severity": "medium",
                    }
                ],
                "visual_verdict": "Viditeľné drobné nedostatky",
            }),
            json.dumps({"risk_score": 3, "allowed_final_verdict": "OPATRNE ZVÁŽIŤ"}),
            "",
            {},
        )
        payload = json.loads(context.split("\n\n", 1)[1])

        interior = payload["vision"]["interior_observations"]
        self.assertTrue(any(item.get("photo_label") == "Foto 08" for item in interior))
        self.assertTrue(any(item.get("photo_label") == "Foto 18" for item in interior))
        self.assertTrue(any("dobrom stave" in item.get("observation", "") for item in interior))

    def test_photo_analysis_section_is_replaced_from_vision_json(self):
        report = """# Analýza: BMW X5 xDrive30i

## 📋 Rýchle zhrnutie

- **Hodnotenie:** TEST

## 📸 Analýza fotografií

- Exteriér: stručné zhrnutie.

## ✅ Klady

- test

<!-- END_ANALYSIS -->
"""
        vision = json.dumps({
            "photos_provided": True,
            "view_coverage": {
                "engine_bay": "missing",
                "underbody": "missing",
            },
            "exterior_observations": [
                {
                    "photo_label": "Foto 01",
                    "observation": "Predné svetlomety vykazujú mierne zahmlenie.",
                    "buyer_relevance": "Môže ovplyvniť svetelný výkon.",
                    "severity": "minor",
                }
            ],
            "interior_observations": [
                {
                    "photo_label": "Foto 18",
                    "observation": "Zadné sedadlá vyzerajú v dobrom stave s bežnými záhybmi.",
                    "buyer_relevance": "Dobrý stav pre zadných pasažierov.",
                    "severity": "minor",
                }
            ],
            "dashboard_or_warning_lights": [
                {
                    "photo_label": "Foto 08",
                    "observation": "Prístrojová doska nie je dostatočne zaostrená.",
                    "confidence": "Nízka",
                    "requires_verification": True,
                }
            ],
            "visible_red_flags": [],
        }, ensure_ascii=False)

        updated = web_server._replace_photo_analysis_section(report, vision, "sk")

        self.assertIn("## 📋 Rýchle zhrnutie", updated)
        self.assertIn("- **Hodnotenie:** TEST", updated)
        self.assertIn("### Exteriér", updated)
        self.assertIn("### Interiér", updated)
        self.assertIn("### Červené vlajky a limity fotografií", updated)
        self.assertIn("**Foto 01:** Predné svetlomety vykazujú mierne zahmlenie.", updated)
        self.assertIn("**Foto 18:** Zadné sedadlá vyzerajú v dobrom stave", updated)
        self.assertIn("**Chýbajúce pohľady:** motorový priestor, podvozok", updated)
        self.assertIn("Fotografie však nevylučujú skryté chyby", updated)
        self.assertNotIn("nie je dostatočne zaostrená", updated)

    def test_photo_analysis_omits_generic_limitations_but_keeps_specific_missing_views(self):
        report = """# Analýza: Suzuki Vitara

## 📸 Analýza fotografií

- Starý text.

## ✅ Klady

- Test.
"""
        vision = json.dumps({
            "photos_provided": True,
            "view_coverage": {
                "exterior": "visible_detail",
                "interior": "visible_detail",
                "dashboard": "visible_detail",
                "engine_bay": "missing",
                "underbody": "missing",
            },
            "exterior_observations": [{
                "photo_label": "Foto 01-03",
                "observation": "Exteriér je dobre viditeľný.",
            }],
            "interior_observations": [{
                "photo_label": "Foto 08-14",
                "observation": "Interiér je dobre viditeľný.",
            }],
            "visible_red_flags": [],
            "photo_limitations": [
                "Niektoré fotografie sú mierne tmavé, čo sťažuje detailnú kontrolu.",
                "Fotografie sú obmedzené na vybrané uhly, čo neumožňuje kompletné posúdenie vozidla.",
                "Odlesk na Foto 09 neumožňuje spoľahlivo posúdiť konkrétnu kontrolku.",
            ],
        }, ensure_ascii=False)

        updated = web_server._replace_photo_analysis_section(report, vision, "sk")

        self.assertNotIn("mierne tmavé", updated)
        self.assertNotIn("vybrané uhly", updated)
        self.assertIn("**Chýbajúce pohľady:** motorový priestor, podvozok", updated)
        self.assertIn("Odlesk na Foto 09", updated)

    def test_photo_analysis_includes_documents_and_red_flag_details(self):
        report = """# Analýza: Mazda CX-5

## 📸 Analýza fotografií

- Starý text.

## ✅ Klady

- Test.

<!-- END_ANALYSIS -->
"""
        vision = json.dumps({
            "photos_provided": True,
            "supported_observations": [
                {
                    "type": "documents",
                    "photo_label": "Foto 15",
                    "observation": "Na sedadle sú viditeľné manuály a servisná knižka.",
                    "notes": "Ich obsah a úplnosť nemožno z fotografie potvrdiť.",
                }
            ],
            "exterior_observations": [],
            "interior_observations": [],
            "visible_red_flags": [
                {
                    "photo_label": "Foto 04",
                    "red_flag": "Pravý blatník má odlišný odtieň.",
                    "why_it_matters": "Treba ho preveriť meračom laku.",
                }
            ],
        }, ensure_ascii=False)

        updated = web_server._replace_photo_analysis_section(report, vision, "sk")

        self.assertIn("**Foto 15:** Na sedadle sú viditeľné manuály a servisná knižka.", updated)
        self.assertIn("Ich obsah a úplnosť nemožno z fotografie potvrdiť.", updated)
        self.assertIn("**Foto 04:** Pravý blatník má odlišný odtieň.", updated)
        self.assertIn("Treba ho preveriť meračom laku.", updated)
        self.assertNotIn("neboli označené zjavné vážne vizuálne poškodenia", updated)

    def test_photo_analysis_replacement_preserves_bare_following_headings(self):
        report = """# Analýza: Mazda CX-5

## 📸 Analýza fotografií

- Starý text.

✅ Klady

- Zachovaný interiér.

❌ Zápory / riziká

- Chýba VIN.

<!-- END_ANALYSIS -->
"""
        vision = json.dumps({
            "photos_provided": True,
            "exterior_observations": [
                {
                    "photo_label": "Foto 01",
                    "observation": "Karoséria bez viditeľného poškodenia.",
                    "buyer_relevance": "Dobrý vizuálny stav.",
                }
            ],
        }, ensure_ascii=False)

        updated = web_server._replace_photo_analysis_section(report, vision, "sk")

        self.assertIn("**Foto 01:** Karoséria bez viditeľného poškodenia.", updated)
        self.assertIn("✅ Klady", updated)
        self.assertIn("- Zachovaný interiér.", updated)
        self.assertIn("❌ Zápory / riziká", updated)
        self.assertIn("- Chýba VIN.", updated)


if __name__ == "__main__":
    unittest.main()
