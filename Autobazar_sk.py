"""
Scraper for autobazar.sk car listings.
Extracts car info (specs, equipment, description, seller) and downloads all gallery images
at the highest available resolution (2560x1440).

Usage:
    python scrape_car2.py <listing_url>

Example:
    python scrape_car2.py "https://www.autobazar.sk/28272841/toyota-corolla-combi-ts-1-8-hybrid-e-cvt-povodny-lak-sr/"
"""

import truststore
truststore.inject_into_ssl()

import os
import sys
import re
import json
import time
import base64
import urllib.parse
import requests
from bs4 import BeautifulSoup

IMAGE_REQUEST_TIMEOUT = int(os.environ.get("DEMO_IMAGE_REQUEST_TIMEOUT", "12"))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "sk,en;q=0.5",
}


def get_page(url):
    """Fetch page HTML and return (text, BeautifulSoup)."""
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.text, BeautifulSoup(resp.text, "html.parser")


def extract_car_info(soup, url):
    """Extract car information from the detail page HTML."""
    info = {}
    info["url"] = url

    # Title
    h1 = soup.find("h1")
    info["title"] = h1.get_text(strip=True) if h1 else ""

    # Price from JSON-LD Product
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(script.string)
            if d.get("@type") == "Product":
                offers = d.get("offers", {})
                info["price"] = int(offers.get("price", 0))
                info["currency"] = offers.get("priceCurrency", "EUR")
                info["condition"] = offers.get("itemCondition", "")
                seller = offers.get("seller", {})
                info["seller_name"] = seller.get("name", "")
                break
        except (json.JSONDecodeError, ValueError):
            continue

    # Parameters from the parameters table
    info["parameters"] = {}
    param_rows = soup.find_all("div", class_="ab-grid parameters__row")
    for row in param_rows:
        label = row.find(class_="parameters__label")
        value = row.find(class_="parameters__value")
        if label and value:
            key = label.get_text(strip=True).rstrip(":")
            val = value.get_text(strip=True)
            # Clean up values (remove "Overiť" button text appended to mileage)
            val = re.sub(r'Overiť$', '', val).strip()
            info["parameters"][key] = val

    # VIN (from parameters)
    info["vin"] = info["parameters"].get("VIN", "")

    # VIN validation
    if info.get("vin"):
        try:
            from vin_utils import validate_vin
            info["vin_validation"] = validate_vin(info["vin"])
        except ImportError:
            pass

    # Equipment sections
    info["equipment"] = {}
    # Find equipment sections by headings
    for heading in soup.find_all(["h2", "h3", "h4"]):
        heading_text = heading.get_text(strip=True)
        if any(k in heading_text.lower() for k in ["bezpečnosť", "výbava", "komfort", "exteriér", "interiér", "multimédia"]):
            # Get the next sibling ul/div with list items (not the whole parent)
            next_el = heading.find_next_sibling()
            if next_el:
                items = [li.get_text(strip=True) for li in next_el.find_all("li")]
                if items:
                    info["equipment"][heading_text] = items

    # Poznámka (seller's note about the car)
    info["poznamka"] = ""
    note_heading = soup.find(string=re.compile(r"Pozn[áa]mka", re.I))
    if note_heading:
        # The note is in a <p class="tab__text"> following the heading
        parent = note_heading.find_parent()
        if parent:
            note_p = parent.find_next_sibling("p", class_="tab__text")
            if note_p:
                # Get text preserving line breaks
                info["poznamka"] = note_p.get_text(separator="\n").strip()
            else:
                # Fallback: get all text from next sibling
                next_el = parent.find_next_sibling()
                if next_el:
                    info["poznamka"] = next_el.get_text(separator="\n").strip()

    # Description / seller note (fallback if poznamka not found)
    info["description"] = ""
    if not info["poznamka"]:
        desc_section = soup.find("div", class_=re.compile(r"description|note|text-body", re.I))
        if desc_section:
            info["description"] = desc_section.get_text(strip=True)

    # Seller info
    info["seller"] = {}
    dts = soup.find_all("dt")
    for dt in dts:
        key = dt.get_text(strip=True).rstrip(":")
        dd = dt.find_next_sibling("dd")
        if dd and key:
            info["seller"][key] = dd.get_text(strip=True)

    # Photo count (from "XX fotiek v galérii" or similar text)
    page_text = soup.get_text()
    photo_match = re.search(r"(\d+)\s*fotiek", page_text)
    info["photos_count"] = int(photo_match.group(1)) if photo_match else 0

    return info


