"""
Unified Car Listing Scraper
Detects the website from the URL and runs the appropriate scraper.
Supports: autobazar.eu, autobazar.sk, bazos.sk, bazos.cz
After scraping, generates analysis_request.md ready to paste into AI chat.

Usage:
    python main.py [listing_url]

Examples:
    python main.py "https://www.autobazar.eu/detail/audi-a4-avant-40-20-tdi-advanced-s-tronic/AmljfprwFka/"
    python main.py "https://www.autobazar.sk/28272841/toyota-corolla-combi-ts-1-8-hybrid-e-cvt-povodny-lak-sr/"
    python main.py "https://auto.bazos.sk/inzerat/192873151/audi-a7-30-bitdi-facelift.php"
    python main.py   (asks for URL interactively)
"""

import sys
import subprocess
import os
import json
import urllib.parse
import time
import difflib

from vin_utils import validate_vin, extract_vin_from_text
from scrapper_demo.logging import safe_log
from scrapper_demo.storage import ListingJobRepository, atomic_write_json, atomic_write_text


safe_print = safe_log


def detect_site(url):
    """Detect which site the URL is from."""
    if "autobazar.eu" in url:
        return "autobazar.eu"
    elif "autobazar.sk" in url:
        return "autobazar.sk"
    elif "bazos.sk" in url or "bazos.cz" in url:
        return "bazos"
    else:
        return None


