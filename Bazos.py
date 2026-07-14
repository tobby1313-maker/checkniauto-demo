"""
Bazos.sk Car Listing Scraper
Scrapes car details and gallery images from a bazos.sk listing page.
Outputs in unified format compatible with the ScrapTest analysis pipeline.

Usage:
    python Bazos.py <listing_url>

Example:
    python Bazos.py "https://auto.bazos.sk/inzerat/192873151/audi-a7-30-bitdi-facelift.php"
"""

import os
import sys
import re
import time
import json
import urllib.parse
import requests
from bs4 import BeautifulSoup

from scrapper_demo.storage import ListingJobRepository

IMAGE_REQUEST_TIMEOUT = int(os.environ.get("DEMO_IMAGE_REQUEST_TIMEOUT", "12"))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "sk-SK,sk;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
}


def get_page(url):
    """Fetch page and return (response_text, soup)."""
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.encoding = 'utf-8'
    resp.raise_for_status()
    return resp.text, BeautifulSoup(resp.text, 'html.parser')


def extract_car_info(soup, url):
    """Extract all car info from the bazos.sk listing page."""
    info = {
        "url": url,
        "title": "",
        "price": 0,
        "currency": "EUR",
        "vin": "",
        "description": "",
        "parameters": {},
        "seller": {},
        "location": "",
        "photos_count": 0,
        "source": "bazos.sk",
    }

    # --- Title ---
    h1 = soup.find('h1', class_='nadpisihlavni') or soup.find('h1')
    if h1:
        info["title"] = h1.text.strip()
    else:
        # Try title tag
        title_tag = soup.find('title')
        if title_tag:
            info["title"] = title_tag.text.strip()
            # Clean up "| Bazos.sk" suffix
            info["title"] = re.sub(r'\s*[|–-]\s*Bazos\.sk.*$', '', info["title"], flags=re.I).strip()
        else:
            info["title"] = "Inzerat"

    # --- Price ---
    price_elem = (
        soup.find('b', string=re.compile(r'Cena:'))
        or soup.find('span', string=re.compile(r'Cena:'))
        or soup.find(string=re.compile(r'Cena:'))
    )
    if price_elem:
        try:
            price_text = price_elem.find_next(string=True).strip() if hasattr(price_elem, 'find_next') else price_elem.next_sibling.strip()
            # Extract numeric price
            price_match = re.search(r'([\d\s]+)\s*[€$€]', price_text)
            if price_match:
                info["price"] = int(price_match.group(1).replace(' ', ''))
        except Exception:
            pass

    # --- Description (Popis) ---
    popis = soup.find('div', class_='popisdetail') or soup.find('div', class_='popis')
    if popis:
        info["description"] = popis.get_text(separator="\n").strip()

    # Bazos occasionally renders the header price in a shape not covered by
    # the selectors above while the seller repeats it in the description.
    # Recover only an explicitly labelled EUR amount; never infer a price from
    # an arbitrary number in the listing text.
    if not info["price"] and info["description"]:
        description_price = re.search(
            r"(?:cena|price)\s*:\s*(\d{1,3}(?:[\s\u00a0.]\d{3})+|\d+)\s*(?:€|eur)(?:\s|$)",
            info["description"],
            re.IGNORECASE,
        )
        if description_price:
            info["price"] = int(re.sub(r"\D", "", description_price.group(1)))

    # --- Seller & Location ---
    vsetky_b = soup.find_all('b')
    for b in vsetky_b:
        b_text = b.text.strip()
        if b_text.startswith("Meno:"):
            seller_name = b.next_sibling.strip() if b.next_sibling else ""
            if seller_name:
                info["seller"]["Meno"] = seller_name
        if b_text.startswith("Lokalita:"):
            a_href = b.find_next('a')
            if a_href:
                info["location"] = a_href.text.strip() if a_href.text else ""
            else:
                loc_text = b.next_sibling.strip() if b.next_sibling else ""
                info["location"] = loc_text

    # --- Parameters from structured data ---
    # Try to extract mileage, year, engine etc from description or structured elements
    desc_text = info.get("description", "")
    
    # Mileage: Bazos descriptions commonly use either "Najazdene km: 161949"
    # or the conventional "161 949 km" order.
    mileage_match = re.search(
        r'(?:najazden[eé]\s*km|najazd|mileage)\s*:?\s*([\d\s.]+)(?:\s*km)?',
        desc_text,
        re.I,
    )
    if not mileage_match:
        mileage_match = re.search(r'(\d{2,3}(?:[\s.]\d{3})+)\s*km\b', desc_text, re.I)
    if mileage_match:
        mileage_digits = re.sub(r'\D', '', mileage_match.group(1))
        if mileage_digits:
            info["parameters"]["Mileage"] = f"{mileage_digits} km"

    # Year
    year_match = re.search(
        r'(?:Mesiac\s*/\s*Rok|Rok|ročník|modelový\s*rok|year)\s*:?\s*(?:\d{1,2}\s*/\s*)?(\d{4})',
        desc_text,
        re.I,
    )
    if year_match:
        info["parameters"]["Year"] = year_match.group(1)

    # Engine power
    power_match = re.search(r'(\d+)\s*kW', desc_text, re.I)
    if power_match:
        info["parameters"]["Engine Power"] = f"{power_match.group(1)} kW"

    engine_match = re.search(
        r'(?:Typ\s+motora|Motor|Engine)\s*:?\s*([^\r\n,;]{2,80})',
        desc_text,
        re.I,
    )
    if engine_match:
        info["parameters"]["Engine"] = engine_match.group(1).strip()

    # Engine capacity
    capacity_match = re.search(r'(\d+[.,]?\d*)\s*(?:cm3|l\b)', desc_text, re.I)
    if capacity_match:
        info["parameters"]["Engine Capacity"] = capacity_match.group(0)

    # Fuel type
    fuel_keywords = {
        "Benzín": r'\b(?:benzín|benzin|benzene|gasoline)\b',
        "Diesel": r'\b(?:diesel|nafta|tdi|tid|bitdi)\b',
        "Hybrid": r'\b(?:hybrid|hybridný)\b',
        "Elektro": r'\b(?:elektro|electric|ev|elektromobil)\b',
    }
    for fuel_name, pattern in fuel_keywords.items():
        if re.search(pattern, desc_text, re.I):
            info["parameters"]["Fuel"] = fuel_name
            break

    # Transmission. Preserve an explicit labelled value because the gear count
    # and DCT/DSG/CVT family materially improve component identification.
    transmission_match = re.search(
        r'(?:Prevodovka|Transmission|Gearbox)\s*:?\s*([^\r\n,;]{2,80})',
        desc_text,
        re.I,
    )
    if transmission_match:
        info["parameters"]["Transmission"] = transmission_match.group(1).strip()
    # First check for explicit manual transmission mentions (e.g. "manualnou prevodovkou", "Manual 6 rýchlostný")
    elif re.search(r'(?:manuálna\s+prevodovka|manualnou\s+prevodovkou|manual\s+\d+\s*rýchlost|manuál\s+\d+\s*stupňov)', desc_text, re.I):
        info["parameters"]["Transmission"] = "Manuálna"
    # Then check for explicit automatic transmission mentions
    elif re.search(r'(?:automatická\s+prevodovka|automatickou\s+prevodovkou|tiptronic|s-tronic|dsg|cvt)', desc_text, re.I):
        info["parameters"]["Transmission"] = "Automatická"
    # Fallback: standalone "automat" vs "manuál/manual" - check which one appears (but "automat" also matches "automatická klimatizácia")
    elif re.search(r'\bautomat\b', desc_text, re.I) and not re.search(r'\b(?:manuál|manual)\b', desc_text, re.I):
        info["parameters"]["Transmission"] = "Automatická"
    elif re.search(r'\b(?:manuál|manual)\b', desc_text, re.I) and not re.search(r'\bautomat\b', desc_text, re.I):
        info["parameters"]["Transmission"] = "Manuálna"
    # If both are mentioned (e.g. "automatická klimatizácia" + "manualná prevodovka"), default to manual
    elif re.search(r'\b(?:manuál|manual)\b', desc_text, re.I):
        info["parameters"]["Transmission"] = "Manuálna"

    # Drivetrain is often present only in the ad title.
    drivetrain_text = f'{info.get("title", "")}\n{desc_text}'
    if re.search(r'\b(?:4x4|quattro|pohon\s*4x4|awd|4wd|allrad)\b', drivetrain_text, re.I):
        info["parameters"]["Drivetrain"] = "4x4"
    elif re.search(r'\b(?:predn[ýi]\s*pohon|fwd|predokolka)\b', drivetrain_text, re.I):
        info["parameters"]["Drivetrain"] = "Predný"
    elif re.search(r'\b(?:zadn[Aa-z]\s*pohon|rwd)\b', drivetrain_text, re.I):
        info["parameters"]["Drivetrain"] = "Zadna"

    # --- VIN Extraction (scan description for VIN pattern) ---
    if info.get("description"):
        try:
            from vin_utils import extract_vin_from_text
            found_vin = extract_vin_from_text(info["description"])
            if found_vin:
                info["vin"] = found_vin
        except ImportError:
            pass

    # Body type
    body_keywords = {
        "SUV": r'\b(?:suv|offroad|off-road)\b',
        "Sedan": r'\b(?:sedan|salón)\b',
        "Kombi": r'\b(?:kombi|combi|avant|estate|tourer)\b',
        "Hatchback": r'\b(?:hatchback|hatch)\b',
        "Coupe": r'\b(?:coupé|coupe|kupé)\b',
        "Cabrio": r'\b(?:cabrio|cabriolet|kabriolet|convertible)\b',
        "MPV": r'\b(?:mpv|minivan|van)\b',
    }
    for body_name, pattern in body_keywords.items():
        if re.search(pattern, info["title"], re.I) or re.search(pattern, desc_text, re.I):
            info["parameters"]["Body Type"] = body_name
            break

    # --- Photo count ---
    # Count images in the gallery
    carousel = soup.find('div', class_='carousel') or soup.find('div', class_='fotky')
    if carousel:
        info["photos_count"] = len(carousel.find_all('img'))
    else:
        all_imgs = soup.find_all('img')
        # Count only reasonable car-like images (skip icons, svgs)
        car_imgs = [img for img in all_imgs if img.get('src') and 'obrazky' not in img.get('src', '') and not img.get('src', '').lower().endswith('.svg')]
        info["photos_count"] = len(car_imgs)

    return info