def get_gallery_images(listing_url, detail_soup):
    """
    Get all gallery images at highest resolution from the gallery page.
    The gallery page has window.photos JSON with 2560x1440 signed URLs.
    """
    # Construct gallery URL from the detail page link
    # Detail: https://www.autobazar.sk/28272841/slug/
    # Gallery: https://www.autobazar.sk/foto/slug/28272841/
    parsed = urllib.parse.urlparse(listing_url)
    path_parts = [p for p in parsed.path.split("/") if p]
    # path_parts = ['28272841', 'slug-part']
    if len(path_parts) >= 2:
        listing_id = path_parts[0]
        slug = path_parts[1]
    else:
        print("  Could not parse URL for gallery path", flush=True)
        return []

    gallery_url = f"https://www.autobazar.sk/foto/{slug}/{listing_id}/"
    print(f"  Gallery URL: {gallery_url}", flush=True)

    try:
        text, soup = get_page(gallery_url)
    except Exception as e:
        print(f"  Failed to fetch gallery page: {e}", flush=True)
        return []

    # Extract window.photos from script
    images = []
    for script in soup.find_all("script"):
        script_text = script.string or ""
        if "window.photos" in script_text:
            # Parse the JSON from window.photos = {...};
            match = re.search(r'window\.photos\s*=\s*(\{.+?\});', script_text, re.DOTALL)
            if match:
                try:
                    photos_data = json.loads(match.group(1))
                    photo_list = photos_data.get("list", [])
                    print(f"  Found {len(photo_list)} images in gallery", flush=True)

                    for i, photo in enumerate(photo_list):
                        src = photo.get("src", "")
                        if src:
                            # Unescape if needed
                            src = src.replace("\\/", "/")
                            # Extract image ID
                            id_match = re.search(r'/foto/[^/]+/([^?]+)', src)
                            img_id = id_match.group(1) if id_match else f"img_{i:02d}"
                            images.append((img_id, src))
                except json.JSONDecodeError as e:
                    print(f"  JSON parse error: {e}", flush=True)
            break

    # If window.photos not found, fall back to ImageGallery JSON-LD
    if not images:
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                d = json.loads(script.string)
                if d.get("@type") == "ImageGallery":
                    for i, media in enumerate(d.get("associatedMedia", [])):
                        url = media.get("contentUrl", "")
                        if url:
                            id_match = re.search(r'/foto/[^/]+/([^?]+)', url)
                            img_id = id_match.group(1) if id_match else f"img_{i:02d}"
                            images.append((img_id, url))
                    print(f"  Found {len(images)} images from ImageGallery JSON-LD (640x480)", flush=True)
                    break
            except json.JSONDecodeError:
                continue

    # If still nothing, try inline script with ImageGallery
    if not images:
        for script in soup.find_all("script"):
            text = script.string or ""
            if '"ImageGallery"' in text:
                try:
                    d = json.loads(text)
                    if d.get("@type") == "ImageGallery":
                        for i, media in enumerate(d.get("associatedMedia", [])):
                            url = media.get("contentUrl", "").replace("\\/", "/")
                            if url:
                                id_match = re.search(r'/foto/[^/]+/([^?]+)', url)
                                img_id = id_match.group(1) if id_match else f"img_{i:02d}"
                                images.append((img_id, url))
                        print(f"  Found {len(images)} images from inline JSON-LD (640x480)", flush=True)
                        break
                except json.JSONDecodeError:
                    continue

    return images


