"""Normalize LLM analysis Markdown before display, download, or storage.

Safe transformations:
  - Google Search grounding redirect URL sanitization
  - Public hyperlink filtering (only comparable-ad links survive in the price section)
  - Basic VIN and mileage canonicalization
  - Table structure repair (split rows across lines)
  - Blank line normalization

NOT performed (too destructive):
  - Line joining (except inside tables)
  - Split word fixing
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse


GROUNDING_REDIRECT_HOST = "vertexaisearch.cloud.google.com"
_BLOCKED_PUBLIC_LINK_HOSTS = {
    GROUNDING_REDIRECT_HOST,
    "example.com",
    "example.org",
    "example.net",
}
_SUPPORTED_COMPARABLE_LINK_HOSTS = {
    "autobazar.eu",
    "autobazar.sk",
    "bazos.sk",
    "bazos.cz",
    "sauto.cz",
    "tipcars.com",
}


@dataclass(frozen=True)
class ListingFacts:
    vin: str = ""
    mileage: str = ""
    vat_explicit: bool = False


# VAT/DPH is relevant to the buyer-facing report only when the advertisement
# explicitly discusses the tax treatment or a net/gross price.  A bare asking
# price is not evidence that the buyer should receive a VAT explanation.
_VAT_MENTION_RE = re.compile(
    r"(?i)\b(?:dph|vat|netto|brutto)\b|"
    r"\b(?:da[nň]\s+z\s+pridanej\s+hodnoty|odpo[cč]et(?:e|u)?\s+dph)\b"
)
_VAT_FOLLOWUP_RE = re.compile(
    r"(?i)\b(?:s[uú]kromn|podnikate[ľl]|odpo[cč]et|kone[cč]n(?:a|e)|"
    r"bez\s+(?:mo[zž]nosti\s+)?uplatnenia|vat[- ]eligible|tax\s+deduct)"
)


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

    vat_explicit = any(_VAT_MENTION_RE.search(line) for line in text.splitlines())
    return ListingFacts(
        vin=vin_match.group(0) if vin_match else "",
        mileage=mileage,
        vat_explicit=vat_explicit,
    )


def normalize_analysis_markdown(text: str | None, car_info_text: str | None = None) -> str:
    """Return cleaned Markdown while preserving the report's intended structure."""
    facts = extract_listing_facts(car_info_text)
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    value = _strip_outer_markdown_fence(value)
    value = _sanitize_grounding_redirects(value)
    value = _remove_public_hyperlinks(value)
    value = _remove_unavailable_url_notes(value)
    value = _remove_unlisted_vat_claims(value, facts)
    value = _remove_generic_photo_limitations(value)
    value = _replace_false_market_database_wording(value)
    value = _normalize_vin_customer_label(value)
    value = _remove_standalone_sources_section(value)
    value = _normalize_tables(value)
    value = _normalize_cost_estimate_tables(value)
    value = _apply_fact_guard(value, facts)
    value = _remove_false_negative_claims(value, facts)
    value = _trim_excess_blank_lines(value)
    return value.rstrip() + ("\n" if value.strip() else "")


def add_verified_comparable_links(
    text: str | None,
    market_comparables: Iterable[Mapping[str, Any]] | None,
) -> str:
    """Link unlinked market-comparable lines using structured evidence.

    Final synthesis occasionally reproduces a comparable correctly but omits
    its Markdown link.  Restore that link without another model call, but only
    when the structured record explicitly marks the URL as verified and the
    price plus descriptive evidence identify exactly one report line.
    """
    comparables = [
        item
        for item in (market_comparables or [])
        if isinstance(item, Mapping)
        and item.get("verified_url") is True
        and _is_allowed_comparable_url(str(item.get("source_url") or item.get("url") or ""))
    ]
    if not comparables:
        return str(text or "")

    output: list[str] = []
    in_price_section = False
    used_urls: set[str] = set()
    for line in str(text or "").split("\n"):
        if re.match(r"^\s*##\s+", line):
            in_price_section = _is_comparable_section_heading(line)
        if in_price_section and "](http" not in line.lower():
            line = _link_matching_comparable_line(line, comparables, used_urls)
        output.append(line)
    return "\n".join(output)


