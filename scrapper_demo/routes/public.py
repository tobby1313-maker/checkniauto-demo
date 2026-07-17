"""Public frontend, health, telemetry, and demo API routes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from flask import Blueprint

from ._registration import RouteSpec, create_route_blueprint


PUBLIC_ROUTES = (
    RouteSpec("/", "index"),
    RouteSpec("/analysis/<slug>", "analysis_page"),
    RouteSpec("/analysis/<slug>/technical", "technical_analysis_page"),
    RouteSpec("/token-dashboard.html", "token_dashboard"),
    RouteSpec("/admin/login", "admin_login", ("GET", "POST")),
    RouteSpec("/admin/logout", "admin_logout", ("POST",)),
    RouteSpec("/healthz", "healthz"),
    RouteSpec("/api/token-usage", "api_token_usage"),
    RouteSpec("/api/demo/current-progress", "api_demo_current_progress"),
    RouteSpec("/api/demo/listings", "api_demo_listings"),
    RouteSpec("/api/demo/listings/<slug>", "api_demo_listing_detail"),
    RouteSpec("/api/demo/listings/<slug>/presentation", "api_demo_listing_presentation"),
    RouteSpec("/api/demo/listings/<slug>/artifacts", "api_demo_listing_artifacts"),
    RouteSpec("/api/demo/listings/<slug>/artifacts/<filename>", "api_demo_listing_artifact"),
    RouteSpec("/api/demo/listings/<slug>/image/<filename>", "api_demo_listing_image"),
    RouteSpec("/api/demo/listings/<slug>/analysis-result/raw", "api_demo_listing_analysis_result_raw"),
    RouteSpec("/api/demo/analyze", "api_demo_analyze", ("POST",)),
    RouteSpec("/api/demo/analyze-manual", "api_demo_analyze_manual", ("POST",)),
    RouteSpec("/api/admin/calibration-bundles/<slug>", "api_admin_calibration_bundle"),
    RouteSpec("/api/admin/debugging-bundles/<slug>", "api_admin_debugging_bundle"),
    RouteSpec("/api/admin/listings", "api_admin_listings"),
    RouteSpec("/api/admin/listings/<slug>/artifacts", "api_admin_listing_artifacts"),
    RouteSpec(
        "/api/admin/listings/<slug>/artifacts/<filename>",
        "api_admin_listing_artifact",
    ),
)


def create_public_blueprint(handlers: Mapping[str, Any]) -> Blueprint:
    return create_route_blueprint("public", handlers, PUBLIC_ROUTES)
