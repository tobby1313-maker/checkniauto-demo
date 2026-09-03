from __future__ import annotations

import time
from typing import Any

from v2_ai import _clamp, _list_of_dicts, _list_of_strings
from v2_config import TEXT_MODEL, VISION_MODEL, _unique, utc_now
from v2_fallback import build_fallback_report
from v2_normalize import _number


def sanitize_report(
    report: dict[str, Any],
    listing: dict[str, Any],
    photo: dict[str, Any],
    research: dict[str, Any],
    language: str,
    job_id: str,
    started_at: float,
) -> dict[str, Any]:
    fallback = build_fallback_report(listing, photo, research, language)
    result = report if isinstance(report, dict) else {}

    for key, default in fallback.items():
        if key not in result or result[key] in (None, ""):
            result[key] = default

    verdict = result.get("verdict") if isinstance(result.get("verdict"), dict) else {}
    fallback_verdict = fallback["verdict"]
    level = (
        verdict.get("level")
        if verdict.get("level") in {"green", "yellow", "orange", "red", "stop"}
        else fallback_verdict["level"]
    )
    quality_score = int(listing.get("data_quality", {}).get("score", 0))
    confidence_cap = min(96, max(35, quality_score + 20))
    verdict.update(
        {
            "level": level,
            "safety_score": _clamp(
                verdict.get("safety_score", fallback_verdict["safety_score"])
            ),
            "confidence": min(
                confidence_cap,
                _clamp(verdict.get("confidence", fallback_verdict["confidence"])),
            ),
            "one_sentence": str(
                verdict.get("one_sentence") or fallback_verdict["one_sentence"]
            ),
            "recommendation": str(
                verdict.get("recommendation") or fallback_verdict["recommendation"]
            ),
        }
    )
    result["verdict"] = verdict

    result["top_findings"] = (
        _list_of_dicts(result.get("top_findings"))[:8] or fallback["top_findings"]
    )
    result["positives"] = _list_of_strings(result.get("positives"))[:6]
    result["seller_questions"] = (
        _list_of_dicts(result.get("seller_questions"))[:8]
        or fallback["seller_questions"]
    )
    result["inspection_checklist"] = (
        _list_of_dicts(result.get("inspection_checklist"))[:6]
        or fallback["inspection_checklist"]
    )
    result["limitations"] = _unique(
        [
            *_list_of_strings(result.get("limitations")),
            *_list_of_strings(photo.get("limitations")),
            *_list_of_strings(research.get("limitations")),
        ]
    )[:12]

    market = research.get("market") if isinstance(research.get("market"), dict) else {}
    price = (
        result.get("price_assessment")
        if isinstance(result.get("price_assessment"), dict)
        else fallback["price_assessment"]
    )
    if market.get("status") != "supported" and price.get("evidence_quality") == "high":
        price["evidence_quality"] = "low"
    if market.get("status") == "unavailable":
        price.update(
            {
                "status": "unknown",
                "evidence_quality": "unavailable",
                "market_min": 0,
                "market_max": 0,
                "recommended_max": 0,
            }
        )
    price["currency"] = price.get("currency") or listing.get("price", {}).get(
        "currency", "EUR"
    )
    result["price_assessment"] = price

    costs = (
        result.get("ownership_costs")
        if isinstance(result.get("ownership_costs"), dict)
        else fallback["ownership_costs"]
    )
    costs["items"] = _list_of_dicts(costs.get("items"))[:10]
    costs["total_min_eur"] = _number(costs.get("total_min_eur"))
    costs["total_max_eur"] = max(
        costs["total_min_eur"], _number(costs.get("total_max_eur"))
    )
    result["ownership_costs"] = costs

    level_labels = {
        "sk": {
            "green": "DOBRÁ KÚPA",
            "yellow": "PRIJATEĽNÉ S PODMIENKAMI",
            "orange": "ZVÁŽIŤ LEN PO KONTROLE",
            "red": "RIZIKOVÁ KÚPA",
            "stop": "NEPOKRAČOVAŤ",
        },
        "cs": {
            "green": "DOBRÁ KOUPĚ",
            "yellow": "PŘIJATELNÉ S PODMÍNKAMI",
            "orange": "ZVÁŽIT JEN PO KONTROLE",
            "red": "RIZIKOVÁ KOUPĚ",
            "stop": "NEPOKRAČOVAT",
        },
    }

    result["schema_version"] = "2.0"
    result["language"] = language
    result["vehicle"] = {
        key: listing.get(key)
        for key in (
            "title",
            "source_url",
            "source_host",
            "price",
            "year",
            "mileage_km",
            "engine",
            "power_kw",
            "fuel",
            "transmission",
            "drivetrain",
            "vin",
            "seller",
            "location",
            "photos_count",
            "service_history_claimed",
        )
    }
    result["data_quality"] = listing.get("data_quality", {})
    result["photo_analysis"] = photo
    result["research"] = {
        "status": research.get("status", "unavailable"),
        "identified_variant": research.get("identified_variant", ""),
        "variant_confidence": research.get("variant_confidence", "low"),
        "market": market,
        "vin_public_mentions": research.get("vin_public_mentions", {}),
        "sources": _list_of_dicts(research.get("sources"))[:12],
    }
    result["verdict_label"] = level_labels[language][level]
    result["meta"] = {
        "job_id": job_id,
        "generated_at": utc_now(),
        "duration_seconds": round(time.monotonic() - started_at, 1),
        "models": {"text": TEXT_MODEL, "vision": VISION_MODEL},
        "fallback_used": bool(result.get("_fallback_used", False)),
    }
    result.pop("_fallback_used", None)
    return result
