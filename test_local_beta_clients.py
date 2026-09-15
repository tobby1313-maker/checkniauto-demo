"""OAuth registration lifetime tests with the real Flask app and disposable SQLite.

The client ID is public; it must survive a clean Render disk without granting
access. Consent, admin authentication, exact callback binding and PKCE still apply.
No production credentials or external network requests are used.
"""
import base64
import hashlib
import json
import unittest
from urllib.parse import urlencode

import test_local_beta_origin as fixtures
from scrapper_demo.local_beta_mcp import CHATGPT_PUBLIC_CLIENT_ID, CHATGPT_PUBLIC_REDIRECT


class StableClientHttpTests(fixtures.OAuthFixture, unittest.TestCase):
    def authorization_url(self, client_id=CHATGPT_PUBLIC_CLIENT_ID,
                          callback=CHATGPT_PUBLIC_REDIRECT, **overrides):
        self.client_id = client_id
        self.verifier = "v" * 64
        challenge = base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).decode().rstrip("=")
        query = {"client_id": client_id, "redirect_uri": callback,
                 "response_type": "code", "code_challenge_method": "S256",
                 "code_challenge": challenge, "resource": fixtures.ORIGIN + "/mcp",
                 "scope": "analysis:read offline_access", "state": "origin-regression"}
        query.update(overrides)
        return "/oauth/authorize?" + urlencode(query)

    def restart_with_empty_disk(self):
        self.tmp.cleanup()
        fixtures.OAuthFixture.setUp(self)

    def test_predefined_client_works_without_registration_or_network(self):
        from unittest.mock import patch
        with patch("requests.request", side_effect=AssertionError("No cloud/model call")), \
             patch("requests.get", side_effect=AssertionError("No metadata/model call")):
            page, form = self.consent()
            self.assertEqual(page.status_code, 200)
            response = self.client.post("/oauth/authorize", base_url=fixtures.ORIGIN,
                                        data=form, headers={"Origin": fixtures.ORIGIN})
            self.assertEqual(response.status_code, 303)
            self.exchange(response.location)
        with self.app.extensions["beta_hub"].connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM oauth_clients").fetchone()[0], 0)

    def test_dynamic_registration_returns_stable_public_client_id(self):
        for _ in range(2):
            response = self.client.post("/oauth/register", base_url=fixtures.ORIGIN,
                                        json={"redirect_uris": [CHATGPT_PUBLIC_REDIRECT]})
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.json["client_id"], CHATGPT_PUBLIC_CLIENT_ID)
            self.assertEqual(response.json["redirect_uris"], [CHATGPT_PUBLIC_REDIRECT])
            self.assertEqual(response.json["token_endpoint_auth_method"], "none")
            self.assertNotIn("client_secret", response.json)

    def test_registered_client_survives_complete_disk_loss_before_consent(self):
        registration = self.client.post("/oauth/register", base_url=fixtures.ORIGIN,
                                         json={"redirect_uris": [CHATGPT_PUBLIC_REDIRECT]})
        old_url = self.authorization_url(client_id=registration.json["client_id"])
        self.restart_with_empty_disk()
        page = self.client.get(old_url, base_url=fixtures.ORIGIN)
        self.assertEqual(page.status_code, 200)
        self.assertIn('name="secret"', page.text)
        _, form = self.consent()
        response = self.client.post("/oauth/authorize", base_url=fixtures.ORIGIN,
                                    data=form, headers={"Origin": fixtures.ORIGIN})
        self.assertEqual(response.status_code, 303)
        self.exchange(response.location)

    def test_legacy_random_client_is_not_silently_reregistered(self):
        old_id = "old-random-client-before-redeploy"
        with self.app.extensions["beta_hub"].connection(True) as db:
            db.execute("INSERT INTO oauth_clients VALUES(?,?,?)",
                       (old_id, json.dumps([CHATGPT_PUBLIC_REDIRECT]), 1))
        url = self.authorization_url(client_id=old_id)
        self.assertEqual(self.client.get(url, base_url=fixtures.ORIGIN).status_code, 200)
        self.restart_with_empty_disk()
        result = self.client.get(url, base_url=fixtures.ORIGIN)
        self.assertEqual(result.status_code, 400)
        self.assertIn("registration is missing", result.json["error"])
        self.assertIn(CHATGPT_PUBLIC_CLIENT_ID, result.json["error"])
        self.assertIsNone(result.headers.get("Location"))
        self.assertIsNone(result.headers.get("Set-Cookie"))
        with self.app.extensions["beta_hub"].connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM oauth_clients").fetchone()[0], 0)

    def test_static_client_requires_one_exact_callback(self):
        callbacks = ("https://evil.example/callback", CHATGPT_PUBLIC_REDIRECT + "/",
                     CHATGPT_PUBLIC_REDIRECT + "?next=evil", CHATGPT_PUBLIC_REDIRECT + "#fragment",
                     "https://chatgpt.com/connector/oauth/another-connector",
                     "https://chatgpt.com.evil.example/connector_platform_oauth_redirect",
                     "https://chatgpt.com:443/connector_platform_oauth_redirect",
                     "http://chatgpt.com/connector_platform_oauth_redirect")
        for callback in callbacks:
            with self.subTest(callback=callback):
                result = self.client.get(self.authorization_url(callback=callback), base_url=fixtures.ORIGIN)
                self.assertEqual(result.status_code, 400)
                self.assertIn("callback does not match", result.json["error"])
                self.assertIsNone(result.headers.get("Location"))
                self.assertIsNone(result.headers.get("Set-Cookie"))

    def test_public_client_id_is_not_an_admin_credential_or_bearer_token(self):
        _, form = self.consent()
        result = self.client.post("/oauth/authorize", base_url=fixtures.ORIGIN,
                                  data={**form, "secret": CHATGPT_PUBLIC_CLIENT_ID},
                                  headers={"Origin": fixtures.ORIGIN})
        self.assertEqual(result.status_code, 401)
        rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "checkniauto_list_pending", "arguments": {}}}
        result = self.client.post("/mcp", base_url=fixtures.ORIGIN, json=rpc,
                                  headers={"Authorization": "Bearer " + CHATGPT_PUBLIC_CLIENT_ID})
        self.assertEqual(result.status_code, 401)
        with self.app.extensions["beta_hub"].connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM oauth_codes").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM oauth_tokens").fetchone()[0], 0)

    def test_static_registration_does_not_skip_pkce_resource_or_scope_checks(self):
        for override in ({"code_challenge": ""}, {"code_challenge_method": "plain"},
                         {"resource": "https://evil.example/mcp"}, {"scope": "admin"}):
            with self.subTest(override=override):
                result = self.client.get(self.authorization_url(**override), base_url=fixtures.ORIGIN)
                self.assertEqual(result.status_code, 400)
                self.assertIsNone(result.headers.get("Set-Cookie"))

    def test_callback_specific_registration_keeps_its_exact_binding(self):
        callback = "https://chatgpt.com/connector/oauth/synthetic-connector"
        response = self.client.post("/oauth/register", base_url=fixtures.ORIGIN,
                                    json={"redirect_uris": [callback]})
        client_id = response.json["client_id"]
        self.assertNotEqual(client_id, CHATGPT_PUBLIC_CLIENT_ID)
        self.assertEqual(self.client.get(self.authorization_url(client_id, callback),
                                        base_url=fixtures.ORIGIN).status_code, 200)
        self.assertEqual(self.client.get(self.authorization_url(client_id, CHATGPT_PUBLIC_REDIRECT),
                                        base_url=fixtures.ORIGIN).status_code, 400)

    def test_database_cannot_override_predefined_callback(self):
        with self.app.extensions["beta_hub"].connection(True) as db:
            db.execute("INSERT INTO oauth_clients VALUES(?,?,?)",
                       (CHATGPT_PUBLIC_CLIENT_ID, json.dumps(["https://evil.example/callback"]), 1))
        result = self.client.get(self.authorization_url(callback="https://evil.example/callback"),
                                  base_url=fixtures.ORIGIN)
        self.assertEqual(result.status_code, 400)
        self.assertEqual(self.client.get(self.authorization_url(), base_url=fixtures.ORIGIN).status_code, 200)

    def test_reset_does_not_resurrect_old_access_tokens_or_codes(self):
        _, form = self.consent()
        response = self.client.post("/oauth/authorize", base_url=fixtures.ORIGIN,
                                    data=form, headers={"Origin": fixtures.ORIGIN})
        from urllib.parse import parse_qs, urlsplit
        body = {"grant_type": "authorization_code", "client_id": self.client_id,
                "redirect_uri": CHATGPT_PUBLIC_REDIRECT, "code_verifier": self.verifier,
                "code": parse_qs(urlsplit(response.location).query)["code"][0],
                "resource": fixtures.ORIGIN + "/mcp"}
        tokens = self.client.post("/oauth/token", base_url=fixtures.ORIGIN, data=body).json
        self.restart_with_empty_disk()
        rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "checkniauto_list_pending", "arguments": {}}}
        result = self.client.post("/mcp", base_url=fixtures.ORIGIN, json=rpc,
                                  headers={"Authorization": "Bearer " + tokens["access_token"]})
        self.assertEqual(result.status_code, 401)
        self.assertEqual(self.client.post("/oauth/token", base_url=fixtures.ORIGIN, data=body).status_code, 400)
        self.assertEqual(self.client.get(self.authorization_url(), base_url=fixtures.ORIGIN).status_code, 200)


class StableClientBrowserTests(fixtures.OAuthOriginBrowserTests):
    """Repeat the actual browser consent tests after deleting registration storage."""
    def authorization_url(self):
        url = super().authorization_url()
        self.assertEqual(self.client_id, CHATGPT_PUBLIC_CLIENT_ID)
        self.tmp.cleanup()
        fixtures.OAuthFixture.setUp(self)
        return url


if __name__ == "__main__":
    unittest.main()
