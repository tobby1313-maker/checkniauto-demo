(function () {
  "use strict";

  const STORAGE = {
    theme: "checkni-auto-theme",
    language: "checkni-auto-language",
    recent: "checkni-auto-recent-analyses-v2",
  };

  const SUPPORTED_LANGUAGES = ["sk", "cs", "en"];

  const czechStatic = {
    "+ Nová analýza": "+ Nová analýza",
    "AI ho preverí": "AI ho prověří",
    "AI screening auta pred kúpou": "AI screening auta před koupí",
    "Ako to funguje": "Jak to funguje",
    "Analýza kombinuje údaje inzerátu, vizuálne pozorovania a dostupné externé zdroje. Nie je náhradou fyzickej obhliadky, diagnostiky ani právneho preverenia vozidla.": "Analýza kombinuje údaje inzerátu, vizuální pozorování a dostupné externí zdroje. Nenahrazuje fyzickou prohlídku, diagnostiku ani právní prověření vozidla.",
    "Analyzovať →": "Analyzovat →",
    "Analyzovať inzerát": "Analyzovat inzerát",
    "Analýzu sa nepodarilo načítať": "Analýzu se nepodařilo načíst",
    "Automaticky podporujeme Bazoš SK/CZ a Autobazar SK/EU.": "Automaticky podporujeme Bazoš SK/CZ a Autobazar SK/EU.",
    "Bezpečnosť a zvolávacie akcie": "Bezpečnost a svolávací akce",
    "Celý technický report": "Celý technický report",
    "Cena": "Cena",
    "Cena EUR": "Cena EUR",
    "Cena voči trhu": "Cena vůči trhu",
    "Čo môže byť problém a ako to overiť.": "Co může být problém a jak to ověřit.",
    "Čo na diaľku nevieme": "Co na dálku nezjistíme",
    "Čo podporuje kúpu": "Co podporuje koupi",
    "Čo spraviť teraz": "Co udělat teď",
    "Čo treba preveriť": "Co je třeba prověřit",
    "Čo vieme a čo nie": "Co umíme a co ne",
    "Čo vieme posúdiť": "Co umíme posoudit",
    "Čo vieme posúdiť a čo nie": "Co umíme posoudit a co ne",
    "Dekódovaná identita": "Rozpoznaná identita",
    "Demo analýzy sú dočasné a po uplynutí úložnej lehoty prestanú byť dostupné.": "Demo analýzy jsou dočasné a po uplynutí doby uchování přestanou být dostupné.",
    "Detailné dáta, riziká, zdroje, fotografie a kontrolný zoznam.": "Podrobné údaje, rizika, zdroje, fotografie a kontrolní seznam.",
    "Domov": "Domů",
    "Dôveryhodnosť dát": "Důvěryhodnost dat",
    "Dôvod": "Důvod",
    "Fakty a odhady zostávajú oddelené.": "Fakta a odhady zůstávají oddělené.",
    "Fotodokumentácia": "Fotodokumentace",
    "Fotodokumentácia a vizuálne zistenia": "Fotodokumentace a vizuální zjištění",
    "Fotografia nie je dostupná": "Fotografie není dostupná",
    "Fotografie inzerátu": "Fotografie inzerátu",
    "Fotografie sú podklad, nie dôkaz skrytého technického stavu.": "Fotografie jsou podklad, ne důkaz skrytého technického stavu.",
    "Identita a VIN": "Identita a VIN",
    "Identita vozidla a VIN": "Identita vozidla a VIN",
    "Jedna analýza, dva pohľady: rýchle rozhodnutie a technický detail.": "Jedna analýza, dva pohledy: rychlé rozhodnutí a technický detail.",
    "JPG, PNG, WebP, BMP alebo AVIF · max. 12 súborov": "JPG, PNG, WebP, BMP nebo AVIF · max. 12 souborů",
    "Každé tvrdenie sa zobrazuje s kategóriou dôkazu, spôsobom overenia a limitmi. Ak dáta chýbajú, report to povie priamo.": "Každé tvrzení se zobrazuje s kategorií důkazu, způsobem ověření a omezeními. Pokud data chybí, report to řekne přímo.",
    "Kompletnú poistnú a servisnú históriu bez dokladov": "Kompletní pojistnou a servisní historii bez dokladů",
    "Kontrola VIN": "Kontrola VIN",
    "Kontrolný zoznam": "Kontrolní seznam",
    "Kontrolný zoznam pred kúpou": "Kontrolní seznam před koupí",
    "Kopírovať": "Kopírovat",
    "Kopírovať správu": "Kopírovat zprávu",
    "Kroky pred obhliadkou": "Kroky před prohlídkou",
    "Kvalitu starších opráv mimo záberov": "Kvalitu starších oprav mimo záběry",
    "Len overené a dostatočne podobné ponuky.": "Pouze ověřené a dostatečně podobné nabídky.",
    "Modelové riziká a praktické kontroly": "Modelová rizika a praktické kontroly",
    "Načítavam technickú analýzu…": "Načítám technickou analýzu…",
    "Nahradiť fyzickú obhliadku a diagnostiku": "Nahradit fyzickou prohlídku a diagnostiku",
    "Nájazd": "Nájezd",
    "Najdôležitejšie zistenia": "Nejdůležitější zjištění",
    "Najprv dostaneš zákaznícky prehľad, potom môžeš otvoriť celý technický report.": "Nejprve dostaneš zákaznický přehled, potom můžeš otevřít celý technický report.",
    "Najprv over. Potom choď na obhliadku.": "Nejprve ověř. Potom jeď na prohlídku.",
    "Najprv podstata, potom dôkazy": "Nejprve podstata, potom důkazy",
    "Najväčšia priorita": "Nejvyšší priorita",
    "Najväčšie plus a riziko": "Největší plus a riziko",
    "Názov auta (voliteľné)": "Název auta (volitelné)",
    "Nedávne analýzy": "Nedávné analýzy",
    "Nová analýza": "Nová analýza",
    "Obsah analýzy": "Obsah analýzy",
    "Očakávané náklady": "Očekávané náklady",
    "Odhad": "Odhad",
    "Odhadované náklady": "Odhadované náklady",
    "Odkaz na inzerát": "Odkaz na inzerát",
    "Orientačný AI screening nenahrádza fyzickú obhliadku vozidla.": "Orientační AI screening nenahrazuje fyzickou prohlídku vozidla.",
    "Otvoriť technický report →": "Otevřít technický report →",
    "Overiteľné porovnateľné ponuky": "Ověřitelné srovnatelné nabídky",
    "Plný generovaný report": "Úplný vygenerovaný report",
    "Plný report": "Úplný report",
    "Plný technický report": "Úplný technický report",
    "Položka": "Položka",
    "Porovnateľné vozidlo": "Srovnatelné vozidlo",
    "Postup": "Postup",
    "Použi podporovaný odkaz alebo vlastný text, cenu a fotografie.": "Použij podporovaný odkaz nebo vlastní text, cenu a fotografie.",
    "Pôvodný odkaz (voliteľné)": "Původní odkaz (volitelné)",
    "Praktický postup pred obhliadkou.": "Praktický postup před prohlídkou.",
    "Prebieha analýza": "Probíhá analýza",
    "Prehľad ti povie čo spraviť teraz. Technický report ukáže prečo.": "Přehled ti řekne, co udělat teď. Technický report ukáže proč.",
    "Pridať fotografie": "Přidat fotografie",
    "Pripravená správa pre predajcu": "Připravená zpráva pro prodejce",
    "Riziká, náklady, trh a zdroje": "Rizika, náklady, trh a zdroje",
    "Rok": "Rok",
    "Rozhodni sa informovane": "Rozhodni se informovaně",
    "Screening nenahrádza fyzickú obhliadku a odbornú diagnostiku.": "Screening nenahrazuje fyzickou prohlídku a odbornou diagnostiku.",
    "Silná stránka": "Silná stránka",
    "Skrytý mechanický stav vozidla": "Skrytý mechanický stav vozidla",
    "Späť na prehľad": "Zpět na přehled",
    "Správa pre predajcu": "Zpráva pro prodejce",
    "Správa predajcovi": "Zpráva prodejci",
    "Spustiť manuálnu analýzu →": "Spustit ruční analýzu →",
    "Spustiť novú analýzu": "Spustit novou analýzu",
    "Systém oddelí fakty, neistoty, modelové riziká, fotografie a trhové dáta.": "Systém oddělí fakta, nejistoty, modelová rizika, fotografie a tržní data.",
    "Technická analýza": "Technická analýza",
    "Technická analýza vozidla": "Technická analýza vozidla",
    "Technické riziká": "Technická rizika",
    "Technický detail": "Technický detail",
    "Tento prehliadač": "Tento prohlížeč",
    "Text inzerátu": "Text inzerátu",
    "To, čo potrebuješ vedieť pred kontaktovaním predajcu.": "To, co potřebuješ vědět před kontaktováním prodejce.",
    "Trh a náklady": "Trh a náklady",
    "Trhový kontext a možné náklady": "Tržní kontext a možné náklady",
    "Tri kroky k jasnému verdiktu": "Tři kroky k jasnému verdiktu",
    "Údaje inzerátu": "Údaje inzerátu",
    "Údaje z inzerátu": "Údaje z inzerátu",
    "Ukážka výsledku": "Ukázka výsledku",
    "Uložiť PDF": "Uložit PDF",
    "Úplnosť a konzistentnosť inzerátu": "Úplnost a konzistentnost inzerátu",
    "Úprimne": "Upřímně",
    "Verdikt": "Verdikt",
    "Viditeľné stopy na dodaných fotografiách": "Viditelné stopy na dodaných fotografiích",
    "Vlastný text a fotky": "Vlastní text a fotky",
    "Vlož inzerát": "Vlož inzerát",
    "Vlož odkaz na inzerát alebo vlastný text s fotkami. Dostaneš zrozumiteľný verdikt, riziká a otázky pre predajcu ešte pred cestou.": "Vlož odkaz na inzerát nebo vlastní text s fotkami. Dostaneš srozumitelný verdikt, rizika a otázky pro prodejce ještě před cestou.",
    "Vstupný servis": "Vstupní servis",
    "Vstupný servis je oddelený od podmienených opráv.": "Vstupní servis je oddělený od podmíněných oprav.",
    "Výskum a overené zdroje": "Výzkum a ověřené zdroje",
    "Výskum a zdroje": "Výzkum a zdroje",
    "Vyskúšať analýzu →": "Vyzkoušet analýzu →",
    "Výsledok": "Výsledek",
    "Výsledok screeningu": "Výsledek screeningu",
    "Zákaznícky prehľad": "Zákaznický přehled",
    "Zákaznícky prehľad analýzy": "Zákaznický přehled analýzy",
    "Zdieľať": "Sdílet",
    "Zdroj": "Zdroj",
    "Zhrnutie": "Shrnutí",
    "Zrušiť": "Zrušit",
  };

  const ui = {
    sk: {
      unavailable: "Nedostupné",
      noData: "Pre túto časť nie je dostatok spoľahlivých údajov.",
      copied: "Skopírované",
      shareCopied: "Odkaz bol skopírovaný",
      recentEmpty: "Zatiaľ nie sú dostupné žiadne uložené analýzy.",
      recentLoading: "Načítavam uložené analýzy…",
      loading: "Načítavam analýzu…",
      expired: "Analýza neexistuje alebo už vypršala.",
      confidence: "Dôveryhodnosť dôkazov",
      high: "Vysoká",
      medium: "Stredná",
      low: "Nízka",
      photos: "fotiek",
      missing: "Chýba",
      source: "Zdroj",
      initialService: "Vstupný servis",
      conditionalRepairs: "Podmienené opravy",
      marketUnavailable: "Nenašli sa aspoň tri dostatočne porovnateľné ponuky. Cenový záver preto nie je dostupný.",
      noCosts: "Spoľahlivý nákladový rozsah nie je dostupný.",
      noPhotos: "Inzerát nemá dostupné fotografie.",
      noRisks: "Neboli dodané štruktúrované technické riziká.",
      report: "Celý generovaný report",
    },
    cs: {
      unavailable: "Nedostupné",
      noData: "Pro tuto část není dostatek spolehlivých údajů.",
      copied: "Zkopírováno",
      shareCopied: "Odkaz byl zkopírován",
      recentEmpty: "Zatím nejsou dostupné žádné uložené analýzy.",
      recentLoading: "Načítám uložené analýzy…",
      loading: "Načítám analýzu…",
      expired: "Analýza neexistuje nebo již vypršela.",
      confidence: "Důvěryhodnost důkazů",
      high: "Vysoká",
      medium: "Střední",
      low: "Nízká",
      photos: "fotografií",
      missing: "Chybí",
      source: "Zdroj",
      initialService: "Vstupní servis",
      conditionalRepairs: "Podmíněné opravy",
      marketUnavailable: "Nebyly nalezeny alespoň tři dostatečně srovnatelné nabídky. Cenový závěr proto není dostupný.",
      noCosts: "Spolehlivý rozsah nákladů není dostupný.",
      noPhotos: "Inzerát nemá dostupné fotografie.",
      noRisks: "Nebyla dodána strukturovaná technická rizika.",
      report: "Celý vygenerovaný report",
    },
    en: {
      unavailable: "Unavailable",
      noData: "There is not enough reliable data for this section.",
      copied: "Copied",
      shareCopied: "Link copied",
      recentEmpty: "There are no saved analyses available yet.",
      recentLoading: "Loading saved analyses…",
      loading: "Loading analysis…",
      expired: "The analysis does not exist or has expired.",
      confidence: "Evidence confidence",
      high: "High",
      medium: "Medium",
      low: "Low",
      photos: "photos",
      missing: "Missing",
      source: "Source",
      initialService: "Initial service",
      conditionalRepairs: "Conditional repairs",
      marketUnavailable: "Fewer than three sufficiently comparable offers were found, so no price conclusion is available.",
      noCosts: "A reliable cost range is unavailable.",
      noPhotos: "The listing has no available photos.",
      noRisks: "No structured technical risks were supplied.",
      report: "Full generated report",
    },
  };

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function language() {
    const stored = localStorage.getItem(STORAGE.language);
    if (SUPPORTED_LANGUAGES.includes(stored)) return stored;
    const languages = Array.isArray(navigator.languages) && navigator.languages.length
      ? navigator.languages
      : [navigator.language || ""];
    const normalized = languages.map((value) => String(value || "").toLowerCase());
    if (normalized.some((value) => value.startsWith("sk"))) return "sk";
    if (normalized.some((value) => value.startsWith("cs") || value.startsWith("cz"))) return "cs";
    const region = normalized.map((value) => value.split("-")[1]).find(Boolean);
    if (region === "sk") return "sk";
    if (region === "cz") return "cs";
    return "en";
  }

  function setLanguage(value) {
    const normalized = String(value || "").toLowerCase();
    const next = normalized === "cs" || normalized === "cz" ? "cs" : normalized === "en" ? "en" : "sk";
    localStorage.setItem(STORAGE.language, next);
    document.documentElement.lang = next;
    document.querySelectorAll("[data-sk][data-en]").forEach((element) => {
      element.textContent = next === "cs"
        ? (element.dataset.cs || czechStatic[element.dataset.sk] || element.dataset.sk)
        : (element.dataset[next] || element.textContent);
    });
    document.querySelectorAll("[data-placeholder-sk][data-placeholder-en]").forEach((element) => {
      element.placeholder = element.dataset[`placeholder${next === "cs" ? "Cs" : next === "en" ? "En" : "Sk"}`] || "";
    });
    document.querySelectorAll("[data-aria-sk][data-aria-en]").forEach((element) => {
      const label = element.dataset[`aria${next === "cs" ? "Cs" : next === "en" ? "En" : "Sk"}`];
      if (label) element.setAttribute("aria-label", label);
    });
    const title = document.documentElement.dataset[`title${next === "cs" ? "Cs" : next === "en" ? "En" : "Sk"}`];
    if (title) document.title = title;
    const description = document.querySelector('meta[name="description"]');
    const localizedDescription = description?.dataset[`content${next === "cs" ? "Cs" : next === "en" ? "En" : "Sk"}`];
    if (localizedDescription) description.content = localizedDescription;
    document.querySelectorAll("[data-language-select]").forEach((element) => {
      element.value = next;
      element.setAttribute("aria-label", next === "sk" ? "Jazyk stránky" : next === "cs" ? "Jazyk stránky" : "Page language");
    });
    if (document.documentElement.dataset.theme) setTheme(document.documentElement.dataset.theme);
    document.dispatchEvent(new CustomEvent("checkni:language", { detail: next }));
    return next;
  }

  function t(key, selectedLanguage) {
    const lang = selectedLanguage || language();
    return ui[lang]?.[key] || ui.sk[key] || key;
  }

  function localize(values, selectedLanguage) {
    const lang = selectedLanguage || language();
    return values?.[lang] || values?.sk || values?.en || "";
  }

  function setTheme(value) {
    const next = value === "light" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem(STORAGE.theme, next);
    document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
      button.textContent = next === "dark" ? "☾" : "☀";
      const lang = language();
      button.setAttribute("aria-label", next === "dark"
        ? (lang === "en" ? "Switch to light theme" : lang === "cs" ? "Přepnout na světlý motiv" : "Prepnúť na svetlú tému")
        : (lang === "en" ? "Switch to dark theme" : lang === "cs" ? "Přepnout na tmavý motiv" : "Prepnúť na tmavú tému"));
    });
  }

  function initializeControls() {
    setTheme(localStorage.getItem(STORAGE.theme) || "dark");
    setLanguage(language());
    document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
      button.addEventListener("click", () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
    });
    document.querySelectorAll("[data-language-select]").forEach((select) => {
      select.addEventListener("change", () => setLanguage(select.value));
    });
  }

  function slugFromPath() {
    const parts = location.pathname.split("/").filter(Boolean);
    const index = parts.indexOf("analysis");
    return index >= 0 && parts[index + 1] ? decodeURIComponent(parts[index + 1]) : "";
  }

  async function fetchPresentation(slug) {
    const response = await fetch(`/api/demo/listings/${encodeURIComponent(slug)}/presentation`, {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.error || t("expired"));
    }
    return response.json();
  }

  function formatMoney(value, selectedLanguage) {
    if (value === null || value === undefined || value === "") return t("unavailable", selectedLanguage);
    const selected = selectedLanguage || language();
    const locale = selected === "en" ? "en-IE" : selected === "cs" ? "cs-CZ" : "sk-SK";
    return new Intl.NumberFormat(locale, { style: "currency", currency: "EUR", maximumFractionDigits: 0 }).format(Number(value));
  }

  function formatNumber(value, selectedLanguage) {
    if (value === null || value === undefined || value === "") return t("unavailable", selectedLanguage);
    const selected = selectedLanguage || language();
    return new Intl.NumberFormat(selected === "en" ? "en-US" : selected === "cs" ? "cs-CZ" : "sk-SK").format(Number(value));
  }

  function formatRange(low, high, selectedLanguage) {
    if (low === null || low === undefined) {
      if (high === null || high === undefined) return t("unavailable", selectedLanguage);
      return formatMoney(high, selectedLanguage);
    }
    if (high === null || high === undefined || Number(low) === Number(high)) return formatMoney(low, selectedLanguage);
    return `${formatMoney(low, selectedLanguage)} – ${formatMoney(high, selectedLanguage)}`;
  }

  function confidenceLabel(value, selectedLanguage) {
    const normalized = String(value || "LOW").toLowerCase();
    return t(normalized === "high" ? "high" : normalized === "medium" ? "medium" : "low", selectedLanguage);
  }

  function rememberAnalysis(slug) {
    if (!slug) return;
    const current = recentSlugs().filter((item) => item !== slug);
    current.unshift(slug);
    localStorage.setItem(STORAGE.recent, JSON.stringify(current.slice(0, 12)));
  }

  function recentSlugs() {
    try {
      const value = JSON.parse(localStorage.getItem(STORAGE.recent) || "[]");
      return Array.isArray(value) ? value.filter((item) => typeof item === "string") : [];
    } catch (_error) {
      return [];
    }
  }

  async function renderRecentAnalyses(container) {
    const localSlugs = recentSlugs();
    container.innerHTML = `<div class="empty-state">${escapeHtml(t("recentLoading"))}</div>`;
    let serverSlugs = [];
    try {
      const response = await fetch("/api/demo/listings", { headers: { Accept: "application/json" } });
      if (response.ok) {
        const listings = await response.json();
        serverSlugs = Array.isArray(listings) ? listings.map((item) => item?.slug).filter(Boolean) : [];
      }
    } catch (_error) {
      serverSlugs = [];
    }
    const slugs = [...new Set([...localSlugs, ...serverSlugs])].slice(0, 12);
    if (!slugs.length) {
      container.innerHTML = `<div class="empty-state">${escapeHtml(t("recentEmpty"))}</div>`;
      return;
    }
    const rows = await Promise.all(slugs.map(async (slug) => {
      try {
        return await fetchPresentation(slug);
      } catch (_error) {
        return null;
      }
    }));
    const valid = rows.filter(Boolean);
    if (valid.length !== slugs.length) {
      localStorage.setItem(STORAGE.recent, JSON.stringify(valid.map((item) => item.listing.slug)));
    }
    if (!valid.length) {
      container.innerHTML = `<div class="empty-state">${escapeHtml(t("recentEmpty"))}</div>`;
      return;
    }
    container.innerHTML = valid.map((item) => {
      const listing = item.listing;
      const image = listing.images?.[0]?.url;
      const thumb = image
        ? `<img src="${escapeHtml(image)}" alt="">`
        : `<span class="recent-thumb" aria-hidden="true"></span>`;
      const meta = [listing.year, listing.price_eur !== null ? formatMoney(listing.price_eur, item.language) : ""].filter(Boolean).join(" · ");
      return `<a class="recent-item" href="/analysis/${encodeURIComponent(listing.slug)}">${thumb}<span><strong>${escapeHtml(listing.title)}</strong><span>${escapeHtml(meta)}</span><span>${escapeHtml(item.verdict.label)}</span></span></a>`;
    }).join("");
  }

  function wireDrawer() {
    const drawer = document.querySelector("[data-recent-drawer]");
    const list = drawer?.querySelector("[data-recent-list]");
    if (!drawer || !list) return;
    const close = () => {
      drawer.classList.remove("open");
      drawer.setAttribute("aria-hidden", "true");
    };
    document.querySelectorAll("[data-open-recent]").forEach((button) => {
      button.addEventListener("click", () => {
        drawer.classList.add("open");
        drawer.setAttribute("aria-hidden", "false");
        renderRecentAnalyses(list);
      });
    });
    drawer.querySelectorAll("[data-close-recent]").forEach((button) => button.addEventListener("click", close));
    drawer.addEventListener("click", (event) => { if (event.target === drawer) close(); });
    document.addEventListener("keydown", (event) => { if (event.key === "Escape") close(); });
  }

  async function writeClipboard(value) {
    const text = String(value || "");
    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(text);
        return;
      } catch (_error) {
        // Fall through to the compatible selection-based copy path.
      }
    }
    const input = document.createElement("textarea");
    input.value = text;
    input.setAttribute("readonly", "");
    input.style.position = "fixed";
    input.style.opacity = "0";
    document.body.appendChild(input);
    input.select();
    document.execCommand("copy");
    input.remove();
  }

  async function copyText(value) {
    await writeClipboard(value);
    toast(t("copied"));
  }

  async function sharePage(title) {
    const data = { title: title || "Checkni Auto", url: location.href };
    if (navigator.share) {
      try { await navigator.share(data); return; } catch (error) { if (error.name === "AbortError") return; }
    }
    await writeClipboard(location.href);
    toast(t("shareCopied"));
  }

  let toastTimer;
  function toast(message) {
    let element = document.querySelector("[data-toast]");
    if (!element) {
      element = document.createElement("div");
      element.className = "toast";
      element.dataset.toast = "";
      document.body.appendChild(element);
    }
    element.textContent = message;
    element.hidden = false;
    element.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => element.classList.remove("show"), 1800);
  }

  function wireLightbox(images) {
    const layer = document.querySelector("[data-lightbox]");
    const target = layer?.querySelector("img");
    if (!layer || !target) return;
    let currentIndex = 0;
    const source = (image) => typeof image === "string" ? image : image?.url;
    const label = (image) => typeof image === "string" ? "Vehicle photo" : image?.filename || "Vehicle photo";
    const show = (index) => {
      if (!images.length) return;
      currentIndex = (index + images.length) % images.length;
      const image = images[currentIndex];
      target.src = source(image);
      target.alt = label(image);
    };
    const close = () => layer.classList.remove("open");
    document.querySelectorAll("[data-photo-index]").forEach((button) => {
      button.addEventListener("click", () => {
        const index = Number(button.dataset.photoIndex);
        const image = images[index];
        if (!image) return;
        show(index);
        layer.classList.add("open");
      });
    });
    layer.querySelector("[data-lightbox-close]")?.addEventListener("click", close);
    layer.querySelector("[data-lightbox-prev]")?.addEventListener("click", () => show(currentIndex - 1));
    layer.querySelector("[data-lightbox-next]")?.addEventListener("click", () => show(currentIndex + 1));
    layer.addEventListener("click", (event) => { if (event.target === layer) close(); });
    document.addEventListener("keydown", (event) => {
      if (!layer.classList.contains("open")) return;
      if (event.key === "Escape") close();
      if (event.key === "ArrowLeft") show(currentIndex - 1);
      if (event.key === "ArrowRight") show(currentIndex + 1);
    });
  }

  function markdownToHtml(markdown) {
    const lines = escapeHtml(markdown || "").split(/\r?\n/);
    const html = [];
    let list = null;
    const closeList = () => { if (list) { html.push(`</${list}>`); list = null; } };
    const inline = (value) => value
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '$1 <span class="markdown-url">($2)</span>');
    const cells = (value) => value.replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());
    for (let index = 0; index < lines.length; index += 1) {
      const rawLine = lines[index];
      const line = rawLine.trim();
      if (!line) { closeList(); continue; }
      const heading = line.match(/^(#{1,3})\s+(.+)$/);
      if (heading) { closeList(); const level = heading[1].length + 1; html.push(`<h${level}>${inline(heading[2])}</h${level}>`); continue; }
      if (line.includes("|") && index + 1 < lines.length && /^\s*\|?\s*:?-{3,}/.test(lines[index + 1])) {
        closeList();
        const headers = cells(line);
        index += 1;
        const rows = [];
        while (index + 1 < lines.length && lines[index + 1].trim().includes("|")) {
          index += 1;
          rows.push(cells(lines[index]));
        }
        html.push(`<div class="markdown-table-wrap"><table><thead><tr>${headers.map((cell) => `<th>${inline(cell)}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${row.map((cell) => `<td>${inline(cell)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
        continue;
      }
      const bullet = line.match(/^[-*]\s+(.+)$/);
      const ordered = line.match(/^\d+\.\s+(.+)$/);
      if (bullet || ordered) {
        const type = bullet ? "ul" : "ol";
        if (list !== type) { closeList(); list = type; html.push(`<${type}>`); }
        html.push(`<li>${inline((bullet || ordered)[1])}</li>`);
        continue;
      }
      closeList();
      if (line.startsWith("&gt;")) html.push(`<blockquote>${inline(line.slice(4).trim())}</blockquote>`);
      else html.push(`<p>${inline(line)}</p>`);
    }
    closeList();
    return html.join("");
  }

  window.Checkni = {
    escapeHtml,
    language,
    setLanguage,
    t,
    localize,
    setTheme,
    initializeControls,
    slugFromPath,
    fetchPresentation,
    formatMoney,
    formatNumber,
    formatRange,
    confidenceLabel,
    rememberAnalysis,
    recentSlugs,
    renderRecentAnalyses,
    wireDrawer,
    copyText,
    sharePage,
    toast,
    wireLightbox,
    markdownToHtml,
  };

  document.addEventListener("DOMContentLoaded", () => {
    initializeControls();
    wireDrawer();
  });
})();
