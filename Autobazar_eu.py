"""
Autobazar.eu Car Listing Scraper
Scrapes car details and gallery images from an autobazar.eu listing page.
Uses the Next.js __NEXT_DATA__ JSON for complete structured data extraction.

Usage: python scrape_car.py <listing_url>
"""

import sys
import os
import re
import time
import json
import urllib.parse
import html
import io

if hasattr(sys, "stdout") and getattr(sys.stdout, "encoding", None) != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)

import truststore
truststore.inject_into_ssl()

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9,sk;q=0.8",
}


def get_page(url):
    """Fetch page and return (response_text, soup)."""
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.text, BeautifulSoup(resp.text, "html.parser")


def extract_next_data(soup):
    """Extract __NEXT_DATA__ JSON from the page."""
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        return None
    return json.loads(script.string)


def get_car_data(next_data):
    """Get the main car record from NEXT_DATA."""
    try:
        return next_data["props"]["pageProps"]["trpcState"]["queries"][0]["state"]["data"]
    except (KeyError, IndexError):
        return None


def extract_car_info(data, url):
    """Extract all car info from the structured JSON data."""
    info = {
        "url": url,
        "id": data.get("id", ""),
        "title": data.get("title", "Unknown"),
        "price": data.get("priceCurrent") or 0,
        "list_price": data.get("listPrice") or 0,
        "vin": data.get("vin", "N/A"),
        "condition": data.get("conditionValue", "N/A"),
        "category": data.get("categoryValue", ""),
        "photos_count": data.get("photosCount", 0),
    }

    # Specs
    info["specs"] = {
        "brand": data.get("brandValue", ""),
        "model": data.get("carModelValue", ""),
        "fuel": data.get("fuelValue", ""),
        "body_type": data.get("bodyworkValue", ""),
        "year": data.get("yearValue", ""),
        "transmission": data.get("gearboxValue", ""),
        "engine_power_kw": data.get("enginePower", 0),
        "engine_capacity_cc": data.get("engineCapacity", 0),
        "mileage_km": data.get("mileage", 0),
        "drivetrain": data.get("driveValue", ""),
        "color": data.get("colorValue", ""),
        "doors": data.get("numberOfDoorsValue", ""),
        "air_conditioning": data.get("airConditionValue", ""),
        "parking_sensors": data.get("parkingSensorsValue", ""),
        "electric_windows": data.get("electricWindowsValue", ""),
        "heated_seats": data.get("heatedSeatsValue", ""),
    }

    # Consumption
    info["consumption"] = {
        "city": data.get("consumptionInTheCity", 0),
        "highway": data.get("consumptionOutOfTown", 0),
        "combined": data.get("consumptionCombined", 0),
    }

    # Equipment
    info["equipment"] = data.get("carEquipmentValue", "")
    info["other_equipment"] = data.get("otherEquipment", "")

    # Additional info (e.g. "Mozny odpocet DPH")
    info["additional_info"] = data.get("additionalInformationValue", "")

    # Description / Seller note (Poznamka) - FULL text from JSON (not truncated)
    info["description"] = data.get("description", "")

    # Dates
    info["created_at"] = data.get("createdAt", "")
    info["updated_at"] = data.get("updatedAt", "")

    # Seller info
    user = data.get("user", {})
    address = user.get("address", {})
    info["seller"] = {
        "name": user.get("displayName", ""),
        "username": user.get("username", ""),
        "city": address.get("city", ""),
        "zip": address.get("zip", ""),
        "street": address.get("street", ""),
        "street_number": address.get("streetNumber", ""),
        "web_url": user.get("webUrl", ""),
        "comment": address.get("comment", ""),
    }

    # Location
    location = data.get("location", {})
    info["location"] = {
        "name": location.get("name", ""),
        "region": ", ".join(location.get("parentNames", [])),
    }

    # Extra links
    extras = data.get("extra", [])
    info["extras"] = {e.get("name", ""): e.get("sValue", "") for e in extras if isinstance(e, dict)}

    return info


def collect_image_urls(data, page_text):
    """
    Collect the best available image URLs from JSON data and HTML.
    Returns list of (image_id, url) tuples.
    """
    images = []
    seen_ids = set()

    # Priority order for URL quality (largest/best first)
    url_keys = ["gallery_premium", "gallery", "orig", "detail_preview", "detail_thumbnail", "aaa_slider", "detail_mobile_slider"]

    def best_url(preview_urls):
        """Get the best available URL from preview URLs dict."""
        for key in url_keys:
            url = preview_urls.get(key, "")
            if url and url.startswith("http"):
                return url
        return None

    # Main/cover image
    main_image = data.get("image", {})
    if main_image:
        img_id = main_image.get("id", "")
        url = best_url(main_image.get("previewUrls", {}))
        if url and img_id not in seen_ids:
            seen_ids.add(img_id)
            images.append((img_id, url))

    # Secondary images from JSON
    for img in data.get("images", []):
        img_id = img.get("id", "")
        url = best_url(img.get("previewUrls", {}))
        if url and img_id not in seen_ids:
            seen_ids.add(img_id)
            images.append((img_id, url))

    return images


