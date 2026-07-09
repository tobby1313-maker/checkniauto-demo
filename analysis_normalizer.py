"""Normalize LLM analysis Markdown before display, download, or storage.

Safe transformations:
  - Google Search grounding redirect URL sanitization
  - Basic VIN and mileage canonicalization
  - Table structure repair (split rows across lines)
  - Blank line normalization

NOT performed (too destructive):
  - Line joining (except inside tables)
  - Split word fixing
"""

from __future__ import annotations

import re
from dataclasses import dataclass


GROUNDING_REDIRECT_HOST = "vertexaisearch.cloud.google.com"


@dataclass(frozen=True)
class ListingFacts:
    vin: str = ""
    mileage: str = ""


def extract_listing_facts(car_info_text: str | None) -> ListingFacts:
    """Extract canonical facts that are safe to enforce in generated reports."""
    text = car_info_text or ""
    vin_match = re.search(r"\b[A-HJ-NPR-Z0-9]{17}\b", text.upper())
    mileage = ""

    mileage_patterns = (
        r"(?im)(?:nájazd|najazd|mileage|kilomet(?:re|ers|rov)?)[^\n\d]{0,30}(\d[\d\s]{2,}\s*km)",
        r"(?im)(\d[\d\s]{2,}\s*km)",
    )
    for pattern in mileage_patterns:
        match = re.search(pattern, text)
        if match:
            mileage = _format_km(match.group(1))
            break

    return ListingFacts(vin=vin_match.group(0) if vin_match else "", mileage=mileage)


def normalize_analysis_markdown(text: str | None, car_info_text: str | None = None) -> str:
    """Return cleaned Markdown while preserving the report's intended structure."""
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    value = _strip_outer_markdown_fence(value)
    value = _sanitize_grounding_redirects(value)
    value = _normalize_tables(value)
    value = _apply_fact_guard(value, extract_listing_facts(car_info_text))
    value = _trim_excess_blank_lines(value)
    return value.rstrip() + ("\n" if value.strip() else "")


def _strip_outer_markdown_fence(text: str) -> str:
    """Unwrap a report when the model returns a single fenced markdown block."""
    stripped = text.strip()
    match = re.match(r"^```(?:markdown|md)?\s*\n([\s\S]*?)\n```$", stripped, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip("\n")
    return text


def _sanitize_grounding_redirects(text: str) -> str:
    """Replace Google Search grounding redirect URLs with their display text."""
    def replace_markdown_link(match: re.Match[str]) -> str:
        label = re.sub(r"\s+", " ", match.group(1)).strip()
        return label or "URL citácia nie je overiteľná"

    text = re.sub(
        r"\[([^\]\n]{1,120})\]\(\s*https?://vertexaisearch\.cloud\.google\.com[\s\S]*?\)",
        replace_markdown_link,
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"https?://vertexaisearch\.cloud\.google\.com/\S+",
        "URL citácia nie je overiteľná",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    return text


# ── Table helpers ────────────────────────────────────────────────

def _is_table_row(line: str) -> bool:
    return bool(re.match(r"^\s*\|.*\|\s*$", line or ""))


def _split_table_row(row: str) -> list[str]:
    text = row.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    return [cell.strip() for cell in text.split("|")]


def _is_table_delimiter(row: str) -> bool:
    cells = _split_table_row(row)
    return bool(cells) and all(re.match(r"^:?-{3,}:?$", cell.replace(" ", "")) for cell in cells)


def _is_incomplete_table_row(row: str) -> bool:
    stripped = row.strip()
    return stripped.startswith("|") and stripped.count("|") < 3


def _table_row(cells: list[str]) -> str:
    return "| " + " | ".join(cell.strip() for cell in cells) + " |"


def _normalize_tables(text: str) -> str:
    """Repair broken table rows where content is split across lines.

    AGemini may produce:
      | Nájazd |
      178 000 km | Uvedené v popise. |
    This function joins the partial row back into:
      | Nájazd | 178 000 km | Uvedené v popise. |

    It also handles column count mismatches between subsequent tables
    by resetting expected_cols on delimiter rows.
    """
    lines = text.split("\n")
    out: list[str] = []
    expected_cols = 0

    for line in lines:
        stripped = line.strip()
        if not stripped:
            expected_cols = 0
            out.append(line)
            continue
        if re.match(r"^#{1,6}\s+", stripped):
            expected_cols = 0
            out.append(line)
            continue

        if _is_table_row(stripped):
            cells = _split_table_row(stripped)
            # New table starts on delimiter row
            if _is_table_delimiter(stripped):
                expected_cols = len(cells)
                out.append(_table_row(cells))
                continue
            if not expected_cols:
                expected_cols = len(cells)
            # If this row has fewer cells than expected, it's a split row.
            # Don't join with previous — keep it separate so the next
            # non-| line can be joined to it via the fallback below.
            if len(cells) < expected_cols:
                out.append(stripped)
                continue
            if expected_cols and len(cells) != expected_cols:
                out.append(_table_row(cells[:expected_cols]))
                continue
            out.append(_table_row(cells))
            continue

        # Fallback: a line that doesn't start with | but contains |
        # is a continuation of a split table row.
        if expected_cols and "|" in stripped and out and _is_incomplete_table_row(out[-1]):
            out[-1] = _join_fragments(out[-1], stripped)
            continue

        expected_cols = 0
        out.append(line)

    return "\n".join(out)


def _join_fragments(previous: str, current: str) -> str:
    prev = previous.rstrip()
    cur = current.strip()
    return prev + " " + cur


# ── Fact guard ──────────────────────────────────────────────────

def _apply_fact_guard(text: str, facts: ListingFacts) -> str:
    """Enforce canonical VIN and mileage values from the listing data."""
    if facts.vin:
        for size in range(16, 11, -1):
            prefix = re.escape(facts.vin[:size])
            text = re.sub(rf"\b{prefix}\b(?![A-Z0-9])", facts.vin, text)

    if facts.mileage:
        canonical_digits = re.sub(r"\D", "", facts.mileage)

        def replace_km(match: re.Match[str]) -> str:
            candidate = match.group(0)
            digits = re.sub(r"\D", "", candidate)
            if digits == canonical_digits:
                return _format_km(candidate)
            if _looks_like_split_mileage(digits, canonical_digits):
                return facts.mileage
            return candidate

        text = re.sub(r"\b\d{1,3}(?:\s+\d{3})+\s*km\b", replace_km, text, flags=re.IGNORECASE)

    return text


def _looks_like_split_mileage(candidate_digits: str, canonical_digits: str) -> bool:
    if not candidate_digits or not canonical_digits:
        return False
    if canonical_digits.endswith(candidate_digits):
        return True
    if candidate_digits in canonical_digits and len(candidate_digits) >= 4:
        return True
    if len(candidate_digits) == len(canonical_digits) - 1:
        return canonical_digits[:2] == candidate_digits[:2] and canonical_digits[-3:] == candidate_digits[-3:]
    return False


def _format_km(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if not digits:
        return value.strip()
    groups = []
    while digits:
        groups.append(digits[-3:])
        digits = digits[:-3]
    return " ".join(reversed(groups)) + " km"


def _trim_excess_blank_lines(text: str) -> str:
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()