def get_scraper_path(site):
    """Get the path to the appropriate scraper script."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    scrapers = {
        "autobazar.eu": os.path.join(script_dir, "Autobazar_eu.py"),
        "autobazar.sk": os.path.join(script_dir, "Autobazar_sk.py"),
        "bazos": os.path.join(script_dir, "Bazos.py"),
    }
    return scrapers.get(site)


def derive_slug(url):
    """Extract the output folder slug from URL."""
    parsed = urllib.parse.urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]

    if "autobazar.eu" in url:
        # .eu uses second-to-last path segment
        return parts[-2] if len(parts) >= 2 else "car-listing"
    elif "autobazar.sk" in url:
        # .sk uses second path segment (slug after the ID)
        return parts[1] if len(parts) >= 2 else "car"
    elif "bazos.sk" in url or "bazos.cz" in url:
        # bazos uses last path segment without .php
        return parts[-1].replace('.php', '') if len(parts) >= 2 else "car-listing"
    else:
        return "car-listing"


# All KB subdirectories with their display category names
KB_CATEGORIES = [
    ("engine", "engines"),
    ("transmission", "transmissions"),
    ("generation", "generations"),
    ("electric_motor", "electric_motors"),
    ("battery", "batteries"),
    ("charging", "charging"),
    ("hybrid_system", "hybrid_systems"),
]

def find_matching_kb_files(kb_dir, car_info_text):
    """
    Find matching knowledge_base files for a car based on index.json aliases
    and fallback substring matching on filenames.
    Returns list of (category, filepath) tuples.
    """
    index_path = os.path.join(kb_dir, "index.json")
    index = {}
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            index = json.load(f)

    car_text_lower = car_info_text.lower()
    matched = []

    for category, subdir in KB_CATEGORIES:
        category_dir = os.path.join(kb_dir, subdir)
        if not os.path.isdir(category_dir):
            continue

        found = False

        # Strategy 1: Match via index.json aliases (word-based)
        if subdir in index:
            best_match = None
            best_word_count = 0
            for alias, filename in index[subdir].items():
                alias_words = alias.lower().split()
                if all(w in car_text_lower for w in alias_words):
                    if len(alias_words) > best_word_count:
                        best_word_count = len(alias_words)
                        best_match = filename
            if best_match:
                filepath = os.path.join(category_dir, best_match)
                if os.path.exists(filepath):
                    matched.append((category, filepath))
                    found = True

        # Strategy 2: Fuzzy alias matching via difflib.SequenceMatcher
        if not found and subdir in index:
            best_match = None
            best_score = 0.0
            FUZZY_THRESHOLD = 0.6
            for alias, filename in index[subdir].items():
                alias_lower = alias.lower()
                alias_words = alias_lower.split()
                if not alias_words:
                    continue
                total_score = 0.0
                for word in alias_words:
                    # SequenceMatcher ratio for the whole word
                    seq = difflib.SequenceMatcher(None, word, car_text_lower)
                    best_ratio = seq.ratio()
                    # Boost score if word appears as exact substring
                    if word in car_text_lower:
                        best_ratio = max(best_ratio, 0.95)
                    total_score += best_ratio
                avg_score = total_score / len(alias_words)
                if avg_score > best_score:
                    best_score = avg_score
                    best_match = filename

            if best_match and best_score >= FUZZY_THRESHOLD:
                filepath = os.path.join(category_dir, best_match)
                if os.path.exists(filepath):
                    matched.append((category, filepath))
                    found = True


        # Strategy 3: Fallback — substring match on filenames
        if not found:
            for f in os.listdir(category_dir):
                if f.startswith("_") or not f.endswith(".json"):
                    continue
                name_parts = f.replace(".json", "").replace("_", " ").replace("-", " ").lower()
                parts = [p for p in name_parts.split() if len(p) > 2]
                if all(p in car_text_lower for p in parts):
                    matched.append((category, os.path.join(category_dir, f)))
                    break

    return matched


def build_analysis_request(script_dir, output_dir, url):
    """
    Combine the manual ChatGPT prompt + car_info.md into analysis_request.md.
    Uses analyze_prompt_v2.txt when available and falls back to analyze_prompt.txt.
    """
    # Find the manual ChatGPT prompt.
    prompt_paths = [
        os.path.join(script_dir, "analyze_prompt_v4_koyeb.txt"),
        os.path.join(script_dir, "analyze_prompt_v2.txt"),
        os.path.join(script_dir, "analyze_prompt.txt"),
    ]
    prompt_path = None
    for p in prompt_paths:
        if os.path.exists(p):
            prompt_path = p
            break

    if not prompt_path:
        print("  WARNING: analysis prompt not found, skipping analysis file generation.")
        return None

    car_info_path = os.path.join(output_dir, "car_info.md")
    if not os.path.exists(car_info_path):
        print("  WARNING: car_info.md not found, skipping analysis file generation.")
        return None

    # Read the analysis prompt
    with open(prompt_path, "r", encoding="utf-8") as f:
        prompt_text = f.read()

    with open(car_info_path, "r", encoding="utf-8") as f:
        car_info_text = f.read()

    # Check for knowledge_base cache
    kb_section = ""
    kb_dirs = [
        os.path.join(script_dir, "knowledge_base"),
    ]
    kb_dir = None
    for d in kb_dirs:
        if os.path.isdir(d):
            kb_dir = d
            break

    if kb_dir:
        matched_files = find_matching_kb_files(kb_dir, car_info_text)
        if matched_files:
            kb_section = "\n\n---\n\n## 💾 KNOWLEDGE BASE (cached component data):\n"
            for category, filepath in matched_files:
                with open(filepath, "r", encoding="utf-8") as f:
                    kb_section += f"\n### [{category.upper()}] {os.path.basename(filepath)}:\n```json\n{f.read()}\n```\n"
            # Note which layers are missing
            found_categories = {cat for cat, _ in matched_files}
            all_categories = ("engine", "transmission", "generation", "electric_motor", "battery", "charging", "hybrid_system")
            missing = [c for c in all_categories if c not in found_categories]
            if missing:
                kb_section += f"\n\n⚠️ **CHÝBAJÚCE CACHE:** {', '.join(missing)} — vygeneruj JSON na konci analýzy.\n"
        else:
            kb_section = "\n\n---\n\n## 💾 KNOWLEDGE BASE:\n⚠️ **Žiadny cache nenájdený.** Vygeneruj všetky JSON súbory (engine, transmission, generation, electric_motor, battery, charging, hybrid_system) na konci analýzy.\n"

    # List images
    images_dir = os.path.join(output_dir, "images")
    image_list = ""
    if os.path.isdir(images_dir):
        images = sorted(os.listdir(images_dir))
        image_list = f"\n\n---\n\n## 📸 FOTOGRAFIE ({len(images)} ks)\nSúbory v priečinku `{images_dir}`:\n"
        for img in images:
            image_list += f"- {img}\n"
        image_list += "\n⚠️ **Vlož fotografie do chatu spolu s týmto textom pre kompletnú analýzu.**"

    # Combine everything
    output = f"""# 🚗 ŽIADOSŤ O ANALÝZU INZERÁTU

**URL inzerátu:** {url}

---

## SYSTÉMOVÝ PROMPT (inštrukcie pre AI):

{prompt_text}

---

## DÁTA Z INZERÁTU:

{car_info_text}
{kb_section}{image_list}

---

