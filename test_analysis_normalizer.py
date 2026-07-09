import unittest

from analysis_normalizer import normalize_analysis_markdown


CAR_INFO = """
# Mitsubishi ASX 1,6 benzÃ­n MIVEC 2WD Invite

- **Mileage:** 178 000 km
- **VIN:** JMBXNGA1WBZ019167
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

    def test_removes_grounding_redirect_urls(self):
        cleaned = normalize_analysis_markdown(BROKEN_MARKDOWN, CAR_INFO)
        self.assertNotIn("vertexaisearch.cloud.google.com", cleaned)
        self.assertIn("autorubik.sk", cleaned)
        self.assertIn("garaz.cz", cleaned)


if __name__ == "__main__":
    unittest.main()
