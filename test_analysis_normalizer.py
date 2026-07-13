import unittest

from analysis_normalizer import add_verified_comparable_links, normalize_analysis_markdown


CAR_INFO = """
# Mitsubishi ASX 1,6 benzÃ­n MIVEC 2WD Invite

- **Mileage:** 178 000 km
- **VIN:** JMBXNGA1WBZ019167
"""


SUZUKI_CAR_INFO = """
# Suzuki SX4 S-Cross 1.4 BoosterJet 4x4 A/T

- **Mileage:** 150 195 km
- **VIN:** TSMJYBA2S00546662
"""


NO_VAT_CAR_INFO = """
# Kia Sportage 2.0 CVVT 16V 2WD EX AT

- **Price:** 9 990 EUR
- **Mileage:** 164 000 km
"""


VAT_CAR_INFO = """
# Volkswagen Passat 2.0 TDI

- **Price:** 12 000 EUR bez DPH
- **Additional info:** Možný odpočet DPH.
"""


BROKEN_MARKDOWN = """# ðŸš— AnalÃ½za: Mitsubishi ASX 1,6 benzÃ­n MIVEC 2WD Invite

## ðŸ§¾ DÃ¡ta z inzerÃ¡tu

| PoloÅ¾ka | Hodnota | PoznÃ¡mka |
|---|---:|---|
| Cena | 6 690 EUR | VzhÄ¾adom na vek a typ vozidla. |
| Rok | 2012 | UvedenÃ© v popise. |
| NÃ¡jazd |
178 000 km | UvedenÃ© v popise. |
| Motor | 1.6 benzÃ­n MIVEC, 86 kW | SpoÄ¾ahlivÃ½ motor, ale s potenciÃ¡lnymi rizik mi pri vysokom nÃ¡jazde. |
| VIN | JMBXNGA1WBZ019167 | UvedenÃ©. |

## ðŸ” VIN a transparentnosÅ¥

- **VIN:** JMBXNGA1WBZ01916
- **Verdikt
Riziko. Hoci je VIN uvedenÃ½, online histÃ³ria nie je verejne dostupnÃ¡.

## ðŸŒ WebovÃ© overenie

WebovÃ© overenie potvrdzuje rizikÃ¡. [autorubik.sk](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQ
6fyugs72K4x2b9e8l55X8cFgu9KPW08nzxVrukk_tqxxSgs6F8oeXiXYQqjTebVK1Az
4km1N9WD3QfzJpeTV7M0fXQ_SUKY3aGRjT_SqCl-s40zPLuhBo4oRthL_n1Gh8uq
HVVa_Ie4n9Zloakz_rVuyCBu7VJtj-BMA8cP6qEw=), [garaz.cz](https://vertexaisearch.cloud.google.com/grounding-api-
redirect/AUZIYQGIl2_6aJq6IoUGXlbHMooVrG7GT865m-MVeK92xP5OIrZti)

1. **Ako vysvetÄ¾ujete nezrovnalosÅ¥ v nÃ¡jazde kilometrov** medzi inzerÃ¡tom (17
000 km) a zÃ¡znamom v servisnej kniÅ¾ke z roku 2019 (179 567 km)?
"""


