import unittest

from bs4 import BeautifulSoup

from Bazos import extract_car_info


class BazosScraperTests(unittest.TestCase):
    def test_recovers_explicit_eur_price_from_description_and_uses_empty_vin(self):
        soup = BeautifulSoup(
            """
            <html><body>
              <h1>Suzuki Vitara 1.6 VVT</h1>
              <div class="popisdetail">Rok výroby: 2016\nCena: 9 800 € + dohoda</div>
            </body></html>
            """,
            "html.parser",
        )

        result = extract_car_info(soup, "https://auto.bazos.sk/inzerat/1/vitara.php")

        self.assertEqual(result["price"], 9800)
        self.assertEqual(result["vin"], "")

    def test_extracts_label_before_value_vehicle_facts(self):
        soup = BeautifulSoup(
            """
            <html><body>
              <h1>Hyundai Tucson 1.6 T-GDi Premium 4x4 DTC</h1>
              <div class="popisdetail">
                Mesiac/Rok: 09/2016\n
                Typ motora: 1.6 T-GDi\n
                Palivo: Benzín\n
                Prevodovka: 7-st. automatická\n
                Výkon: 130kW\n
                Najazdené km: 161949
              </div>
            </body></html>
            """,
            "html.parser",
        )

        result = extract_car_info(soup, "https://auto.bazos.sk/inzerat/1/test.php")

        self.assertEqual(result["parameters"]["Mileage"], "161949 km")
        self.assertEqual(result["parameters"]["Year"], "2016")
        self.assertEqual(result["parameters"]["Engine Power"], "130 kW")
        self.assertEqual(result["parameters"]["Engine"], "1.6 T-GDi")
        self.assertEqual(result["parameters"]["Transmission"], "7-st. automatická")
        self.assertEqual(result["parameters"]["Drivetrain"], "4x4")

    def test_dealer_alternatives_do_not_override_advertised_troc_facts(self):
        soup = BeautifulSoup(
            """
            <html><body>
              <h1>VW T-Roc 2023, 1.5TSi, DSG</h1>
              <div class="popisdetail">
                Predám Volkswagen T-Roc r.v. 2/2023, benzín 1.5TSi 110kW
                s pohonom FWD (máme aj 4x4), prevodovka DSG 7-stupňov
                (máme aj manuál), najazdené 159tis.km (garantovaných).
              </div>
            </body></html>
            """,
            "html.parser",
        )

        result = extract_car_info(soup, "https://auto.bazos.sk/inzerat/1/troc.php")

        self.assertEqual(result["parameters"]["Mileage"], "159000 km")
        self.assertEqual(result["parameters"]["Drivetrain"], "Predný")
        self.assertEqual(result["parameters"]["Transmission"], "DSG 7-stupňov")


if __name__ == "__main__":
    unittest.main()