def _is_logo_url(url):
    """Check if a URL is a seller logo (not a car photo)."""
    # On autobazar.sk all car photos use /ask/ path, so this is less relevant
    # But we can check for very small fixed-size images that might be logos
    match = re.search(r'/foto/([^/]+)/', url)
    if not match:
        return False
    try:
        decoded = base64.b64decode(match.group(1) + "==").decode("utf-8", errors="ignore")
        # Very small images are likely thumbnails/logos
        size_match = re.search(r'(\d+)x(\d+)', decoded)
        if size_match:
            w, h = int(size_match.group(1)), int(size_match.group(2))
            if w < 50 and h < 50:
                return True
    except Exception:
        pass
    return False


def format_car_info_md(info, num_images):
    """Format car info as a comprehensive Markdown document."""
    lines = []
    lines.append(f"# {info['title']}")
    lines.append("")
    lines.append(f"**URL:** {info['url']}")
    lines.append(f"**Scraped:** {time.strftime('%Y-%m-%d %H:%M')}")
    lines.append("")

    # Price
    price = info.get("price", 0)
    currency = info.get("currency", "EUR")
    if price:
        lines.append(f"**Price:** {price:,} {currency}".replace(",", " "))
    lines.append("")

    # Parameters
    if info.get("parameters"):
        lines.append("## Technical Parameters")
        lines.append("")
        lines.append("| Parameter | Value |")
        lines.append("|-----------|-------|")
        for key, val in info["parameters"].items():
            lines.append(f"| {key} | {val} |")
        lines.append("")

    # VIN
    if info.get("vin"):
        lines.append(f"**VIN:** {info['vin']}")
        lines.append("")
        if info.get("vin_validation"):
            vin_info = info["vin_validation"]
            valid = vin_info.get("valid", False)
            icon = "✅" if valid else "❌"
            lines.append(f"**VIN Status:** {icon} {vin_info.get('validation_message', '')}")
            if vin_info.get("manufacturer"):
                lines.append(f"**Manufacturer:** {vin_info['manufacturer']}")
            if vin_info.get("region"):
                lines.append(f"**Origin:** {vin_info['wmi']} ({vin_info['region']})")
            if vin_info.get("model_year_hint"):
                lines.append(f"**Model year (approx):** {vin_info['model_year_hint']}")
            lines.append("")


    # Equipment
    if info.get("equipment"):
        lines.append("## Equipment")
        lines.append("")
        for section, items in info["equipment"].items():
            lines.append(f"### {section}")
            lines.append("")
            for item in items:
                lines.append(f"- {item}")
            lines.append("")

    # Poznámka
    if info.get("poznamka"):
        lines.append("## Poznámka")
        lines.append("")
        lines.append(info["poznamka"])
        lines.append("")

    # Description (fallback)
    if info.get("description"):
        lines.append("## Description")
        lines.append("")
        lines.append(info["description"])
        lines.append("")

    # Seller
    if info.get("seller"):
        lines.append("## Seller")
        lines.append("")
        if info.get("seller_name"):
            lines.append(f"**Name:** {info['seller_name']}")
        for key, val in info["seller"].items():
            lines.append(f"- **{key}:** {val}")
        lines.append("")

    # Photos
    lines.append("## Photos")
    lines.append("")
    lines.append(f"Downloaded {num_images} images to `images/` folder.")
    lines.append("")

    return "\n".join(lines)


