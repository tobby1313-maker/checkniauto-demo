import importlib
import json
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scrapper_demo import create_app
from scrapper_demo import legacy_server


def _sse_payloads(response_text):
    payloads = []
    for block in response_text.split("\n\n"):
        if not block.startswith("data: "):
            continue
        value = block[6:].strip()
        payloads.append(value if value == "[DONE]" else json.loads(value))
    return payloads


class FinalVerificationTests(unittest.TestCase):
    def test_local_entrypoint_starts_the_compatibility_application(self):
        entrypoint = Path(__file__).parent / "web_server.py"

        with patch.object(legacy_server.app, "run") as run:
            runpy.run_path(str(entrypoint), run_name="__main__")

        run.assert_called_once_with(host="0.0.0.0", port=5000, debug=True)

    def test_procfile_target_imports_and_health_check_passes(self):
        procfile = (Path(__file__).parent / "Procfile").read_text(encoding="utf-8")
        server_module = importlib.import_module("web_server")

        self.assertIn("web_server:app", procfile)
        self.assertIn("--workers 1", procfile)
        response = server_module.app.test_client().get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "ok")

    def test_supported_url_flow_completes_with_mocked_scraper_and_analysis(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_dir = Path(temp_dir) / "Auta"
            app = create_app(
                {
                    "TESTING": True,
                    "DEMO_MODE": True,
                    "DEMO_RATE_LIMIT_PER_IP": "0",
                    "SCRAPPER_AUTA_DIR": str(jobs_dir),
                }
            )

            class FakeScraperProcess:
                def __init__(self, *args, **kwargs):
                    listing_dir = jobs_dir / "audi-a4"
                    listing_dir.mkdir(parents=True)
                    (listing_dir / "car_info.md").write_text(
                        "# Audi A4\n\n- **Mileage:** 100000 km\n- **Fuel:** diesel\n",
                        encoding="utf-8",
                    )
                    self.stdout = iter(("scraper complete\n",))
                    self.returncode = 0

                def wait(self, timeout=None):
                    return self.returncode

                def kill(self):
                    self.returncode = -1

            def fake_analysis_events(slug, output_language="sk"):
                yield f'data: {json.dumps({"text": "Mock report"})}\n\n'
                yield f'data: {json.dumps({"done": True, "slug": slug})}\n\n'
                yield "data: [DONE]\n\n"

            with (
                patch.object(legacy_server.subprocess, "Popen", FakeScraperProcess),
                patch.object(
                    legacy_server,
                    "_demo_analysis_events",
                    side_effect=fake_analysis_events,
                ),
            ):
                response = app.test_client().post(
                    "/api/demo/analyze",
                    json={
                        "url": "https://auto.bazos.sk/inzerat/123/audi-a4.php",
                        "output_language": "en",
                    },
                )
                payloads = _sse_payloads(response.get_data(as_text=True))

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, "text/event-stream")
            self.assertTrue(any(item.get("slug") == "audi-a4" for item in payloads if isinstance(item, dict)))
            self.assertIn({"text": "Mock report"}, payloads)
            self.assertEqual(payloads[-1], "[DONE]")
            self.assertTrue((jobs_dir / "audi-a4" / "car_info.md").is_file())


if __name__ == "__main__":
    unittest.main()
