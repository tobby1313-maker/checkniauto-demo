from __future__ import annotations

from typing import Any

RESEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["supported", "limited", "unavailable"]},
        "identified_variant": {"type": "string"},
        "variant_confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "known_risks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "component": {"type": "string"},
                    "issue": {"type": "string"},
                    "applicability": {"type": "string"},
                    "typical_trigger": {"type": "string"},
                    "cost_min_eur": {"type": "integer"},
                    "cost_max_eur": {"type": "integer"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "source_urls": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "component",
                    "issue",
                    "applicability",
                    "typical_trigger",
                    "cost_min_eur",
                    "cost_max_eur",
                    "confidence",
                    "source_urls",
                ],
            },
        },
        "market": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["supported", "limited", "unavailable"]},
                "summary": {"type": "string"},
                "position": {"type": "string", "enum": ["low", "fair", "high", "unknown"]},
                "currency": {"type": "string"},
                "range_min": {"type": "integer"},
                "range_max": {"type": "integer"},
                "median": {"type": "integer"},
                "recommended_max": {"type": "integer"},
                "comparable_count": {"type": "integer"},
                "comparables": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "price": {"type": "integer"},
                            "currency": {"type": "string"},
                            "year": {"type": "integer"},
                            "mileage_km": {"type": "integer"},
                            "url": {"type": "string"},
                            "note": {"type": "string"},
                        },
                        "required": [
                            "title",
                            "price",
                            "currency",
                            "year",
                            "mileage_km",
                            "url",
                            "note",
                        ],
                    },
                },
            },
            "required": [
                "status",
                "summary",
                "position",
                "currency",
                "range_min",
                "range_max",
                "median",
                "recommended_max",
                "comparable_count",
                "comparables",
            ],
        },
        "vin_public_mentions": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["found", "not_found", "not_checked"]},
                "summary": {"type": "string"},
                "source_urls": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["status", "summary", "source_urls"],
        },
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "url": {"type": "string"},
                    "supports": {"type": "string"},
                },
                "required": ["title", "url", "supports"],
            },
        },
        "limitations": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "status",
        "identified_variant",
        "variant_confidence",
        "known_risks",
        "market",
        "vin_public_mentions",
        "sources",
        "limitations",
    ],
}
