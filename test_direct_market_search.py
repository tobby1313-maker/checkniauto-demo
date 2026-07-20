import unittest
from unittest.mock import patch

import requests

from scrapper_demo.direct_market_search import (
    _engine,
    _similarity,
    bazos_search_url,
    derive_bazos_identity,
    parse_bazos_search_page,
    parse_local_portal_search_page,
    parse_mobile_de_search_page,
    search_all_marketplaces,
    search_bazos_sk_cz,
    search_local_marketplaces,
    search_mobile_de,
)


def _card(ad_id, title, description, price="19 990 €"):
    return f"""
    <div class="inzeraty inzeratyflex">
      <div class="inzeratynadpis">
        <h2 class="nadpis"><a href="/inzerat/{ad_id}/vehicle.php">{title}</a></h2>
        <div class="popis">{description}</div>
      </div>
      <div class="inzeratycena"><b><span>{price}</span></b></div>
      <div class="inzeratylok">Bratislava</div>
    </div>
    """


class DirectMarketSearchTests(unittest.TestCase):
    def setUp(self):
        self.listing = {
            "title": "VW T-Roc 2023, 1.5TSI, DSG",
            "year": "2023",
            "mileage_km": 159000,
            "transmission": "DSG 7-stupnov",
            "drive": "FWD",
            "description_excerpt": "Volkswagen T-Roc 1.5 TSI DSG FWD",
            "source_url": "https://auto.bazos.sk/inzerat/100/current.php",
        }
        self.identity = derive_bazos_identity(self.listing)

    def test_identity_uses_title_not_misleading_source_slug(self):
        self.assertEqual(
            self.identity,
            {"make": "Volkswagen", "model": "T-Roc", "query": "Volkswagen T-Roc"},
        )
        self.assertEqual(
            bazos_search_url("SK", self.identity["query"]),
            "https://auto.bazos.sk/inzeraty/volkswagen-t-roc/",
        )

    def test_identity_skips_drivetrain_prefix_before_model(self):
        identity = derive_bazos_identity({
            "title": "VOLVO 4x4 XC40 2.0 D4 INSCRIPTION /2018/",
        })

        self.assertEqual(
            identity,
            {"make": "Volvo", "model": "XC40", "query": "Volvo XC40"},
        )

    def test_identity_preserves_known_multiword_model(self):
        identity = derive_bazos_identity({"title": "Suzuki Grand Vitara 3,2l V6"})

        self.assertEqual(identity, {
            "make": "Suzuki",
            "model": "Grand Vitara",
            "query": "Suzuki Grand Vitara",
        })
        self.assertEqual(
            bazos_search_url("SK", identity["query"]),
            "https://auto.bazos.sk/inzeraty/suzuki-grand-vitara/",
        )

    def test_identity_recovers_make_from_description_for_model_only_title(self):
        identity = derive_bazos_identity({
            "title": "Rav4",
            "description_excerpt": "Predám Toyota rav4 ročník 2015, automat 4x4.",
        })

        self.assertEqual(identity, {
            "make": "Toyota", "model": "Rav4", "query": "Toyota Rav4",
        })

    def test_tier_a_requires_visible_matching_power_and_drivetrain(self):
        listing = {
            "title": "Škoda Kodiaq 2.0 TDI 110 kW DSG",
            "engine": "2.0 TDI",
            "power": "110 kW",
            "transmission": "DSG",
            "drive": "Predný",
            "description_excerpt": "2.0 TDI 110 kW DSG, pohon: predný náhon",
        }

        exact = _similarity(
            listing,
            "Škoda Kodiaq 2.0 TDI 110 kW DSG, predný náhon",
        )
        different = _similarity(
            listing,
            "Škoda Kodiaq 2.0 TDI 147 kW DSG 4x4",
        )
        missing_drive = _similarity(
            listing,
            "Škoda Kodiaq 2.0 TDI 110 kW DSG",
        )

        self.assertEqual(exact[0], "A")
        self.assertEqual(different[0], "B")
        self.assertIn("different power", different[1])
        self.assertIn("different drive", different[1])
        self.assertEqual(missing_drive, ("B", "drive not visible"))

    def test_tier_a_requires_engine_and_rejects_opposite_fuel_family(self):
        listing = {
            "title": "Škoda Karoq 2020 TDi",
            "fuel": "Diesel",
            "transmission": "DSG",
            "drive": "FWD",
            "description_excerpt": "diesel TDI, DSG, FWD",
        }

        self.assertEqual(
            _similarity(listing, "Škoda Karoq 1.5 TSI DSG FWD")[0], "B"
        )
        self.assertEqual(
            _similarity(listing, "Škoda Karoq TDI DSG FWD")[0], "B"
        )

    def test_tier_a_normalizes_ps_to_kw_with_rounding_tolerance(self):
        listing = {
            "title": "Mazda CX-5 2.0 160 PS AWD manual",
            "engine": "2.0 L",
            "power": "118 kW (160 PS)",
            "transmission": "Manuálna 6-st.",
            "drive": "4x4",
        }

        self.assertEqual(
            _similarity(listing, "Mazda CX-5 2.0 119 kW AWD manual 6-speed")[0],
            "A",
        )

    def test_tier_a_recognizes_at_as_automatic(self):
        listing = {
            "title": "Hyundai Santa Fe 2.2 CRDI 147 kW 4x4 A/T",
            "engine": "2.2 CRDI",
            "power": "147 kW",
            "transmission": "Automatická",
            "drive": "4x4",
        }

        self.assertEqual(
            _similarity(listing, "Hyundai Santa Fe 2.2 CRDI 147 kW 4x4 A/T")[0],
            "A",
        )

    def test_tier_a_normalizes_eskyactiv_spelling(self):
        listing = {
            "title": "Mazda CX-60 3.3 e-Skyactive D200 147 kW A/T RWD",
            "engine": "3.3 e-Skyactive D200",
            "power": "147 kW",
            "transmission": "Automatická",
            "drive": "Zadný",
        }

        self.assertEqual(
            _similarity(
                listing,
                "Mazda CX-60 3.3 e-Skyactiv D200 147 kW automatic RWD",
            )[0],
            "A",
        )

    def test_parser_keeps_exact_detail_url_and_visible_fields(self):
        html = _card(
            123456,
            "Volkswagen T-Roc 1.5 TSI DSG 2022",
            "Najazdene 145 000 km, benzin, predny pohon.",
        )
        search_url = bazos_search_url("SK", self.identity["query"])
        candidates, counts = parse_bazos_search_page(
            html,
            country="SK",
            search_url=search_url,
            identity=self.identity,
            listing=self.listing,
        )

        self.assertEqual(counts["result_card_count"], 1)
        self.assertEqual(len(candidates), 1)
        item = candidates[0]
        self.assertEqual(
            item["source_url"],
            "https://auto.bazos.sk/inzerat/123456/vehicle.php",
        )
        self.assertEqual(item["evidence_url"], search_url)
        self.assertTrue(item["verified_url"])
        self.assertEqual(item["data_provenance"], "DIRECT_PORTAL_SEARCH")
        self.assertEqual(item["year"], 2022)
        self.assertEqual(item["mileage_km"], 145000)
        self.assertEqual(item["price_eur"], 19990)
        self.assertEqual(item["similarity_tier"], "A")

    def test_parser_filters_self_listing_parts_and_other_models(self):
        html = "".join(
            (
                _card(100, "VW T-Roc 2023", "159 000 km"),
                _card(101, "Alu disky Volkswagen T-Roc", "sada kolies"),
                _card(102, "Volkswagen Tiguan 2023", "150 000 km"),
                _card(103, "VW T-Roc 2021", "130 000 km"),
            )
        )
        candidates, counts = parse_bazos_search_page(
            html,
            country="SK",
            search_url=bazos_search_url("SK", self.identity["query"]),
            identity=self.identity,
            listing=self.listing,
        )

        self.assertEqual([item["candidate_id"] for item in candidates], ["BAZOS-SK-103"])
        self.assertEqual(counts["self_listing_filtered_count"], 1)
        self.assertEqual(counts["non_vehicle_filtered_count"], 1)
        self.assertEqual(counts["model_mismatch_count"], 1)

    def test_parser_filters_specific_parts_and_wheel_fitment_ads(self):
        html = "".join((
            _card(201, "Spínač svetiel Mercedes-Benz GLK", "X204 2008 - 2015"),
            _card(202, "Klima trúbka Mercedes GLK", "náhradný diel"),
            _card(203, "5x112 R20 Mercedes AMG", "vhodné aj pre GLK"),
        ))
        identity = derive_bazos_identity({"title": "Mercedes-Benz GLK"})

        candidates, counts = parse_bazos_search_page(
            html,
            country="SK",
            search_url=bazos_search_url("SK", identity["query"]),
            identity=identity,
            listing={"title": "Mercedes-Benz GLK", "source_url": ""},
        )

        self.assertEqual(candidates, [])
        self.assertEqual(counts["non_vehicle_filtered_count"], 3)

    def test_engine_parser_accepts_litres_but_not_consumption(self):
        self.assertEqual(_engine("Mercedes GLK 3,0 lit Benzín"), "3.0 L")
        self.assertEqual(_engine("Spotreba 7.5L/100 km"), "")

    def test_q3_tfsi_does_not_accept_diesel_or_rsq3_as_exact(self):
        listing = {
            "title": "Audi Q3 2.0 TFSI S-tronic Quattro",
            "engine": "2.0 TFSI",
            "transmission": "S tronic",
            "drive": "4x4",
        }

        self.assertEqual(
            _similarity(listing, "Audi Q3 2.0 TFSI DSG Quattro")[0],
            "A",
        )
        self.assertEqual(
            _similarity(listing, "Audi Q3 2.0 TDI DSG Quattro")[0],
            "B",
        )
        identity = derive_bazos_identity(listing)
        html = _card(301, "Audi RSQ3 Quattro", "2012, 160 000 km, automat")
        candidates, counts = parse_bazos_search_page(
            html,
            country="SK",
            search_url=bazos_search_url("SK", identity["query"]),
            identity=identity,
            listing=listing,
        )
        self.assertEqual(candidates, [])
        self.assertEqual(counts["model_mismatch_count"], 1)

    def test_two_country_pass_survives_one_source_failure_and_parses_czk(self):
        sk_html = _card(201, "VW T-Roc 2022", "1.5 TSI DSG, 140 000 km")

        def fetch(url, timeout):
            self.assertEqual(timeout, 3.0)
            if url.endswith(".cz/inzeraty/volkswagen-t-roc/"):
                raise TimeoutError("CZ unavailable")
            return sk_html

        result = search_bazos_sk_cz(self.listing, timeout=3.0, fetch_html=fetch)[0]

        self.assertEqual(result["status"], "FOUND")
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["verified_detail_count"], 1)
        self.assertEqual(result["source_attempts"][0]["status"], "SUCCESS")
        self.assertEqual(result["source_attempts"][1]["status"], "ERROR")

        cz_html = _card(
            301,
            "Volkswagen T-Roc 2021",
            "Najeto 120 000 km",
            price="449 900 Kc",
        )
        candidates, _ = parse_bazos_search_page(
            cz_html,
            country="CZ",
            search_url=bazos_search_url("CZ", self.identity["query"]),
            identity=self.identity,
            listing=self.listing,
        )
        self.assertEqual(candidates[0]["price_display"], "449 900 CZK")
        self.assertIsNone(candidates[0]["price_eur"])

    def test_cards_found_but_unusable_are_not_reported_as_nothing_found(self):
        html = _card(400, "Volkswagen Tiguan", "2022, 100 000 km")
        result = search_bazos_sk_cz(
            self.listing,
            fetch_html=lambda url, timeout: html,
        )[0]

        self.assertEqual(result["status"], "SEARCH_RESULTS_FOUND_NOT_STRUCTURED")
        self.assertEqual(result["citation_count"], 2)
        self.assertEqual(result["candidate_count"], 0)

    def test_local_portal_parser_keeps_sauto_detail_and_czk_fields(self):
        html = """
        <article class="listing-card">
          <a href="/osobni/detail/volkswagen/t-roc/210123456">
            Volkswagen T-Roc 1.5 TSI DSG FWD
          </a>
          <div>2022, 145 000 km, Benzín, Automatická, 449 900 Kč</div>
        </article>
        """
        candidates, counts = parse_local_portal_search_page(
            html,
            portal="sauto_cz",
            search_url="https://www.sauto.cz/inzerce/osobni/volkswagen/t-roc",
            identity=self.identity,
            listing=self.listing,
        )

        self.assertEqual(counts["result_card_count"], 1)
        self.assertEqual(len(candidates), 1)
        item = candidates[0]
        self.assertEqual(
            item["source_url"],
            "https://www.sauto.cz/osobni/detail/volkswagen/t-roc/210123456",
        )
        self.assertEqual(item["source_portal"], "sauto_cz")
        self.assertEqual(item["source_country"], "CZ")
        self.assertEqual(item["year"], 2022)
        self.assertEqual(item["mileage_km"], 145000)
        self.assertEqual(item["price_display"], "449 900 CZK")
        self.assertEqual(item["similarity_tier"], "A")

    def test_local_marketplace_wrapper_records_all_portal_attempts(self):
        def fetch(url, timeout):
            if "bazos." in url:
                return ""
            if "autobazar.sk" in url:
                href = "/28272841/volkswagen-t-roc-1-5-tsi-dsg/"
            elif "autobazar.eu" in url:
                href = "/detail/volkswagen-t-roc/AmExample123/"
            else:
                href = "/osobni/detail/volkswagen/t-roc/210123456"
            return f"""
            <article>
              <a href="{href}">Volkswagen T-Roc 1.5 TSI DSG FWD</a>
              <div>2022, 145 000 km, 1.5 TSI DSG FWD, 19 990 EUR</div>
            </article>
            """

        result = search_local_marketplaces(
            self.listing,
            timeout=3.0,
            fetch_html=fetch,
        )[0]

        self.assertEqual(result["status"], "FOUND")
        self.assertEqual(
            {item["source_portal"] for item in result["candidates"]},
            {"autobazar_sk", "autobazar_eu", "sauto_cz"},
        )
        self.assertEqual(len(result["source_attempts"]), 6)
        self.assertEqual(
            {attempt["portal"] for attempt in result["source_attempts"]},
            {"bazos_sk", "bazos_cz", "autobazar_sk", "autobazar_eu", "sauto_cz"},
        )

    def test_mobile_de_parser_creates_hidden_background_comparable(self):
        listing = {
            "title": "Volkswagen Tiguan 2.0 TSI 4Motion DSG",
            "year": 2014,
            "mileage_km": 205350,
            "engine": "2.0 TSI",
            "transmission": "DSG",
            "drivetrain": "4X4",
            "description_excerpt": "Volkswagen Tiguan 2.0 TSI 4Motion DSG",
            "source_url": "https://auto.bazos.sk/inzerat/1/tiguan.php",
        }
        identity = derive_bazos_identity(listing)
        search_url = "https://suchen.mobile.de/auto/volkswagen-tiguan-2-0-tsi.html"
        html = """
        <article class="result-card">
          <a href="/fahrzeuge/details.html?id=123456789&scopeId=C">
            Volkswagen Tiguan 2.0 TSI DSG 4MOTION Track &amp; Style
          </a>
          <div>9.899 € • EZ 04/2013 • 170.000 km • 155 kW (211 PS) • Benzin</div>
        </article>
        """

        candidates, counts = parse_mobile_de_search_page(
            html,
            search_url=search_url,
            identity=identity,
            listing=listing,
        )

        self.assertEqual(counts["result_card_count"], 1)
        self.assertEqual(len(candidates), 1)
        item = candidates[0]
        self.assertIn("/fahrzeuge/details.html?id=123456789", item["source_url"])
        self.assertEqual(item["source_country"], "DE")
        self.assertEqual(item["market_scope"], "BACKGROUND_EU")
        self.assertEqual(item["search_pass"], "mobile_de")
        self.assertEqual(item["price_eur"], 9899)
        self.assertEqual(item["year"], 2013)
        self.assertEqual(item["mileage_km"], 170000)
        self.assertEqual(item["similarity_tier"], "A")
        self.assertFalse(item["display_in_report"])

    def test_all_marketplaces_adds_mobile_de_as_separate_background_pass(self):
        mobile_html = """
        <article>
          <a href="/fahrzeuge/details.html?id=987654321">Volkswagen T-Roc 1.5 TSI DSG FWD</a>
          <div>18.500 € • EZ 2022 • 145.000 km • Benzin</div>
        </article>
        """

        def fetch(url, timeout):
            return mobile_html if "suchen.mobile.de" in url else ""

        passes = search_all_marketplaces(
            self.listing,
            timeout=3.0,
            fetch_html=fetch,
        )

        self.assertEqual([item["pass_id"] for item in passes], ["sk_cz", "mobile_de"])
        self.assertEqual(passes[1]["status"], "FOUND")
        self.assertEqual(passes[1]["candidate_count"], 1)
        self.assertFalse(passes[1]["candidates"][0]["display_in_report"])

    def test_all_marketplaces_skips_mobile_when_three_strict_local_ads_exist(self):
        candidates = [
            {
                "candidate_id": f"local-{index}",
                "description": f"Volkswagen T-Roc 1.5 TSI DSG FWD offer {index}",
                "year": 2022,
                "mileage_km": 140000 + index * 5000,
                "price_eur": 18000 + index * 500,
                "source_country": "SK",
                "market_scope": "PUBLIC_SK_CZ",
                "similarity_tier": "A",
                "price_basis": "gross_asking",
                "source_url": f"https://auto.bazos.sk/inzerat/20000{index}/car.php",
                "verified_url": True,
            }
            for index in range(3)
        ]
        local_pass = {
            "pass_id": "sk_cz",
            "candidates": candidates,
            "candidate_count": len(candidates),
        }

        with patch(
            "scrapper_demo.direct_market_search.search_local_marketplaces",
            return_value=[local_pass],
        ), patch(
            "scrapper_demo.direct_market_search.search_mobile_de"
        ) as mobile_search:
            passes = search_all_marketplaces(self.listing)

        mobile_search.assert_not_called()
        self.assertEqual(passes[0]["strict_eligible_count"], 3)
        self.assertEqual(passes[1]["status"], "SKIPPED")
        self.assertEqual(
            passes[1]["skip_reason"],
            "strict_local_sample_sufficient",
        )

    def test_all_marketplaces_uses_mobile_when_local_strict_sample_is_thin(self):
        candidates = [
            {
                "candidate_id": f"local-{index}",
                "description": f"Volkswagen T-Roc 1.5 TSI DSG FWD offer {index}",
                "year": 2022,
                "mileage_km": 145000 + index * 5000,
                "price_eur": 18000 + index * 500,
                "source_country": "SK",
                "market_scope": "PUBLIC_SK_CZ",
                "similarity_tier": "A",
                "price_basis": "gross_asking",
                "source_url": f"https://auto.bazos.sk/inzerat/30000{index}/car.php",
                "verified_url": True,
            }
            for index in range(2)
        ]
        local_pass = {"pass_id": "sk_cz", "candidates": candidates}
        mobile_pass = {"pass_id": "mobile_de", "candidates": [], "candidate_count": 0}

        with patch(
            "scrapper_demo.direct_market_search.search_local_marketplaces",
            return_value=[local_pass],
        ), patch(
            "scrapper_demo.direct_market_search.search_mobile_de",
            return_value=[mobile_pass],
        ) as mobile_search:
            passes = search_all_marketplaces(self.listing)

        mobile_search.assert_called_once()
        self.assertEqual(passes[0]["strict_eligible_count"], 2)
        self.assertEqual(passes[1]["strict_local_eligible_count"], 2)

    def test_mobile_de_preserves_http_access_diagnostics(self):
        response = requests.Response()
        response.status_code = 403
        response.url = "https://suchen.mobile.de/auto/volkswagen-tiguan-2-0-tsi.html"
        response._content = b"<html><title>Zugriff verweigert / Access denied</title></html>"
        response.encoding = "utf-8"

        def fetch(url, timeout):
            raise requests.HTTPError("403 Client Error", response=response)

        result = search_mobile_de(self.listing, timeout=3.0, fetch_html=fetch)[0]

        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["http_status"], 403)
        self.assertIn("Access denied", result["response_preview"])
        self.assertEqual(result["source_attempts"][0]["http_status"], 403)


    def test_tier_a_recognizes_delimited_m6_as_manual(self):
        listing = {
            "title": "Kia Sportage 1.6 T-GDI 110 kW",
            "engine": "1.6 T-GDI",
            "power": "110 kW",
            "fuel": "Benzin",
            "transmission": "Manuálna 6-st.",
            "drive": "FWD",
        }

        self.assertEqual(
            _similarity(
                listing,
                "Kia Sportage 1.6 T-GDI 110 kW, M6, benzin, FWD",
            )[0],
            "A",
        )
        self.assertNotEqual(
            _similarity(listing, "BMW M6 Gran Coupe 412 kW benzin FWD")[0],
            "A",
        )


if __name__ == "__main__":
    unittest.main()
