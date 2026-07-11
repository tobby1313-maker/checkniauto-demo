#!/usr/bin/env python3
"""Compatibility entry point for the packaged Scrapper demo server."""

import sys

from scrapper_demo import legacy_server as _legacy_server


if __name__ == "__main__":
    _legacy_server.app.run(host="0.0.0.0", port=5000, debug=True)
else:
    sys.modules[__name__] = _legacy_server
