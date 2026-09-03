#!/usr/bin/env python3
"""Fast, domain-aware Bazoš scraper used by the Checkni Auto V2 pipeline."""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable

import requests
from bs4 import BeautifulSoup

try:
    import truststore

    truststore.inject_into_ssl()
except Exception:
    pass

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "sk-SK,sk;q=0.9,cs;q=0.8,en;q=0.6",
}
REQUEST_TIMEOUT = max(5, int(os.environ.get("DEMO_IMAGE_REQUEST_TIMEOUT", "12")))
MAX_IMAGE_BYTES = max(2, int(os.environ.get("CHECKNI_MAX_IMAGE_MB", "12"))) * 1024 * 1024
ALLOWED_HOSTS = ("bazos.sk", "bazos.cz")


def normalized_host(url: str) -> str:
    host = (urllib.parse.urlparse(url).hostname or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def validate_listing_url(url: str) -> urllib.parse.ParseResult:
    parsed = urllib.parse.urlparse(url)
    host = normalized_host(url)
    if parsed.scheme not in {"http", "https"} or not any(
        host == allowed or host.endswith(f".{allowed}") for allowed in ALLOWED_HOSTS
    ):
        raise ValueError("URL must be a Bazoš SK or Bazoš CZ listing.")
    return parsed


def origin_for(url: str) -> str:
    parsed = validate_listing_url(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def derive_slug(url: str) -> str:
    parsed = validate_listing_url(url)
    tail = Path(parsed.path.rstrip("/")).name.replace(".php", "") or "bazos-listing"
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", tail).strip("-").lower()
    return slug[:100] or "bazos-listing"


def fetch_page(url: str) -> tuple[str, BeautifulSoup]:
    response = requests.get(url, headers=HEADERS, timeout=20)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return response.text, BeautifulSoup(response.text, "html.parser")


def iter_json_nodes(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from iter_json_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_json_nodes(child)


def json_ld_records(soup: BeautifulSoup) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text() or ""
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            continue
        records.extend(iter_json_nodes(value))
    return records


def first_text(soup: BeautifulSoup, selectors: Iterable[str]) -> str:
    for selector in selectors:
        node = soup.select_one(selector)
        if node:
            value = node.get("content") if node.name == "meta" else node.get_text(" ", strip=True)
            if value:
                return str(value).strip()
    return ""


def parse_price(soup: BeautifulSoup, page_text: str, currency: str) -> int:
    for record in json_ld_records(soup):
        offers = record.get("offers")
        if isinstance(offers, dict):
            value = offers.get("price") or offers.get("lowPrice")
            digits = re.sub(r"\D", "", str(value or ""))
            if digits:
                return int(digits)

    candidates = [
        first_text(
            soup,
            [
                '[itemprop="price"]',
                'meta[property="product:price:amount"]',
                ".cenainzeratu",
                ".cena",
                ".price",
            ],
        ),
        page_text,
    ]
    symbol_pattern = r"(?:Kč|CZK)" if currency == "CZK" else r"(?:€|EUR)"
    patterns = [
        rf"(?:Cena|Price)\s*:?\s*([\d\s.]+)\s*{symbol_pattern}",
        rf"\b([\d\s.]+)\s*{symbol_pattern}\b",
    ]
    for candidate in candidates:
        for pattern in patterns:
            match = re.search(pattern, candidate or "", re.IGNORECASE)
            if not match:
                continue
            digits = re.sub(r"\D", "", match.group(1))
            if digits:
                value = int(digits)
                if 100 <= value <= 100_000_000:
                    return value
    return 0


def label_value(soup: BeautifulSoup, labels: Iterable[str]) -> str:
    wanted = tuple(label.lower() for label in labels)
    for node in soup.find_all(["b", "strong", "dt", "span"]):
        text = node.get_text(" ", strip=True).lower()
        if not any(text.startswith(label) for label in wanted):
            continue
        sibling = node.find_next_sibling()
        if sibling:
            value = sibling.get_text(" ", strip=True)
            if value:
                return value
        parent_text = node.parent.get_text(" ", strip=True) if node.parent else ""
        for label in labels:
            cleaned = re.sub(rf"^{re.escape(label)}\s*:?\s*", "", parent_text, flags=re.I)
            if cleaned and cleaned != parent_text:
                return cleaned.strip()
    return ""


def extract_parameters(text: str) -> dict[str, str]:
    parameters: dict[str, str] = {}
    patterns = {
        "Mileage": [
            r"(?:najazden[ée]|najeto|stav tachometra)\s*:?\s*([\d\s.]+)\s*km",
            r"\b([1-9]\d{3,6})\s*km\b",
        ],
        "Year": [
            r"(?:rok|ročník|r\.v\.|modelový rok|v provozu od)\s*:?\s*((?:19|20)\d{2})"
        ],
        "Engine Power": [r"\b(\d{2,3})\s*kW\b"],
        "Engine Capacity": [
            r"\b(\d[.,]\d)\s*(?:l|lit(?:er|re|ru|rů))\b",
            r"\b(\d{3,4})\s*cm3\b",
        ],
    }
    for label, options in patterns.items():
        for pattern in options:
            match = re.search(pattern, text, re.I)
            if match:
                suffix = " km" if label == "Mileage" else " kW" if label == "Engine Power" else ""
                parameters[label] = f"{match.group(1).strip()}{suffix}"
                break

    lower = text.lower()
    if re.search(r"\b(hybrid|phev|mhev)\b", lower):
        parameters["Fuel"] = "Hybrid"
    elif re.search(r"\b(elektr|\bev\b)", lower):
        parameters["Fuel"] = "Electric"
    elif re.search(r"\b(diesel|nafta|tdi|dci|hdi|crdi)\b", lower):
        parameters["Fuel"] = "Diesel"
    elif re.search(r"\b(benz[ií]n|tsi|tfsi|tce|mpi)\b", lower):
        parameters["Fuel"] = "Petrol"

    if re.search(r"\b(dsg|s[ -]?tronic|tiptronic|e-?cvt|automat)\b", lower):
        parameters["Transmission"] = "Automatic"
    elif re.search(r"\b(manu[aá]l)\b", lower):
        parameters["Transmission"] = "Manual"

    if re.search(r"\b(4x4|awd|4wd|quattro|xdrive|4matic)\b", lower):
        parameters["Drivetrain"] = "4x4 / AWD"
    elif re.search(r"\b(predn[ýi]\s+pohon|přední\s+pohon|predokolka|fwd)\b", lower):
        parameters["Drivetrain"] = "Front"
    elif re.search(r"\b(zadn[ýi]\s+pohon|zadní\s+pohon|rwd)\b", lower):
        parameters["Drivetrain"] = "Rear"
    return parameters


def extract_vin(text: str) -> str:
    match = re.search(r"\b[A-HJ-NPR-Z0-9]{17}\b", text, re.I)
    return match.group(0).upper() if match else "N/A"


def extract_listing(soup: BeautifulSoup, url: str) -> dict[str, Any]:
    host = normalized_host(url)
    currency = "CZK" if host.endswith("bazos.cz") else "EUR"
    title = first_text(soup, ["h1.nadpisihlavni", "h1", 'meta[property="og:title"]'])
    title = re.sub(r"\s*[|–-]\s*Bazo[sš]\.(?:sk|cz).*$", "", title, flags=re.I).strip()
    description = first_text(
        soup,
        [
            ".popisdetail",
            ".popis",
            '[itemprop="description"]',
            'meta[property="og:description"]',
        ],
    )
    combined = f"{title}\n{description}"
    page_text = soup.get_text(" ", strip=True)
    seller = label_value(soup, ["Meno", "Jméno", "Predajca", "Prodejce"])
    location = label_value(soup, ["Lokalita", "Mesto", "Město"])
    return {
        "url": url,
        "title": title or "Bazoš inzerát",
        "price": parse_price(soup, page_text, currency),
        "currency": currency,
        "vin": extract_vin(combined),
        "description": description,
        "parameters": extract_parameters(combined),
        "seller": {"Name": seller} if seller else {},
        "location": location,
        "photos_count": 0,
        "source": "bazos.cz" if currency == "CZK" else "bazos.sk",
    }


def image_candidate(node: Any) -> str:
    for attribute in (
        "data-flickity-lazyload",
        "data-lazy-src",
        "data-src",
        "data-original",
        "src",
        "data-srcset",
        "srcset",
    ):
        value = node.get(attribute)
        if not value:
            continue
        candidates = [
            item.strip().split(" ")[0]
            for item in str(value).split(",")
            if item.strip()
        ]
        if candidates:
            return candidates[-1]
    return ""


def collect_image_urls(soup: BeautifulSoup, listing_url: str) -> list[str]:
    origin = origin_for(listing_url)
    preferred_containers = soup.select(".carousel, .fotky, .gallery, [data-flickity]")
    nodes = []
    for container in preferred_containers:
        nodes.extend(container.find_all("img"))
    if not nodes:
        nodes = soup.find_all("img")

    images: list[str] = []
    seen: set[str] = set()
    for node in nodes:
        if any(
            "podobn" in " ".join(parent.get("class") or []).lower()
            for parent in node.find_parents(limit=5)
        ):
            continue
        source = image_candidate(node)
        if not source:
            continue
        if source.startswith("//"):
            source = f"https:{source}"
        elif source.startswith("/"):
            source = urllib.parse.urljoin(origin, source)
        source = re.sub(r"/img/(\d+)t/", r"/img/\1/", source)
        lowered = source.lower()
        if not lowered.startswith(("http://", "https://")):
            continue
        if any(token in lowered for token in ("/obrazky/", "logo", "sprite", ".svg")):
            continue
        if "/img/" not in lowered and "bazos" not in normalized_host(source):
            continue
        if source not in seen:
            seen.add(source)
            images.append(source)
    return images


def extension_from_content_type(value: str) -> str:
    lowered = value.lower()
    if "webp" in lowered:
        return ".webp"
    if "png" in lowered:
        return ".png"
    return ".jpg"


def download_one(index: int, url: str, output_dir: Path) -> tuple[int, str | None]:
    temporary = output_dir / f".{index:02d}.part"
    try:
        with requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, stream=True) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if not content_type.lower().startswith("image/"):
                return index, None
            total = 0
            with temporary.open("wb") as handle:
                for chunk in response.iter_content(64 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_IMAGE_BYTES:
                        raise ValueError("image too large")
                    handle.write(chunk)
        destination = output_dir / f"{index:02d}{extension_from_content_type(content_type)}"
        temporary.replace(destination)
        return index, destination.name
    except Exception:
        temporary.unlink(missing_ok=True)
        return index, None


def download_images(urls: list[str], output_dir: Path) -> int:
    max_images = int(os.environ.get("DEMO_MAX_SCRAPED_IMAGES", "0") or "0")
    selected = urls[:max_images] if max_images > 0 else urls
    output_dir.mkdir(parents=True, exist_ok=True)
    completed: list[tuple[int, str | None]] = []
    with ThreadPoolExecutor(max_workers=min(6, max(1, len(selected)))) as pool:
        futures = [
            pool.submit(download_one, index, url, output_dir)
            for index, url in enumerate(selected, 1)
        ]
        for future in as_completed(futures):
            completed.append(future.result())
    for index, filename in sorted(completed):
        print(f"  [{index}/{len(selected)}] {filename or 'FAILED'}", flush=True)
    return sum(bool(filename) for _index, filename in completed)


def format_markdown(info: dict[str, Any], downloaded: int) -> str:
    lines = [
        f"# {info['title']}",
        "",
        f"**Source:** {info['url']}",
        f"**Scraped:** {time.strftime('%Y-%m-%d %H:%M')}",
        "",
    ]
    if info.get("price"):
        lines.extend(
            [
                "## Price",
                f"- **Price:** {info['price']:,} {info['currency']}".replace(",", " "),
                "",
            ]
        )
    if info.get("parameters"):
        lines.extend(["## Specifications", "", "| Parameter | Value |", "|---|---|"])
        lines.extend(f"| {key} | {value} |" for key, value in info["parameters"].items())
        lines.append("")
    if info.get("vin") and info["vin"] != "N/A":
        lines.extend([f"**VIN:** {info['vin']}", ""])
    if info.get("description"):
        lines.extend(["## Seller Note (Poznamka)", "", info["description"], ""])
    if info.get("seller"):
        lines.extend(["## Seller", ""])
        lines.extend(f"- **{key}:** {value}" for key, value in info["seller"].items())
        lines.append("")
    if info.get("location"):
        lines.extend(["## Location", "", f"- **Location:** {info['location']}", ""])
    lines.extend(["## Photos", f"- **Downloaded:** {downloaded}", ""])
    return "\n".join(lines)


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python Bazos_v2.py <bazos listing URL>")
        return 2
    url = sys.argv[1].strip()
    try:
        validate_listing_url(url)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 2

    root = Path(
        os.environ.get("SCRAPPER_AUTA_DIR")
        or Path(__file__).resolve().parent / "Auta"
    )
    output_dir = root / derive_slug(url)
    images_dir = output_dir / "images"
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Scraping: {url}", flush=True)
    print("[1/4] Fetching page...", flush=True)
    _page_text, soup = fetch_page(url)
    print("[2/4] Extracting listing data...", flush=True)
    info = extract_listing(soup, url)
    print(f"  Title: {info['title']}", flush=True)
    print("[3/4] Collecting and downloading images...", flush=True)
    image_urls = collect_image_urls(soup, url)
    info["photos_count"] = len(image_urls)
    downloaded = download_images(image_urls, images_dir) if image_urls else 0
    print("[4/4] Saving normalized scraper output...", flush=True)
    (output_dir / "car_info.md").write_text(
        format_markdown(info, downloaded), encoding="utf-8"
    )
    (output_dir / "raw_data.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Done: {output_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
