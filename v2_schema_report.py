from __future__ import annotations

from typing import Any

FINAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "executive_summary": {"type": "string"},
        "verdict": {
            "type": "object",
            "properties": {
                "level": {
                    "type": "string",
                    "enum": ["green", "yellow", "orange", "red", "stop"],
                },
                "safety_score": {"type": "integer"},
                "confidence": {"type": "integer"},
                "one_sentence": {"type": "string"},
                "recommendation": {"type": "string"},
            },
            "required": [
                "level",
                "safety_score",
                "confidence",
                "one_sentence",
                "recommendation",
            ],
        },
        "top_findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "category": {"type": "string"},
                    "severity": {
                        "type": "string",
                        "enum": ["info", "watch", "risk", "critical"],
                    },
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "evidence_type": {
                        "type": "string",
                        "enum": [
                            "listing",
                            "photo",
                            "web",
                            "general_knowledge",
                            "estimate",
                            "manual_check",
                        ],
                    },
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "action": {"type": "string"},
                    "cost_min_eur": {"type": "integer"},
                    "cost_max_eur": {"type": "integer"},
                },
                "required": [
                    "id",
                    "category",
                    "severity",
                    "title",
                    "summary",
                    "evidence_type",
                    "evidence_refs",
                    "confidence",
                    "action",
                    "cost_min_eur",
                    "cost_max_eur",
                ],
            },
        },
        "price_assessment": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["good", "fair", "high", "low", "unknown"]},
                "summary": {"type": "string"},
                "evidence_quality": {
                    "type": "string",
                    "enum": ["high", "medium", "low", "unavailable"],
                },
                "market_min": {"type": "integer"},
                "market_max": {"type": "integer"},
                "recommended_max": {"type": "integer"},
                "currency": {"type": "string"},
                "negotiation_points": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "status",
                "summary",
                "evidence_quality",
                "market_min",
                "market_max",
                "recommended_max",
                "currency",
                "negotiation_points",
            ],
        },
        "ownership_costs": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "total_min_eur": {"type": "integer"},
                "total_max_eur": {"type": "integer"},
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "item": {"type": "string"},
                            "reason": {"type": "string"},
                            "min_eur": {"type": "integer"},
                            "max_eur": {"type": "integer"},
                            "urgency": {
                                "type": "string",
                                "enum": ["now", "soon", "reserve", "unknown"],
                            },
                            "evidence_type": {
                                "type": "string",
                                "enum": [
                                    "listing",
                                    "photo",
                                    "web",
                                    "general_knowledge",
                                    "estimate",
                                    "manual_check",
                                ],
                            },
                        },
                        "required": [
                            "item",
                            "reason",
                            "min_eur",
                            "max_eur",
                            "urgency",
                            "evidence_type",
                        ],
                    },
                },
            },
            "required": ["summary", "total_min_eur", "total_max_eur", "items"],
        },
        "positives": {"type": "array", "items": {"type": "string"}},
        "seller_questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "why_it_matters": {"type": "string"},
                    "red_flag_answer": {"type": "string"},
                },
                "required": ["question", "why_it_matters", "red_flag_answer"],
            },
        },
        "inspection_checklist": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "group": {"type": "string"},
                    "items": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["group", "items"],
            },
        },
        "limitations": {"type": "array", "items": {"type": "string"}},
        "disclaimer": {"type": "string"},
    },
    "required": [
        "headline",
        "executive_summary",
        "verdict",
        "top_findings",
        "price_assessment",
        "ownership_costs",
        "positives",
        "seller_questions",
        "inspection_checklist",
        "limitations",
        "disclaimer",
    ],
}
