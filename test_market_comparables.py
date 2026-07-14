import unittest

from scrapper_demo.market_comparables import (
    customer_link_priority,
    deduplicate_market_comparables,
    is_customer_facing_market_comparable,
    reconcile_market_comparable_urls,
)


class MarketComparableDeduplicationTests(unittest.TestCase):
    def test_same_vehicle_cross_posted_on_two_sites_is_kept_once(self):
        research = {
            "market_assessment": {"available": True, "comparable_count": 3},
            "market_comparables": [
                {
                    "description": "Dacia Duster 1.3 TCe 4x4 Prestige, 2020",
                    "year": 2020,
                    "mileage_km": 92540,
                    "price_display": "359 900 CZK",
                    "vin": "VF1HJD40465517684",
                    "seller_or_location": "Auto ESA Praha",
                    "relevance": "HIGH",
                    "source_url": "https://www.sauto.cz/osobni/detail/dacia/duster/210093410",
                    "verified_url": True,
                },
                {
                    "description": "Dacia Duster 1.3 TCe 4x4 Prestige, 2020",
                    "year": 2020,
                    "mileage_km": 92540,
                    "price_display": "368 900 CZK",
                    "vin": "VF1HJD40465517684",
                    "seller_or_location": "Auto ESA Praha",
                    "relevance": "MEDIUM",
                    "source_url": "https://www.tipcars.com/auto-inserat/dacia-duster/example.html",
                    "verified_url": True,
                },
                {
                    "description": "Dacia Duster 1.3 TCe 4x4 Celebration, 2020",
                    "year": 2020,
                    "mileage_km": 165000,
                    "price_eur": 11590,
                    "price_display": "11 590 EUR",
                    "vin": "VF1HJD40565034429",
                    "seller_or_location": "Nitra",
                    "relevance": "HIGH",
                    "source_url": "https://www.autobazar.eu/detail/dacia-duster/example/",
                    "verified_url": True,
                },
            ],
        }

        result = deduplicate_market_comparables(research, "# Other listing")

        self.assertEqual(len(result["market_comparables"]), 2)
        self.assertEqual(result["market_assessment"]["comparable_count"], 2)
        self.assertEqual(result["market_assessment"]["observed_market_low_eur"], 11590)
        self.assertEqual(result["market_assessment"]["observed_market_high_eur"], 11590)

    def test_fingerprint_deduplicates_without_vin_but_keeps_distinct_mileage(self):
        research = {
            "market_assessment": {"available": True},
            "market_comparables": [
                {
                    "description": "Suzuki Grand Vitara 2.4 VVT automat 4x4, 2011",
                    "year": 2011,
                    "mileage_km": 98673,
                    "price_eur": 9900,
                    "seller_or_location": "Brno",
                    "relevance": "HIGH",
                    "source_url": "https://www.sauto.cz/osobni/detail/suzuki/grand-vitara/1",
                    "verified_url": True,
                },
                {
                    "description": "Suzuki Grand Vitara 2.4 VVT automat 4WD, 2011",
                    "year": 2011,
                    "mileage_km": 98673,
                    "price_eur": 10100,
                    "seller_or_location": "Brno",
                    "relevance": "MEDIUM",
                    "source_url": "https://www.tipcars.com/auto-inserat/suzuki-grand-vitara/1.html",
                    "verified_url": True,
                },
                {
                    "description": "Suzuki Grand Vitara 2.4 VVT automat 4x4, 2011",
                    "year": 2011,
                    "mileage_km": 110450,
                    "price_eur": 10200,
                    "seller_or_location": "Praha",
                    "relevance": "HIGH",
                    "source_url": "https://www.sauto.cz/osobni/detail/suzuki/grand-vitara/2",
                    "verified_url": True,
                },
            ],
        }

        result = deduplicate_market_comparables(research, "# Other listing")

        self.assertEqual(len(result["market_comparables"]), 2)
        self.assertEqual(
            {item["mileage_km"] for item in result["market_comparables"]},
            {98673, 110450},
        )

    def test_analyzed_listing_and_cross_post_are_removed(self):
        listing = """# Dacia Duster 1.3 TCe 4x4 Prestige

**Source:** https://auto.bazos.sk/inzerat/192918253/dacia-duster.php

## Price
- **Price:** 12 690 EUR

| Mileage | 135000 km |

## Seller Note
r.v. 5/2020
"""
        research = {
            "market_assessment": {"available": True},
            "market_comparables": [
                {
                    "description": "Dacia Duster 1.3 TCe 4x4 Prestige, 2020",
                    "year": 2020,
                    "mileage_km": 135000,
                    "price_eur": 12690,
                    "relevance": "HIGH",
                    "source_url": "https://www.other-market.example/auto-inserat/dacia-duster-crosspost",
                    "verified_url": True,
                },
                {
                    "description": "Dacia Duster 1.3 TCe 4x4 Prestige, 2020",
                    "year": 2020,
                    "mileage_km": 108500,
                    "price_eur": 12900,
                    "relevance": "HIGH",
                    "source_url": "https://www.other-market.example/auto-inserat/dacia-duster-other",
                    "verified_url": True,
                },
            ],
        }

        result = deduplicate_market_comparables(research, listing)

        self.assertEqual(len(result["market_comparables"]), 1)
        self.assertEqual(result["market_comparables"][0]["mileage_km"], 108500)

    def test_customer_links_are_limited_and_ranked_slovakia_then_czechia(self):
        sk = {
            "source_url": "https://auto.bazos.sk/inzerat/1/duster.php",
            "verified_url": True,
        }
        cz = {
            "source_url": "https://auto.bazos.cz/inzerat/2/duster.php",
            "verified_url": True,
        }
        unsupported = {
            "source_url": "https://www.sauto.cz/osobni/detail/dacia/duster/3",
            "verified_url": True,
        }
        foreign_autobazar = {
            "source_url": "https://www.autobazar.eu/detail/dacia-duster/4/",
            "source_country": "DE",
            "price_display": "12 500 EUR",
            "verified_url": True,
        }

        self.assertGreater(customer_link_priority(sk), customer_link_priority(cz))
        self.assertTrue(is_customer_facing_market_comparable(sk))
        self.assertTrue(is_customer_facing_market_comparable(cz))
        self.assertFalse(is_customer_facing_market_comparable(unsupported))
        self.assertFalse(is_customer_facing_market_comparable(foreign_autobazar))

    def test_supported_cross_post_wins_while_foreign_ads_stay_in_market_stats(self):
        research = {
            "market_assessment": {"available": True},
            "market_comparables": [
                {
                    "description": "Dacia Duster 1.3 TCe 4x4 Prestige 2020",
                    "year": 2020,
                    "mileage_km": 92540,
                    "price_eur": 13900,
                    "vin": "VF1HJD40465517684",
                    "relevance": "HIGH",
                    "source_url": "https://www.sauto.cz/osobni/detail/dacia/duster/1",
                    "verified_url": True,
                },
                {
                    "description": "Dacia Duster 1.3 TCe 4x4 Prestige 2020",
                    "year": 2020,
                    "mileage_km": 92540,
                    "price_eur": 13900,
                    "vin": "VF1HJD40465517684",
                    "relevance": "MEDIUM",
                    "source_url": "https://auto.bazos.cz/inzerat/2/duster.php",
                    "verified_url": True,
                },
                {
                    "description": "Dacia Duster 1.3 TCe 4x4 Prestige 2021",
                    "year": 2021,
                    "mileage_km": 110000,
                    "price_eur": 15000,
                    "source_country": "DE",
                    "relevance": "HIGH",
                    "source_url": "https://www.mobile.de/auto-inserat/duster/3.html",
                    "verified_url": True,
                },
            ],
        }

        result = deduplicate_market_comparables(research, "# Other listing")

        self.assertEqual(len(result["market_comparables"]), 2)
        self.assertIn("bazos.cz", result["market_comparables"][0]["source_url"])
        self.assertTrue(result["market_comparables"][0]["display_in_report"])
        self.assertFalse(result["market_comparables"][1]["display_in_report"])
        self.assertEqual(result["market_assessment"]["comparable_count"], 2)
        self.assertEqual(result["market_assessment"]["public_comparable_count"], 1)
        self.assertEqual(result["market_assessment"]["eur_priced_comparable_count"], 2)
        self.assertEqual(result["market_assessment"]["observed_market_low_eur"], 13900)
        self.assertEqual(result["market_assessment"]["observed_market_high_eur"], 15000)
        self.assertEqual(result["market_assessment"]["observed_market_average_eur"], 14450)

    def test_stale_narrative_urls_are_replaced_by_current_grounding_citations(self):
        research = {
            "market_assessment": {"available": True},
            "market_comparables": [
                {
                    "description": "Hyundai Tucson 1.6 T-GDi Premium 4x4 DCT",
                    "price_eur": 16000,
                    "source_url": "https://www.autobazar.eu/hyundai-tucson-16-t-gdi-premium-4x4-dtc-id28014498.html",
                    "verified_url": True,
                },
                {
                    "description": "Hyundai Tucson 1.6 T-GDi 4x4 2016",
                    "price_display": "359 000 CZK",
                    "source_url": "https://auto.bazos.cz/inzerat/192663959/hyundai-tucson-16-t-gdi-4x4-62016-130kw-1majitel.php",
                    "verified_url": True,
                },
                {
                    "description": "Unsupported stale Autobazar.sk result",
                    "price_eur": 13990,
                    "source_url": "https://www.autobazar.sk/9937746/hyundai-tucson-old/",
                    "verified_url": True,
                },
            ],
        }
        grounded = """## Trh
- [old EU](https://www.autobazar.eu/hyundai-tucson-16-t-gdi-premium-4x4-dtc-id28014498.html)

### Citacie z Google Search
- [autobazar.eu](https://www.autobazar.eu/en/detail/hyundai-tucson-16-t-gdi-premium-4x4-dtc/Am1qTX0VW5q)
- [bazos.cz](https://auto.bazos.cz/inzerat/221171808/hyundai-tucson-16-t-gdi-4x4-62016-130kw-1majitel.php)
- [autobazar.sk category](https://hyundai-tucson.autobazar.sk)
"""

        result = reconcile_market_comparable_urls(research, grounded)

        self.assertIn("/detail/", result["market_comparables"][0]["source_url"])
        self.assertIn("221171808", result["market_comparables"][1]["source_url"])
        self.assertEqual(result["market_comparables"][2]["source_url"], "")
        self.assertFalse(result["market_comparables"][2]["verified_url"])

        deduplicated = deduplicate_market_comparables(result, "# Other listing")
        self.assertEqual(len(deduplicated["market_comparables"]), 2)


if __name__ == "__main__":
    unittest.main()
