import unittest

from scrapper_demo.url_normalizer import normalize_bazos_listing_url


class BazosUrlNormalizerTests(unittest.TestCase):
    def test_encoded_space_in_slug_is_repaired(self):
        bad = (
            "https://auto.bazos.sk/inzerat/195434376/"
            "jeep-compass-20l-mjet%20140-4wd-limited-at.php"
        )
        self.assertEqual(
            normalize_bazos_listing_url(bad),
            (
                "https://auto.bazos.sk/inzerat/195434376/"
                "jeep-compass-20l-mjet-140-4wd-limited-at.php"
            ),
        )

    def test_literal_spaces_and_duplicate_hyphens_are_repaired(self):
        bad = (
            "https://auto.bazos.cz/inzerat/123456789/"
            "jeep--compass  20-mjet.php?foo=bar#gallery"
        )
        self.assertEqual(
            normalize_bazos_listing_url(bad),
            (
                "https://auto.bazos.cz/inzerat/123456789/"
                "jeep-compass-20-mjet.php?foo=bar#gallery"
            ),
        )

    def test_valid_bazos_url_is_unchanged(self):
        url = "https://auto.bazos.sk/inzerat/195434376/jeep-compass-20l-mjet-140-4wd-limited-at.php"
        self.assertEqual(normalize_bazos_listing_url(url), url)

    def test_non_bazos_url_is_unchanged(self):
        url = "https://example.com/inzerat/195434376/jeep compass.php"
        self.assertEqual(normalize_bazos_listing_url(url), url)

    def test_non_listing_bazos_path_is_unchanged(self):
        url = "https://auto.bazos.sk/inzeraty/jeep compass/"
        self.assertEqual(normalize_bazos_listing_url(url), url)


if __name__ == "__main__":
    unittest.main()
