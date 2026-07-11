"""Shared definitions for the application's route blueprints."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from flask import Blueprint, Flask


@dataclass(frozen=True, slots=True)
class RouteSpec:
    rule: str
    handler: str
    methods: tuple[str, ...] = ("GET",)


def create_route_blueprint(
    name: str,
    handlers: Mapping[str, Any],
    route_specs: Sequence[RouteSpec],
) -> Blueprint:
    """Bind stable HTTP contracts to handler callables at composition time."""
    blueprint = Blueprint(name, __name__)
    for index, spec in enumerate(route_specs):
        handler = handlers.get(spec.handler)
        if not callable(handler):
            raise RuntimeError(f"Missing route handler: {spec.handler}")

        def route_adapter(*args: Any, __handler: Callable[..., Any] = handler, **kwargs: Any):
            return __handler(*args, **kwargs)

        route_adapter.__name__ = spec.handler
        blueprint.add_url_rule(
            spec.rule,
            endpoint=f"{spec.handler}_{index}",
            view_func=route_adapter,
            methods=spec.methods,
        )
    return blueprint


def register_app_request_hooks(source: Flask, target: Flask) -> None:
    """Keep global request safety hooks active after route extraction."""
    for function in source.before_request_funcs.get(None, ()):
        target.before_request(function)
    for function in source.after_request_funcs.get(None, ()):
        target.after_request(function)
    for function in source.teardown_request_funcs.get(None, ()):
        target.teardown_request(function)
