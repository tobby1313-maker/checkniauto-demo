"""Deterministic beta preparation. This module never calls an AI provider."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests

ROOT = Path(__file__).resolve().parents[1]
MAX_PHOTOS = 60
MAX_BUNDLE_BYTES = 40_000_000
MAX_RESPONSE_BYTES = 8_000_000
HOSTS = {"auto.bazos.sk", "auto.bazos.cz", "www.autobazar.eu", "autobazar.eu",
         "www.autobazar.sk", "autobazar.sk"}


class PreparationError(ValueError):
    pass


def normalize_url(value: str) -> str:
    from scrapper_demo.url_normalizer import normalize_bazos_listing_url
    url = normalize_bazos_listing_url(str(value or "").strip())
    try:
        parts = urlsplit(url)
        if (parts.scheme not in {"http", "https"} or parts.hostname not in HOSTS
                or parts.username or parts.password or parts.port not in {None, 80, 443}
                or "\\" in url or len(url) > 2000):
            raise ValueError()
    except ValueError:
        raise PreparationError("Vlož priamy odkaz na auto z Bazoš.sk/.cz alebo Autobazar.sk/.eu.") from None
    patterns = (r"^/inzerat/[0-9]+/[^/]+\.php$",) if "bazos" in parts.hostname else (
        r"^/detail/[^/]+/[^/]+/?$", r"^/[0-9]+/[^/]+/?$")
    if not any(re.fullmatch(pattern, parts.path) for pattern in patterns):
        raise PreparationError("Potrebujeme odkaz na konkrétny inzerát, nie na vyhľadávanie.")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def safe_get(url, *, headers=None, timeout=12, **kwargs):
    """Bounded public-Internet GET. Validate every redirect, not just the first URL."""
    session = requests.Session()
    session.trust_env = False
    try:
        for _ in range(5):
            parts = urlsplit(url)
            if (parts.scheme not in {"http", "https"} or not parts.hostname
                    or parts.username or parts.password or parts.port not in {None, 80, 443}):
                raise PreparationError("Neplatná adresa zdroja.")
            addresses = socket.getaddrinfo(parts.hostname, parts.port or 443, type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
                raise PreparationError("Zdroj musí byť verejná internetová adresa.")
            response = session.get(url, headers=headers, timeout=(5, min(float(timeout), 15)),
                                   allow_redirects=False, stream=True)
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location")
                response.close()
                if not location:
                    raise PreparationError("Neplatné presmerovanie inzerátu.")
                url = urljoin(url, location)
                continue
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    response.close()
                    raise PreparationError("Súbor presahuje limit bezplatnej bety.")
                chunks.append(chunk)
            response._content = b"".join(chunks)
            response._content_consumed = True
            response.close()
            return response
        raise PreparationError("Zdroj sa presmeroval príliš veľakrát.")
    finally:
        session.close()


def run_child(args: list[str], timeout: int, env: dict | None = None):
    """Kill the entire process group on deadline, including nested scraper children."""
    proc = subprocess.Popen([sys.executable, "-m", "scrapper_demo.beta_prepare", *args],
                            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, start_new_session=True)
    try:
        output, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        raise PreparationError("Príprava prekročila časový limit. Skús inzerát neskôr alebo vlož údaje ručne.") from None
    if proc.returncode:
        if "404" in output or "410" in output:
            raise PreparationError("Inzerát nie je dostupný. Mohol byť odstránený; skontroluj odkaz.")
        raise PreparationError("Inzerát sa nepodarilo načítať. Vlož údaje a fotografie ručne.")


def scrape(url: str, work: Path) -> Path:
    url = normalize_url(url)
    env = os.environ.copy()
    env["SCRAPPER_AUTA_DIR"] = str(work)
    # One extra image detects oversized galleries instead of silently dropping photos.
    env["DEMO_MAX_SCRAPED_IMAGES"] = str(MAX_PHOTOS + 1)
    env["DEMO_IMAGE_REQUEST_TIMEOUT"] = "10"
    run_child(["scrape", url], 160, env)
    listings = list(work.glob("*/raw_data.json"))
    if len(listings) != 1:
        raise PreparationError("Scraper nevytvoril jednoznačné údaje inzerátu.")
    return listings[0].parent


def prepare_assets(folder: Path, *, market: bool = True) -> dict:
    """Preserve every photo, build labelled sheets, and mark ALL photos uninspected."""
    from PIL import Image, ImageOps, UnidentifiedImageError
    from scrapper_demo.services.image_catalog import scan_originals, cluster_similar_items
    from scrapper_demo.services.image_collages import create_llm_collage

    raw = json.loads((folder / "raw_data.json").read_text(encoding="utf-8"))
    title = str(raw.get("title") or "").strip()
    description = str(raw.get("description") or "").strip()
    if not title or not description:
        raise PreparationError("Inzerát nemá dostatok údajov na prípravu analýzy.")
    image_dir = folder / "images"
    image_dir.mkdir(exist_ok=True)
    originals = sorted(p for p in image_dir.iterdir() if p.is_file())
    reported_count = raw.get("photos_count") or 0
    if len(originals) > MAX_PHOTOS or (isinstance(reported_count, int) and reported_count > MAX_PHOTOS):
        raise PreparationError("Bezplatná beta podporuje najviac 60 fotografií. Galériu nebudeme potichu orezávať.")
    staged = folder / "beta_assets"
    staged.mkdir()
    (staged / "images").mkdir()
    photos = []
    # Scrapers prefix filenames with the source gallery position. Preserve gaps.
    indexed = []
    used = set()
    for fallback, source in enumerate(originals, 1):
        match = re.match(r"^(\d+)(?:_|\.)", source.name)
        number = int(match.group(1)) if match else fallback
        if number in used or not 1 <= number <= MAX_PHOTOS:
            raise PreparationError("Nejednoznačné číslovanie galérie; príprava bola zastavená.")
        used.add(number)
        indexed.append((number, source))
    for number, source in indexed:
        photo = {"id": f"p{number:03d}", "number": number, "inspection": "not_inspected",
                 "status": "available", "filename": f"images/p{number:03d}.jpg"}
        try:
            with Image.open(source) as image:
                if image.width * image.height > 40_000_000:
                    raise ValueError("image pixel limit")
                photo["source_dimensions"] = [image.width, image.height]
                image = ImageOps.exif_transpose(image).convert("RGB")
                image.thumbnail((1600, 1600))
                image.save(staged / photo["filename"], "JPEG", quality=85, optimize=True)
                photo["stored_dimensions"] = list(image.size)
                photo["stored_variant"] = "jpeg_max_1600px"
        except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
            photo["status"] = "unreadable"
            photo["filename"] = None
        photos.append(photo)
    maximum = max([n for n, _ in indexed] + [reported_count if isinstance(reported_count,int) else 0])
    for number in range(1, maximum+1):
        if number not in used:
            photos.append({"id":f"p{number:03d}","number":number,"status":"download_failed",
                           "filename":None,"inspection":"not_inspected"})
    photos.sort(key=lambda p:p["number"])
    # Explicit IDs stay stable even when an earlier photo is unreadable.
    names = [Path(p["filename"]).name for p in photos if p["filename"]]
    items, _ = scan_originals(str(staged / "images"), names)
    for item in items:
        item["gallery_number"] = int(re.search(r"p([0-9]+)", item["original_name"]).group(1))
    clusters = cluster_similar_items(items)
    selected = [f"p{group['representative']['gallery_number']:03d}" for group in clusters]
    selection = {"prepared_for_detail": selected, "inspection_performed": False,
                 "similarity_cluster_count": len(clusters)}
    sheets = []
    (staged / "sheets").mkdir()
    for start in range(0, len(items), 4):
        number = len(sheets) + 1
        filename = f"sheets/overview_{number:03d}.jpg"
        group = items[start:start+4]
        create_llm_collage(group, str(staged / filename))
        sheets.append({"id": f"s{number:03d}", "filename": filename, "type": "overview",
                       "photo_ids": [f"p{item['gallery_number']:03d}" for item in group]})
    facts = {"title": title, "description_excerpt": description[:10000],
             "source_url": raw.get("url", ""), "parameters": raw.get("parameters", {}),
             "price": raw.get("price"), "currency": raw.get("currency"),
             "provenance": "seller_claims_not_independently_verified"}
    market_data = {"status": "unavailable", "candidates": [], "verified_comparison": False}
    (folder / "beta_facts.json").write_text(json.dumps(facts, ensure_ascii=False), encoding="utf-8")
    if market:
        try:
            run_child(["market", str(folder / "beta_facts.json"), str(folder / "beta_market.json")], 25)
            market_data = json.loads((folder / "beta_market.json").read_text(encoding="utf-8"))
        except (PreparationError, OSError, ValueError):
            market_data["reason"] = "Direct marketplace search unavailable; no AI fallback used."
    payloads = {"raw_data.json": raw, "listing_facts.json": facts, "market_data.json": market_data,
                "photo_selection.json": selection}
    for name, value in payloads.items():
        (staged / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    manifest = {"schema_version": 1, "title": title, "listing": facts, "photos": photos,
                "sheets": sheets, "market": market_data, "prepared_at": int(time.time()),
                "ai_calls": 0, "chargeable": False,
                "warnings": ["Fotografie pripravené v kolážach ešte neboli posúdené AI.",
                             "Podklady z inzerátu sú tvrdenia predajcu."]}
    if any(p["status"] != "available" for p in photos):
        manifest["warnings"].append("Niektoré fotografie sa nepodarilo spracovať; pri každej je uvedený stav.")
    files = [p for p in staged.rglob("*") if p.is_file() and ".analysis_images" not in p.parts]
    if sum(p.stat().st_size for p in files) > MAX_BUNDLE_BYTES:
        raise PreparationError("Podklady presahujú 40 MB limit bezplatnej bety.")
    manifest["files"] = [{"name": str(p.relative_to(staged)), "bytes": p.stat().st_size,
                          "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in files]
    return {"manifest": manifest, "directory": staged}


def _main():
    # This patch is isolated in a short-lived process, never in the web server.
    requests.get = safe_get
    if sys.argv[1] == "scrape":
        url = normalize_url(sys.argv[2])
        from main import get_scraper_path, detect_site
        import runpy
        script = get_scraper_path(detect_site(url))
        sys.argv = [script, url]
        runpy.run_path(script, run_name="__main__")
    elif sys.argv[1] == "market":
        from scrapper_demo.direct_market_search import search_bazos_sk_cz
        facts = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        passes = search_bazos_sk_cz(facts, timeout=6)
        candidates = [item for result in passes for item in result.get("candidates", [])][:12]
        for item in candidates:
            item["verified_url"] = False
            item["verification_status"] = "SEARCH_CARD_ONLY"
            item["url_verification_status"] = "NOT_VERIFIED"
            item["display_in_report"] = False
        data = {"status": "search_cards_only" if candidates else "unavailable",
                "verified_comparison": False, "candidates": candidates,
                "searched_at": int(time.time()),
                "notice": "Search-card offers, not checked vehicle detail pages or a verified price benchmark."}
        Path(sys.argv[3]).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    else:
        raise SystemExit("Unknown preparation command")


if __name__ == "__main__":
    _main()
