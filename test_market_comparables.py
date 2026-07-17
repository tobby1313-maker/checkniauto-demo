import unittest

from scrapper_demo.market_comparables import (
    _parse_ecb_reference_rates,
    build_market_benchmark,
    customer_link_priority,
    deduplicate_market_comparables,
    extract_grounded_market_search_pass,
    is_customer_facing_market_comparable,
    reconcile_market_comparable_urls,
    select_market_recommendations,
    strict_background_market_precheck,
    strict_local_market_precheck,
)


class MarketComparableDeduplicationTests(unittest.TestCase):
    def test_strict_background_precheck_does_not_count_irrelevant_cards(self):
        listing = {"year": 2014, "mileage_km": 200000}
        base = {
            "description": "Volkswagen Tiguan 2.0 TSI DSG 4Motion",
            "year": 2013,
            "mileage_km": 190000,
            "price_eur": 12000,
            "source_country": "DE",
            "market_scope": "BACKGROUND_EU",
            "similarity_tier": "A",
            "price_basis": "gross_asking",
            "verified_url": True,
        }
        candidates = [
            {**base, "candidate_id": "wrong-tier", "similarity_tier": "B", "source_url": "https://suchen.mobile.de/fahrzeuge/details.html?id=1"},
            {**base, "candidate_id": "wrong-year", "year": 2021, "source_url": "https://suchen.mobile.de/fahrzeuge/details.html?id=2"},
            {**base, "candidate_id": "wrong-mileage", "mileage_km": 50000, "source_url": "https://suchen.mobile.de/fahrzeuge/details.html?id=3"},
        ]

        result = strict_background_market_precheck(candidates, listing)

        self.assertEqual(result["candidate_count"], 3)
        self.assertEqual(result["eligible_count"], 0)
        self.assertFalse(result["sufficient"])

    def test_strict_local_precheck_uses_tier_year_mileage_and_dedup(self):
        listing = {
            "year": 2014,
            "mileage_km": 200000,
            "source_url": "https://auto.bazos.sk/inzerat/100/current.php",
        }
        base = {
            "description": "Volkswagen Tiguan 2.0 TSI DSG 4Motion",
            "year": 2013,
            "mileage_km": 190000,
            "price_eur": 12000,
            "source_country": "SK",
            "market_scope": "PUBLIC_SK_CZ",
            "similarity_tier": "A",
            "price_basis": "gross_asking",
            "verified_url": True,
        }
        candidates = [
            {**base, "candidate_id": "a", "source_url": "https://auto.bazos.sk/inzerat/201/a.php"},
            {**base, "candidate_id": "duplicate", "source_url": "https://auto.bazos.sk/inzerat/202/a.php"},
            {**base, "candidate_id": "b", "mileage_km": 210000, "source_url": "https://auto.bazos.sk/inzerat/203/b.php"},
            {**base, "candidate_id": "wrong-tier", "similarity_tier": "B", "mileage_km": 220000, "source_url": "https://auto.bazos.sk/inzerat/204/c.php"},
        ]

        result = strict_local_market_precheck(candidates, listing)

        self.assertEqual(result["eligible_count"], 2)
        self.assertFalse(result["sufficient"])
        self.assertEqual(result["rejection_counts"]["DUPLICATE_VEHICLE"], 1)
        self.assertEqual(
            result["rejection_counts"]["SIMILARITY_BELOW_STRICT_TIER"],
            1,
        )

    def test_grounded_detail_url_is_accepted_only_on_exact_citation_match(self):
        url = "https://auto.bazos.sk/inzerat/123456/toyota-rav4.php"
        grounded = (
            '{"candidates":[{"description":"Toyota RAV4","year":2008,'
            '"mileage_km":130000,"price_eur":7500,"source_country":"SK",'
            '"similarity_tier":"A","price_basis":"gross_asking",'
            f'"detail_url":"{url}","evidence_url":"{url}"}}]}}'
            "\n\n### Citacie z Google Search\n"
            f"- [Toyota RAV4]({url})\n"
        )

        result = extract_grounded_market_search_pass(grounded, "sk_cz")

        item = result["candidates"][0]
        self.assertEqual(item["source_url"], url)
        self.assertTrue(item["verified_url"])
        self.assertEqual(item["url_verification_status"], "VERIFIED_DETAIL")

    def test_model_invented_url_is_retained_as_unverified_diagnostic(self):
        invented = "https://auto.bazos.sk/inzerat/999999/invented.php"
        cited = "https://auto.bazos.sk/inzerat/123456/real.php"
        grounded = (
            '{"candidates":[{"description":"Toyota RAV4","year":2008,'
            '"mileage_km":130000,"price_eur":7500,"source_country":"SK",'
            '"similarity_tier":"A","price_basis":"gross_asking",'
            f'"detail_url":"{invented}","evidence_url":"{cited}"}}]}}'
            "\n\n### Citacie z Google Search\n"
            f"- [Real result]({cited})\n"
        )

        result = extract_grounded_market_search_pass(grounded, "sk_cz")

        item = result["candidates"][0]
        self.assertEqual(item["source_url"], "")
        self.assertEqual(item["claimed_source_url"], invented)
        self.assertEqual(item["url_verification_status"], "URL_UNVERIFIED")
        self.assertEqual(result["url_unverified_count"], 1)

    def test_mobile_results_card_without_detail_url_is_background_evidence(self):
        results_url = "https://suchen.mobile.de/auto/toyota-rav-4-automat.html"
        grounded = (
            '{"candidates":[{"description":"Toyota RAV4 2.0 Automatik 4x4",'
            '"year":2008,"mileage_km":149500,"price_eur":9850,'
            '"source_country":"DE","similarity_tier":"A",'
            '"price_basis":"gross_asking","detail_url":"",'
            f'"evidence_url":"{results_url}","url_kind":"RESULTS_PAGE"}}]}}'
            "\n\n### Citacie z Google Search\n"
            f"- [Mobile results]({results_url})\n"
        )

        result = extract_grounded_market_search_pass(grounded, "mobile_de")

        item = result["candidates"][0]
        self.assertFalse(item["verified_url"])
        self.assertTrue(item["background_evidence_verified"])
        self.assertEqual(item["data_provenance"], "GROUNDED_SEARCH_RESULT")
        self.assertFalse(item["display_in_report"])

    def test_inline_grounding_annotation_record_becomes_verified_detail(self):
        url = "https://www.autobazar.eu/detail/volkswagen-t-roc/123456"
        grounded = (
            "CANDIDATE | description=Volkswagen T-Roc 1.5 TSI DSG | "
            "year=2023 | mileage_km=159000 | engine=1.5 TSI | "
            "transmission=DSG | drivetrain=FWD | price_display=18 990 EUR | "
            "price_eur=18990 | price_basis=gross_asking | source_country=SK | "
            "similarity_tier=A | material_difference=rovnaka konfiguracia | "
            f"grounding_url={url}\n\n"
            "### Citacie z Google Search\n"
            f"- [VW T-Roc]({url})\n"
        )

        result = extract_grounded_market_search_pass(grounded, "sk_cz")

        item = result["candidates"][0]
        self.assertEqual(item["source_url"], url)
        self.assertTrue(item["verified_url"])
        self.assertEqual(item["url_verification_status"], "VERIFIED_DETAIL")

    def test_model_authored_grounding_marker_without_annotation_is_unverified(self):
        invented = "https://www.autobazar.eu/detail/volkswagen-t-roc/invented"
        grounded = (
            "CANDIDATE | description=Volkswagen T-Roc 1.5 TSI DSG | "
            "year=2023 | mileage_km=159000 | price_eur=18990 | "
            f"grounding_url={invented}\n"
        )

        result = extract_grounded_market_search_pass(grounded, "sk_cz")

        item = result["candidates"][0]
        self.assertFalse(item["verified_url"])
        self.assertEqual(item["url_verification_status"], "URL_UNVERIFIED")

    def test_inline_results_page_record_is_eu_background_only(self):
        url = "https://suchen.mobile.de/fahrzeuge/search.html?vc=Car&ms=25200"
        grounded = (
            "CANDIDATE | description=Volkswagen T-Roc 1.5 TSI DSG | "
            "year=2022 | mileage_km=145000 | engine=1.5 TSI | "
            "transmission=DSG | drivetrain=FWD | price_display=18 500 EUR | "
            "price_eur=18500 | price_basis=gross_asking | source_country=DE | "
            "similarity_tier=A | material_difference=rok -1 | "
            f"grounding_url={url}\n\n"
            "### Citacie z Google Search\n"
            f"- [Mobile results]({url})\n"
        )

        result = extract_grounded_market_search_pass(grounded, "mobile_de")

        item = result["candidates"][0]
        self.assertFalse(item["verified_url"])
        self.assertTrue(item["background_evidence_verified"])
        self.assertEqual(item["url_verification_status"], "VERIFIED_SEARCH_RESULT")

    @staticmethod
    def _benchmark_candidate(
        index,
        *,
        year=2008,
        mileage=130000,
        price=8000,
        market_scope="BACKGROUND_EU",
        source_country="DE",
    ):
        source_url = (
            f"https://auto.bazos.sk/inzerat/{10000 + index}/rav4.php"
            if market_scope == "PUBLIC_SK_CZ"
            else f"https://suchen.mobile.de/auto/toyota-rav-4-{index}.html"
        )
        return {
            "description": f"Toyota RAV4 comparable {index}",
            "year": year,
            "mileage_km": mileage,
            "price_eur": price,
            "source_country": source_country,
            "market_scope": market_scope,
            "similarity_tier": "A",
            "price_basis": "gross_asking",
            "source_url": source_url,
            "verified_url": market_scope == "PUBLIC_SK_CZ",
            "url_verification_status": (
                "VERIFIED_DETAIL"
                if market_scope == "PUBLIC_SK_CZ"
                else "VERIFIED_SEARCH_RESULT"
            ),
            "evidence_url": f"https://suchen.mobile.de/auto/toyota-rav-4-{index}.html",
            "background_evidence_verified": market_scope != "PUBLIC_SK_CZ",
            "data_provenance": "GROUNDED_SEARCH_RESULT",
            "search_pass": "mobile_de",
        }

    def test_tolerance_expands_year_before_mileage_and_lowers_weight(self):
        research = {
            "listing_facts": {"year": 2008, "mileage_km": 122000},
            "market_assessment": {"advertised_price_eur": 6900},
            "market_comparables": [
                self._benchmark_candidate(1, mileage=130000, market_scope="PUBLIC_SK_CZ", source_country="SK"),
                self._benchmark_candidate(2, mileage=145000, market_scope="PUBLIC_SK_CZ", source_country="SK"),
                self._benchmark_candidate(3, year=2012, mileage=135000, market_scope="PUBLIC_SK_CZ", source_country="SK"),
            ],
        }
        deduplicate_market_comparables(research, "# Other listing")

        benchmark = build_market_benchmark(research, "# Other listing")

        self.assertTrue(benchmark["available"])
        self.assertEqual(benchmark["tolerance_stage"], "EXPANDED_YEAR")
        expanded = next(
            item for item in benchmark["accepted_comparables"]
            if item["tolerance_stage"] == "EXPANDED_YEAR"
        )
        self.assertLess(expanded["weight"], 0.75)

    def test_just_outside_mileage_limit_enters_expanded_mileage_stage(self):
        research = {
            "listing_facts": {"year": 2008, "mileage_km": 122000},
            "market_assessment": {"advertised_price_eur": 6900},
            "market_comparables": [
                self._benchmark_candidate(1, mileage=130000, market_scope="PUBLIC_SK_CZ", source_country="SK"),
                self._benchmark_candidate(2, mileage=145000, market_scope="PUBLIC_SK_CZ", source_country="SK"),
                self._benchmark_candidate(3, mileage=165000, market_scope="PUBLIC_SK_CZ", source_country="SK"),
            ],
        }
        deduplicate_market_comparables(research, "# Other listing")

        benchmark = build_market_benchmark(research, "# Other listing")

        self.assertTrue(benchmark["available"])
        self.assertEqual(benchmark["tolerance_stage"], "EXPANDED_MILEAGE")
        expanded = next(
            item for item in benchmark["accepted_comparables"]
            if item["tolerance_stage"] == "EXPANDED_MILEAGE"
        )
        self.assertLess(expanded["weight"], 0.5)

    def test_zero_local_sample_can_use_hidden_european_background(self):
        research = {
            "listing_facts": {"year": 2008, "mileage_km": 122000},
            "market_assessment": {"advertised_price_eur": 6900},
            "market_comparables": [
                self._benchmark_candidate(1, mileage=126000, price=8999),
                self._benchmark_candidate(2, mileage=140000, price=9850),
                self._benchmark_candidate(3, mileage=130700, price=7500),
            ],
        }
        deduplicate_market_comparables(research, "# Other listing")

        benchmark = build_market_benchmark(research, "# Other listing")

        self.assertTrue(benchmark["available"])
        self.assertEqual(benchmark["benchmark_scope"], "EU_MIXED_BACKGROUND")
        self.assertEqual(benchmark["diagnostic_counts"]["europe_background_only"], 3)
        self.assertEqual(research["market_assessment"]["public_comparable_count"], 0)

    def test_foreign_background_requires_tier_a_year_and_tight_mileage(self):
        def foreign(index, *, year, mileage, tier="A"):
            return {
                "candidate_id": f"foreign-{index}",
                "description": "Toyota RAV4 2.0 TSI DSG 4x4",
                "year": year,
                "mileage_km": mileage,
                "price_eur": 9000 + index,
                "source_country": "DE",
                "similarity_tier": tier,
                "price_basis": "gross_asking",
                "source_url": f"https://www.mobile.de/auto-inserat/rav4/{index}.html",
                "verified_url": True,
            }

        research = {
            "listing_facts": {"year": 2016, "mileage_km": 160000},
            "market_assessment": {"advertised_price_eur": 10000},
            "market_comparables": [
                foreign(1, year=2016, mileage=180000),
                foreign(2, year=2018, mileage=160000),
                foreign(3, year=2016, mileage=200000),
                foreign(4, year=2016, mileage=160000, tier="B"),
            ],
        }
        benchmark = build_market_benchmark(research, "# Other listing")

        self.assertFalse(benchmark["available"])
        self.assertEqual(len(benchmark["eligible_comparables"]), 1)
        self.assertEqual(
            {item["exclusion_reason"] for item in benchmark["rejected_comparables"]},
            {
                "BACKGROUND_YEAR_OUTSIDE_TIGHT_BAND",
                "BACKGROUND_MILEAGE_OUTSIDE_TIGHT_BAND",
                "BACKGROUND_SIMILARITY_MISMATCH",
            },
        )

        forced_public = foreign(5, year=2016, mileage=160000)
        forced_public["market_scope"] = "PUBLIC_SK_CZ"
        forced_research = {
            "listing_facts": {"year": 2016, "mileage_km": 160000},
            "market_assessment": {"advertised_price_eur": 10000},
            "market_comparables": [forced_public],
        }
        build_market_benchmark(forced_research, "# Other listing")
        self.assertEqual(forced_public["market_scope"], "BACKGROUND_EU")
        self.assertFalse(forced_public["display_in_report"])

    def test_local_verified_details_are_counted_as_full_comparables(self):
        comparables = [
            {
                "description": f"Toyota RAV4 local {index}",
                "year": 2008,
                "mileage_km": 125000 + index * 1000,
                "price_eur": 7000 + index * 200,
                "source_country": "SK",
                "market_scope": "PUBLIC_SK_CZ",
                "similarity_tier": "A",
                "price_basis": "gross_asking",
                "source_url": f"https://auto.bazos.sk/inzerat/{1000 + index}/rav4.php",
                "verified_url": True,
                "url_verification_status": "VERIFIED_DETAIL",
                "search_pass": "sk_cz",
            }
            for index in range(3)
        ]
        research = {
            "listing_facts": {"year": 2008, "mileage_km": 122000},
            "market_assessment": {"advertised_price_eur": 6900},
            "market_comparables": comparables,
        }
        deduplicate_market_comparables(research, "# Other listing")

        benchmark = build_market_benchmark(research, "# Other listing")

        self.assertEqual(benchmark["benchmark_scope"], "SK_CZ")
        self.assertEqual(benchmark["diagnostic_counts"]["full_comparable_accepted"], 3)
        self.assertEqual(benchmark["diagnostic_counts"]["europe_background_only"], 0)

    def test_public_benchmark_and_links_use_strict_configuration_and_price_band(self):
        def candidate(index, *, tier, price, mileage, year=2008):
            return {
                "candidate_id": f"strict-{index}",
                "description": f"Toyota RAV4 2.0 TSI DSG 4x4 {index}",
                "year": year,
                "mileage_km": mileage,
                "price_eur": price,
                "price_basis": "gross_asking",
                "source_country": "SK",
                "market_scope": "PUBLIC_SK_CZ",
                "similarity_tier": tier,
                "source_url": f"https://auto.bazos.sk/inzerat/{index}/rav4.php",
                "verified_url": True,
                "url_verification_status": "VERIFIED_DETAIL",
                "search_pass": "sk_cz",
            }

        research = {
            "listing_facts": {
                "year": 2008,
                "mileage_km": 122000,
                "asking_price_gross_eur": 10000,
            },
            "market_assessment": {"advertised_price_eur": 10000},
            "market_comparables": [
                candidate(1, tier="A", price=11000, mileage=126000),
                candidate(2, tier="A", price=15000, mileage=127000),
                candidate(3, tier="B", price=10500, mileage=128000),
                candidate(4, tier="A", price=9900, mileage=None),
                candidate(5, tier="A", price=9900, mileage=125000, year=2015),
                candidate(6, tier="A", price=9900, mileage=220000),
            ],
        }

        deduplicate_market_comparables(research, "# Other listing")
        benchmark = build_market_benchmark(research, "# Other listing")

        self.assertFalse(benchmark["available"])
        self.assertEqual(benchmark["diagnostic_counts"]["similarity_rejected"], 1)
        self.assertEqual(
            benchmark["recommendation_policy"]["recommended_candidate_ids"],
            ["strict-1"],
        )
        flags = {
            item["candidate_id"]: item["display_in_report"]
            for item in research["market_comparables"]
        }
        self.assertEqual(
            flags,
            {
                "strict-1": True,
                "strict-2": False,
                "strict-3": False,
                "strict-4": False,
                "strict-5": False,
                "strict-6": False,
            },
        )

    def test_recommendations_have_no_fallback_when_no_candidate_is_in_price_band(self):
        items = [
            {
                "candidate_id": "far-away",
                "source_url": "https://auto.bazos.sk/inzerat/1/rav4.php",
                "verified_url": True,
                "market_scope": "PUBLIC_SK_CZ",
                "similarity_tier": "A",
                "normalized_price_eur": 13000,
            }
        ]

        self.assertEqual(select_market_recommendations(items, 10000), [])
        self.assertFalse(items[0]["display_in_report"])

    def test_market_diagnostics_separate_url_year_and_mileage_rejections(self):
        unverified_local = {
            "description": "Unverified local Toyota",
            "year": 2008,
            "mileage_km": 130000,
            "price_eur": 7000,
            "source_country": "SK",
            "market_scope": "PUBLIC_SK_CZ",
            "similarity_tier": "A",
            "price_basis": "gross_asking",
            "search_pass": "sk_cz",
            "url_verification_status": "URL_UNVERIFIED",
            "verified_url": False,
        }
        far_year = self._benchmark_candidate(2, year=2015, mileage=130000)
        far_mileage = self._benchmark_candidate(3, year=2008, mileage=250000)
        research = {
            "listing_facts": {"year": 2008, "mileage_km": 122000},
            "market_assessment": {"advertised_price_eur": 6900},
            "market_search_summary": {
                "search_results_found_count": 3,
                "candidate_count": 3,
                "nothing_found_passes": 0,
            },
            "market_comparables": [unverified_local, far_year, far_mileage],
        }
        deduplicate_market_comparables(research, "# Other listing")

        benchmark = build_market_benchmark(research, "# Other listing")

        self.assertFalse(benchmark["available"])
        self.assertEqual(benchmark["diagnostic_counts"]["url_unverified"], 1)
        self.assertEqual(benchmark["diagnostic_counts"]["year_rejected"], 1)
        self.assertEqual(benchmark["diagnostic_counts"]["mileage_rejected"], 1)
        self.assertEqual(
            research["market_assessment"]["summary"],
            "Boli nájdené ponuky, ale nepodarilo sa overiť ich detailné URL.",
        )

    def test_czk_uses_30_calendar_day_ecb_average_and_other_currency_latest(self):
        payload = b"""<?xml version='1.0' encoding='UTF-8'?>
<Envelope xmlns='urn:gesmes'><Cube>
  <Cube time='2026-06-01'><Cube currency='CZK' rate='23.000'/><Cube currency='PLN' rate='4.100'/></Cube>
  <Cube time='2026-06-20'><Cube currency='CZK' rate='24.000'/><Cube currency='PLN' rate='4.200'/></Cube>
  <Cube time='2026-07-10'><Cube currency='CZK' rate='25.000'/><Cube currency='PLN' rate='4.300'/></Cube>
</Cube></Envelope>"""

        result = _parse_ecb_reference_rates(payload)

        self.assertEqual(result["rate_date"], "2026-07-10")
        self.assertEqual(result["rates_per_eur"]["CZK"], 24.5)
        self.assertEqual(result["rates_per_eur"]["PLN"], 4.3)
        self.assertEqual(
            result["rate_details"]["CZK"],
            {
                "method": "ECB_30_CALENDAR_DAY_AVERAGE",
                "window_start": "2026-06-11",
                "window_end": "2026-07-10",
                "observations": 2,
            },
        )

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
        supported_czech = {
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
        self.assertTrue(is_customer_facing_market_comparable(supported_czech))
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
        self.assertIn("sauto.cz", result["market_comparables"][0]["source_url"])
        self.assertTrue(result["market_comparables"][0]["display_in_report"])
        self.assertFalse(result["market_comparables"][1]["display_in_report"])
        self.assertEqual(result["market_assessment"]["comparable_count"], 2)
        self.assertEqual(result["market_assessment"]["public_comparable_count"], 1)
        self.assertEqual(result["market_assessment"]["eur_priced_comparable_count"], 2)
        self.assertEqual(result["market_assessment"]["observed_market_low_eur"], 13900)
        self.assertEqual(result["market_assessment"]["observed_market_high_eur"], 15000)
        self.assertEqual(result["market_assessment"]["observed_market_average_eur"], 14450)

    def test_background_czk_and_pln_prices_use_ecb_rates_for_median(self):
        research = {
            "market_assessment": {"advertised_price_eur": 15000},
            "listing_facts": {
                "year": 2016,
                "advertised_mileage_km": 160000,
                "asking_price_gross_eur": 16000,
            },
            "market_comparables": [
                {
                    "description": "Hyundai Tucson 1.6 T-GDi 4x4 DCT",
                    "year": 2016,
                    "mileage_km": 150000,
                    "price_display": "350 000 CZK",
                    "source_country": "CZ",
                    "similarity_tier": "A",
                    "price_basis": "gross_asking",
                    "source_url": "https://www.sauto.cz/osobni/detail/hyundai/tucson/1",
                    "verified_url": True,
                },
                {
                    "description": "Hyundai Tucson 1.6 T-GDi 4x4 DCT",
                    "year": 2017,
                    "mileage_km": 170000,
                    "price_display": "60 000 PLN",
                    "source_country": "PL",
                    "similarity_tier": "A",
                    "price_basis": "gross_asking",
                    "source_url": "https://www.otomoto.pl/osobowe/oferta/hyundai-tucson-ID1.html",
                    "verified_url": True,
                },
                {
                    "description": "Hyundai Tucson 1.6 T-GDi 4x4 DCT",
                    "year": 2016,
                    "mileage_km": 165000,
                    "price_eur": 14000,
                    "source_country": "DE",
                    "similarity_tier": "A",
                    "price_basis": "gross_asking",
                    "source_url": "https://www.mobile.de/auto-inserat/hyundai-tucson/3.html",
                    "verified_url": True,
                },
            ],
        }
        deduplicate_market_comparables(research, "# Other listing")
        benchmark = build_market_benchmark(
            research,
            "# Other listing",
            exchange_rates={
                "source": "ECB_REFERENCE_RATE",
                "rate_date": "2026-07-10",
                "rates_per_eur": {"EUR": 1, "CZK": 25, "PLN": 4.285714},
                "rate_details": {
                    "CZK": {"method": "ECB_30_CALENDAR_DAY_AVERAGE"}
                },
            },
        )

        self.assertTrue(benchmark["available"])
        self.assertEqual(benchmark["median_eur"], 14000)
        self.assertEqual(benchmark["advertised_price_eur"], 16000)
        self.assertEqual(benchmark["benchmark_scope"], "EU_MIXED_BACKGROUND")
        self.assertEqual(research["market_assessment"]["price_view"], "fair")
        self.assertEqual(research["market_assessment"]["benchmark_comparable_count"], 3)
        self.assertEqual(
            research["market_comparables"][0]["normalization_method"],
            "ECB_30_CALENDAR_DAY_AVERAGE",
        )
        self.assertFalse(research["market_comparables"][1]["display_in_report"])

    def test_thin_sample_and_tier_c_do_not_classify_price(self):
        research = {
            "market_assessment": {"advertised_price_eur": 9000},
            "listing_facts": {"year": 2016, "advertised_mileage_km": 160000},
            "market_comparables": [
                {
                    "description": "Different engine fallback",
                    "year": 2016,
                    "mileage_km": 160000,
                    "price_eur": 13000,
                    "similarity_tier": "C",
                    "source_country": "DE",
                    "source_url": "https://www.mobile.de/auto-inserat/example/1.html",
                    "verified_url": True,
                },
                {
                    "description": "Net export offer",
                    "year": 2016,
                    "mileage_km": 160000,
                    "price_display": "10 000 EUR netto",
                    "similarity_tier": "A",
                    "price_basis": "net",
                    "source_country": "DE",
                    "source_url": "https://www.mobile.de/auto-inserat/example/2.html",
                    "verified_url": True,
                },
            ],
        }
        deduplicate_market_comparables(research, "# Other listing")
        benchmark = build_market_benchmark(research, "# Other listing")

        self.assertFalse(benchmark["available"])
        self.assertEqual(benchmark["price_view"], "requires_manual_verification")
        self.assertEqual(len(benchmark["accepted_comparables"]), 0)

    def test_three_local_ads_take_priority_over_foreign_background_prices(self):
        comparables = []
        for index, price in enumerate((15000, 15200, 15400), start=1):
            comparables.append(
                {
                    "description": f"Local A-tier {index}",
                    "year": 2016,
                    "mileage_km": 150000 + (index * 5000),
                    "price_eur": price,
                    "similarity_tier": "A",
                    "source_country": "CZ",
                    "source_url": f"https://www.sauto.cz/osobni/detail/hyundai/tucson/{index}",
                    "verified_url": True,
                }
            )
        comparables.append(
            {
                "description": "Cheaper foreign background",
                "year": 2016,
                "mileage_km": 160000,
                "price_eur": 9000,
                "similarity_tier": "A",
                "source_country": "DE",
                "source_url": "https://www.mobile.de/auto-inserat/hyundai-tucson/99.html",
                "verified_url": True,
            }
        )
        research = {
            "market_assessment": {"advertised_price_eur": 15300},
            "listing_facts": {"year": 2016, "advertised_mileage_km": 160000},
            "market_comparables": comparables,
        }
        deduplicate_market_comparables(research, "# Other listing")

        benchmark = build_market_benchmark(research, "# Other listing")

        self.assertEqual(benchmark["benchmark_scope"], "SK_CZ")
        self.assertEqual(benchmark["median_eur"], 15200)
        self.assertEqual(benchmark["foreign_background_median_eur"], 9000)
        self.assertEqual(research["market_assessment"]["price_view"], "fair")

    def test_mismatched_narrative_urls_are_not_repaired_from_citations(self):
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

        for item in result["market_comparables"]:
            self.assertEqual(item["source_url"], "")
            self.assertFalse(item["verified_url"])
            self.assertEqual(item["url_verification_status"], "URL_UNVERIFIED")
            self.assertTrue(item["claimed_source_url"])

        deduplicated = deduplicate_market_comparables(result, "# Other listing")
        self.assertEqual(len(deduplicated["market_comparables"]), 0)

    def test_unverified_market_sources_are_removed_from_source_registry(self):
        research = {
            "market_assessment": {},
            "market_comparables": [],
            "sources_used": [
                {
                    "source_id": "stale-market",
                    "source_type": "MARKET_COMPARABLE",
                    "source_url": "https://auto.bazos.sk/inzerat/111111/stale.php",
                    "verified_url": True,
                    "used_for": "market comparable",
                },
                {
                    "source_id": "reliability",
                    "source_type": "TECHNICAL_PUBLICATION",
                    "source_url": "https://example.invalid/reliability",
                    "verified_url": True,
                    "used_for": "reliability",
                },
            ],
        }

        result = deduplicate_market_comparables(research, "# Other listing")

        self.assertEqual(
            [item["source_id"] for item in result["sources_used"]],
            ["reliability"],
        )

    def test_mobile_detail_id_is_preserved_but_tracking_query_is_removed(self):
        research = {
            "market_assessment": {},
            "market_comparables": [
                {
                    "description": "Hyundai Tucson",
                    "price_eur": 15000,
                    "source_country": "DE",
                    "source_url": "https://suchen.mobile.de/fahrzeuge/details.html?id=12345&utm_source=test",
                    "verified_url": True,
                }
            ],
        }
        grounded = (
            "### Citacie z Google Search\n"
            "- [Mobile](https://suchen.mobile.de/fahrzeuge/details.html?id=12345&utm_source=test)\n"
        )

        result = reconcile_market_comparable_urls(research, grounded)

        self.assertEqual(
            result["market_comparables"][0]["source_url"],
            "https://suchen.mobile.de/fahrzeuge/details.html?id=12345",
        )


if __name__ == "__main__":
    unittest.main()
