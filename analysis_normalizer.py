"""Normalize LLM analysis Markdown before display, download, or storage.

Safe transformations:
  - Google Search grounding redirect URL sanitization
  - Public hyperlink removal (raw research artifacts retain their URLs)
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
    facts = extract_listing_facts(car_info_text)
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    value = _strip_outer_markdown_fence(value)
    value = _sanitize_grounding_redirects(value)
    value = _remove_public_hyperlinks(value)
    value = _remove_unavailable_url_notes(value)
    value = _remove_standalone_sources_section(value)
    value = _normalize_tables(value)
    value = _apply_fact_guard(value, facts)
    value = _remove_false_negative_claims(value, facts)
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
    """Remove Google Search grounding redirects from the public report."""
    def replace_markdown_link(match: re.Match[str]) -> str:
        return ""

    text = re.sub(
        r"\[([^\]\n]{1,120})\]\(\s*https?://vertexaisearch\.cloud\.google\.com[\s\S]*?\)",
        replace_markdown_link,
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"https?://vertexaisearch\.cloud\.google\.com/\S+",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    return text


def _remove_public_hyperlinks(text: str) -> str:
    """Remove Markdown and plain URLs while keeping the surrounding finding."""
    value = str(text or "")
    output: list[str] = []
    index = 0

    while index < len(value):
        label_start = value.find("[", index)
        if label_start < 0:
            output.append(value[index:])
            break
        label_end = value.find("](", label_start + 1)
        if label_end < 0:
            output.append(value[index:])
            break
        url_start = label_end + 2
        if not value.startswith(("http://", "https://"), url_start):
            output.append(value[index : label_end + 2])
            index = label_end + 2
            continue

        depth = 0
        url_end = url_start
        while url_end < len(value):
            char = value[url_end]
            if char.isspace():
                break
            if char == "(":
                depth += 1
            elif char == ")":
                if depth == 0:
                    break
                depth -= 1
            url_end += 1

        if url_end >= len(value) or value[url_end] != ")":
            output.append(value[index:url_start])
            index = url_start
            continue

        output.append(value[index:label_start])
        index = url_end + 1

    value = "".join(output)
    value = re.sub(r"https?://[^\s<>]+", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\(\s*\)", "", value)
    value = re.sub(r"\[\s*\]", "", value)
    value = re.sub(r"\s+([,.;:])", r"\1", value)
    value = re.sub(r"\(\s*([,.;:])", r"\1", value)
    return value


# ── Table helpers ────────────────────────────────────────────────

def _remove_unavailable_url_notes(text: str) -> str:
    """Hide internal/unusable citation labels from the public report."""
    unavailable = r"URL (?:nie je priamo overite[ľl]n[áa]|cit[aá]cia nie je overite[ľl]n[áa])"
    text = re.sub(
        rf"\s*\((?:Google Search|Zdroj z Google Search|Google|Web|Search)?\s*,?\s*{unavailable}\)",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        rf"\s*-\s*(?:Google Search|Zdroj z Google Search|Google|Web|Search)?\s*,?\s*{unavailable}\b\.?",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    return text


def _remove_standalone_sources_section(text: str) -> str:
    """Remove a standalone Sources/Zdroje block while preserving inline citations."""
    heading = r"(?:#{1,6}[ \t]+)?(?:ðŸ”­|ðŸ“š|🔭|📚)?[ \t]*(?:Zdroje|Sources)"
    return re.sub(
        rf"(?ims)^[ \t]*{heading}[ \t]*\n.*?(?=^[ \t]*#{{1,6}}[ \t]+|^[ \t]*<!--\s*END_ANALYSIS|\Z)",
        "",
        text,
    )


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


def _is_placeholder_table_row(row: str) -> bool:
    """Return True for model-generated rows containing only dash/dot placeholders."""
    cells = _split_table_row(row)
    if not cells:
        return False
    return all(
        not cell.strip()
        or bool(re.fullmatch(r"(?:[-–—.]+|…)", cell.replace(" ", "")))
        for cell in cells
    )


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
    table_has_delimiter = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            expected_cols = 0
            table_has_delimiter = False
            out.append(line)
            continue
        if re.match(r"^#{1,6}\s+", stripped):
            expected_cols = 0
            table_has_delimiter = False
            out.append(line)
            continue

        if _is_table_row(stripped):
            cells = _split_table_row(stripped)
            # Keep the first Markdown header delimiter, but discard a repeated
            # delimiter that the model emitted as an empty data row.
            if _is_table_delimiter(stripped):
                if table_has_delimiter:
                    continue
                expected_cols = len(cells)
                table_has_delimiter = True
                out.append(_table_row(cells))
                continue
            if table_has_delimiter and _is_placeholder_table_row(stripped):
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
        table_has_delimiter = False
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


def _remove_false_negative_claims(text: str, facts: ListingFacts) -> str:
    """Remove known model overreaches that conflict with extracted listing facts."""
    text = _remove_public_database_no_result_filler(text)
    if facts.mileage:
        text = _remove_supported_mileage_false_negatives(text)
    if facts.vin and not _has_invalid_or_conflicting_vin_claim(text):
        text = _neutralize_public_vin_history_false_negatives(text)
    return text


def _remove_supported_mileage_false_negatives(text: str) -> str:
    patterns = (
        r"(?i)\bch[yý]baj[úu]ci [^.!\n;]*(?:n[aá]jazd|kilomet)[^.!\n;]*(?:popise|inzer[aá]t)[^.!\n;]*[.!]?",
        r"(?i)\babsencia [^.!\n;]*(?:n[aá]jazd|kilomet)[^.!\n;]*(?:popise|inzer[aá]t)[^.!\n;]*[.!]?",
        r"(?i)\bn[aá]jazd [^.!\n;]*(?:ch[yý]ba|nie je uveden[yý]|neuveden[yý])[^.!\n;]*(?:popise|inzer[aá]t)[^.!\n;]*[.!]?",
    )
    return _clean_lines_with_patterns(text, patterns)


def _remove_public_database_no_result_filler(text: str) -> str:
    patterns = (
        r"(?i)\bverejn[eé] datab[aá]zy neposkytli [^.!\n;]*(?:dodato[čc]n[eé] inform[aá]cie|inform[aá]cie)[^.!\n;]*(?:absencii VIN|bez VIN|VIN ch[yý]ba)[^.!\n;]*[.!]?",
        r"(?i)\bverejn[eé] datab[aá]zy [^.!\n;]*(?:neposkytli|neuv[aá]dzaj[úu]|nemaj[úu])[^.!\n;]*(?:tomuto konkr[eé]tnemu vozidlu|vozidlu|VIN)[^.!\n;]*[.!]?",
        r"(?i)\b(?:Google|web|verejn[eé] vyh[ľl]ad[aá]vanie) [^.!\n;]*(?:nena[šs]iel|nena[šs]lo|neposkytol|neposkytlo)[^.!\n;]*(?:VIN|vozidlu|dodato[čc]n[eé] inform[aá]cie)[^.!\n;]*[.!]?",
    )
    return _clean_lines_with_patterns(text, patterns)


def _neutralize_public_vin_history_false_negatives(text: str) -> str:
    vin_manual_check = (
        "VIN je uvedené; pred kúpou ho overte cez Cebia, CarVertical, "
        "overenie originality alebo podobnú službu histórie vozidla."
    )
    replacements = (
        (
            r"(?i)Jeho form[aá]t je v poriadku, av[šs]ak nebolo mo[zž]n[eé] ho [^.!\n]*"
            r"(?:datab[aá]zach hist[oó]rie vozidla|overi[tť] jeho minulos[tť])[^.!\n]*\."
            r"\s*To zvy[šs]uje riziko skryt[yý]ch v[aá]d a nejasnej minulosti vozidla\.",
            vin_manual_check,
        ),
        (
            r"(?i)nebolo mo[zž]n[eé] ho [^.!\n]*(?:datab[aá]zach hist[oó]rie vozidla|overi[tť] jeho minulos[tť])[^.!\n]*\."
            r"\s*To zvy[šs]uje riziko skryt[yý]ch v[aá]d a nejasnej minulosti vozidla\.",
            "Históriu vozidla overte cez Cebia, CarVertical, overenie originality alebo podobnú službu.",
        ),
        (
            r"(?i)Nevyskytli sa [^.!\n]*verejn[eé] z[aá]znamy alebo hist[oó]ria spojen[aá] s dan[yý]m VIN [čc][íi]slom[^.!\n]*\.",
            "VIN overte cez Cebia, CarVertical, overenie originality alebo podobnú službu.",
        ),
        (
            r"(?i)existuj[úu] rizik[aá] spojen[eé] s neoverite[ľl]n[yý]m VIN(?: [^.!\n]*)?",
            "VIN je uvedené a pred kúpou ho treba overiť cez Cebia, CarVertical alebo podobnú službu",
        ),
    )
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text)

    line_patterns = (
        r"(?i)\bneoverite[ľl]n[aá] hist[oó]ria vozidla cez VIN [čc][íi]slo [^.!\n;]*(?:verejn[yý]ch datab[aá]zach|datab[aá]z)[^.!\n;]*[.!]?",
        r"(?i)\bVIN [^.!\n;]*(?:neoverite[ľl]n[yý]|nie je verejne overite[ľl]n[yý]|nebolo mo[zž]n[eé] overi[tť])[^.!\n;]*(?:hist[oó]ri|datab[aá]z|minulos)[^.!\n;]*[.!]?",
        r"(?i)\bne[úu]pln[aá] hist[oó]ria vozidla kv[oô]li neoverite[ľl]n[eé]mu VIN[^.!\n;]*[.!]?",
    )
    return _clean_lines_with_patterns(text, line_patterns)


def _clean_lines_with_patterns(text: str, patterns: tuple[str, ...]) -> str:
    cleaned_lines: list[str] = []
    for line in text.split("\n"):
        original = line
        updated = line
        for pattern in patterns:
            updated = re.sub(pattern, "", updated)
        updated = _clean_false_negative_line(updated)
        if _line_became_empty_bullet(original, updated):
            continue
        cleaned_lines.append(updated)
    return "\n".join(cleaned_lines)


def _clean_false_negative_line(line: str) -> str:
    # Sentence-level punctuation cleanup must never rewrite Markdown table
    # delimiters such as ``| --- | ---: |`` into invalid data rows.
    if _is_table_row(line):
        return line.rstrip()
    line = re.sub(r"\s+([,.;:])", r"\1", line)
    line = re.sub(r"([,(;-])\s*([).!?:;])", r"\2", line)
    line = re.sub(r"\s{2,}", " ", line)
    line = re.sub(r"\s+-\s*[,.!?;:]\s*", " - ", line)
    line = re.sub(r"\s*,\s*(?:ako aj|a)\b\s*,?\s*", " ", line, flags=re.IGNORECASE)
    line = re.sub(r"\s*,\s*(?:ako aj|a)\b\s*$", ".", line, flags=re.IGNORECASE)
    line = re.sub(r"\s+ale\s*[,.!?;:]\s*", " ", line, flags=re.IGNORECASE)
    line = re.sub(r"\(\s*\)", "", line)
    line = re.sub(r"\s+([.!?])$", r"\1", line)
    if re.fullmatch(r"\s*(?:[-*]\s*)?(?:\*\*[^*]+:\*\*)?\s*", line):
        return ""
    if re.fullmatch(r"\s*vzh[ľl]adom na\s*", line, flags=re.IGNORECASE):
        return ""
    return line.rstrip()


def _line_became_empty_bullet(original: str, updated: str) -> bool:
    if original.lstrip().startswith(("-", "*")):
        return not re.sub(r"^[-*]\s*", "", updated.strip()).strip()
    return False


def _has_invalid_or_conflicting_vin_claim(text: str) -> bool:
    vin_lines = [line.lower() for line in text.split("\n") if "vin" in line.lower()]
    risk_words = (
        "neplat",
        "invalid",
        "problem",
        "konflikt",
        "conflict",
        "rozpor",
        "nesedi",
        "nesúlad",
        "nesulad",
        "odmiet",
        "refus",
        "krad",
        "stolen",
        "hav[aá]ri",
        "accident",
    )
    return any(any(re.search(word, line) for word in risk_words) for line in vin_lines)


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