def collect_image_urls(soup, listing_url):
    """
    Collect all car image URLs from a bazos.sk listing.
    Returns list of (image_id, url) tuples.
    """
    images = []
    seen = set()

    # 1) Prefer images from the main carousel (they often use data-flickity-lazyload)
    carousel = soup.find('div', class_='carousel') or soup.find('div', class_='fotky')
    if carousel:
        for img in carousel.find_all('img'):
            src = img.get('src') or img.get('data-flickity-lazyload') or img.get('data-src') or img.get('data-srcset')
            if not src:
                continue
            # if data-srcset, pick first url
            if ',' in src and ' ' in src:
                src = src.split(',')[0].strip().split(' ')[0]

            # skip site UI icons and svgs
            if 'obrazky' in src or src.lower().endswith('.svg'):
                continue

            # skip images that are within "podobne" (similar) section
            skip = False
            for parent in img.find_parents('div'):
                classes = parent.get('class') or []
                if any('podobn' in c for c in classes):
                    skip = True
                    break
            if skip:
                continue

            # normalize to absolute URL
            if src.startswith('//'):
                src = 'https:' + src
            elif src.startswith('/'):
                src = urllib.parse.urljoin('https://www.bazos.sk', src)

            if src not in seen:
                seen.add(src)
                # Generate image ID from URL hash or filename
                img_id = re.sub(r'[^\w\-]', '_', src.split('/')[-1].split('?')[0])
                images.append((img_id, src))
    else:
        # fallback: gather images but convert thumbnails (1t -> 1) to full-size when possible
        for img in soup.find_all('img'):
            src = img.get('src') or img.get('data-src') or img.get('data-flickity-lazyload')
            if not src:
                continue
            if src.startswith('//'):
                src = 'https:' + src
            elif src.startswith('/'):
                src = urllib.parse.urljoin('https://www.bazos.sk', src)

            # convert thumbnail path like '/img/2t/893/...' to '/img/2/893/...'
            src = re.sub(r'/img/(\d+)t/', r'/img/\1/', src)

            # skip site UI svgs etc
            if 'obrazky' in src or src.lower().endswith('.svg'):
                continue

            if src not in seen:
                seen.add(src)
                img_id = re.sub(r'[^\w\-]', '_', src.split('/')[-1].split('?')[0])
                images.append((img_id, src))

    return images