SUZUKI_FALSE_NEGATIVE_MARKDOWN = """# Analýza: Suzuki SX4 S-Cross

## Rýchle zhrnutie

- **Cena:** Férová - cena vozidla je v rámci trhového priemeru, ale absencia nájazdu kilometrov v inzeráte môže byť argumentom na mierne zníženie ceny.
- **Najväčšie riziko:** Neúplná história vozidla kvôli neoveriteľnému VIN a chýbajúci údaj o nájazde kilometrov v inzeráte.

## Dáta z inzerátu

| Položka | Hodnota | Poznámka |
|---|---:|---|
| Nájazd | 150 195 km | Údaj z fotografie prístrojovej dosky. |

## VIN a transparentnosť

VIN číslo TSMJYBA2S00546662 je uvedené v inzeráte. Jeho formát je v poriadku, avšak nebolo možné ho v rámci verejne dostupných zdrojov priamo identifikovať v databázach histórie vozidla alebo overiť jeho minulosť. To zvyšuje riziko skrytých vád a nejasnej minulosti vozidla.

## Webové overenie

- Nevyskytli sa žiadne verejné záznamy alebo história spojená s daným VIN číslom (Google Search, URL nie je priamo overiteľná).

## Cena a vyjednávanie

Vzhľadom na chýbajúci údaj o nájazde kilometrov v pôvodnom inzeráte (ktorý bol zistený až z fotky), ako aj potenciálne náklady na údržbu prevodovky a pohonu 4x4, je možné skúsiť vyjednávať o miernom znížení ceny.

## Zápory / riziká

- Chýbajúci údaj o nájazde kilometrov v popise inzerátu (zistený až z fotky).
- Neoveriteľná história vozidla cez VIN číslo vo verejných databázach.
- Potenciálne drahé opravy motora, automatickej prevodovky a pohonu 4x4 pri zanedbanej údržbe alebo vyššom nájazde.

## Záverečné odporúčanie

Hoci sú vizuálne nedostatky minimálne a nájazd kilometrov zistený z fotky je konzistentný s opotrebením, existujú riziká spojené s neoveriteľným VIN a potenciálnymi nákladmi na údržbu motora, prevodovky a pohonu 4x4.
"""


INVALID_VIN_MARKDOWN = """# Analýza: Test

## VIN a transparentnosť

VIN číslo TSMJYBA2S00546662 je uvedené, ale kontrola ukazuje neplatný alebo konfliktný VIN.

## Zápory / riziká

- Neplatný alebo konfliktný VIN je reálne riziko identity vozidla.
"""


