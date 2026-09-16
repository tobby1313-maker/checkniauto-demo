"""Local beta regressions: real SQLite/files, HTTP, OAuth and image MCP payloads."""
import base64
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import io
import json
from pathlib import Path
import re
import secrets
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit
import zipfile

from scrapper_demo.beta_contracts import HubError, validate_manifest, validate_report, review_policy
from scrapper_demo.local_beta_hub import LocalHub, digest

ADMIN = "test-only-admin-not-a-real-credential-1234567890"
ORIGIN = "https://checkni.example"
CALLBACK = "https://chatgpt.com/connector_platform_oauth_redirect"


def prepared(hub, job_id=None, photos=False):
    job_id = job_id or "beta-" + secrets.token_hex(16)
    hub.create_job({"id": job_id, "language": "sk", "source_url": "https://auto.bazos.sk/inzerat/123/test.php"})
    files = {"raw_data.json": b'{"title":"Test vehicle","description":"Seller claim, not verified"}', "listing_facts.json": b'{}'}
    gallery, sheets = [], []
    if photos:
        from PIL import Image
        data = io.BytesIO()
        Image.new("RGB", (20, 20)).save(data, "JPEG")
        files.update({"images/p001.jpg": data.getvalue(), "sheets/overview_001.jpg": data.getvalue()})
        gallery = [{"id": "p001", "number": 1, "status": "available", "filename": "images/p001.jpg", "inspection": "not_inspected"},
                   {"id": "p002", "number": 2, "status": "download_failed", "filename": None, "inspection": "not_inspected"}]
        sheets = [{"id": "s001", "filename": "sheets/overview_001.jpg", "photo_ids": ["p001"]}]
    manifest = {"schema_version": 1, "title": "Test vehicle", "photos": gallery, "sheets": sheets, "ai_calls": 0, "chargeable": False,
                "files": [hub.upload_asset(job_id, name, data) for name, data in files.items()]}
    hub.ready(job_id, manifest)
    return job_id, manifest


def finished_report(hub, job_id):
    report = hub.operator_job(job_id)["report_template"]
    report["summary"] = "There are insufficient verified data for a purchase decision. Request documents and inspect the vehicle."
    return report


class LocalStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.time = 1_800_000_000
        self.hub = LocalHub(self.tmp.name, ADMIN, clock=lambda: self.time)

    def test_no_cloud_needed_and_reopen_same_disk(self):
        with patch("socket.getaddrinfo", side_effect=AssertionError("No network")):
            job, _ = prepared(self.hub)
            other = LocalHub(self.tmp.name, ADMIN)
            self.assertEqual(other.get_job(job)["status"], "WAITING_FOR_AI")
        self.assertFalse(other.persistent)
        self.assertTrue(other.configured)

    def test_disk_loss_gives_explicit_missing_job_not_fabricated_data(self):
        job, _ = prepared(self.hub)
        with tempfile.TemporaryDirectory() as clean:
            with self.assertRaises(HubError) as e:
                LocalHub(clean, ADMIN).get_job(job)
        self.assertEqual(e.exception.status, 404)
        self.assertIn("Render", str(e.exception))

    def test_atomic_claim_and_expired_lease_reclaim(self):
        job, _ = prepared(self.hub)
        def claim():
            try: return self.hub.claim(job)
            except HubError: return None
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: claim(), range(4)))
        valid = [r for r in results if r]
        self.assertEqual(len(valid), 1)
        self.time += 5401
        new = self.hub.claim(job)
        self.assertNotEqual(new["lease_token"], valid[0]["lease_token"])
        with self.assertRaises(HubError):
            self.hub.complete(job, valid[0]["lease_token"], finished_report(self.hub, job))

    def test_report_publish_idempotence_and_wrong_lease(self):
        job, _ = prepared(self.hub)
        lease = self.hub.claim(job)["lease_token"]
        report = finished_report(self.hub, job)
        with self.assertRaises(HubError): self.hub.complete(job, "wrong", report)
        result = self.hub.complete(job, lease, report)
        self.assertEqual(result["status"], "DONE")
        self.assertEqual(self.hub.complete(job, lease, report), result)
        report["summary"] = "Different content"
        with self.assertRaises(HubError): self.hub.complete(job, lease, report)

    def test_partial_upload_not_queued_and_retry_is_idempotent(self):
        job = "beta-" + "1"*32
        self.hub.create_job({"id": job, "language": "sk", "source_url": ""})
        f = self.hub.upload_asset(job, "raw_data.json", b"{}")
        manifest = {"schema_version": 1, "title": "Test", "photos": [], "sheets": [], "ai_calls": 0, "chargeable": False,
                    "files": [f, {**f, "name": "listing_facts.json"}]}
        with self.assertRaises(HubError): self.hub.ready(job, manifest)
        self.assertEqual(self.hub.get_job(job)["status"], "PREPARING")
        self.assertEqual(self.hub.upload_asset(job, "raw_data.json", b"{}"), f)
        self.hub.upload_asset(job, "listing_facts.json", b"{}")
        self.assertEqual(self.hub.ready(job, manifest)["status"], "WAITING_FOR_AI")
        self.assertEqual(self.hub.ready(job, manifest)["status"], "WAITING_FOR_AI")

    def test_paths_and_oversized_assets_rejected(self):
        job = "beta-"+"2"*32
        self.hub.create_job({"id": job, "language": "sk", "source_url": ""})
        for name in ("../secret", "/tmp/secret", "images/../../../x", "queue.sqlite3"):
            with self.subTest(name=name), self.assertRaises(HubError):
                self.hub.upload_asset(job, name, b"x")
        with self.assertRaises(HubError): self.hub.upload_asset(job, "raw_data.json", b"x"*4_000_001)

    def test_daily_capacity_and_source_immutability(self):
        for _ in range(10): job, _ = prepared(self.hub)
        with self.assertRaises(HubError) as e: prepared(self.hub)
        self.assertEqual(e.exception.status, 429)
        with self.assertRaises(HubError): self.hub.upload_asset(job, "raw_data.json", b"{}")

    def test_all_photos_retained_with_honest_inspection_levels(self):
        job, manifest = prepared(self.hub, photos=True)
        report = finished_report(self.hub, job)
        validate_report(report, job, manifest)
        self.assertEqual(len(self.hub.public_job(self.hub.get_job(job))["photos"]), 2)
        report["photo_review"][1]["level"] = "detail"
        with self.assertRaises(HubError): validate_report(report, job, manifest)

    def test_overview_gap_is_invalid(self):
        _, manifest = prepared(self.hub, photos=True)
        manifest["sheets"] = []
        with self.assertRaises(HubError): validate_manifest(manifest)

    def test_invalid_report_types_and_template_are_rejected(self):
        job, manifest = prepared(self.hub)
        with self.assertRaises(HubError): validate_report(self.hub.operator_job(job)["report_template"], job, manifest)
        report = finished_report(self.hub, job)
        for field, value in (("verdict", []), ("sources", [None]), ("schema_version", True), ("findings", [{}])):
            bad = copy.deepcopy(report);bad[field] = value
            with self.subTest(field=field), self.assertRaises(HubError): validate_report(bad, job, manifest)
        bad = copy.deepcopy(report);bad["job_id"] = "beta-"+"a"*32
        with self.assertRaises(HubError): validate_report(bad, job, manifest)

    def test_uninspected_photo_cannot_be_cited_as_evidence(self):
        job, m = prepared(self.hub, photos=True)
        r = finished_report(self.hub, job)
        r["findings"] = [{"title": "Visible mark", "detail": "A mark may be present.", "next_step": "Inspect it.", "confidence": "LOW", "evidence_type": "photo", "photo_ids": ["p001"], "source_ids": []}]
        with self.assertRaises(HubError): validate_report(r, job, m)
        r["photo_review"][0]["level"] = "overview"
        validate_report(r, job, m)

    def test_private_reads_require_auth_and_policy_is_not_actual_model(self):
        job, _ = prepared(self.hub)
        with self.assertRaises(HubError): self.hub.call("GET", "/v1/jobs")
        with self.assertRaises(HubError): self.hub.call("GET", "/v1/jobs/"+job, token="wrong-token")
        policy = self.hub.call("GET", "/v1/jobs/"+job, token=ADMIN)["review_policy"]
        self.assertEqual(policy["primary"]["model"], "GPT-6 Pro")
        self.assertEqual(policy["fallback"]["reasoning_effort"], "xhigh")
        self.assertFalse(policy["automatic_model_switching"])
        self.assertFalse(policy["paid_api_fallback"])
        self.assertIsNone(policy["actual_model"])
        policy["fallback"]["model"] = "mutated"
        self.assertEqual(review_policy()["fallback"]["model"], "GPT-5.6 Sol")