def format_car_info_md(info, num_images):
    """Format car info as a comprehensive Markdown document (unified format)."""
    lines = []
    lines.append(f"# {info['title']}")
    lines.append("")
    lines.append(f"**Source:** {info['url']}")
    lines.append(f"**Scraped:** {time.strftime('%Y-%m-%d %H:%M')}")
    lines.append("")

    # Price
    price = info.get("price", 0)
    if price:
        lines.append("## Price")
        lines.append(f"- **Price:** {price:,} EUR".replace(",", " "))
        lines.append("")

    # Parameters (as table)
    if info.get("parameters"):
        lines.append("## Specifications")
        lines.append("")
        lines.append("| Parameter | Value |")
        lines.append("|-----------|-------|")
        for key, val in info["parameters"].items():
            lines.append(f"| {key} | {val} |")
        lines.append("")

    # VIN
    if info.get("vin") and info["vin"] != "N/A":
        lines.append(f"**VIN:** {info['vin']}")
        lines.append("")

    # Description / Seller Note
    description = info.get("description", "")
    if description:
        lines.append("## Seller Note (Poznamka)")
        lines.append("")
        lines.append(description)
        lines.append("")

    # Seller
    if info.get("seller"):
        lines.append("## Seller")
        lines.append("")
        for key, val in info["seller"].items():
            lines.append(f"- **{key}:** {val}")
        lines.append("")

    # Location
    if info.get("location"):
        lines.append("## Location")
        lines.append("")
        lines.append(f"- **Location:** {info['location']}")
        lines.append("")

    # Photos
    lines.append("## Photos")
    lines.append(f"- **Downloaded:** {num_images}")
    lines.append(f"- See `images/` folder for downloaded photos.")
    lines.append("")

    return "\n".join(lines)


