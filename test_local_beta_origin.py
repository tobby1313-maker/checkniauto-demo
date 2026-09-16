"""Consent-page origin regressions, including a real browser form submission.

Browser tests run in CI with CHECKNI_BROWSER_TESTS=1 and Playwright installed.
CDP Fetch intercepts every browser request, including redirects, and feeds
Flask's app in-process. No external service, production credential, model API
or user data is required. The browser computes Origin and cookies itself.
"""
import base64
import hashlib
import html
import os
import re
import tempfile
import unittest
from urllib.parse import parse_qs, urlencode, urlsplit

from beta_server import create_beta_app

ORIGIN = "https://checkni.example"
CALLBACK = "https://chatgpt.com/connector_platform_oauth_redirect"
ADMIN = "synthetic-origin-regression-only-1234567890"


class OAuthFixture:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = create_beta_app({
            "TESTING": True, "CHECKNI_STORAGE_MODE": "local",
            "CHECKNI_LOCAL_DATA_DIR": self.tmp.name, "CHECKNI_OPERATOR_TOKEN": ADMIN,
            "CHECKNI_PUBLIC_ORIGIN": ORIGIN, "CHECKNI_MCP_READ_ONLY": False,
            "CHECKNI_BETA_MARKET_SEARCH": False,
        })
        self.client = self.app.test_client()

    def authorization_url(self):
        response = self.client.post("/oauth/register", base_url=ORIGIN,
                                    json={"redirect_uris": [CALLBACK]})
        self.assertEqual(response.status_code, 201)
        self.client_id = response.json["client_id"]
        self.verifier = "v" * 64
        challenge = base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).decode().rstrip("=")
        query = {"client_id": self.client_id, "redirect_uri": CALLBACK,
                 "response_type": "code", "code_challenge_method": "S256",
                 "code_challenge": challenge, "resource": ORIGIN + "/mcp",
                 "scope": "analysis:read offline_access", "state": "origin-regression"}
        return "/oauth/authorize?" + urlencode(query)

    def consent(self):
        response = self.client.get(self.authorization_url(), base_url=ORIGIN)
        self.assertEqual(response.status_code, 200)
        signed = html.unescape(re.search(r'name="signed" value="([^"]+)"', response.text).group(1))
        return response, {"signed": signed, "secret": ADMIN}

    def exchange(self, callback):
        query = parse_qs(urlsplit(callback).query)
        self.assertEqual(query["state"], ["origin-regression"])
        self.assertEqual(query["iss"], [ORIGIN])
        response = self.client.post("/oauth/token", base_url=ORIGIN, data={
            "grant_type": "authorization_code", "client_id": self.client_id,
            "redirect_uri": CALLBACK, "code_verifier": self.verifier,
            "code": query["code"][0], "resource": ORIGIN + "/mcp",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        token = response.json["access_token"]
        result = self.client.post("/mcp", base_url=ORIGIN,
            headers={"Authorization": "Bearer " + token},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "checkniauto_list_pending", "arguments": {}}})
        self.assertEqual(result.status_code, 200)
        self.assertNotIn("error", result.json)
        self.assertFalse(result.json["result"].get("isError", False))


class OAuthOriginHttpTests(OAuthFixture, unittest.TestCase):
    def test_only_consent_document_preserves_same_origin(self):
        page, _ = self.consent()
        self.assertEqual(page.headers["Referrer-Policy"], "same-origin")
        self.assertEqual(page.headers["Cache-Control"], "no-store")
        self.assertIn("form-action 'self' " + CALLBACK + ";", page.headers["Content-Security-Policy"])
        self.assertNotIn("*", page.headers["Content-Security-Policy"])
        self.assertIn("Secure", page.headers["Set-Cookie"])
        for path in ("/_beta/config", "/healthz", "/.well-known/oauth-authorization-server",
                     "/.well-known/oauth-protected-resource", "/oauth/authorize"):
            with self.subTest(path=path):
                response = self.client.get(path, base_url=ORIGIN)
                self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")

    def test_valid_consent_and_token_exchange_still_require_real_origin(self):
        _, form = self.consent()
        response = self.client.post("/oauth/authorize", base_url=ORIGIN,
                                    data=form, headers={"Origin": ORIGIN})
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.exchange(response.location)

    def test_null_missing_and_foreign_origins_remain_blocked(self):
        _, form = self.consent()
        for origin in ("null", None, "https://evil.example", "https://chatgpt.com",
                       ORIGIN + ".evil.example", "http://checkni.example"):
            with self.subTest(origin=origin):
                response = self.client.post("/oauth/authorize", base_url=ORIGIN, data=form,
                                            headers={} if origin is None else {"Origin": origin})
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json["ai_api_calls"], 0)
                self.assertIs(response.json["chargeable"], False)

    def test_same_origin_does_not_bypass_signed_form_or_browser_cookie(self):
        _, form = self.consent()
        response = self.client.post("/oauth/authorize", base_url=ORIGIN,
            headers={"Origin": ORIGIN}, data={**form, "signed": form["signed"] + "tampered"})
        self.assertEqual(response.status_code, 400)
        separate_browser = self.app.test_client()
        response = separate_browser.post("/oauth/authorize", base_url=ORIGIN,
                                          headers={"Origin": ORIGIN}, data=form)
        self.assertEqual(response.status_code, 403)
        self.assertIn("browser session", response.json["error"])

    def test_mcp_origin_validation_is_not_relaxed(self):
        rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        for origin in ("null", "https://evil.example", "https://chatgpt.com.evil.example"):
            with self.subTest(origin=origin):
                response = self.client.post("/mcp", base_url=ORIGIN, json=rpc, headers={"Origin": origin})
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json["error"], "Origin not allowed.")
        for origin in (None, ORIGIN, "https://chatgpt.com"):
            with self.subTest(origin=origin):
                response = self.client.post("/mcp", base_url=ORIGIN, json=rpc,
                                            headers={} if origin is None else {"Origin": origin})
                self.assertEqual(response.status_code, 200)
        protected = self.client.post("/mcp", base_url=ORIGIN, json={
            **rpc, "method": "tools/call", "params": {"name": "checkniauto_list_pending", "arguments": {}}})
        self.assertEqual(protected.status_code, 401)


