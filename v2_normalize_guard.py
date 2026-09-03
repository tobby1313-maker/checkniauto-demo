from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from v2_normalize import (
    _load_raw_listing,
    _pick,
    _raw_parameter,
    _title_from_markdown,
    _year,
    calculate_data_quality,
    normalize_listing as _normalize_listing,
    parse_markdown_pairs,
)

_YEAR_ALIASES = (
    "yearValue",
    "year",
    "rok",
    "rok výroby",
    "v prevádzke od",
    "v provozu od",
    "model year",
)
_YEAR_LABEL_RE = re.compile(
    r"(?:^|\n)\s*(?:[-*]\s*)?(?:rok(?:\s+výroby)?|r\.v\.|year|model(?:ový)?\s+rok|v\s+(?:prevádzke|provozu)\s+od)\s*:?\s*((?:19|20)\d{2})\b",
    re.IGNORECASE,
)


def _trusted_year(listing_dir: Path, text: str, title: str) -> int:
    raw = _load_raw_listing(listing_dir)
    pairs = parse_markdown_pairs(text)
    explicit: Any = _raw_parameter(raw, _YEAR_ALIASES) or _pick(pairs, _YEAR_ALIASES)
    if explicit:
        return _year(str(explicit), "")
    label_match = _YEAR_LABEL_RE.search(text)
    if label_match:
        return int(label_match.group(1))
    return _year("", title)


def normalize_listing(listing_dir: Path, source_url: str = "") -> dict[str, Any]:
    """Normalize a listing while excluding scrape/create timestamps from vehicle year."""
    result = _normalize_listing(listing_dir, source_url=source_url)
    text = (listing_dir / "car_info.md").read_text(encoding="utf-8", errors="replace")
    title = str(result.get("title") or _title_from_markdown(text))
    result["year"] = _trusted_year(listing_dir, text, title)
    result["data_quality"] = calculate_data_quality(result)
    return result
