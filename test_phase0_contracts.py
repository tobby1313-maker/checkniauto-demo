import io
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import web_server
from scrapper_demo.progress import RUNTIME_STATE_KEY, DemoRuntimeState


FIXTURE_DIR = Path(__file__).parent / "test_fixtures" / "successful_pipeline"


def _sse_items(response_text):
    items = []
    for block in response_text.split("\n\n"):
        if not block.startswith("data: "):
            continue
        payload = block[6:].strip()
        if payload == "[DONE]":
            items.append(payload)
        elif payload:
            items.append(json.loads(payload))
    return items


class Phase0ContractTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        self.original_auta_dir = web_server.AUTA_DIR
        self.original_demo_mode = web_server.DEMO_MODE
        self.original_rate_limit = web_server.DEMO_RATE_LIMIT_PER_IP
        self.original_ttl = web_server.DEMO_JOB_TTL_MINUTES
        self.original_runtime_state = web_server.app.extensions[RUNTIME_STATE_KEY]
        self.original_max_content_length = web_server.app.config.get("MAX_CONTENT_LENGTH")
        self.original_admin_token = web_server.app.config.get("ADMIN_DASHBOARD_TOKEN", "")
        self.original_secret_key = web_server.app.config.get("SECRET_KEY")

        web_server.AUTA_DIR = os.path.join(self.temp_dir.name, "Auta")
        os.makedirs(web_server.AUTA_DIR, exist_ok=True)
        web_server.DEMO_MODE = True
        web_server.DEMO_RATE_LIMIT_PER_IP = "0"
        self.runtime_state = DemoRuntimeState(1)
        web_server.app.extensions[RUNTIME_STATE_KEY] = self.runtime_state
        web_server._set_current_progress(reset=True)
        web_server.app.testing = True
        web_server.app.config["ADMIN_DASHBOARD_TOKEN"] = "test-admin-token"
        web_server.app.config["SECRET_KEY"] = "test-secret-key"
        self.client = web_server.app.test_client()
        self.client.post("/admin/login", data={"token": "test-admin-token"})
        self.addCleanup(self._restore_globals)

    def _restore_globals(self):
        web_server.AUTA_DIR = self.original_auta_dir
        web_server.DEMO_MODE = self.original_demo_mode
        web_server.DEMO_RATE_LIMIT_PER_IP = self.original_rate_limit
        web_server.DEMO_JOB_TTL_MINUTES = self.original_ttl
        web_server.app.extensions[RUNTIME_STATE_KEY] = self.original_runtime_state
        web_server.app.config["MAX_CONTENT_LENGTH"] = self.original_max_content_length
        web_server.app.config["ADMIN_DASHBOARD_TOKEN"] = self.original_admin_token
        web_server.app.config["SECRET_KEY"] = self.original_secret_key

    def _install_success_fixture(self, slug="phase0-success"):
        destination = Path(web_server.AUTA_DIR) / slug
        shutil.copytree(FIXTURE_DIR, destination)
        return destination

    def test_demo_route_allowlist_and_private_route_gate(self):
        health = self.client.get("/healthz")
        progress = self.client.get("/api/demo/current-progress")
        private_listings = self.client.get("/api/listings")
        private_kb = self.client.get("/api/kb")

        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.get_json(), {"status": "ok", "demo_mode": True})
        self.assertEqual(progress.status_code, 200)
        self.assertEqual(set(progress.get_json()), {"status", "log_lines", "done"})
        for response in (private_listings, private_kb):
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.get_json(), {"error": "This route is disabled in demo mode."})

    def test_saved_job_fixture_preserves_public_shapes_and_artifact_names(self):
        self._install_success_fixture()

        listing_response = self.client.get("/api/demo/listings")
        detail_response = self.client.get("/api/demo/listings/phase0-success")
        artifact_response = self.client.get("/api/demo/listings/phase0-success/artifacts")

        self.assertEqual(listing_response.status_code, 200)
        listings = listing_response.get_json()
        self.assertEqual(len(listings), 1)
        self.assertEqual(listings[0]["slug"], "phase0-success")
        self.assertTrue(listings[0]["has_analysis"])

        self.assertEqual(detail_response.status_code, 200)
        detail = detail_response.get_json()
        self.assertEqual(detail["slug"], "phase0-success")
        self.assertEqual(detail["parsed"]["specs"]["Mileage"], "84 200 km")
        self.assertIn("Representative public report", detail["analysis_content"])

        self.assertEqual(artifact_response.status_code, 200)
        filenames = [item["filename"] for item in artifact_response.get_json()["artifacts"]]
        self.assertEqual(
            filenames,
            [
                "raw_data.json",
                "car_info.md",
                "analysis_request.md",
                "vin_decoded.json",
                "listing_facts.json",
                "component_identity.json",
                "reliability_research.md",
                "market_research.md",
                "web_research.md",
                "grok_research.json",
                "gemini_vision.json",
                "risk_score.json",
                "validation_warnings.json",
                "analysis_result_raw.md",
                "analysis_result.md",
            ],
        )

        fixture_path = Path(web_server.AUTA_DIR) / "phase0-success"
        for artifact_name, schema_name in (
            ("component_identity.json", "component_identity.schema.json"),
            ("grok_research.json", "grok_research.schema.json"),
            ("gemini_vision.json", "gemini_vision.schema.json"),
            ("risk_score.json", "risk_score.schema.json"),
        ):
            content = (fixture_path / artifact_name).read_text(encoding="utf-8")
            self.assertEqual(
                web_server._soft_validate_json_contract(artifact_name, content, schema_name),
                [],
            )

    def test_url_analysis_rejects_missing_invalid_and_unsupported_urls_before_streaming(self):
        missing = self.client.post("/api/demo/analyze", json={})
        invalid = self.client.post("/api/demo/analyze", json={"url": "not-a-url"})
        mobile = self.client.post(
            "/api/demo/analyze",
            json={"url": "https://suchen.mobile.de/fahrzeuge/details.html?id=123"},
        )
        unknown = self.client.post(
            "/api/demo/analyze",
            json={"url": "https://example.com/listing/123"},
        )

        self.assertEqual(missing.status_code, 400)
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(mobile.status_code, 400)
        self.assertTrue(mobile.get_json()["unsupported"])
        self.assertEqual(unknown.status_code, 400)
        self.assertTrue(unknown.get_json()["unsupported"])

    def test_slug_and_artifact_paths_remain_inside_the_job_root(self):
        self._install_success_fixture()

        safe_path = Path(web_server._safe_slug_dir("../../outside")).resolve()
        root_path = Path(web_server.AUTA_DIR).resolve()
        artifact_traversal = self.client.get(
            "/api/demo/listings/phase0-success/artifacts/..%5Csecret.txt"
        )

        self.assertEqual(os.path.commonpath([str(root_path), str(safe_path)]), str(root_path))
        self.assertEqual(artifact_traversal.status_code, 400)

    def test_concurrent_demo_job_is_rejected_without_blocking(self):
        self.runtime_state.jobs.acquire()
        try:
            response = self.client.post(
                "/api/demo/analyze-manual",
                data={
                    "price": "10000",
                    "manual_text": "A valid vehicle description.",
                },
            )
        finally:
            self.runtime_state.jobs.release()

        self.assertEqual(response.status_code, 429)
        self.assertIn("already running", response.get_json()["error"])

    def test_manual_input_validation_and_upload_count_release_job_lock(self):
        missing_price = self.client.post(
            "/api/demo/analyze-manual",
            data={"price": "0", "manual_text": "A valid vehicle description."},
        )
        missing_text = self.client.post(
            "/api/demo/analyze-manual",
            data={"price": "12000", "manual_text": ""},
        )
        unsupported_image = self.client.post(
            "/api/demo/analyze-manual",
            data={
                "price": "12000",
                "manual_text": "A valid vehicle description.",
                "images": (io.BytesIO(b"not-an-image"), "vehicle.txt"),
            },
            content_type="multipart/form-data",
        )
        too_many_images = self.client.post(
            "/api/demo/analyze-manual",
            data={
                "price": "12000",
                "manual_text": "A valid vehicle description.",
                "images": [
                    (io.BytesIO(b"image"), f"vehicle-{index}.jpg")
                    for index in range(web_server.MAX_MANUAL_IMAGES + 1)
                ],
            },
            content_type="multipart/form-data",
        )

        for response in (missing_price, missing_text, unsupported_image, too_many_images):
            self.assertEqual(response.status_code, 400)

        acquired = self.runtime_state.jobs.acquire(blocking=False)
        self.assertTrue(acquired)
        if acquired:
            self.runtime_state.jobs.release()

    def test_manual_success_stream_contract_and_generated_job_inputs(self):
        def fake_analysis_events(slug, output_language="sk"):
            yield f"data: {json.dumps({'status': 'Phase test', 'slug': slug})}\n\n"
            yield f"data: {json.dumps({'text': 'Report chunk'})}\n\n"
            yield f"data: {json.dumps({'token_usage': {'input_tokens': 10, 'output_tokens': 2}})}\n\n"
            yield f"data: {json.dumps({'done': True, 'slug': slug})}\n\n"
            yield "data: [DONE]\n\n"

        with (
            patch("main._run_vin_decoding"),
            patch("main.build_analysis_request"),
            patch.object(web_server, "_demo_analysis_events", side_effect=fake_analysis_events),
        ):
            response = self.client.post(
                "/api/demo/analyze-manual",
                data={
                    "title": "Phase Zero Vehicle",
                    "price": "14500",
                    "manual_text": "2020 vehicle, 84 200 km, documented service history.",
                    "output_language": "en",
                },
            )
            response_text = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/event-stream")
        self.assertEqual(response.headers["Cache-Control"], "no-cache")
        self.assertEqual(response.headers["X-Accel-Buffering"], "no")

        items = _sse_items(response_text)
        self.assertEqual(items[0]["slug"], "phase-zero-vehicle")
        self.assertIn("status", items[0])
        self.assertIn("status", items[1])
        self.assertEqual(items[2], {"text": "Report chunk"})
        self.assertIn("token_usage", items[3])
        self.assertEqual(items[-2], {"done": True, "slug": "phase-zero-vehicle"})
        self.assertEqual(items[-1], "[DONE]")

        job_dir = Path(web_server.AUTA_DIR) / "phase-zero-vehicle"
        raw_data = json.loads((job_dir / "raw_data.json").read_text(encoding="utf-8"))
        self.assertEqual(raw_data["price"], 14500)
        self.assertEqual(raw_data["source"], "manual")
        self.assertTrue((job_dir / "car_info.md").exists())

    def test_missing_server_keys_produce_error_then_terminal_sse_marker(self):
        with patch.dict(
            os.environ,
            {"GEMINI_PRIMARY_API_KEY": "", "GEMINI_BACKUP_API_KEY": ""},
            clear=False,
        ):
            events = list(web_server._demo_analysis_events("missing-keys"))

        items = _sse_items("".join(events))
        self.assertIn("error", items[0])
        self.assertEqual(items[-1], "[DONE]")

    def test_progress_mirroring_contract(self):
        event_text = "".join(
            [
                f"data: {json.dumps({'status': 'Researching'})}\n\n",
                f"data: {json.dumps({'log': 'Scraper line'})}\n\n",
                f"data: {json.dumps({'token_usage': {'input_tokens': 12, 'output_tokens': 3}})}\n\n",
                f"data: {json.dumps({'done': True})}\n\n",
            ]
        )
        web_server._track_demo_sse_progress(event_text)

        progress = self.client.get("/api/demo/current-progress").get_json()
        self.assertEqual(progress["status"], "Done")
        self.assertTrue(progress["done"])
        self.assertIn("Scraper line", progress["log_lines"])
        self.assertIn("Tokens sent: ~12, received: ~3", progress["log_lines"])

    def test_rate_limit_uses_first_forwarded_ip_and_daily_counter(self):
        web_server.DEMO_RATE_LIMIT_PER_IP = "1/day"
        headers = {"X-Forwarded-For": "203.0.113.10, 10.0.0.1"}

        with web_server.app.test_request_context(headers=headers):
            first = web_server._check_demo_rate_limit()
        with web_server.app.test_request_context(headers=headers):
            second = web_server._check_demo_rate_limit()

        self.assertIsNone(first)
        response, status = second
        self.assertEqual(status, 429)
        self.assertIn("Demo limit reached", response.get_json()["error"])
        self.assertEqual(len(self.runtime_state.rate_limiter.snapshot()), 1)

    def test_cleanup_removes_only_expired_job_directories(self):
        old_job = Path(web_server.AUTA_DIR) / "old-job"
        current_job = Path(web_server.AUTA_DIR) / "current-job"
        old_job.mkdir()
        current_job.mkdir()
        web_server.DEMO_JOB_TTL_MINUTES = 60
        old_timestamp = time.time() - (2 * 60 * 60)
        os.utime(old_job, (old_timestamp, old_timestamp))

        web_server._cleanup_old_demo_jobs()

        self.assertFalse(old_job.exists())
        self.assertTrue(current_job.exists())

    def test_client_disconnect_closes_stream_and_releases_job_lock(self):
        def fake_analysis_events(slug, output_language="sk"):
            yield f"data: {json.dumps({'status': 'Still working', 'slug': slug})}\n\n"
            yield f"data: {json.dumps({'done': True, 'slug': slug})}\n\n"
            yield "data: [DONE]\n\n"

        with (
            patch("main._run_vin_decoding"),
            patch("main.build_analysis_request"),
            patch.object(web_server, "_demo_analysis_events", side_effect=fake_analysis_events),
        ):
            response = self.client.post(
                "/api/demo/analyze-manual",
                data={
                    "title": "Cancelled Vehicle",
                    "price": "10000",
                    "manual_text": "Manual listing that will be cancelled by the client.",
                },
                buffered=False,
            )
            first_chunk = next(iter(response.response))
            self.assertIn(b"Manual listing ready", first_chunk)
            response.close()

        acquired = self.runtime_state.jobs.acquire(blocking=False)
        self.assertTrue(acquired)
        if acquired:
            self.runtime_state.jobs.release()

    def test_oversized_manual_request_releases_job_lock(self):
        """Oversized multipart requests must not acquire or leak a job slot."""
        web_server.app.config["MAX_CONTENT_LENGTH"] = 256
        response = self.client.post(
            "/api/demo/analyze-manual",
            data={
                "price": "10000",
                "manual_text": "x" * 2048,
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 413)
        acquired = self.runtime_state.jobs.acquire(blocking=False)
        self.assertTrue(acquired)
        if acquired:
            self.runtime_state.jobs.release()


if __name__ == "__main__":
    unittest.main()