class AnalysisNormalizerTest(unittest.TestCase):
    def test_strips_outer_markdown_fence(self):
        fenced = "```markdown\n# Analýza: Test\n\n- **Cena:** 10 000 EUR\n```\n"
        cleaned = normalize_analysis_markdown(fenced, CAR_INFO)
        self.assertTrue(cleaned.startswith("# Analýza: Test\n"))
        self.assertNotIn("```markdown", cleaned)
        self.assertNotIn("\n```", cleaned)

    def test_repairs_table_rows_and_split_labels(self):
        cleaned = normalize_analysis_markdown(BROKEN_MARKDOWN, CAR_INFO)
        self.assertIn("| NÃ¡jazd | 178 000 km | UvedenÃ© v popise. |", cleaned)
        self.assertNotIn("\n178 000 km | UvedenÃ©", cleaned)
        # Split bold markers are preserved as-is (Gemini's output is not reformatted)
        self.assertIn("- **Verdikt", cleaned)
        self.assertIn("Riziko.", cleaned)

    def test_applies_canonical_vin_and_mileage(self):
        cleaned = normalize_analysis_markdown(BROKEN_MARKDOWN, CAR_INFO)
        self.assertNotIn("JMBXNGA1WBZ01916\n", cleaned)
        self.assertIn("- **VIN:** JMBXNGA1WBZ019167", cleaned)
        self.assertIn("inzerÃ¡tom (178 000 km)", cleaned)

    def test_removes_grounding_redirect_urls_and_source_labels(self):
        cleaned = normalize_analysis_markdown(BROKEN_MARKDOWN, CAR_INFO)
        self.assertNotIn("vertexaisearch.cloud.google.com", cleaned)
        self.assertNotIn("autorubik.sk", cleaned)
        self.assertNotIn("garaz.cz", cleaned)

    def test_removes_public_markdown_and_plain_urls_but_keeps_finding(self):
        markdown = (
            "- Prevodovku treba skontrolovať ([Honda](https://www.honda.eu/)).\n"
            "- Servisná kontrola: https://www.example.org/check?part=cvt.\n"
        )

        cleaned = normalize_analysis_markdown(markdown, CAR_INFO)

        self.assertIn("- Prevodovku treba skontrolovať.", cleaned)
        self.assertIn("- Servisná kontrola:", cleaned)
        self.assertNotIn("Honda", cleaned)
        self.assertNotIn("http://", cleaned)
        self.assertNotIn("https://", cleaned)

    def test_keeps_only_verified_comparable_links_in_price_section(self):
        markdown = (
            "## Cena a vyjednávanie\n\n"
            "- [Kia Sportage 2.0 CVVT A/T (2013, 105 000 km)]"
            "(https://www.autobazar.eu/ponuka/kia-sportage-2013) – 10 999 EUR.\n"
            "- [Blocked example](https://example.com/ad) – 9 000 EUR.\n\n"
            "## Webové overenie\n\n"
            "- [Research source](https://www.autobazar.eu/source)\n"
        )

        cleaned = normalize_analysis_markdown(markdown, CAR_INFO)

        self.assertIn(
            "[Kia Sportage 2.0 CVVT A/T (2013, 105 000 km)]"
            "(https://www.autobazar.eu/ponuka/kia-sportage-2013)",
            cleaned,
        )
        self.assertNotIn("https://example.com/ad", cleaned)
        self.assertNotIn("https://www.autobazar.eu/source", cleaned)
        self.assertNotIn("Research source", cleaned)

    def test_adds_verified_links_to_matching_comparables_only(self):
        markdown = (
            "## 💰 Cena a vyjednávanie\n\n"
            "Suzuki Grand Vitara 2.4i automat (r.v. 2010, 86 980 km) — 9 900 EUR — ponuka z ČR.\n"
            "Suzuki Grand Vitara 2.4 automat (r.v. 2011, 100 000 km) — 9 500 EUR — podobná ponuka.\n\n"
            "## Webové overenie\n\n"
            "Suzuki Grand Vitara — 9 900 EUR.\n"
        )
        comparables = [
            {
                "description": "Suzuki Grand Vitara 2.4i automat, 2010, 86 980 km",
                "price_eur": 9900,
                "mileage_km": 86980,
                "source_url": "https://www.sauto.cz/osobni/detail/suzuki/grand-vitara/1",
                "verified_url": True,
            },
            {
                "description": "Suzuki Grand Vitara 2.4 automat, 2011, 100 000 km",
                "price_eur": 9500,
                "mileage_km": 100000,
                "source_url": "https://auto.bazos.sk/inzerat/2/grand-vitara.php",
                "verified_url": True,
            },
        ]

        linked = add_verified_comparable_links(markdown, comparables)

        self.assertIn(
            "[Suzuki Grand Vitara 2.4i automat (r.v. 2010, 86 980 km)]"
            "(https://www.sauto.cz/osobni/detail/suzuki/grand-vitara/1)",
            linked,
        )
        self.assertIn(
            "[Suzuki Grand Vitara 2.4 automat (r.v. 2011, 100 000 km)]"
            "(https://auto.bazos.sk/inzerat/2/grand-vitara.php)",
            linked,
        )
        self.assertNotIn("## Webové overenie\n\n[Suzuki", linked)

    def test_does_not_add_unverified_or_ambiguous_comparable_link(self):
        markdown = "## Cena a vyjednávanie\n\nSuzuki Grand Vitara — 9 900 EUR — podobná ponuka.\n"
        comparables = [
            {
                "description": "Suzuki Grand Vitara 2010",
                "price_eur": 9900,
                "source_url": "https://cars.example.invalid/1",
                "verified_url": False,
            }
        ]

        self.assertEqual(markdown, add_verified_comparable_links(markdown, comparables))

    def test_sorts_cost_tables_and_removes_repeated_eur_units(self):
        markdown = """## Očakávané náklady na najbližších 30 000 km

| Položka | Prečo | Odhad EUR | Urgentnosť |
|---|---|---:|---|
| Diagnostika | Kontrola chýb | 50 - 100 EUR | Vysoká |
| Rozvody | Pri hluku | 800 - 1 500 EUR | Stredná |
| Olej | Vstupný servis | 100 - 150 € | Stredná |

**Pravdepodobný orientačný súčet:** 250 - 450 EUR
"""

        cleaned = normalize_analysis_markdown(markdown, CAR_INFO)

        rows = [line for line in cleaned.splitlines() if line.startswith("| ")][2:]
        self.assertEqual(rows[0], "| Rozvody | Pri hluku | 800 - 1 500 | Stredná |")
        self.assertEqual(rows[1], "| Olej | Vstupný servis | 100 - 150 | Stredná |")
        self.assertEqual(rows[2], "| Diagnostika | Kontrola chýb | 50 - 100 | Vysoká |")
        self.assertIn("**Pravdepodobný orientačný súčet:** 250 - 450 EUR", cleaned)

    def test_removes_generic_photo_limitations_only(self):
        markdown = """## Analýza fotografií

- **Obmedzenie:** Niektoré fotografie sú mierne tmavé, čo sťažuje detailnú kontrolu.
- **Obmedzenie:** Fotografie sú obmedzené na vybrané uhly, čo neumožňuje kompletné posúdenie vozidla.
- **Obmedzenie:** Odlesk na Foto 09 znemožňuje prečítať konkrétnu kontrolku.
"""

        cleaned = normalize_analysis_markdown(markdown, CAR_INFO)

        self.assertNotIn("mierne tmavé", cleaned)
        self.assertNotIn("vybrané uhly", cleaned)
        self.assertIn("Odlesk na Foto 09", cleaned)

    def test_replaces_false_market_database_wording(self):
        markdown = (
            "## Cena a vyjednávanie\n\n"
            "Vzhľadom na to, že v databáze neboli nájdené žiadne priamo porovnateľné inzeráty, "
            "trh vyžaduje manuálne overenie.\n"
        )

        cleaned = normalize_analysis_markdown(markdown, CAR_INFO)

        self.assertNotIn("v databáze", cleaned.lower())
        self.assertIn("pri webovom vyhľadávaní sa nenašli", cleaned.lower())

    def test_removes_unavailable_url_parentheticals(self):
        markdown = (
            "- Motor treba overiť (Google Search, URL nie je priamo overiteľná).\n"
            "- Prevodovka má servisné riziko (URL nie je priamo overiteľná).\n"
            "- Zdroj ostáva bez interného popisku.\n"
        )
        cleaned = normalize_analysis_markdown(markdown, CAR_INFO)
        self.assertNotIn("URL nie je priamo overiteľná", cleaned)
        self.assertNotIn("Google Search", cleaned)
        self.assertIn("- Motor treba overiť.", cleaned)
        self.assertIn("- Prevodovka má servisné riziko.", cleaned)

    def test_removes_public_database_no_result_filler(self):
        markdown = (
            "## Webové overenie\n\n"
            "- Verejné databázy neposkytli k tomuto konkrétnemu vozidlu žiadne dodatočné informácie, čo je pri absencii VIN bežný jav.\n"
            "- Pri motore a prevodovke treba overiť servisnú históriu.\n"
        )
        cleaned = normalize_analysis_markdown(markdown, SUZUKI_CAR_INFO)
        self.assertNotIn("Verejné databázy neposkytli", cleaned)
        self.assertNotIn("absencii VIN", cleaned)
        self.assertIn("Pri motore a prevodovke", cleaned)

    def test_removes_generic_dph_context_when_ad_has_no_vat_statement(self):
        markdown = (
            "## Cena a vyjednávanie\n\n"
            "DPH kontext: V inzeráte nie je uvedená možnosť odpočtu DPH. "
            "Pre súkromnú osobu to nepredstavuje nevýhodu, avšak pre podnikateľa "
            "platiaceho DPH je táto cena konečná.\n\n"
            "Cena je orientačná podľa stavu vozidla.\n"
        )

        cleaned = normalize_analysis_markdown(markdown, NO_VAT_CAR_INFO)

        self.assertNotIn("DPH", cleaned)
        self.assertNotIn("súkromnú osobu", cleaned)
        self.assertNotIn("podnikateľa", cleaned)
        self.assertIn("Cena je orientačná", cleaned)

    def test_preserves_dph_context_when_ad_explicitly_mentions_vat(self):
        markdown = (
            "## Cena a vyjednávanie\n\n"
            "DPH kontext: Inzerát uvádza cenu 12 000 EUR bez DPH a možnosť odpočtu DPH.\n"
        )

        cleaned = normalize_analysis_markdown(markdown, VAT_CAR_INFO)

        self.assertIn("DPH kontext", cleaned)
        self.assertIn("odpočtu DPH", cleaned)

    def test_uses_neutral_vin_decoding_label_in_customer_report(self):
        markdown = (
            "## VIN a transparentnosť\n\n"
            "- **Ľahké dekódovanie:** WMI zodpovedá Volkswagenu.\n"
        )

        cleaned = normalize_analysis_markdown(markdown, CAR_INFO)

        self.assertNotIn("Ľahké dekódovanie", cleaned)
        self.assertIn("**Dekódovanie:**", cleaned)

    def test_removes_supported_mileage_false_negative_from_report(self):
        cleaned = normalize_analysis_markdown(SUZUKI_FALSE_NEGATIVE_MARKDOWN, SUZUKI_CAR_INFO)
        self.assertNotIn("Chýbajúci údaj o nájazde kilometrov", cleaned)
        self.assertNotIn("absencia nájazdu kilometrov", cleaned)
        self.assertNotIn("chýbajúci údaj o nájazde kilometrov", cleaned)
        self.assertIn("150 195 km", cleaned)
        self.assertIn("Potenciálne drahé opravy motora", cleaned)

    def test_neutralizes_public_vin_history_when_vin_is_present(self):
        cleaned = normalize_analysis_markdown(SUZUKI_FALSE_NEGATIVE_MARKDOWN, SUZUKI_CAR_INFO)
        self.assertNotIn("Neoveriteľná história vozidla cez VIN číslo", cleaned)
        self.assertNotIn("zvyšuje riziko skrytých vád", cleaned)
        self.assertNotIn("riziká spojené s neoveriteľným VIN", cleaned)
        self.assertNotIn("formát je v poriadku", cleaned)
        self.assertIn("overte cez Cebia", cleaned)
        self.assertIn("CarVertical", cleaned)

    def test_preserves_invalid_vin_risk(self):
        cleaned = normalize_analysis_markdown(INVALID_VIN_MARKDOWN, SUZUKI_CAR_INFO)
        self.assertIn("neplatný alebo konfliktný VIN", cleaned)
        self.assertIn("Neplatný alebo konfliktný VIN je reálne riziko", cleaned)

    def test_removes_placeholder_only_rows_but_keeps_table_delimiters(self):
        markdown = """## Očakávané náklady

| Položka | Prečo | Odhad EUR | Urgentnosť |
|---|---|---:|---|
| --- | --- | - | --- |
| Vstupný servis | Bežná údržba | 290 - 490 EUR | Vysoká |
| VIN kontrola | - | 20 EUR | Nízka |

### Podmienené opravy

| Položka | Podmienka | Odhad EUR | Ako overiť |
|---|---|---:|---|
|---|---|---|---|
| Palivové čerpadlo | Len pri potvrdenej chybe | 300 - 500 EUR | Diagnostika |
"""

        cleaned = normalize_analysis_markdown(markdown, CAR_INFO)

        self.assertNotIn("| --- | --- | - | --- |", cleaned)
        self.assertEqual(cleaned.count("| --- | --- | ---: | --- |"), 2)
        self.assertIn("| VIN kontrola | - | 20 | Nízka |", cleaned)
        self.assertIn("| Palivové čerpadlo | Len pri potvrdenej chybe", cleaned)

    def test_removes_standalone_sources_section_and_inline_links(self):
        markdown = """## Webové overenie

- Prevodovku treba skontrolovať ([Honda](https://www.honda.eu/)).

## Zdroje

- Auto-Data.net ([auto-data.net](https://www.auto-data.net/))
- Car-recalls.eu ([recalls](https://car-recalls.eu/))

## Záverečné odporúčanie

Auto riešiť iba po kontrole.
"""

        cleaned = normalize_analysis_markdown(markdown, CAR_INFO)

        self.assertNotIn("## Zdroje", cleaned)
        self.assertNotIn("Auto-Data.net", cleaned)
        self.assertNotIn("Car-recalls.eu", cleaned)
        self.assertIn("Prevodovku treba skontrolovať.", cleaned)
        self.assertNotIn("Honda", cleaned)
        self.assertNotIn("https://", cleaned)
        self.assertIn("## Záverečné odporúčanie", cleaned)

    def test_removes_english_sources_section_before_end_marker(self):
        markdown = """## Final Recommendation

Proceed after inspection.

## 📚 Sources

- Example source

<!-- END_ANALYSIS -->
"""

        cleaned = normalize_analysis_markdown(markdown, CAR_INFO)

        self.assertNotIn("Sources", cleaned)
        self.assertNotIn("Example source", cleaned)
        self.assertIn("## Final Recommendation", cleaned)
        self.assertIn("<!-- END_ANALYSIS -->", cleaned)


if __name__ == "__main__":
    unittest.main()
