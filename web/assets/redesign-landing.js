(function () {
  "use strict";
  const $ = (selector) => document.querySelector(selector);
  let abortController = null;
  let activeMode = "url";
  let selectedFiles = [];
  let previewUrls = [];
  let highestProgress = 5;

  function setMode(mode) {
    activeMode = mode === "manual" ? "manual" : "url";
    document.querySelectorAll("[data-mode]").forEach((tab) => {
      const active = tab.dataset.mode === activeMode;
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-selected", String(active));
    });
    $("#urlForm").classList.toggle("hidden", activeMode !== "url");
    $("#manualForm").classList.toggle("hidden", activeMode !== "manual");
  }

  function localizedServerError(message) {
    const text = String(message || "");
    const exact = {
      "URL is required.": { sk: "URL adresa je povinná.", cs: "URL adresa je povinná.", en: text },
      "Enter a valid http(s) listing URL.": { sk: "Zadaj platnú http(s) adresu inzerátu.", cs: "Zadej platnou http(s) adresu inzerátu.", en: text },
      "Automatic mobile.de scraping is not available. Use manual listing mode.": { sk: "Automatické načítanie z mobile.de nie je dostupné. Použi manuálny režim.", cs: "Automatické načtení z mobile.de není dostupné. Použij ruční režim.", en: text },
      "This marketplace is not supported for automatic scraping. Use manual listing mode.": { sk: "Tento portál nepodporujeme automaticky. Použi manuálny režim.", cs: "Tento portál nepodporujeme automaticky. Použij ruční režim.", en: text },
      "Another demo analysis is already running. Try again in a moment.": { sk: "Práve prebieha iná demo analýza. Skús to znova o chvíľu.", cs: "Právě probíhá jiná demo analýza. Zkus to znovu za chvíli.", en: text },
      "Scraping timed out.": { sk: "Načítanie inzerátu prekročilo časový limit.", cs: "Načtení inzerátu překročilo časový limit.", en: text },
      "Scraper finished but did not create listing data.": { sk: "Načítanie sa skončilo bez údajov inzerátu.", cs: "Načtení skončilo bez údajů inzerátu.", en: text },
      "Gemini API keys are not configured on the server.": { sk: "Na serveri nie sú nastavené kľúče Gemini API.", cs: "Na serveru nejsou nastavené klíče Gemini API.", en: text },
    };
    if (exact[text]) return Checkni.localize(exact[text]);

    const dynamic = [
      [/^Demo limit reached \((.+)\)\. Try again later\.$/, { sk: "Demo limit bol vyčerpaný ($1). Skús to neskôr.", cs: "Demo limit byl vyčerpán ($1). Zkus to později.", en: text }],
      [/^Failed to start scraper: (.+)$/s, { sk: "Načítanie inzerátu sa nepodarilo spustiť: $1", cs: "Načtení inzerátu se nepodařilo spustit: $1", en: text }],
      [/^Scraper failed with exit code (.+)\.$/, { sk: "Načítanie inzerátu zlyhalo s kódom $1.", cs: "Načtení inzerátu selhalo s kódem $1.", en: text }],
      [/^Manual listing import failed: (.+)$/s, { sk: "Manuálny import inzerátu zlyhal: $1", cs: "Ruční import inzerátu selhal: $1", en: text }],
      [/^Analysis failed: (.+)$/s, { sk: "Analýza zlyhala: $1", cs: "Analýza selhala: $1", en: text }],
    ];
    for (const [pattern, translations] of dynamic) {
      const match = text.match(pattern);
      if (!match) continue;
      const localized = Checkni.localize(translations);
      return localized.replace("$1", match[1]);
    }
    return text;
  }

  function showError(form, message) {
    const target = form.querySelector("[data-form-error]");
    target.textContent = localizedServerError(message);
    target.classList.toggle("hidden", !message);
  }

  async function showDebuggingBundleLink(form, slug) {
    if (!slug) return;
    try {
      const adminCheck = await fetch("/api/token-usage?limit=1", {
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      if (!adminCheck.ok) return;
    } catch (_error) {
      return;
    }
    const target = form.querySelector("[data-form-error]");
    const link = document.createElement("a");
    link.className = "button ghost small debug-download";
    link.href = `/api/admin/debugging-bundles/${encodeURIComponent(slug)}`;
    link.download = "";
    link.textContent = Checkni.localize({
      sk: "Stiahnuť diagnostický balík",
      cs: "Stáhnout diagnostický balíček",
      en: "Download debugging bundle",
    });
    target.append(document.createElement("br"), link);
    target.classList.remove("hidden");
  }

  function syncSelectedFiles() {
    const input = $("#manualImages");
    if (typeof DataTransfer === "undefined") return;
    const transfer = new DataTransfer();
    selectedFiles.forEach((file) => transfer.items.add(file));
    input.files = transfer.files;
  }

  function updateFileLabel() {
    const count = selectedFiles.length;
    $("[data-file-label]").textContent = count
      ? `${count} / 12 ${Checkni.t("photos")}`
      : Checkni.localize({
        sk: "JPG, PNG, WebP, BMP alebo AVIF · max. 12 súborov",
        cs: "JPG, PNG, WebP, BMP nebo AVIF · max. 12 souborů",
        en: "JPG, PNG, WebP, BMP or AVIF · max. 12 files",
      });
  }

  function renderFilePreviews() {
    previewUrls.forEach((url) => URL.revokeObjectURL(url));
    previewUrls = [];
    const target = $("[data-file-previews]");
    target.innerHTML = selectedFiles.map((file, index) => {
      const url = URL.createObjectURL(file);
      previewUrls.push(url);
      const removeLabel = Checkni.localize({ sk: "Odstrániť fotografiu", cs: "Odebrat fotografii", en: "Remove photo" });
      return `<div class="upload-preview"><img src="${url}" alt="${Checkni.escapeHtml(file.name)}"><button type="button" data-remove-file="${index}" aria-label="${removeLabel}">×</button></div>`;
    }).join("");
    target.querySelectorAll("[data-remove-file]").forEach((button) => button.addEventListener("click", () => {
      selectedFiles.splice(Number(button.dataset.removeFile), 1);
      syncSelectedFiles();
      renderFilePreviews();
      updateFileLabel();
    }));
  }

  function progressStage(status) {
    const value = String(status || "").toLowerCase();
    const message = (sk, cs, en) => Checkni.localize({ sk, cs, en });
    if (/ready|done|complete/.test(value)) return { value: 100, message: message("Analýza je pripravená a otvárame tvoj výsledok.", "Analýza je připravená a otevíráme tvůj výsledek.", "Your analysis is ready and we are opening the result.") };
    if (/final|report|synthesis/.test(value)) return { value: 92, message: message("Pripravujeme jasný prehľad všetkého, čo sme zistili.", "Připravujeme jasný přehled všeho, co jsme zjistili.", "We are preparing a clear summary of everything we found.") };
    if (/risk|score/.test(value)) return { value: 82, message: message("Triedime veci, ktoré sa oplatí preveriť s predajcom.", "Třídíme věci, které se vyplatí prověřit s prodejcem.", "We are sorting the things worth checking with the seller.") };
    if (/vision|photo|image/.test(value)) return { value: 70, message: message("Na fotografiách hľadáme viditeľné detaily a chýbajúce zábery.", "Na fotografiích hledáme viditelné detaily a chybějící záběry.", "We are checking the photos for visible details and missing views.") };
    if (/market|compar/.test(value)) return { value: 57, message: message("Porovnávame ponúkanú cenu s podobnými autami na trhu.", "Porovnáváme nabízenou cenu s podobnými auty na trhu.", "We are comparing the asking price with similar cars on the market.") };
    if (/research|web/.test(value)) return { value: 44, message: message("Overujeme užitočné informácie k modelu a inzerátu.", "Ověřujeme užitečné informace k modelu a inzerátu.", "We are checking useful information about this model and listing.") };
    if (/identity|component/.test(value)) return { value: 28, message: message("Triedime údaje o aute dôležité pred obhliadkou.", "Třídíme údaje o autě důležité před prohlídkou.", "We are organising the vehicle details that matter before a viewing.") };
    if (/scrap|listing|manual/.test(value)) return { value: 14, message: message("Zbierame údaje a fotografie z inzerátu.", "Shromažďujeme údaje a fotografie z inzerátu.", "We are collecting the details and photos from the listing.") };
    return { value: 5, message: message("Chystáme všetko, aby sme mohli začať s analýzou.", "Připravujeme vše potřebné, abychom mohli začít s analýzou.", "We are getting everything ready to start the analysis.") };
  }

  function openProgress() {
    const overlay = $("[data-progress-overlay]");
    overlay.classList.add("open");
    overlay.setAttribute("aria-hidden", "false");
    $("[data-progress-road]").classList.remove("is-complete");
    highestProgress = 5;
    updateProgress("starting");
    $("[data-cancel-analysis]").disabled = false;
  }

  function closeProgress() {
    const overlay = $("[data-progress-overlay]");
    overlay.classList.remove("open");
    overlay.setAttribute("aria-hidden", "true");
  }

  function updateProgress(status) {
    const stage = progressStage(status);
    highestProgress = Math.max(highestProgress, stage.value);
    $("[data-progress-status]").textContent = stage.message;
    $("[data-progress-percent]").textContent = `${highestProgress}%`;
    $("[data-progress-road]").style.setProperty("--journey-progress", `${highestProgress}%`);
  }

  function completeJourney() {
    updateProgress("Done");
    $("[data-progress-road]").classList.add("is-complete");
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    return new Promise((resolve) => window.setTimeout(resolve, reducedMotion ? 220 : 1050));
  }

  async function consumeSse(response) {
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      const error = new Error(payload.error || `Request failed (${response.status})`);
      error.unsupported = payload.unsupported === true;
      throw error;
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let slug = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split("\n\n");
      buffer = events.pop() || "";
      for (const event of events) {
        const line = event.split("\n").find((item) => item.startsWith("data: "));
        if (!line) continue;
        const raw = line.slice(6);
        if (raw === "[DONE]") {
          if (!slug) throw new Error(Checkni.localize({ sk: "Analýza sa skončila bez uloženého výsledku.", cs: "Analýza skončila bez uloženého výsledku.", en: "Analysis finished without a saved result." }));
          Checkni.rememberAnalysis(slug);
          await completeJourney();
          location.assign(`/analysis/${encodeURIComponent(slug)}`);
          return;
        }
        const data = JSON.parse(raw);
        if (data.error) {
          const error = new Error(data.error);
          error.slug = data.slug || slug;
          throw error;
        }
        if (data.slug) slug = data.slug;
        updateProgress(data.status);
      }
    }
    throw new Error(Checkni.localize({ sk: "Spojenie sa ukončilo pred dokončením analýzy.", cs: "Spojení se ukončilo před dokončením analýzy.", en: "Connection ended before the analysis completed." }));
  }

  async function submitUrl(event) {
    event.preventDefault();
    const form = event.currentTarget;
    showError(form, "");
    openProgress();
    abortController = new AbortController();
    try {
      const response = await fetch("/api/demo/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: abortController.signal,
        body: JSON.stringify({ url: $("#listingUrl").value.trim(), output_language: Checkni.language() }),
      });
      await consumeSse(response);
    } catch (error) {
      closeProgress();
      if (error.name === "AbortError") return;
      showError(form, error.message);
      await showDebuggingBundleLink(form, error.slug);
      if (error.unsupported || /manual|not supported|mobile\.de/i.test(error.message)) {
        $("#sourceUrl").value = $("#listingUrl").value.trim();
        setMode("manual");
      }
    }
  }

  async function submitManual(event) {
    event.preventDefault();
    const form = event.currentTarget;
    showError(form, "");
    const files = $("#manualImages").files;
    if (files.length > 12) {
      showError(form, Checkni.localize({ sk: "Vyber najviac 12 fotografií.", cs: "Vyber nejvýše 12 fotografií.", en: "Select at most 12 images." }));
      return;
    }
    openProgress();
    abortController = new AbortController();
    try {
      const data = new FormData(form);
      data.set("output_language", Checkni.language());
      const response = await fetch("/api/demo/analyze-manual", { method: "POST", body: data, signal: abortController.signal });
      await consumeSse(response);
    } catch (error) {
      closeProgress();
      if (error.name === "AbortError") return;
      showError(form, error.message);
      await showDebuggingBundleLink(form, error.slug);
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    const legacySlug = new URLSearchParams(location.search).get("analysis");
    if (legacySlug) {
      location.replace(`/analysis/${encodeURIComponent(legacySlug)}`);
      return;
    }
    document.querySelectorAll("[data-mode]").forEach((tab) => tab.addEventListener("click", () => setMode(tab.dataset.mode)));
    $("#urlForm").addEventListener("submit", submitUrl);
    $("#manualForm").addEventListener("submit", submitManual);
    $("#manualImages").addEventListener("change", (event) => {
      const files = Array.from(event.target.files || []);
      selectedFiles = files.slice(0, 12);
      syncSelectedFiles();
      renderFilePreviews();
      updateFileLabel();
      showError($("#manualForm"), files.length > 12
        ? Checkni.localize({ sk: "Vybraných bolo iba prvých 12 fotografií.", cs: "Bylo vybráno pouze prvních 12 fotografií.", en: "Only the first 12 images were selected." })
        : "");
    });
    $("[data-cancel-analysis]").addEventListener("click", () => {
      $("[data-cancel-analysis]").disabled = true;
      abortController?.abort();
      closeProgress();
    });
  });
})();
