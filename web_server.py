#!/usr/bin/env python3
"""Compatibility entry point for the packaged Scrapper demo server."""

import sys

from flask import request

from scrapper_demo import legacy_server as _legacy_server
from scrapper_demo.url_normalizer import normalize_bazos_listing_url


@_legacy_server.app.before_request
def _normalize_incoming_listing_url():
    """Repair malformed Bazos listing slugs before the analysis route reads JSON."""
    if request.method not in {"POST", "PUT", "PATCH"} or not request.is_json:
        return None

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return None

    submitted_url = payload.get("url")
    if not isinstance(submitted_url, str):
        return None

    normalized_url = normalize_bazos_listing_url(submitted_url)
    if normalized_url != submitted_url:
        payload["url"] = normalized_url
        print(f"Normalized Bazos listing URL: {normalized_url}", flush=True)

    return None


if __name__ == "__main__":
    _legacy_server.app.run(host="0.0.0.0", port=5000, debug=True)
else:
    sys.modules[__name__] = _legacy_server
