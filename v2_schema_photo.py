from __future__ import annotations

from typing import Any

PHOTO_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "available": {"type": "boolean"},
        "images_reviewed": {"type": "integer"},
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "severity": {
                        "type": "string",
                        "enum": ["info", "watch", "risk", "critical"],
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                    },
                    "photo_refs": {"type": "array", "items": {"type": "string"}},
                    "observation": {"type": "string"},
                    "interpretation": {"type": "string"},
                    "action": {"type": "string"},
                    "cost_min_eur": {"type": "integer"},
                    "cost_max_eur": {"type": "integer"},
                },
                "required": [
                    "title",
                    "severity",
                    "confidence",
                    "photo_refs",
                    "observation",
                    "interpretation",
                    "action",
                    "cost_min_eur",
                    "cost_max_eur",
                ],
            },
        },
        "positive_signals": {"type": "array", "items": {"type": "string"}},
        "coverage_gaps": {"type": "array", "items": {"type": "string"}},
        "limitations": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "available",
        "images_reviewed",
        "summary",
        "findings",
        "positive_signals",
        "coverage_gaps",
        "limitations",
    ],
}
