"""Public frontend, health, telemetry, and demo API routes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from flask import Blueprint

from ._registration import RouteSpec, create_route_blueprint


PUBLIC_ROUTES = (
    RouteSpec("/", "index"),
    RouteSpec("/token-dashboard.html", "token_dashboard"),
    RouteSpec("/healthz", "healthz"),
    RouteSpec("/api/token-usage", "api_token_usage"),
    RouteSpec("/api/demo/current-progress", "api_demo_current_progress"),
    RouteSpec("/api/demo/listings", "api_demo_listings"),
    RouteSpec("/api/demo/listings/<slug>", "api_demo_listing_detail"),
    RouteSpec("/api/demo/listings/<slug>/artifacts", "api_demo_listing_artifacts"),
    RouteSpec("/api/demo/listings/<slug>/artifacts/<filename>", "api_demo_listing_artifact"),
    RouteSpec("/api/demo/listings/<slug>/image/<filename>", "api_demo_listing_image"),
    RouteSpec("/api/demo/listings/<slug>/analysis-result/raw", "api_demo_listing_analysis_result_raw"),
    RouteSpec("/api/demo/analyze", "api_demo_analyze", ("POST",)),
    RouteSpec("/api/demo/analyze-manual", "api_demo_analyze_manual", ("POST",)),
)


def create_public_blueprint(handlers: Mapping[str, Any]) -> Blueprint:
    return create_route_blueprint("public", handlers, PUBLIC_ROUTES)