## ✅ INŠTRUKCIA:
Analyzuj tento inzerát podľa systémového promptu vyššie. Použi všetky 5 fáz analýzy a vygeneruj kompletný výstup vrátane hodnotenia.
"""

    output_path = os.path.join(output_dir, "analysis_request.md")
    atomic_write_text(output_path, output)

    return output_path


# ─── VIN Decoding ──────────────────────────────────────────────────

def _run_vin_decoding(output_dir):
    """Scan scraped data for VIN, decode it, and save vin_decoded.json."""
    car_info_path = os.path.join(output_dir, "car_info.md")
    raw_data_path = os.path.join(output_dir, "raw_data.json")
    vin_text = None

    # 1. Try raw_data.json first (structured field)
    if os.path.exists(raw_data_path):
        try:
            with open(raw_data_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            vin_text = raw.get("vin") or raw.get("VIN") or raw.get("vehicle_identification_number")
            if isinstance(vin_text, dict):
                # unwrap if nested
                vin_text = vin_text.get("value") or vin_text.get("#text")
        except (json.JSONDecodeError, IOError):
            pass

    # 2. Fallback — extract from car_info.md text
    if not vin_text and os.path.exists(car_info_path):
        try:
            with open(car_info_path, "r", encoding="utf-8") as f:
                md_text = f.read()
            vin_text = extract_vin_from_text(md_text)
        except IOError:
            pass

    if not vin_text:
        print("  VIN: Not found in scraped data.")
        return

    # Decode
    decoded = validate_vin(vin_text)
    decoded_path = os.path.join(output_dir, "vin_decoded.json")
    atomic_write_json(decoded_path, decoded)

    status = "[OK]" if decoded.get("valid") else "[WARN]"
    safe_print(f"  VIN: {status} {decoded.get('manufacturer', '?')} - saved to vin_decoded.json")


def ensure_scraped_timestamp(output_dir):
    """Ensure car_info.md always contains a scraped timestamp."""
    car_info_path = os.path.join(output_dir, "car_info.md")
    if not os.path.exists(car_info_path):
        return

    with open(car_info_path, "r", encoding="utf-8") as f:
        content = f.read()

    if "**Scraped:**" in content:
        return

    timestamp = time.strftime("%Y-%m-%d %H:%M")
    lines = content.splitlines()
    insert_at = 1
    for i, line in enumerate(lines):
        if line.startswith("**Source:**") or line.startswith("**URL:**"):
            insert_at = i + 1
            break

    lines.insert(insert_at, f"**Scraped:** {timestamp}")
    atomic_write_text(car_info_path, "\n".join(lines) + "\n")


def main():
    if len(sys.argv) >= 2:
        url = sys.argv[1].strip()
        scraper_args = sys.argv[2:]
    else:
        url = input("Vlož link na inzerát (autobazar.eu, autobazar.sk alebo bazos.sk/cz): ").strip()
        scraper_args = []
        if not url:
            print("ERROR: Nebol zadaný žiadny link.")
            sys.exit(1)

    # Detect site
    site = detect_site(url)
    if not site:
        print("ERROR: Nepodporovaná stránka. URL musí obsahovať autobazar.eu, autobazar.sk, bazos.sk alebo bazos.cz")
        print(f"  Zadané: {url}")
        sys.exit(1)

    scraper_path = get_scraper_path(site)
    if not scraper_path or not os.path.exists(scraper_path):
        print(f"ERROR: Scraper pre '{site}' nebol nájdený: {scraper_path}")
        sys.exit(1)

    site_names = {
        "autobazar.eu": "autobazar.eu",
        "autobazar.sk": "autobazar.sk",
        "bazos": "bazos.sk / bazos.cz",
    }
    print(f"Detekovaná stránka: {site_names.get(site, site)}")
    print(f"Používam scraper:   {os.path.basename(scraper_path)}")
    print()

    # Run the scraper
    script_dir = os.path.dirname(os.path.abspath(__file__))
    result = subprocess.run([sys.executable, scraper_path, url, *scraper_args], cwd=script_dir)

    # Generate analysis request file (only if scraping succeeded)
    slug = derive_slug(url)
    auta_root = os.environ.get("SCRAPPER_AUTA_DIR") or os.path.join(script_dir, "Auta")
    output_dir = str(ListingJobRepository(auta_root).job_dir(slug))

    # VIN Decoding: run after scrape succeeds regardless
    if os.path.exists(os.path.join(output_dir, "car_info.md")):
        ensure_scraped_timestamp(output_dir)
        try:
            _run_vin_decoding(output_dir)
        except Exception as e:
            safe_print(f"  VIN decoding (non-critical): {e}")

    if result.returncode == 0:
        print()
        print("=" * 60)
        print("Generujem analysis_request.md...")
        analysis_path = build_analysis_request(script_dir, output_dir, url)

        if analysis_path:
            print("Hotovo!")
            print()
            print("=" * 60)
            print("Dalsi KROK:")
            print(f"   1. Otvor: {analysis_path}")
            print(f"   2. Skopiruj cely obsah (Ctrl+A, Ctrl+C)")
            print(f"   3. Vloz do AI chatu (ChatGPT, Copilot, Claude...)")
            print(f"   4. Pridaj fotky z: {os.path.join(output_dir, 'images')}")
            print("=" * 60)
    else:
        # Scraping failed - check if there's previous data
        if os.path.exists(os.path.join(output_dir, "car_info.md")):
            print("Scraping zlyhal, ale pouzivam predchadzajuce data.")
            print()
            print("=" * 60)
            analysis_path = build_analysis_request(script_dir, output_dir, url)
            if analysis_path:
                print("Hotovo (zo starych dat)!")
                print(f"   Otvor: {analysis_path}")
                print("=" * 60)
        else:
            print("Scraping zlyhal a neexistuju ziadne predchadzajuce data.")
            sys.exit(result.returncode)


if __name__ == "__main__":
    main()