def collect_all_images_via_gallery_api(listing_url, page_text, existing_ids=None):
    """
    Fetch the Next.js _next/data gallery endpoint to get all image URLs.
    This endpoint returns the full photo list without needing a browser.
    Returns list of (image_id, url) tuples for images not already found.
    """
    images = []
    seen_ids = set(existing_ids or [])

    # Extract build ID from the page HTML
    build_id_match = re.search(r'"buildId":"([^"]+)"', page_text)
    if not build_id_match:
        print("  Could not find Next.js build ID", flush=True)
        return []

    build_id = build_id_match.group(1)

    # Derive gallery path from listing URL
    parsed = urllib.parse.urlparse(listing_url)
    path_parts = [p for p in parsed.path.split("/") if p]
    # e.g. ['detail-nove-auto', 'peugeot-2008-...', 'Amt-UwXkjp0']
    if len(path_parts) >= 3:
        slug = path_parts[-2]
        record_id = path_parts[-1]
    else:
        print("  Could not parse URL for gallery path", flush=True)
        return []

    gallery_api_url = f"https://www.autobazar.eu/_next/data/{build_id}/galeria/{slug}/{record_id}.json"
    print(f"  Fetching gallery API: .../{slug}/{record_id}.json", flush=True)

    try:
        resp = requests.get(gallery_api_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        gallery_data = resp.json()
    except Exception as e:
        print(f"  Gallery API request failed: {e}", flush=True)
        return []

    # Extract image data from the gallery response
    # The gallery response has same tRPC structure
    try:
        gallery_record = gallery_data["pageProps"]["trpcState"]["queries"][0]["state"]["data"]
    except (KeyError, IndexError):
        # Try alternative structure
        gallery_str = json.dumps(gallery_data)
        # Find all image objects with previewUrls
        pass
        gallery_record = None

    if gallery_record:
        # Get main image
        main_img = gallery_record.get("image", {})
        if main_img:
            img_id = main_img.get("id", "")
            if img_id and img_id not in seen_ids:
                url = _best_preview_url(main_img.get("previewUrls", {}))
                if url:
                    seen_ids.add(img_id)
                    images.append((img_id, url))

        # Get all gallery images
        for img in gallery_record.get("images", []):
            img_id = img.get("id", "")
            if img_id and img_id not in seen_ids:
                url = _best_preview_url(img.get("previewUrls", {}))
                if url and not _is_logo_url(url):
                    seen_ids.add(img_id)
                    images.append((img_id, url))

    return images


def _is_logo_url(url):
    """Check if a URL is a seller logo/non-car image based on path patterns."""
    # Seller logos have '/ask/' as a path segment in the decoded base64 prefix
    # Car photos have '/abm/' in the path
    # Note: gallery_premium URLs contain 'watermark(...)' which has 'ask' as substring - must not match that
    match = re.search(r'/foto/([^/]+)/', url)
    if not match:
        return False
    try:
        import base64
        decoded = base64.b64decode(match.group(1) + "==").decode("utf-8", errors="ignore")
        # Look for '/ask/' as a path segment (not substring in watermark params)
        if re.search(r'/ask/', decoded):
            return True
    except Exception:
        pass
    return False


def _best_preview_url(preview_urls):
    """Get the best (largest) available URL from a previewUrls dict."""
    # Priority: gallery_premium (2560x1440) > gallery (1024x768) > detail_thumbnail (190x143)
    for key in ["gallery_premium", "gallery", "orig", "detail_preview", "detail_mobile_slider", "detail_thumbnail", "aaa_slider"]:
        url = preview_urls.get(key, "")
        if url and url.startswith("http"):
            return url
    return None


def format_car_info_md(info, num_images):
    """Format car info as a comprehensive Markdown document."""
    lines = []
    lines.append(f"# {info['title']}")
    lines.append("")
    lines.append(f"**Source:** {info['url']}")
    lines.append(f"**Listing ID:** {info['id']}")
    lines.append(f"**Scraped:** {time.strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**Created:** {info.get('created_at', 'N/A')}")
    lines.append(f"**Updated:** {info.get('updated_at', 'N/A')}")
    lines.append("")

    # Price
    lines.append("## Price")
    price = info.get("price", 0)
    list_price = info.get("list_price", 0)
    lines.append(f"- **Current Price:** {price:,} EUR".replace(",", " "))
    if list_price and list_price != price:
        lines.append(f"- **List Price:** {list_price:,} EUR".replace(",", " "))
        discount = list_price - price
        pct = discount * 100 // list_price if list_price else 0
        lines.append(f"- **Discount:** {discount:,} EUR ({pct}% off)".replace(",", " "))
    lines.append("")

    # Specifications
    lines.append("## Specifications")
    specs = info.get("specs", {})
    spec_labels = {
        "brand": "Brand",
        "model": "Model",
        "fuel": "Fuel",
        "body_type": "Body Type",
        "year": "Year",
        "transmission": "Transmission",
        "engine_power_kw": "Engine Power",
        "engine_capacity_cc": "Engine Capacity",
        "mileage_km": "Mileage",
        "drivetrain": "Drivetrain",
        "color": "Color",
        "doors": "Doors",
        "air_conditioning": "Air Conditioning",
        "parking_sensors": "Parking Sensors",
        "electric_windows": "Electric Windows",
        "heated_seats": "Heated Seats",
    }
    for key, label in spec_labels.items():
        val = specs.get(key, "")
        if not val or val == 0:
            continue
        if key == "engine_power_kw" and val:
            lines.append(f"- **{label}:** {val} kW ({int(val * 1.36)} PS)")
        elif key == "engine_capacity_cc" and val:
            lines.append(f"- **{label}:** {val} cm3")
        elif key == "mileage_km":
            lines.append(f"- **{label}:** {val} km")
        else:
            lines.append(f"- **{label}:** {val}")
    lines.append(f"- **VIN:** {info.get('vin', 'N/A')}")
    lines.append(f"- **Condition:** {info.get('condition', 'N/A')}")
    lines.append(f"- **Category:** {info.get('category', 'N/A')}")
    lines.append("")

    # Consumption
    consumption = info.get("consumption", {})
    if any(v for v in consumption.values()):
        lines.append("## Fuel Consumption")
        if consumption.get("city"):
            lines.append(f"- **City:** {consumption['city']} l/100km")
        if consumption.get("highway"):
            lines.append(f"- **Highway:** {consumption['highway']} l/100km")
        if consumption.get("combined"):
            lines.append(f"- **Combined:** {consumption['combined']} l/100km")
        lines.append("")

    # Equipment
    equipment = info.get("equipment", "")
    if equipment:
        lines.append("## Standard Equipment")
        for item in equipment.split(", "):
            item = item.strip()
            if item:
                lines.append(f"- {item}")
        lines.append("")

    other_eq = info.get("other_equipment", "")
    if other_eq:
        lines.append("## Additional Equipment")
        for item in other_eq.split(", "):
            item = item.strip()
            if item:
                lines.append(f"- {item}")
        lines.append("")

    # Additional info
    add_info = info.get("additional_info", "")
    if add_info:
        lines.append("## Additional Information")
        lines.append(f"- {add_info}")
        lines.append("")

    # Description / Seller Note (Poznamka) - FULL text
    description = info.get("description", "")
    if description and description.strip():
        lines.append("## Seller Note (Poznamka)")
        lines.append("")
        lines.append(description.strip())
        lines.append("")

    # Seller
    seller = info.get("seller", {})
    if seller.get("name"):
        lines.append("## Seller")
        lines.append(f"- **Name:** {seller['name']}")
        street = seller.get("street", "")
        number = seller.get("street_number", "")
        city = seller.get("city", "")
        zip_code = seller.get("zip", "")
        if street:
            lines.append(f"- **Address:** {street} {number}, {zip_code} {city}".strip())
        if seller.get("web_url"):
            lines.append(f"- **Website:** {seller['web_url']}")
        comment = seller.get("comment", "")
        if comment:
            lines.append(f"- **Contact:** {comment}")
        lines.append("")

    # Location
    loc = info.get("location", {})
    if loc.get("name"):
        lines.append("## Location")
        lines.append(f"- **City:** {loc['name']}")
        if loc.get("region"):
            lines.append(f"- **Region:** {loc['region']}")
        lines.append("")

    # Extras
    extras = info.get("extras", {})
    if extras:
        lines.append("## External Links")
        for name, value in extras.items():
            lines.append(f"- **{name}:** {value}")
        lines.append("")

    # Images reference
    lines.append("## Photos")
    lines.append(f"- **Total on listing:** {info.get('photos_count', 'N/A')}")
    lines.append(f"- **Downloaded:** {num_images}")
    lines.append(f"- See `images/` folder for downloaded photos.")
    lines.append("")

    return "\n".join(lines)


def download_images(image_list, output_dir):
    """Download images. image_list is [(id, url), ...]."""
    max_images = int(os.environ.get("DEMO_MAX_SCRAPED_IMAGES", "0") or "0")
    if max_images > 0 and len(image_list) > max_images:
        print(f"  Demo limit: downloading first {max_images}/{len(image_list)} images", flush=True)
        image_list = image_list[:max_images]

    os.makedirs(output_dir, exist_ok=True)
    total = len(image_list)
    downloaded = 0

    for i, (img_id, url) in enumerate(image_list, 1):
        filename = f"{i:02d}_{img_id}.webp"
        filepath = os.path.join(output_dir, filename)
        print(f"  [{i}/{total}] {filename} ...", end=" ", flush=True)

        try:
            resp = requests.get(url, headers=HEADERS, timeout=30, stream=True)
            resp.raise_for_status()
            with open(filepath, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            size_kb = os.path.getsize(filepath) / 1024
            downloaded += 1
            print(f"OK ({size_kb:.0f} KB)")
        except Exception as e:
            print(f"FAILED ({e})")

        if i < total:
            time.sleep(0.3)

    return downloaded


def derive_slug(detail_url):
    """Extract the car slug from the URL for folder naming."""
    parsed = urllib.parse.urlparse(detail_url)
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) >= 2:
        return parts[-2]
    return "car-listing"


def main():
    if len(sys.argv) < 2:
        print("Usage: python scrape_car.py <autobazar_listing_url>")
        print("Example: python scrape_car.py https://www.autobazar.eu/detail-nove-auto/peugeot-2008-allure-hybrid-145k-e-dcs6-mhev/Amt-UwXkjp0/")
        sys.exit(1)

    listing_url = sys.argv[1].strip()
    if "autobazar.eu" not in listing_url:
        print("Error: URL must be from autobazar.eu")
        sys.exit(1)

    slug = derive_slug(listing_url)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(script_dir, "Auta", slug)
    images_dir = os.path.join(output_dir, "images")

    print(f"Scraping: {listing_url}")
    print(f"Output:   {output_dir}")
    print()

    # Step 1: Fetch page
    print("[1/4] Fetching page...", flush=True)
    page_text, soup = get_page(listing_url)

    # Step 2: Extract JSON data
    print("[2/4] Extracting data from __NEXT_DATA__...", flush=True)
    next_data = extract_next_data(soup)
    if not next_data:
        print("  ERROR: Could not find __NEXT_DATA__ JSON. Site structure may have changed.")
        sys.exit(1)

    car_data = get_car_data(next_data)
    if not car_data:
        print("  ERROR: Could not find car record in JSON data.")
        sys.exit(1)

    car_info = extract_car_info(car_data, listing_url)
    print(f"  Title: {car_info['title']}")
    price_str = f"{car_info['price']:,} EUR".replace(",", " ") if car_info['price'] else "N/A"
    list_price_str = f" (list: {car_info['list_price']:,} EUR)".replace(",", " ") if car_info.get('list_price') else ""
    print(f"  Price: {price_str}{list_price_str}")
    print(f"  VIN:   {car_info['vin']}")
    print(f"  Photos available: {car_info['photos_count']}")
    # VIN validation
    if car_info.get("vin") and car_info["vin"] not in ("N/A", ""):
        try:
            from vin_utils import validate_vin
            car_info["vin_validation"] = validate_vin(car_info["vin"])
        except ImportError:
            pass


    # Step 3: Collect image URLs
    print("[3/5] Collecting images from HTML/JSON...", flush=True)
    image_list = collect_image_urls(car_data, page_text)
    print(f"  Found {len(image_list)} images from static page (of {car_info['photos_count']} total)")

    # Step 3b: Use gallery API to get ALL images at high resolution
    # Gallery API provides gallery_premium (2560x1440) URLs for all photos
    print("[4/5] Fetching high-res images via gallery API...", flush=True)
    gallery_images = collect_all_images_via_gallery_api(listing_url, page_text, existing_ids=set())
    if gallery_images:
        # Replace the image_list with gallery images (higher quality)
        # Keep any images from detail page that aren't in gallery (shouldn't happen)
        gallery_ids = {img_id for img_id, _ in gallery_images}
        extra_from_detail = [(img_id, url) for img_id, url in image_list if img_id not in gallery_ids]
        image_list = gallery_images + extra_from_detail
        print(f"  Got {len(gallery_images)} high-res images from gallery API")

    # Step 5: Save everything
    print("[5/5] Saving...", flush=True)
    os.makedirs(output_dir, exist_ok=True)

    # Download images first
    downloaded = 0
    if image_list:
        downloaded = download_images(image_list, images_dir)
        print(f"\n  Downloaded {downloaded}/{len(image_list)} images")

    # Save car info
    md_content = format_car_info_md(car_info, downloaded)
    md_path = os.path.join(output_dir, "car_info.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    print(f"  Saved car info: {md_path}")

    # Also save raw JSON for reference
    json_path = os.path.join(output_dir, "raw_data.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(car_data, f, indent=2, ensure_ascii=False)
    print(f"  Saved raw JSON: {json_path}")

    print(f"\nDone! Output in: {output_dir}")


if __name__ == "__main__":
    main()