class LocalHttpTests(unittest.TestCase):
    def setUp(self):
        from beta_server import create_beta_app
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.app = create_beta_app({"TESTING": True, "CHECKNI_STORAGE_MODE": "local", "CHECKNI_LOCAL_DATA_DIR": self.tmp.name,
            "CHECKNI_OPERATOR_TOKEN": ADMIN, "CHECKNI_PUBLIC_ORIGIN": ORIGIN, "CHECKNI_BETA_MARKET_SEARCH": False})
        self.hub = self.app.extensions["beta_hub"]
        self.client = self.app.test_client()

    def rpc(self, method, params=None, token=None):
        return self.client.post("/mcp", base_url=ORIGIN, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
                                headers={"Authorization": "Bearer "+token} if token else {})

    def login(self, scope="analysis:read analysis:write offline_access"):
        import html
        registered = self.client.post("/oauth/register", base_url=ORIGIN, json={"redirect_uris": [CALLBACK]})
        self.assertEqual(registered.status_code, 201)
        cid = registered.json["client_id"]
        verifier = "v"*64
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        auth = {"client_id": cid, "redirect_uri": CALLBACK, "response_type": "code", "code_challenge_method": "S256",
                "code_challenge": challenge, "resource": ORIGIN+"/mcp", "scope": scope, "state": "operator-state"}
        page = self.client.get("/oauth/authorize?"+urlencode(auth), base_url=ORIGIN)
        self.assertEqual(page.status_code, 200)
        signed = html.unescape(re.search('name="signed" value="([^"]+)"', page.text).group(1))
        result = self.client.post("/oauth/authorize", base_url=ORIGIN, data={"signed": signed, "secret": ADMIN}, headers={"Origin": ORIGIN})
        self.assertEqual(result.status_code, 303)
        code = parse_qs(urlsplit(result.location).query)["code"][0]
        body = {"grant_type": "authorization_code", "code": code, "code_verifier": verifier,
                "client_id": cid, "redirect_uri": CALLBACK, "resource": ORIGIN+"/mcp"}
        response = self.client.post("/oauth/token", base_url=ORIGIN, data=body)
        self.assertEqual(response.status_code, 200)
        return response.json, body

    def test_health_has_ready_local_storage_without_cloud(self):
        r = self.client.get("/healthz").json
        self.assertEqual(r["storage_mode"], "local_ephemeral")
        self.assertTrue(r["storage_ready"]);self.assertFalse(r["cloud_configured"])
        self.assertFalse(r["storage_persistent"]);self.assertFalse(r["ai_api_calls_enabled"])
        self.assertTrue(r["operator_configured"])
        self.assertNotIn(ADMIN, json.dumps(r))

    def test_real_manual_preparation_export_import_and_gallery_without_network(self):
        from PIL import Image
        photo = io.BytesIO();Image.new("RGB", (100, 60)).save(photo, "JPEG");photo.seek(0)
        with patch("requests.request", side_effect=AssertionError("No cloud/API")), patch("requests.get", side_effect=AssertionError("No model/API")):
            r = self.client.post("/api/demo/analyze-manual", buffered=True, data={"title": "Synthetic test car", "description": "Seller claims current mileage 120000 km, priced at 5000 EUR.", "images": (photo, "../../test.jpg")})
        self.assertIn("WAITING_FOR_AI", r.text)
        job = self.hub.list_jobs()["jobs"][0]["id"]
        self.assertEqual(self.client.get("/_beta/jobs/"+job).json["photos"][0]["inspection"], "not_inspected")
        self.assertEqual(self.client.get(f"/_beta/jobs/{job}/photos/p001").status_code, 200)
        auth = {"Authorization": "Bearer "+ADMIN}
        bundle = self.client.get(f"/_beta/operator/jobs/{job}/bundle", headers=auth)
        self.assertEqual(bundle.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(bundle.data)) as z:
            self.assertIn("sheets/overview_001.jpg", z.namelist())
            self.assertIn("review_policy", json.loads(z.read("analysis.json")))
        lease = self.client.post(f"/_beta/operator/jobs/{job}/claim", headers=auth, json={}).json["lease_token"]
        result = self.client.post(f"/_beta/operator/jobs/{job}/complete", headers=auth, json={"lease_token": lease, "report": finished_report(self.hub, job)})
        self.assertEqual(result.json["status"], "DONE")

    def test_mcp_initialize_discovery_and_auth(self):
        self.assertEqual(self.rpc("initialize").json["result"]["serverInfo"]["name"], "checkniauto-local-beta")
        tools = self.rpc("tools/list").json["result"]["tools"]
        self.assertEqual(len(tools), 7)
        self.assertTrue(tools[0]["annotations"]["readOnlyHint"])
        r = self.rpc("tools/call", {"name": "checkniauto_list_pending", "arguments": {}})
        self.assertEqual(r.status_code, 401)
        self.assertIn("resource_metadata", r.headers["WWW-Authenticate"])
        self.assertEqual(self.client.get("/mcp").status_code, 405)

    def test_oauth_code_replay_and_refresh_rotation(self):
        tokens, body = self.login()
        self.assertEqual(self.client.post("/oauth/token", base_url=ORIGIN, data=body).status_code, 400)
        refresh = {"grant_type": "refresh_token", "client_id": body["client_id"], "resource": ORIGIN+"/mcp", "refresh_token": tokens["refresh_token"]}
        r = self.client.post("/oauth/token", base_url=ORIGIN, data=refresh)
        self.assertEqual(r.status_code, 200)
        self.assertNotEqual(r.json["refresh_token"], tokens["refresh_token"])
        self.assertEqual(self.client.post("/oauth/token", base_url=ORIGIN, data=refresh).status_code, 400)

    def test_mcp_actual_image_content_and_report_publication(self):
        job, _ = prepared(self.hub, photos=True)
        tokens, _ = self.login()
        def call(name, args):
            return self.rpc("tools/call", {"name": name, "arguments": args}, tokens["access_token"])
        image = call("checkniauto_get_collage", {"job_id": job, "sheet_id": "s001"}).json["result"]["content"][1]
        self.assertEqual(image["type"], "image")
        self.assertTrue(base64.b64decode(image["data"]).startswith(b"\xff\xd8"))
        claim = call("checkniauto_claim_analysis", {"job_id": job}).json["result"]
        lease = json.loads(claim["content"][0]["text"])["lease_token"]
        r = call("checkniauto_complete_analysis", {"job_id": job, "lease_token": lease, "report": finished_report(self.hub, job)}).json["result"]
        self.assertEqual(json.loads(r["content"][0]["text"])["status"], "DONE")

    def test_read_scope_cannot_claim_or_publish(self):
        tokens, _ = self.login("analysis:read offline_access")
        job, _ = prepared(self.hub)
        result = self.rpc("tools/call", {"name": "checkniauto_claim_analysis", "arguments": {"job_id": job}}, tokens["access_token"])
        self.assertEqual(result.status_code, 401)
        self.assertEqual(self.hub.get_job(job)["status"], "WAITING_FOR_AI")

    def test_hostile_origin_and_redirect_registration_are_rejected(self):
        self.assertEqual(self.client.post("/mcp", json={}, headers={"Origin": "https://evil.example"}).status_code, 403)
        for callback in ("https://evil.example/callback", "https://chatgpt.com.evil.example/connector_platform_oauth_redirect", "https://chatgpt.com/connector_platform_oauth_redirect?next=evil"):
            with self.subTest(callback=callback):
                self.assertEqual(self.client.post("/oauth/register", json={"redirect_uris": [callback]}).status_code, 400)

    def test_invalid_tool_json_is_handled(self):
        tokens, _ = self.login()
        result = self.rpc("tools/call", {"name": "checkniauto_get_analysis", "arguments": {"job_id": []}}, tokens["access_token"])
        self.assertTrue(result.json["result"]["isError"])
        response = self.client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": []})
        self.assertEqual(response.json["error"]["code"], -32602)

    def test_legacy_router_forwards_mcp_and_blocks_paid_writes(self):
        from beta_server import BetaRouter
        from flask import Flask
        from werkzeug.test import Client
        from werkzeug.wrappers import Response
        legacy = Flask("legacy");legacy.add_url_rule("/analysis/old", "old", lambda: "old report")
        client = Client(BetaRouter(legacy, self.app), Response)
        self.assertEqual(client.get("/.well-known/oauth-protected-resource").json["resource"], ORIGIN+"/mcp")
        self.assertEqual(client.get("/analysis/old").text, "old report")
        self.assertEqual(client.post("/api/analyze/x").status_code, 403)


if __name__ == "__main__":
    unittest.main()