def _link_matching_comparable_line(
    line: str,
    comparables: list[Mapping[str, Any]],
    used_urls: set[str],
) -> str:
    """Return a linked comparable line only for one unambiguous match."""
    match = re.match(r"^(\s*(?:[-*+]\s+)?)(.+?)(\s+[—–-]\s+)(.+)$", str(line or ""))
    if not match:
        return line
    prefix, label, delimiter, remainder = match.groups()
    if not label.strip() or not re.search(r"(?i)\b(?:eur|czk|k[cč]|pln|huf)\b|€", remainder):
        return line

    line_numbers = _normalized_numbers(f"{label} {remainder}")
    label_words = _meaningful_words(label)
    candidates: list[tuple[int, str]] = []
    for item in comparables:
        url = str(item.get("source_url") or item.get("url") or "").strip()
        if not url or url in used_urls:
            continue
        price = _whole_number(item.get("price_eur"))
        if price is None:
            price_numbers = _normalized_numbers(str(item.get("price_display") or ""))
            price = max(price_numbers) if price_numbers else None
        if price is None or price not in line_numbers:
            continue

        description = str(item.get("description") or "")
        description_words = _meaningful_words(description)
        word_overlap = len(label_words & description_words)
        mileage = _whole_number(item.get("mileage_km"))
        mileage_matches = mileage is not None and mileage in line_numbers
        year_matches = bool(
            {number for number in _normalized_numbers(description) if 1900 <= number <= 2100}
            & line_numbers
        )
        # Price alone is too weak: require a mileage/year match or enough of
        # the vehicle description to be present in the line.
        if not (mileage_matches or year_matches or word_overlap >= 3):
            continue
        score = word_overlap + (4 if mileage_matches else 0) + (2 if year_matches else 0)
        candidates.append((score, url))

    if not candidates:
        return line
    candidates.sort(reverse=True)
    if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
        return line
    url = candidates[0][1]
    used_urls.add(url)
    return f"{prefix}[{label.strip()}]({url}){delimiter}{remainder}"


def _whole_number(value: Any) -> int | None:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _normalized_numbers(value: str) -> set[int]:
    """Extract numbers while treating grouped thousands as one value."""
    numbers: set[int] = set()
    pattern = r"(?<!\d)(?:\d{1,3}(?:[ \u00a0]\d{3})+|\d+)(?!\d)"
    for token in re.findall(pattern, str(value or "")):
        compact = re.sub(r"[ \u00a0]", "", token)
        if compact.isdigit():
            numbers.add(int(compact))
    return numbers


def _meaningful_words(value: str) -> set[str]:
    folded = unicodedata.normalize("NFKD", str(value or ""))
    folded = "".join(char for char in folded if not unicodedata.combining(char)).lower()
    return {
        word
        for word in re.findall(r"[a-z0-9]{2,}", folded)
        if word not in {"automat", "automatic", "model", "vozidlo", "ponuka", "km", "eur"}
    }


def _normalize_vin_customer_label(text: str) -> str:
    """Use a neutral customer-facing label for the VIN decoding note."""
    return re.sub(
        r"(?i)\b(?:ľahké|lahke)\s+dekódovanie\b",
        "Dekódovanie",
        str(text or ""),
    )


def _remove_generic_photo_limitations(text: str) -> str:
    """Drop low-value photo disclaimers while preserving specific limitations."""
    generic_fragments = (
        "mierne tmav",
        "slightly dark",
        "vybrane uhly",
        "selected angles",
        "limited angles",
        "kompletne posudenie vozidla",
        "complete assessment of the vehicle",
        "fully assess the vehicle",
    )
    output: list[str] = []
    for line in str(text or "").split("\n"):
        folded = unicodedata.normalize("NFKD", line)
        folded = "".join(char for char in folded if not unicodedata.combining(char)).lower()
        is_limitation_line = bool(
            re.search(r"(?i)(?:\*\*)?(?:obmedzenie|limitation)(?:\*\*)?\s*:", folded)
        )
        if is_limitation_line and any(fragment in folded for fragment in generic_fragments):
            continue
        output.append(line)
    return "\n".join(output)


def _replace_false_market_database_wording(text: str) -> str:
    """Describe the live market pass as web search, never as a database lookup."""
    return re.sub(
        r"(?i)v\s+datab[aá]ze\s+neboli\s+n[aá]jden[eé]",
        "pri webovom vyhľadávaní sa nenašli",
        str(text or ""),
    )


def _remove_unlisted_vat_claims(text: str, facts: ListingFacts) -> str:
    """Remove generic VAT/DPH commentary when the ad says nothing about it.

    The model sometimes turns the absence of a VAT label into a full tax
    explanation (for example, a ``DPH kontext`` paragraph).  That is not a
    useful finding for most buyers and is not supported by the listing.  Keep
    the rule at the public-output boundary as a deterministic backstop for all
    providers, while preserving the section when the advertisement explicitly
    mentions VAT/DPH/net/gross treatment.
    """
    if facts.vat_explicit:
        return text

    cleaned: list[str] = []
    skip_vat_section = False
    drop_followup = False
    for line in str(text or "").split("\n"):
        stripped = line.strip()
        is_heading = bool(re.match(r"^\s*#{1,6}\s+", line))
        heading_text = re.sub(r"^\s*#{1,6}\s+", "", line).strip(" :")
        is_dedicated_vat_heading = bool(
            re.fullmatch(r"(?i)(?:dph|vat)(?:\s+(?:kontext|context|treatment))?", heading_text)
        )

        if skip_vat_section:
            if is_heading:
                skip_vat_section = False
            else:
                continue

        if _VAT_MENTION_RE.search(line):
            # A dedicated heading should remove its whole body, including
            # wrapped lines that do not repeat the DPH/VAT acronym.
            if is_dedicated_vat_heading:
                skip_vat_section = True
            drop_followup = True
            continue

        if drop_followup:
            if not stripped:
                drop_followup = False
                cleaned.append(line)
                continue
            if _VAT_FOLLOWUP_RE.search(line):
                continue
            drop_followup = False

        cleaned.append(line)

    return "\n".join(cleaned)


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


