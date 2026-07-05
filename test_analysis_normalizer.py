import unittest

from analysis_normalizer import normalize_analysis_markdown


CAR_INFO = """
# Mitsubishi ASX 1,6 benzín MIVEC 2WD Invite

- **Mileage:** 178 000 km
- **VIN:** JMBXNGA1WBZ019167
"""


BROKEN_MARKDOWN = """# 🚗 Analýza: Mitsubishi ASX 1,6 benzín MIVEC 2WD Invite

## 🧾 Dáta z inzerátu

| Položka | Hodnota | Poznámka |
|---|---:|---|
| Cena | 6 690 EUR | Vzhľadom na vek a typ vozidla. |
| Rok | 2012 | Uvedené v popise. |
| Nájazd |
178 000 km | Uvedené v popise. |
| Motor | 1.6 benzín MIVEC, 86 kW | Spoľahlivý motor, ale s potenciálnymi rizik mi pri vysokom nájazde. |
| VIN | JMBXNGA1WBZ019167 | Uvedené. |

## 🔍 VIN a transparentnosť

- **VIN:** JMBXNGA1WBZ01916
- **Verdikt
Riziko. Hoci je VIN uvedený, online história nie je verejne dostupná.

## 🌐 Webové overenie

Webové overenie potvrdzuje riziká. [autorubik.sk](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQ
6fyugs72K4x2b9e8l55X8cFgu9KPW08nzxVrukk_tqxxSgs6F8oeXiXYQqjTebVK1Az
4km1N9WD3QfzJpeTV7M0fXQ_SUKY3aGRjT_SqCl-s40zPLuhBo4oRthL_n1Gh8uq
HVVa_Ie4n9Zloakz_rVuyCBu7VJtj-BMA8cP6qEw=), [garaz.cz](https://vertexaisearch.cloud.google.com/grounding-api-
redirect/AUZIYQGIl2_6aJq6IoUGXlbHMooVrG7GT865m-MVeK92xP5OIrZti)

1. **Ako vysvetľujete nezrovnalosť v nájazde kilometrov** medzi inzerátom (17
000 km) a záznamom v servisnej knižke z roku 2019 (179 567 km)?
"""


class AnalysisNormalizerTest(unittest.TestCase):
    def test_repairs_table_rows_and_split_labels(self):
        cleaned = normalize_analysis_markdown(BROKEN_MARKDOWN, CAR_INFO)
        self.assertIn("| Nájazd | 178 000 km | Uvedené v popise. |", cleaned)
        self.assertNotIn("\n178 000 km | Uvedené", cleaned)
        # Split bold markers are preserved as-is (Gemini's output is not reformatted)
        self.assertIn("- **Verdikt", cleaned)
        self.assertIn("Riziko.", cleaned)

    def test_applies_canonical_vin_and_mileage(self):
        cleaned = normalize_analysis_markdown(BROKEN_MARKDOWN, CAR_INFO)
        self.assertNotIn("JMBXNGA1WBZ01916\n", cleaned)
        self.assertIn("- **VIN:** JMBXNGA1WBZ019167", cleaned)
        self.assertIn("inzerátom (178 000 km)", cleaned)

    def test_removes_grounding_redirect_urls(self):
        cleaned = normalize_analysis_markdown(BROKEN_MARKDOWN, CAR_INFO)
        self.assertNotIn("vertexaisearch.cloud.google.com", cleaned)
        self.assertIn("autorubik.sk", cleaned)
        self.assertIn("garaz.cz", cleaned)


if __name__ == "__main__":
    unittest.main()
