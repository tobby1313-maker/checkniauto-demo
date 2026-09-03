import os
import tempfile
import time
import unittest

_TEST_DATA_DIR = tempfile.mkdtemp(prefix="checkni-v2-test-")
os.environ["CHECKNI_DATA_DIR"] = _TEST_DATA_DIR
os.environ["CHECKNI_ACCESS_MODE"] = "open"
for _key in ("GEMINI_PRIMARY_API_KEY", "GEMINI_BACKUP_API_KEY", "GEMINI_API_KEY"):
    os.environ.pop(_key, None)

import v2_app  # noqa: E402


class V2AppSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = v2_app.app.test_client()

    def test_public_routes_and_security_headers(self):
        index = self.client.get("/")
        health = self.client.get("/healthz")
        config = self.client.get("/api/v2/config")

        self.assertEqual(index.status_code, 200)
        self.assertIn(b"Checkni Auto V2", index.data)
        self.assertEqual(index.headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.get_json()["version"], "2.0")
        self.assertEqual(config.status_code, 200)
        self.assertFalse(config.get_json()["checkout_enabled"])

    def test_manual_job_completes_with_safe_fallback_report(self):
        response = self.client.post(
            "/api/v2/jobs/manual",
            data={
                "title": "Škoda Octavia 2.0 TDI DSG 2019",
                "price": "12900",
                "currency": "EUR",
                "manual_text": (
                    "Najazdené 168 400 km. Servisná knižka a faktúry. "
                    "Automatická prevodovka DSG, diesel, 110 kW, predný pohon. "
                    "Predajca deklaruje pravidelný servis."
                ),
                "language": "sk",
            },
        )
        self.assertEqual(response.status_code, 202, response.get_data(as_text=True))
        job_id = response.get_json()["id"]

        state = None
        for _ in range(100):
            state_response = self.client.get(f"/api/v2/jobs/{job_id}")
            self.assertEqual(state_response.status_code, 200)
            state = state_response.get_json()
            if state["status"] in {"done", "failed"}:
                break
            time.sleep(0.05)

        self.assertIsNotNone(state)
        self.assertEqual(state["status"], "done", state)
        report = state["report"]
        self.assertEqual(report["schema_version"], "2.0")
        self.assertTrue(report["meta"]["fallback_used"])
        self.assertEqual(report["vehicle"]["year"], 2019)
        self.assertGreater(report["data_quality"]["score"], 0)
        self.assertGreater(len(report["top_findings"]), 0)

        download = self.client.get(f"/api/v2/jobs/{job_id}/report")
        self.assertEqual(download.status_code, 200)
        self.assertIn("attachment", download.headers.get("Content-Disposition", ""))

        events = self.client.get(f"/api/v2/jobs/{job_id}/events")
        self.assertEqual(events.status_code, 200)
        event_text = events.get_data(as_text=True)
        self.assertIn("event: complete", event_text)
        self.assertIn(job_id, event_text)


if __name__ == "__main__":
    unittest.main()
