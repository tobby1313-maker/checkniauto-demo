"""URL normalization helpers for supported listing sources."""

from __future__ import annotations

import re
from urllib.parse import quote, unquote, urlsplit, urlunsplit


_BAZOS_LISTING_PATH = re.compile(
    r"^(/inzerat/\d+/)([^/?#]+?)(\.php)?/?$",
    re.IGNORECASE,
)


def _is_bazos_host(hostname: str) -> bool:
    host = str(hostname or "").strip().lower().rstrip(".")
    return host in {"bazos.sk", "bazos.cz"} or host.endswith((".bazos.sk", ".bazos.cz"))


def normalize_bazos_listing_url(value: object) -> str:
    """Return a safe canonical Bazos listing URL when the slug is malformed.

    Browsers and copied links sometimes contain an encoded or literal space in
    the descriptive slug, for example ``mjet%20140``. Bazos expects that word
    boundary to be a hyphen and returns 404 for the malformed variant. The
    numeric listing ID is authoritative, so only the descriptive slug is
    normalized; the host, listing ID, query string and fragment are preserved.

    Unknown hosts and non-listing Bazos paths are returned unchanged.
    """

    raw = str(value or "").strip()
    if not raw:
        return raw

    try:
        parsed = urlsplit(raw)
    except ValueError:
        return raw

    if not _is_bazos_host(parsed.hostname or ""):
        return raw

    decoded_path = unquote(parsed.path or "")
    match = _BAZOS_LISTING_PATH.match(decoded_path)
    if not match:
        return raw

    prefix, slug, suffix = match.groups()
    normalized_slug = re.sub(r"\s+", "-", slug.strip())
    normalized_slug = re.sub(r"-{2,}", "-", normalized_slug).strip("-")
    if not normalized_slug:
        return raw

    normalized_path = f"{prefix}{quote(normalized_slug, safe='-._~')}{suffix or ''}"
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            normalized_path,
            parsed.query,
            parsed.fragment,
        )
    )
