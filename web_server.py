#!/usr/bin/env python3
"""Compatibility entry point; new analyses default to the no-paid-API beta."""
import os
import sys
from flask import request
from scrapper_demo import legacy_server as _legacy_server
from scrapper_demo.url_normalizer import normalize_bazos_listing_url


@_legacy_server.app.before_request
def _normalize_incoming_listing_url():
    if request.method not in {"POST", "PUT", "PATCH"} or not request.is_json:
        return None
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get("url"), str):
        return None
    payload["url"] = normalize_bazos_listing_url(payload["url"])
    return None


# Only an explicit operator rollback can re-enable the previous paid pipeline.
# Missing Cloudflare configuration fails closed, never falls back to an AI API.
if os.environ.get("CHECKNI_AI_MODE", "chatgpt_beta") != "legacy_api":
    from beta_server import BetaRouter, create_beta_app
    _legacy_server.app.wsgi_app = BetaRouter(_legacy_server.app.wsgi_app, create_beta_app())

if __name__ == "__main__":
    _legacy_server.app.run(host="0.0.0.0", port=5000, debug=False)
else:
    sys.modules[__name__] = _legacy_server