@unittest.skipUnless(os.environ.get("CHECKNI_BROWSER_TESTS") == "1", "Enable Playwright browser regressions explicitly")
class OAuthOriginBrowserTests(OAuthFixture, unittest.TestCase):
    def browser_consent(self, *, reproduce_old_policy=False):
        from playwright.sync_api import sync_playwright
        url = self.authorization_url()
        transport = self.app.test_client(use_cookies=False)
        seen = {"posts": [], "callbacks": [], "console": []}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(service_workers="block")
            context.set_default_timeout(15000)
            page = context.new_page()
            cdp = context.new_cdp_session(page)
            page.on("console", lambda msg: seen["console"].append(msg.text))

            def fulfill(request_id, status, headers, data):
                cdp.send("Fetch.fulfillRequest", {
                    "requestId": request_id, "responseCode": status,
                    "responseHeaders": [{"name": k, "value": v} for k, v in headers
                                        if k.lower() != "content-length"],
                    "body": base64.b64encode(data).decode(),
                })

            def route_request(event):
                req = event["request"]
                parsed = urlsplit(req["url"])
                headers = {k.lower(): v for k, v in req["headers"].items()}
                request_id = event["requestId"]
                if parsed.scheme + "://" + parsed.netloc == ORIGIN:
                    path = parsed.path + ("?" + parsed.query if parsed.query else "")
                    response = transport.open(path, base_url=ORIGIN, method=req["method"],
                                              headers=headers, data=req.get("postData", "").encode())
                    out_headers = response.headers.copy()
                    if reproduce_old_policy and parsed.path == "/oauth/authorize" and req["method"] == "GET":
                        out_headers["Referrer-Policy"] = "no-referrer"
                    if parsed.path == "/oauth/authorize" and req["method"] == "POST":
                        seen["posts"].append({"origin": headers.get("origin"), "status": response.status_code,
                                              "error": response.json.get("error") if response.is_json else None})
                    fulfill(request_id, response.status_code, list(out_headers), response.get_data())
                elif req["url"].startswith(CALLBACK + "?"):
                    seen["callbacks"].append({"url": req["url"], "referer": headers.get("referer")})
                    fulfill(request_id, 200, [("Content-Type", "text/html")], b"<h1>Callback received</h1>")
                else:
                    cdp.send("Fetch.failRequest", {"requestId": request_id, "errorReason": "BlockedByClient"})

            # Playwright route handlers cover only the first URL of a redirect.
            # CDP Fetch covers each hop, so the synthetic callback never reaches
            # the real ChatGPT service. No Origin/Cookie/security policy is forged.
            cdp.on("Fetch.requestPaused", route_request)
            cdp.send("Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]})
            try:
                page.goto(ORIGIN + url)
                page.locator('input[name="secret"]').fill(ADMIN)
                with page.expect_navigation(wait_until="load"):
                    page.get_by_role("button", name="Schváliť pripojenie").click()
            except Exception as exc:
                self.fail(f"Browser consent failed: {exc}; posts={seen['posts']}; console={seen['console']}")
            finally:
                context.close()
                browser.close()
        return seen

    def test_browser_reproduces_original_null_origin_failure(self):
        seen = self.browser_consent(reproduce_old_policy=True)
        self.assertEqual(seen["posts"], [{"origin": "null", "status": 403, "error": "Origin not allowed."}])
        self.assertEqual(seen["callbacks"], [])

    def test_browser_sends_real_origin_and_finishes_oauth_without_header_override(self):
        seen = self.browser_consent()
        self.assertEqual(seen["posts"], [{"origin": ORIGIN, "status": 303, "error": None}])
        self.assertEqual(len(seen["callbacks"]), 1, seen["console"])
        self.assertIsNone(seen["callbacks"][0]["referer"])
        self.exchange(seen["callbacks"][0]["url"])


if __name__ == "__main__":
    unittest.main()
