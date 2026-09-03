import unittest

from bs4 import BeautifulSoup

from Bazos_v2 import collect_image_urls, extract_listing, validate_listing_url


class BazosV2Tests(unittest.TestCase):
    def test_czech_listing_uses_czk_and_czech_image_origin(self):
        soup = BeautifulSoup(
            """
            <html><body>
              <h1 class="nadpisihlavni">Škoda Octavia 2.0 TDI DSG</h1>
              <div class="cena">Cena: 249 000 Kč</div>
              <div class="popisdetail">Rok 2019, najeto 168 400 km, 110 kW, automat DSG, servisní kniha.</div>
              <div class="carousel"><img src="/img/1/123/test.jpg"></div>
            </body></html>
            """,
            "html.parser",
        )
        url = "https://auto.bazos.cz/inzerat/123/test.php"
        listing = extract_listing(soup, url)
        images = collect_image_urls(soup, url)

        self.assertEqual(listing["currency"], "CZK")
        self.assertEqual(listing["price"], 249000)
        self.assertEqual(listing["parameters"]["Year"], "2019")
        self.assertEqual(listing["parameters"]["Mileage"], "168 400 km")
        self.assertEqual(images, ["https://auto.bazos.cz/img/1/123/test.jpg"])

    def test_rejects_non_bazos_domain(self):
        with self.assertRaises(ValueError):
            validate_listing_url("https://bazos.cz.example.com/inzerat/123/test.php")


if __name__ == "__main__":
    unittest.main()
