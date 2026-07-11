"""Private listing administration and analysis routes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from flask import Blueprint

from ._registration import RouteSpec, create_route_blueprint


PRIVATE_ROUTES = (
    RouteSpec("/api/listings", "api_listings"),
    RouteSpec("/api/listings/<slug>", "api_listing_detail"),
    RouteSpec("/api/listings/<slug>", "api_update_listing_detail", ("PUT",)),
    RouteSpec("/api/listings/<slug>/artifacts", "api_listing_artifacts"),
    RouteSpec("/api/listings/<slug>/artifacts/<filename>", "api_listing_artifact"),
    RouteSpec("/api/listings/<slug>/images", "api_listing_images"),
    RouteSpec("/api/listings/<slug>/image/<filename>", "api_listing_image"),
    RouteSpec("/api/listings/<slug>/analysis-images", "api_listing_analysis_images"),
    RouteSpec("/api/listings/<slug>/analysis-image/<filename>", "api_listing_analysis_image"),
    RouteSpec("/api/listings/<slug>/analysis", "api_listing_analysis"),
    RouteSpec("/api/listings/<slug>/analysis-result", "api_listing_analysis_result"),
    RouteSpec("/api/listings/<slug>/analysis-result/raw", "api_listing_analysis_result_raw"),
    RouteSpec("/api/listings/<slug>/analysis-result/export", "api_listing_analysis_export"),
    RouteSpec("/api/kb", "api_kb"),
    RouteSpec("/api/kb/<category>/<filename>", "api_kb_file"),
    RouteSpec("/api/manual-listing", "api_manual_listing", ("POST",)),
    RouteSpec("/api/scrape", "api_scrape", ("POST",)),
    RouteSpec("/api/analyze/<slug>", "api_analyze", ("POST",)),
    RouteSpec("/api/analyze/<slug>/save-pasted-result", "api_save_pasted_result", ("POST",)),
    RouteSpec("/api/listings/<slug>/open-folder", "api_open_folder"),
    RouteSpec("/api/analyze/<slug>/save-kb", "api_save_kb", ("POST",)),
    RouteSpec("/api/test-api-key", "api_test_api_key", ("POST",)),
    RouteSpec("/api/test-backup-key", "api_test_api_key", ("POST",)),
)


def create_private_blueprint(handlers: Mapping[str, Any]) -> Blueprint:
    return create_route_blueprint("private", handlers, PRIVATE_ROUTES)
