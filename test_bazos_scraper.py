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

    def test_engine_label_does_not_match_inflected_motor_word(self):
        soup = BeautifulSoup(
            """
            <html><body>
              <h1>Audi A5 Coupe 3.0 TDI Quattro</h1>
              <div class="popisdetail">
                Vozidlo s nesmrteľným 3.0 TDI V6 motorom a manuálnou prevodovkou.
                • Motor: 3.0 TDI V6 / 176 kW (240 PS) / Palivo Diesel
              </div>
            </body></html>
            """,
            "html.parser",
        )

        result = extract_car_info(soup, "https://auto.bazos.sk/inzerat/1/audi.php")

        self.assertEqual(result["parameters"]["Engine"], "3.0 TDI V6")

    def test_extracts_czech_manual_transmission_and_gear_count(self):
        soup = BeautifulSoup(
            """
            <html><body>
              <h1>BMW X3 xDrive20i F25</h1>
              <div class="popisdetail">
                manuální převodovka, 6 rychlostních stupňů, pohon 4x4
              </div>
            </body></html>
            """,
            "html.parser",
        )

        result = extract_car_info(soup, "https://auto.bazos.sk/inzerat/1/bmw.php")

        self.assertEqual(result["parameters"]["Transmission"], "Manuálna 6-st.")

    def test_extracts_slovak_front_wheel_nahon(self):
        soup = BeautifulSoup(
            """
            <html><body>
              <h1>Škoda Kodiaq 2.0 TDI DSG</h1>
              <div class="popisdetail">Pohon: predný náhon</div>
            </body></html>
            """,
            "html.parser",
        )

        result = extract_car_info(soup, "https://auto.bazos.sk/inzerat/1/kodiaq.php")

        self.assertEqual(result["parameters"]["Drivetrain"], "Predný")

    def test_extracts_slovak_manual_gear_count_and_ps_power(self):
        soup = BeautifulSoup(
            """
            <html><body>
              <h1>Mazda CX-5 2.0 benzín 4x4</h1>
              <div class="popisdetail">
                2,0L, 160 PS, pohon 4x4, manuálna prevodovka 6 stupnova
              </div>
            </body></html>
            """,
            "html.parser",
        )

        result = extract_car_info(soup, "https://auto.bazos.sk/inzerat/1/mazda.php")

        self.assertEqual(result["parameters"]["Transmission"], "Manuálna 6-st.")
        self.assertEqual(result["parameters"]["Engine Power"], "118 kW (160 PS)")

    def test_extracts_engine_from_title_and_at_abbreviation(self):
        soup = BeautifulSoup(
            """
            <html><body>
              <h1>HYUNDAI SANTA FE 2.2 CRDI PREMIUM 4X4 A/T</h1>
              <div class="popisdetail">
                R.V.: 3/2019, 2199 CM3, 147KW, A/T, NAFTA, 183 270 KM
              </div>
            </body></html>
            """,
            "html.parser",
        )

        result = extract_car_info(soup, "https://auto.bazos.sk/inzerat/1/santa-fe.php")

        self.assertEqual(result["parameters"]["Engine"].upper(), "2.2 CRDI")
        self.assertEqual(result["parameters"]["Transmission"], "Automatická")

    def test_ignores_motor_section_heading_and_extracts_cx60_facts(self):
        soup = BeautifulSoup(
            """
            <html><body>
              <h1>CX-60 3.3 e-Skyactive D200 mHEV Exclusive Line A/T</h1>
              <div class="popisdetail">
                Dátum výroby: 01/2023
                Pohon kolies: zadný
                Objem valcov: 3 283cm3
                Motor a prevodovka :
                -3,3-litrový 6-valcový dieslový motor
              </div>
            </body></html>
            """,
            "html.parser",
        )

        result = extract_car_info(soup, "https://auto.bazos.sk/inzerat/1/cx60.php")

        self.assertIn("E-SKYACTIV D200", result["parameters"]["Engine"].upper())
        self.assertEqual(result["parameters"]["Engine Capacity"], "3283 cm3")
        self.assertEqual(result["parameters"]["Drivetrain"], "Zadný")

    def test_expands_masked_thousands_and_extracts_volvo_engine_badge(self):
        soup = BeautifulSoup(
            """
            <html><body>
              <h1>VOLVO 4x4 XC40 2.0 D4 INSCRIPTION</h1>
              <div class="popisdetail">
                Rok výroby: 4/2018
                NÁJAZD 284xxx KILOMETROV
                Výkon: 140 kW
              </div>
            </body></html>
            """,
            "html.parser",
        )

        result = extract_car_info(soup, "https://auto.bazos.sk/inzerat/1/xc40.php")

        self.assertEqual(result["parameters"]["Mileage"], "284000 km")
        self.assertEqual(result["parameters"]["Engine"].upper(), "2.0 D4")

    def test_extracts_tfsi_and_preserves_s_tronic(self):
        soup = BeautifulSoup(
            """
            <html><body>
              <h1>Predám Audi Q3 2.0 TFSi, S-tronic Quattro automat</h1>
              <div class="popisdetail">
                Audi Q3 2.0 benzín, Quattro S-tronic, 160 000 km.
              </div>
            </body></html>
            """,
            "html.parser",
        )

        result = extract_car_info(soup, "https://auto.bazos.sk/inzerat/1/q3.php")

        self.assertEqual(result["parameters"]["Engine"].upper(), "2.0 TFSI")
        self.assertEqual(result["parameters"]["Transmission"], "S tronic")
        self.assertEqual(result["parameters"]["Drivetrain"], "4x4")

    def test_expands_dot_masked_mileage(self):
        soup = BeautifulSoup(
            """
            <html><body>
              <h1>Rav4</h1>
              <div class="popisdetail">
                Predám Toyota RAV4 ročník 2015, automat benzín 212.., nové gumy.
              </div>
            </body></html>
            """,
            "html.parser",
        )

        result = extract_car_info(soup, "https://auto.bazos.sk/inzerat/1/rav4.php")

        self.assertEqual(result["parameters"]["Mileage"], "212000 km")

    def test_preserves_automatic_kind_before_captured_gear_count(self):
        soup = BeautifulSoup(
            """
            <html><body>
              <h1>Suzuki Grand Vitara 3,2l V6</h1>
              <div class="popisdetail">
                r.v. 2010, 4x4, benzín, automatická prevodovka 5-stupňová
              </div>
            </body></html>
            """,
            "html.parser",
        )

        result = extract_car_info(soup, "https://auto.bazos.sk/inzerat/1/grand-vitara.php")

        self.assertEqual(result["parameters"]["Transmission"], "Automatická 5-stupňová")

    def test_inventory_alternative_does_not_override_primary_fuel_or_power(self):
        soup = BeautifulSoup(
            """
            <html><body>
              <h1>Škoda Karoq 2020 TDi</h1>
              <div class="popisdetail">
                Predám Škodu Karoq, diesel TDi (máme aj benzín TSi 110kW/150k),
                automat DSG 7-stupňov.
              </div>
            </body></html>
            """,
            "html.parser",
        )

        result = extract_car_info(soup, "https://auto.bazos.sk/inzerat/1/karoq.php")

        self.assertEqual(result["parameters"]["Fuel"], "Diesel")
        self.assertNotIn("Engine Power", result["parameters"])


    def test_extracts_delimited_m6_transmission_shorthand(self):
        soup = BeautifulSoup(
            """
            <html><body>
              <h1>KIA SPORTAGE 1.6 T-GDI</h1>
              <div class="popisdetail">
                R.V.: 8/2022, 1598CM3, 110KW (150PS), P, M6, BENZIN, 88436 KM
              </div>
            </body></html>
            """,
            "html.parser",
        )

        result = extract_car_info(soup, "https://auto.bazos.sk/inzerat/1/sportage.php")

        self.assertEqual(result["parameters"]["Transmission"], "Manuálna 6-st.")

    def test_does_not_treat_bmw_m6_model_name_as_transmission(self):
        soup = BeautifulSoup(
            """
            <html><body>
              <h1>BMW M6 Gran Coupe</h1>
              <div class="popisdetail">Benzin, 412 kW, krasny stav.</div>
            </body></html>
            """,
            "html.parser",
        )

        result = extract_car_info(soup, "https://auto.bazos.sk/inzerat/1/bmw-m6.php")

        self.assertNotIn("Transmission", result["parameters"])


if __name__ == "__main__":
    unittest.main()
