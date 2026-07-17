import html
import re
import unittest
from pathlib import Path


class FrontendLocalizationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parent
        cls.pages = [
            (cls.root / "web" / name).read_text(encoding="utf-8")
            for name in ("index.html", "analysis.html", "technical-analysis.html")
        ]
        cls.common = (cls.root / "web" / "assets" / "redesign-common.js").read_text(
            encoding="utf-8"
        )
        cls.css = (cls.root / "web" / "assets" / "redesign.css").read_text(
            encoding="utf-8"
        )

    def test_public_pages_offer_all_three_languages(self):
        for page in self.pages:
            self.assertIn('data-language-select', page)
            self.assertIn('<option value="sk">SK</option>', page)
            self.assertIn('<option value="cs">CZ</option>', page)
            self.assertIn('<option value="en">EN</option>', page)
            self.assertNotIn('data-language-toggle', page)

    def test_every_static_customer_string_has_a_czech_translation(self):
        mapped_values = {
            html.unescape(match.group(1))
            for match in re.finditer(r'^\s+"([^"]+)":\s+"', self.common, re.MULTILINE)
        }
        missing = []
        for page in self.pages:
            for match in re.finditer(r'<[^>]+data-sk="([^"]+)"[^>]*>', page):
                tag = match.group(0)
                slovak = html.unescape(match.group(1))
                if 'data-cs=' not in tag and slovak not in mapped_values:
                    missing.append(slovak)
        self.assertEqual(missing, [])

    def test_first_visit_uses_browser_locale_and_manual_choice_persists(self):
        self.assertIn("navigator.languages", self.common)
        self.assertIn('value.startsWith("cs")', self.common)
        self.assertIn('value.startsWith("sk")', self.common)
        self.assertIn('localStorage.setItem(STORAGE.language, next)', self.common)

    def test_long_content_is_contained_or_scrollable(self):
        self.assertIn("overflow-wrap: anywhere", self.css)
        self.assertIn(".table-wrap { max-width: 100%; overflow-x: auto", self.css)
        self.assertIn("min-width: 620px", self.css)


if __name__ == "__main__":
    unittest.main()