def download_images(image_list, output_dir):
    """Download all images to output directory."""
    max_images = int(os.environ.get("DEMO_MAX_SCRAPED_IMAGES", "0") or "0")
    if max_images > 0 and len(image_list) > max_images:
        print(f"  Collage limit: downloading first {max_images}/{len(image_list)} images", flush=True)
        image_list = image_list[:max_images]

    os.makedirs(output_dir, exist_ok=True)
    downloaded = 0

    for i, (img_id, url) in enumerate(image_list, 1):
        ext = "jpg"  # default for bazos.sk
        filepath = os.path.join(output_dir, f"{i:02d}_{img_id}.{ext}")

        print(f"  [{i}/{len(image_list)}] {i:02d}_{img_id}.{ext} ...", end=" ", flush=True)

        try:
            resp = requests.get(url, headers=HEADERS, timeout=IMAGE_REQUEST_TIMEOUT)
            resp.raise_for_status()

            # Determine correct extension from content type
            ct = resp.headers.get("content-type", "")
            if "webp" in ct:
                ext = "webp"
            elif "png" in ct:
                ext = "png"
            elif "jpeg" in ct or "jpg" in ct:
                ext = "jpg"

            filepath = os.path.join(output_dir, f"{i:02d}_{img_id}.{ext}")
            with open(filepath, "wb") as f:
                f.write(resp.content)

            size_kb = len(resp.content) / 1024
            print(f"OK ({size_kb:.0f} KB)")
            downloaded += 1
        except Exception as e:
            print(f"FAILED: {e}")

        if i < len(image_list):
            time.sleep(0.3)

    return downloaded


def derive_slug(url):
    """Extract folder slug from URL."""
    parsed = urllib.parse.urlparse(url)
    path_parts = [p for p in parsed.path.split("/") if p]
    if len(path_parts) >= 2:
        # bazos.sk uses format: /inzerat/192873151/audi-a7-30-bitdi-facelift.php
        # Use the descriptive part (last part) without .php
        slug = path_parts[-1].replace('.php', '')
        return slug
    return "car-listing"


def main():
    if len(sys.argv) < 2:
        print("Usage: python Bazos.py <bazos listing URL>")
        print("Example: python Bazos.py \"https://auto.bazos.sk/inzerat/192873151/audi-a7-30-bitdi-facelift.php\"")
        sys.exit(1)

    listing_url = sys.argv[1].strip()
    if "bazos.sk" not in listing_url and "bazos.cz" not in listing_url:
        print("Error: URL must be from bazos.sk or bazos.cz")
        sys.exit(1)

    slug = derive_slug(listing_url)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    auta_root = os.environ.get("SCRAPPER_AUTA_DIR") or os.path.join(script_dir, "Auta")
    repository = ListingJobRepository(auta_root)
    output_dir = str(repository.job_dir(slug))
    images_dir = str(repository.images_dir(slug))

    print(f"Scraping: {listing_url}")
    print(f"Output:   {output_dir}")
    print()

    # Step 1: Fetch page
    print("[1/4] Fetching page...", flush=True)
    page_text, soup = get_page(listing_url)

    # Step 2: Extract car info
    print("[2/4] Extracting car info...", flush=True)
    car_info = extract_car_info(soup, listing_url)
    print(f"  Title: {car_info['title']}")
    price_str = f"{car_info['price']:,} EUR".replace(",", " ") if car_info['price'] else "N/A"
    print(f"  Price: {price_str}")

    # Step 3: Collect image URLs
    print("[3/4] Collecting images...", flush=True)
    image_list = collect_image_urls(soup, listing_url)
    print(f"  Found {len(image_list)} images")

    # Step 4: Save everything
    print("[4/4] Saving...", flush=True)
    repository.job_dir(slug, create=True)

    # Download images
    downloaded = 0
    if image_list:
        downloaded = download_images(image_list, images_dir)
        print(f"  Downloaded {downloaded}/{len(image_list)} images")

    # Save car info markdown
    md_content = format_car_info_md(car_info, downloaded)
    md_path = repository.write_text(slug, "car_info.md", md_content)
    print(f"  Saved car info: {md_path}")

    # Save raw data as JSON
    json_path = repository.write_json(slug, "raw_data.json", car_info)
    print(f"  Saved raw JSON: {json_path}")

    print(f"\nDone! Output in: {output_dir}")


if __name__ == '__main__':
    main()
