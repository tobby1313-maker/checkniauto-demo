"""Flask application factory and mode-aware blueprint registration."""

from __future__ import annotations

import importlib
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from flask import Flask

from .config import DemoServerConfig
from .progress import RUNTIME_STATE_KEY, DemoRuntimeState
from .routes import create_private_blueprint, create_public_blueprint
from .routes._registration import register_app_request_hooks


def _apply_config_overrides(app: Flask, overrides: Mapping[str, Any]) -> None:
    app.config.update(overrides)
    if "SCRAPPER_DATA_DIR" in overrides and "SCRAPPER_AUTA_DIR" not in overrides:
        app.config["SCRAPPER_AUTA_DIR"] = os.path.join(
            str(app.config["SCRAPPER_DATA_DIR"]), "Auta"
        )
    if "DEMO_MAX_UPLOAD_MB" in overrides and "MAX_CONTENT_LENGTH" not in overrides:
        app.config["MAX_CONTENT_LENGTH"] = (
            int(app.config["DEMO_MAX_UPLOAD_MB"]) * 1024 * 1024
        )


def create_app(
    config: Mapping[str, Any] | None = None,
    *,
    register_legacy_routes: bool = True,
    import_name: str | None = None,
) -> Flask:
    """Create an independently configured Flask application instance."""
    script_dir = Path(__file__).resolve().parent.parent
    server_config = DemoServerConfig.from_env(script_dir)
    static_folder = (
        str(config["SCRAPPER_WEB_DIR"])
        if config and "SCRAPPER_WEB_DIR" in config
        else server_config.web_dir
    )
    app = Flask(
        import_name or __name__,
        static_folder=static_folder,
        static_url_path="",
    )
    app.config.from_mapping(server_config.as_flask_mapping())
    if config:
        _apply_config_overrides(app, config)

    Path(str(app.config["SCRAPPER_AUTA_DIR"])).mkdir(parents=True, exist_ok=True)
    app.extensions[RUNTIME_STATE_KEY] = DemoRuntimeState(
        int(app.config["DEMO_MAX_CONCURRENT_JOBS"])
    )

    if register_legacy_routes:
        legacy_server = importlib.import_module("web_server")
        handlers = vars(legacy_server)
        app.register_blueprint(create_public_blueprint(handlers))
        if not app.config["DEMO_MODE"]:
            app.register_blueprint(create_private_blueprint(handlers))
        register_app_request_hooks(legacy_server.app, app)

    return app
