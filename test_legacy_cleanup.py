import unittest
from pathlib import Path
from unittest.mock import patch

import main
import web_server


class LegacyCleanupTests(unittest.TestCase):
    def test_mobile_de_is_manual_only_and_has_no_cli_scraper_branch(self):
        url = "https://suchen.mobile.de/fahrzeuge/details.html?id=123"

        self.assertIsNone(main.detect_site(url))
        self.assertIsNone(main.get_scraper_path("mobile.de"))
        self.assertEqual(main.derive_slug(url), "car-listing")
        self.assertNotIn("Mobile_de.py", Path(main.__file__).read_text(encoding="utf-8"))

    def test_runtime_dependencies_use_exact_direct_version_pins(self):
        requirement_lines = [
            line.strip()
            for line in (Path(__file__).parent / "requirements.txt").read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]

        self.assertTrue(requirement_lines)
        self.assertTrue(all("==" in line for line in requirement_lines))

    def test_demo_pipeline_cannot_write_knowledge_base_blocks(self):
        blocks = [{"category": "engines", "filename": "fixture.json", "data": {}}]
        with (
            patch.object(web_server, "DEMO_MODE", True),
            patch.object(web_server, "DEMO_SKIP_KB", True),
            patch.object(web_server, "_save_kb_blocks") as save_blocks,
        ):
            saved = web_server._pipeline_save_kb_blocks(blocks)

        self.assertEqual(saved, [])
        save_blocks.assert_not_called()

    def test_private_mode_retains_tested_knowledge_base_compatibility(self):
        blocks = [{"category": "engines", "filename": "fixture.json", "data": {}}]
        with (
            patch.object(web_server, "DEMO_MODE", False),
            patch.object(web_server, "_save_kb_blocks", return_value=[{"filename": "fixture.json"}]) as save_blocks,
        ):
            saved = web_server._pipeline_save_kb_blocks(blocks)

        self.assertEqual(saved, [{"filename": "fixture.json"}])
        save_blocks.assert_called_once_with(blocks)


if __name__ == "__main__":
    unittest.main()