def _is_allowed_comparable_url(url: str) -> bool:
    """Allow customer links only from marketplaces supported by the app."""
    try:
        parsed = urlparse(str(url or "").strip())
    except Exception:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    host = (parsed.hostname or "").lower()
    if not host or any(
        host == blocked or host.endswith(f".{blocked}")
        for blocked in _BLOCKED_PUBLIC_LINK_HOSTS
    ):
        return False
    return any(
        host == supported or host.endswith(f".{supported}")
        for supported in _SUPPORTED_COMPARABLE_LINK_HOSTS
    )


def _is_comparable_section_heading(line: str) -> bool:
    if not re.match(r"^\s*##\s+", line):
        return False
    heading = re.sub(r"^\s*##\s+", "", line).lower()
    return ("cena" in heading and "vyjed" in heading) or (
        "price" in heading and "negoti" in heading
    )


def _remove_public_hyperlinks(text: str) -> str:
    """Keep verified comparable-ad links only inside the price section."""
    output: list[str] = []
    in_price_section = False
    for line in str(text or "").split("\n"):
        if re.match(r"^\s*##\s+", line):
            in_price_section = _is_comparable_section_heading(line)
        output.append(
            _sanitize_public_link_line(
                line,
                allow_comparable_links=in_price_section,
            )
        )
    return "\n".join(output)


def _sanitize_public_link_line(line: str, *, allow_comparable_links: bool) -> str:
    """Strip links from one report line, preserving approved comparable links."""
    value = str(line or "")
    output: list[str] = []
    preserved_links: list[str] = []
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

        label = value[label_start + 1 : label_end]
        url = value[url_start:url_end]
        output.append(value[index:label_start])
        if allow_comparable_links and label.strip() and _is_allowed_comparable_url(url):
            token = f"\x00COMPARABLE_LINK_{len(preserved_links)}\x00"
            output.append(token)
            preserved_links.append(f"[{label}]({url})")
        else:
            # Preserve the surrounding sentence, but remove the linked label
            # outside the dedicated comparable-ad exception just as before.
            if allow_comparable_links:
                output.append(label)
        index = url_end + 1

    value = "".join(output)
    # Bare URLs are never customer-facing; comparable links must be Markdown
    # links with a descriptive label.
    value = re.sub(r"https?://[^\s<>]+", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\(\s*\)", "", value)
    value = re.sub(r"\[\s*\]", "", value)
    value = re.sub(r"\s+([,.;:])", r"\1", value)
    value = re.sub(r"\(\s*([,.;:])", r"\1", value)
    for index, link in enumerate(preserved_links):
        value = value.replace(f"\x00COMPARABLE_LINK_{index}\x00", link)
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


def _normalize_cost_estimate_tables(text: str) -> str:
    """Sort cost tables and keep their shared EUR unit in the header only."""
    lines = str(text or "").split("\n")
    output: list[str] = []
    index = 0
    while index < len(lines):
        if (
            index + 1 < len(lines)
            and _is_table_row(lines[index])
            and _is_table_delimiter(lines[index + 1])
        ):
            header_cells = _split_table_row(lines[index])
            estimate_index = next(
                (
                    position
                    for position, cell in enumerate(header_cells)
                    if _is_estimate_eur_heading(cell)
                ),
                None,
            )
            if estimate_index is not None:
                data_end = index + 2
                rows: list[list[str]] = []
                while data_end < len(lines) and _is_table_row(lines[data_end]):
                    cells = _split_table_row(lines[data_end])
                    if len(cells) == len(header_cells):
                        cells[estimate_index] = _strip_eur_unit(cells[estimate_index])
                        rows.append(cells)
                    else:
                        rows.append(cells)
                    data_end += 1
                if rows:
                    rows.sort(
                        key=lambda cells: _estimate_sort_key(
                            cells[estimate_index] if len(cells) > estimate_index else ""
                        ),
                        reverse=True,
                    )
                    output.extend((lines[index], lines[index + 1]))
                    output.extend(_table_row(cells) for cells in rows)
                    index = data_end
                    continue
        output.append(lines[index])
        index += 1
    return "\n".join(output)


def _is_estimate_eur_heading(value: str) -> bool:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    normalized = "".join(char for char in normalized if not unicodedata.combining(char)).lower()
    return "odhad" in normalized and ("eur" in normalized or "€" in normalized)


def _strip_eur_unit(value: str) -> str:
    cleaned = re.sub(r"(?i)\s*(?:eur|€)(?!\w)", "", str(value or ""))
    return re.sub(r"\s+", " ", cleaned).strip()


def _estimate_sort_key(value: str) -> int:
    """Use the upper bound so the largest possible buyer exposure is first."""
    numbers = _normalized_numbers(value)
    return max(numbers) if numbers else -1


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
        r"(?i)\bn[aá]jazd [^.!\n;]*(?:ch[yý]ba\b|nie je uveden[yý]|neuveden[yý])[^.!\n;]*(?:popise|inzer[aá]t)[^.!\n;]*[.!]?",
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