def download_images(image_list, output_dir):
    """Download all images to output directory."""
    max_images = int(os.environ.get("DEMO_MAX_SCRAPED_IMAGES", "0") or "0")
    if max_images > 0 and len(image_list) > max_images:
        print(f"  Demo limit: downloading first {max_images}/{len(image_list)} images", flush=True)
        image_list = image_list[:max_images]

    os.makedirs(output_dir, exist_ok=True)
    downloaded = 0

    for i, (img_id, url) in enumerate(image_list, 1):
        # Determine extension from content-type or URL
        ext = "jpg"  # default for autobazar.sk
        if "format(webp)" in url or ".webp" in url:
            ext = "webp"

        # Clean image ID for filename
        clean_id = re.sub(r'[^\w\-]', '_', img_id.replace("_fss", ""))
        filename = f"{i:02d}_{clean_id}.{ext}"
        filepath = os.path.join(output_dir, filename)

        print(f"  [{i}/{len(image_list)}] {filename} ...", end=" ", flush=True)

        try:
            resp = requests.get(url, headers=HEADERS, timeout=IMAGE_REQUEST_TIMEOUT)
            resp.raise_for_status()

            # Check actual content type
            ct = resp.headers.get("content-type", "")
            if "webp" in ct:
                ext = "webp"
            elif "png" in ct:
                ext = "png"
            elif "jpeg" in ct or "jpg" in ct:
                ext = "jpg"

            # Update filename with correct extension
            filename = f"{i:02d}_{clean_id}.{ext}"
            filepath = os.path.join(output_dir, filename)

            with open(filepath, "wb") as f:
                f.write(resp.content)

            size_kb = len(resp.content) / 1024
            print(f"OK ({size_kb:.0f} KB)", flush=True)
            downloaded += 1
        except Exception as e:
            print(f"FAILED: {e}", flush=True)

        # Small delay between downloads
        if i < len(image_list):
            time.sleep(0.3)

    return downloaded


def main():
    if len(sys.argv) < 2:
        print("Usage: python scrape_car2.py <autobazar.sk listing URL>")
        sys.exit(1)

    listing_url = sys.argv[1].strip()
    print(f"Scraping: {listing_url}", flush=True)

    # Derive output directory from URL slug
    parsed = urllib.parse.urlparse(listing_url)
    path_parts = [p for p in parsed.path.split("/") if p]
    slug = path_parts[1] if len(path_parts) >= 2 else "car"
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Auta", slug)
    images_dir = os.path.join(output_dir, "images")
    print(f"Output:   {output_dir}", flush=True)

    # Step 1: Fetch detail page
    print("\n[1/4] Fetching detail page...", flush=True)
    page_text, soup = get_page(listing_url)

    # Step 2: Extract car info
    print("[2/4] Extracting car info...", flush=True)
    car_info = extract_car_info(soup, listing_url)
    print(f"  Title: {car_info['title']}", flush=True)
    price_str = f"{car_info['price']:,} {car_info.get('currency', 'EUR')}".replace(",", " ") if car_info.get('price') else "N/A"
    print(f"  Price: {price_str}", flush=True)
    print(f"  VIN:   {car_info.get('vin', 'N/A')}", flush=True)
    print(f"  Photos available: {car_info['photos_count']}", flush=True)

    # Step 3: Get gallery images
    print("[3/4] Fetching gallery images...", flush=True)
    image_list = get_gallery_images(listing_url, soup)
    # Filter out logo images
    image_list = [(img_id, url) for img_id, url in image_list if not _is_logo_url(url)]
    print(f"  Total images to download: {len(image_list)}", flush=True)

    # Step 4: Save everything
    print("[4/4] Saving...", flush=True)
    os.makedirs(output_dir, exist_ok=True)

    # Download images
    downloaded = 0
    if image_list:
        downloaded = download_images(image_list, images_dir)
        print(f"\n  Downloaded {downloaded}/{len(image_list)} images", flush=True)

    # Save car info markdown
    md_content = format_car_info_md(car_info, downloaded)
    md_path = os.path.join(output_dir, "car_info.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    print(f"  Saved car info: {md_path}", flush=True)

    # Save raw extracted data as JSON
    json_path = os.path.join(output_dir, "raw_data.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(car_info, f, indent=2, ensure_ascii=False)
    print(f"  Saved raw JSON: {json_path}", flush=True)

    print(f"\nDone! Output in: {output_dir}", flush=True)


if __name__ == "__main__":
    main()
