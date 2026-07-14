import unittest

from bs4 import BeautifulSoup

from Bazos import extract_car_info


class BazosScraperTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
