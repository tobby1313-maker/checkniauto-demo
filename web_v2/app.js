(() => {
  "use strict";

  const translations = {
    sk: {
      pageTitle: "Checkni Auto V2 — kontrola auta pred obhliadkou",
      navReport: "Obsah reportu",
      navCta: "Preveriť auto",
      eyebrow: "Rozhodovací report pred obhliadkou",
      heroTitle: "Zistite, či sa oplatí ísť auto pozrieť.",
      heroLead: "Jedna analýza spojí údaje z inzerátu, fotografie, typické technické riziká a dostupné trhové podklady do jasného verdiktu.",
      heroPoint1: "Konkrétne zistenia z fotografií",
      heroPoint2: "Dôkaz a istota pri každom riziku",
      heroPoint3: "Otázky predajcovi a checklist obhliadky",
      targetText: "cieľový čas; výsledok prežije obnovenie stránky",
      analyzerTitle: "Preverte inzerát",
      betaFree: "beta zdarma",
      urlTab: "Odkaz",
      manualTab: "Vlastné údaje",
      urlLabel: "Odkaz na inzerát",
      supported: "Autobazar.eu, Autobazar.sk, Bazoš.sk a Bazoš.cz",
      start: "Spustiť analýzu",
      carTitle: "Názov auta",
      priceForm: "Cena",
      sourceUrl: "Zdrojový odkaz (voliteľné)",
      listingText: "Text inzerátu a servisné údaje",
      listingPlaceholder: "Skopírujte popis, výbavu, VIN a servisnú históriu...",
      uploadTitle: "Pridať fotografie",
      uploadHelp: "Maximálne {count} obrázkov",
      startManual: "Analyzovať vlastné údaje",
      privacy: "API kľúče zostávajú na serveri. Report nenahrádza VIN databázu ani fyzickú kontrolu.",
      progressKicker: "Prebieha kontrola",
      progressTitle: "Analyzujeme vozidlo",
      stage1: "Načítanie inzerátu",
      stage2: "Kontrola úplnosti údajov",
      stage3: "Analýza fotografií",
      stage4: "Technické a trhové overenie",
      stage5: "Zostavenie reportu",
      preview: "Načítané vozidlo",
      waiting: "Čakám na údaje",
      priceWord: "Cena",
      yearWord: "Rok",
      mileageWord: "Najazdené",
      qualityWord: "Úplnosť",
      refreshSafe: "Stránku môžete obnoviť. Stav sa priebežne ukladá.",
      result: "Výsledok kontroly",
      download: "Dáta JSON",
      pdf: "Uložiť PDF",
      new: "Nová analýza",
      confidence: "Istota",
      confidenceHelp: "podľa kvality podkladov",
      completeness: "Úplnosť inzerátu",
      reserve: "Rezerva na 30 000 km",
      estimate: "orientačný odhad",
      photos: "Fotografie",
      summary: "Rozhodnutie za 30 sekúnd",
      priority: "Priorita",
      findings: "Najdôležitejšie zistenia",
      priceNegotiation: "Cena a vyjednávanie",
      negotiation: "Argumenty na vyjednávanie",
      transparency: "Transparentnosť",
      missing: "Čo v inzeráte chýba",
      visual: "Vizuálna kontrola",
      photoTitle: "Čo ukázali fotografie",
      notShown: "Čo na fotkách chýba",
      buffer: "Finančný vankúš",
      costs: "Odhad nákladov na 30 000 km",
      item: "Položka",
      reason: "Prečo",
      when: "Kedy",
      amount: "Odhad",
      beforeCall: "Pred telefonátom",
      questions: "Otázky pre predajcu",
      copy: "Kopírovať",
      inspection: "Na obhliadke",
      checklist: "Kontrolný zoznam",
      evidence: "Dôkazy",
      sourcesLimits: "Webové zdroje a limity",
      sources: "Použité zdroje",
      limits: "Limity analýzy",
      whatKicker: "Nie generický AI text",
      whatTitle: "Každé zistenie vedie ku konkrétnemu kroku.",
      f1: "Fotografie",
      f1t: "Viditeľné poškodenia, opotrebenie a chýbajúce zábery.",
      f2: "Technika",
      f2t: "Riziká konkrétnej generácie, motora a prevodovky.",
      f3: "Cena",
      f3t: "Porovnania len vtedy, keď existujú použiteľné zdroje.",
      f4: "Akčný plán",
      f4t: "Otázky, červené odpovede a checklist obhliadky.",
      footer: "Orientačný prvý filter. Pred kúpou využite nezávislú fyzickú kontrolu.",
      top: "Hore",
      plannedPrice: "plánovaná cena",
      betaLabel: "beta zdarma",
      openLabel: "test zdarma",
      queued: "Analýza je zaradená.",
      invalidUrl: "Vložte platný odkaz na podporovaný inzerát.",
      genericError: "Požiadavku sa nepodarilo spracovať.",
      connectionError: "Spojenie bolo prerušené. Stav analýzy kontrolujem znova.",
      failedTitle: "Analýzu sa nepodarilo dokončiť",
      failedAction: "Skontrolujte vstup alebo použite manuálny režim. Pri platenom režime sa kredit nemá odpočítať.",
      noData: "Údaj nie je dostupný",
      noFindings: "Nie sú dostupné konkrétne zistenia.",
      noPhotos: "Fotografie neboli vyhodnotené.",
      noSources: "Webové zdroje neboli dostupné.",
      noLimitations: "Neboli uvedené ďalšie limity.",
      completeData: "Základné kľúčové údaje sú v inzeráte uvedené.",
      reviewed: "vyhodnotených",
      notReviewed: "nevyhodnotené",
      missingCount: "Chýba {count} dôležitých údajov",
      evidenceLabel: "Dôkaz",
      confidenceLabel: "Istota",
      actionLabel: "Čo urobiť",
      impactLabel: "Finančný dopad",
      whyLabel: "Prečo",
      redFlagLabel: "Varovná odpoveď",
      severityInfo: "informácia",
      severityWatch: "overiť",
      severityRisk: "riziko",
      severityCritical: "kritické",
      confidenceHigh: "vysoká",
      confidenceMedium: "stredná",
      confidenceLow: "nízka",
      evidenceListing: "inzerát",
      evidencePhoto: "fotografia",
      evidenceWeb: "web",
      evidenceGeneral: "všeobecná znalosť",
      evidenceEstimate: "odhad",
      evidenceManual: "manuálne overiť",
      priceGood: "Výhodná cena",
      priceFair: "Primeraná cena",
      priceHigh: "Skôr vysoká cena",
      priceLow: "Podozrivo nízka cena",
      priceUnknown: "Cena nebola spoľahlivo overená",
      marketMin: "Spodok trhu",
      marketMax: "Vrchná hranica",
      recommendedMax: "Odporúčané maximum",
      urgencyNow: "hneď",
      urgencySoon: "čoskoro",
      urgencyReserve: "rezerva",
      urgencyUnknown: "nejasné",
      copied: "Skopírované",
      reportDefaultTitle: "Report vozidla",
      vin: "VIN",
      engine: "Motor",
      transmission: "Prevodovka",
      drivetrain: "Pohon",
      fuel: "Palivo",
      power: "Výkon",
      filesSelected: "Vybrané fotografie: {count}",
      maxFiles: "Vyberte najviac {count} fotografií.",
      costUnavailable: "Náklady neboli vyčíslené",
      sourceSupports: "Podklad",
      photoRefs: "Fotografie",
      newAnalysisConfirm: "Rozpracovaná analýza zostane uložená, ale formulár sa vyčistí. Pokračovať?"
    },
    cs: {
      pageTitle: "Checkni Auto V2 — kontrola auta před prohlídkou",
      navReport: "Obsah reportu",
      navCta: "Prověřit auto",
      eyebrow: "Rozhodovací report před prohlídkou",
      heroTitle: "Zjistěte, zda se vyplatí jet auto prohlédnout.",
      heroLead: "Jedna analýza spojí údaje z inzerátu, fotografie, typická technická rizika a dostupné tržní podklady do jasného verdiktu.",
      heroPoint1: "Konkrétní zjištění z fotografií",
      heroPoint2: "Důkaz a jistota u každého rizika",
      heroPoint3: "Otázky pro prodejce a checklist prohlídky",
      targetText: "cílový čas; výsledek přežije obnovení stránky",
      analyzerTitle: "Prověřte inzerát",
      betaFree: "beta zdarma",
      urlTab: "Odkaz",
      manualTab: "Vlastní údaje",
      urlLabel: "Odkaz na inzerát",
      supported: "Autobazar.eu, Autobazar.sk, Bazoš.sk a Bazoš.cz",
      start: "Spustit analýzu",
      carTitle: "Název auta",
      priceForm: "Cena",
      sourceUrl: "Zdrojový odkaz (volitelné)",
      listingText: "Text inzerátu a servisní údaje",
      listingPlaceholder: "Zkopírujte popis, výbavu, VIN a servisní historii...",
      uploadTitle: "Přidat fotografie",
      uploadHelp: "Maximálně {count} obrázků",
      startManual: "Analyzovat vlastní údaje",
      privacy: "API klíče zůstávají na serveru. Report nenahrazuje VIN databázi ani fyzickou kontrolu.",
      progressKicker: "Probíhá kontrola",
      progressTitle: "Analyzujeme vozidlo",
      stage1: "Načtení inzerátu",
      stage2: "Kontrola úplnosti údajů",
      stage3: "Analýza fotografií",
      stage4: "Technické a tržní ověření",
      stage5: "Sestavení reportu",
      preview: "Načtené vozidlo",
      waiting: "Čekám na údaje",
      priceWord: "Cena",
      yearWord: "Rok",
      mileageWord: "Najeto",
      qualityWord: "Úplnost",
      refreshSafe: "Stránku můžete obnovit. Stav se průběžně ukládá.",
      result: "Výsledek kontroly",
      download: "Data JSON",
      pdf: "Uložit PDF",
      new: "Nová analýza",
      confidence: "Jistota",
      confidenceHelp: "podle kvality podkladů",
      completeness: "Úplnost inzerátu",
      reserve: "Rezerva na 30 000 km",
      estimate: "orientační odhad",
      photos: "Fotografie",
      summary: "Rozhodnutí za 30 sekund",
      priority: "Priorita",
      findings: "Nejdůležitější zjištění",
      priceNegotiation: "Cena a vyjednávání",
      negotiation: "Argumenty pro vyjednávání",
      transparency: "Transparentnost",
      missing: "Co v inzerátu chybí",
      visual: "Vizuální kontrola",
      photoTitle: "Co ukázaly fotografie",
      notShown: "Co na fotografiích chybí",
      buffer: "Finanční polštář",
      costs: "Odhad nákladů na 30 000 km",
      item: "Položka",
      reason: "Proč",
      when: "Kdy",
      amount: "Odhad",
      beforeCall: "Před telefonátem",
      questions: "Otázky pro prodejce",
      copy: "Kopírovat",
      inspection: "Na prohlídce",
      checklist: "Kontrolní seznam",
      evidence: "Důkazy",
      sourcesLimits: "Webové zdroje a limity",
      sources: "Použité zdroje",
      limits: "Limity analýzy",
      whatKicker: "Ne generický AI text",
      whatTitle: "Každé zjištění vede ke konkrétnímu kroku.",
      f1: "Fotografie",
      f1t: "Viditelná poškození, opotřebení a chybějící záběry.",
      f2: "Technika",
      f2t: "Rizika konkrétní generace, motoru a převodovky.",
      f3: "Cena",
      f3t: "Srovnání jen tehdy, když existují použitelné zdroje.",
      f4: "Akční plán",
      f4t: "Otázky, varovné odpovědi a checklist prohlídky.",
      footer: "Orientační první filtr. Před koupí využijte nezávislou fyzickou kontrolu.",
      top: "Nahoru",
      plannedPrice: "plánovaná cena",
      betaLabel: "beta zdarma",
      openLabel: "test zdarma",
      queued: "Analýza je zařazena.",
      invalidUrl: "Vložte platný odkaz na podporovaný inzerát.",
      genericError: "Požadavek se nepodařilo zpracovat.",
      connectionError: "Spojení bylo přerušeno. Stav analýzy kontroluji znovu.",
      failedTitle: "Analýzu se nepodařilo dokončit",
      failedAction: "Zkontrolujte vstup nebo použijte ruční režim. V placeném režimu se kredit nemá odečíst.",
      noData: "Údaj není dostupný",
      noFindings: "Nejsou dostupná konkrétní zjištění.",
      noPhotos: "Fotografie nebyly vyhodnoceny.",
      noSources: "Webové zdroje nebyly dostupné.",
      noLimitations: "Nebyly uvedeny další limity.",
      completeData: "Základní klíčové údaje jsou v inzerátu uvedeny.",
      reviewed: "vyhodnocených",
      notReviewed: "nevyhodnoceno",
      missingCount: "Chybí {count} důležitých údajů",
      evidenceLabel: "Důkaz",
      confidenceLabel: "Jistota",
      actionLabel: "Co udělat",
      impactLabel: "Finanční dopad",
      whyLabel: "Proč",
      redFlagLabel: "Varovná odpověď",
      severityInfo: "informace",
      severityWatch: "ověřit",
      severityRisk: "riziko",
      severityCritical: "kritické",
      confidenceHigh: "vysoká",
      confidenceMedium: "střední",
      confidenceLow: "nízká",
      evidenceListing: "inzerát",
      evidencePhoto: "fotografie",
      evidenceWeb: "web",
      evidenceGeneral: "obecná znalost",
      evidenceEstimate: "odhad",
      evidenceManual: "ručně ověřit",
      priceGood: "Výhodná cena",
      priceFair: "Přiměřená cena",
      priceHigh: "Spíše vysoká cena",
      priceLow: "Podezřele nízká cena",
      priceUnknown: "Cena nebyla spolehlivě ověřena",
      marketMin: "Spodní část trhu",
      marketMax: "Horní hranice",
      recommendedMax: "Doporučené maximum",
      urgencyNow: "hned",
      urgencySoon: "brzy",
      urgencyReserve: "rezerva",
      urgencyUnknown: "nejasné",
      copied: "Zkopírováno",
      reportDefaultTitle: "Report vozidla",
      vin: "VIN",
      engine: "Motor",
      transmission: "Převodovka",
      drivetrain: "Pohon",
      fuel: "Palivo",
      power: "Výkon",
      filesSelected: "Vybrané fotografie: {count}",
      maxFiles: "Vyberte nejvýše {count} fotografií.",
      costUnavailable: "Náklady nebyly vyčísleny",
      sourceSupports: "Podklad",
      photoRefs: "Fotografie",
      newAnalysisConfirm: "Rozpracovaná analýza zůstane uložená, ale formulář se vyčistí. Pokračovat?"
    }
  };

  const state = {
    language: localStorage.getItem("checkni-v2-language") === "cs" ? "cs" : "sk",
    config: {
      max_manual_images: 10,
      price_eur: 1.99,
      access_mode: "beta",
      checkout_enabled: false
    },
    activeJobId: null,
    currentJobStatus: null,
    eventSource: null,
    pollTimer: null,
    report: null,
    completedStages: new Set()
  };

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => Array.from(document.querySelectorAll(selector));

  function tr(key, replacements = {}) {
    let value = translations[state.language]?.[key] ?? translations.sk[key] ?? key;
    for (const [name, replacement] of Object.entries(replacements)) {
      value = value.replaceAll(`{${name}}`, String(replacement));
    }
    return value;
  }

  function createElement(tag, className = "", text = "") {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== "" && text !== null && text !== undefined) {
      node.textContent = String(text);
    }
    return node;
  }

  function setChildren(node, children) {
    node.replaceChildren(...children.filter(Boolean));
  }

  function asArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function asObject(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  function clamp(value, min = 0, max = 100) {
    const number = Number(value);
    if (!Number.isFinite(number)) return min;
    return Math.max(min, Math.min(max, Math.round(number)));
  }

  function locale() {
    return state.language === "cs" ? "cs-CZ" : "sk-SK";
  }

  function formatInteger(value) {
    const number = Number(value);
    return Number.isFinite(number) && number > 0
      ? new Intl.NumberFormat(locale(), { maximumFractionDigits: 0 }).format(number)
      : "—";
  }

  function formatCurrency(value, currency = "EUR") {
    const number = Number(value);
    if (!Number.isFinite(number) || number <= 0) return "—";
    const normalizedCurrency = currency === "CZK" ? "CZK" : "EUR";
    return new Intl.NumberFormat(locale(), {
      style: "currency",
      currency: normalizedCurrency,
      maximumFractionDigits: 0
    }).format(number);
  }

  function formatRange(minimum, maximum, currency = "EUR") {
    const min = Number(minimum);
    const max = Number(maximum);
    if (!Number.isFinite(min) || !Number.isFinite(max) || min <= 0 || max <= 0) {
      return tr("costUnavailable");
    }
    if (Math.round(min) === Math.round(max)) return formatCurrency(min, currency);
    return `${formatCurrency(min, currency)} – ${formatCurrency(max, currency)}`;
  }

  function applyLanguage(language) {
    state.language = language === "cs" ? "cs" : "sk";
    localStorage.setItem("checkni-v2-language", state.language);
    document.documentElement.lang = state.language;
    document.title = tr("pageTitle");
    $("#languageSelect").value = state.language;

    $$('[data-i18n]').forEach((node) => {
      const key = node.dataset.i18n;
      if (key) node.textContent = tr(key);
    });
    $$('[data-i18n-placeholder]').forEach((node) => {
      const key = node.dataset.i18nPlaceholder;
      if (key) node.setAttribute("placeholder", tr(key));
    });

    updateConfigUi();
    renderSelectedFiles();
    if (state.report) renderReport(state.report, false);
  }

  async function loadConfig() {
    try {
      const response = await fetch("/api/v2/config", {
        headers: { Accept: "application/json" },
        cache: "no-store"
      });
      if (response.ok) state.config = { ...state.config, ...(await response.json()) };
    } catch (_) {
      // Defaults keep the beta usable during a transient config request failure.
    }
    updateConfigUi();
  }

  function updateConfigUi() {
    const price = Number(state.config.price_eur || 1.99);
    $("#priceLabel").textContent = new Intl.NumberFormat(locale(), {
      style: "currency",
      currency: "EUR",
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    }).format(price);
    const accessText = state.config.access_mode === "beta" ? tr("betaLabel") : tr("openLabel");
    $("#accessLabel").textContent = state.config.checkout_enabled
      ? tr("plannedPrice")
      : accessText;
    $("#uploadHelp").textContent = tr("uploadHelp", {
      count: state.config.max_manual_images || 10
    });
  }

  function setMode(mode) {
    const manual = mode === "manual";
    $("#urlForm").classList.toggle("hidden", manual);
    $("#manualForm").classList.toggle("hidden", !manual);
    $("#urlTab").classList.toggle("active", !manual);
    $("#manualTab").classList.toggle("active", manual);
    $("#urlTab").setAttribute("aria-selected", String(!manual));
    $("#manualTab").setAttribute("aria-selected", String(manual));
    hideFormError();
  }

  function setBusy(isBusy) {
    $$("#urlForm button[type='submit'], #manualForm button[type='submit']").forEach((button) => {
      button.disabled = isBusy;
    });
  }

  function showFormError(message) {
    const node = $("#formError");
    node.textContent = message || tr("genericError");
    node.classList.remove("hidden");
  }

  function hideFormError() {
    const node = $("#formError");
    node.textContent = "";
    node.classList.add("hidden");
  }

  async function readJson(response) {
    try {
      return await response.json();
    } catch (_) {
      return {};
    }
  }

  async function createUrlJob(event) {
    event.preventDefault();
    hideFormError();
    const url = $("#listingUrl").value.trim();
    try {
      const parsed = new URL(url);
      if (!/^https?:$/.test(parsed.protocol)) throw new Error("invalid protocol");
    } catch (_) {
      showFormError(tr("invalidUrl"));
      return;
    }

    setBusy(true);
    try {
      const response = await fetch("/api/v2/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ url, language: state.language })
      });
      const payload = await readJson(response);
      if (!response.ok) throw new Error(payload.error || tr("genericError"));
      beginTracking(payload);
    } catch (error) {
      showFormError(error.message || tr("genericError"));
    } finally {
      setBusy(false);
    }
  }

  async function createManualJob(event) {
    event.preventDefault();
    hideFormError();
    const formData = new FormData(event.currentTarget);
    formData.set("language", state.language);
    const limit = Number(state.config.max_manual_images || 10);
    if (($("#manualImages").files || []).length > limit) {
      showFormError(tr("maxFiles", { count: limit }));
      return;
    }

    setBusy(true);
    try {
      const response = await fetch("/api/v2/jobs/manual", {
        method: "POST",
        headers: { Accept: "application/json" },
        body: formData
      });
      const payload = await readJson(response);
      if (!response.ok) throw new Error(payload.error || tr("genericError"));
      beginTracking(payload);
    } catch (error) {
      showFormError(error.message || tr("genericError"));
    } finally {
      setBusy(false);
    }
  }

  function closeTrackingConnections() {
    if (state.eventSource) {
      state.eventSource.close();
      state.eventSource = null;
    }
    if (state.pollTimer) {
      clearTimeout(state.pollTimer);
      state.pollTimer = null;
    }
  }

  function beginTracking(job) {
    closeTrackingConnections();
    state.activeJobId = job.id;
    state.currentJobStatus = job.status || "queued";
    state.report = null;
    state.completedStages.clear();
    localStorage.setItem("checkni-v2-job", job.id);
    $("#reportSection").classList.add("hidden");
    $("#progressSection").classList.remove("hidden");
    $(".progress-card").classList.remove("failed");
    handleJob(job);
    $("#progressSection").scrollIntoView({ behavior: "smooth", block: "start" });
    openEventStream(job.id);
  }

  function openEventStream(jobId) {
    if (!("EventSource" in window)) {
      schedulePoll(jobId, 300);
      return;
    }
    const source = new EventSource(`/api/v2/jobs/${encodeURIComponent(jobId)}/events`);
    state.eventSource = source;
    let receivedEvent = false;

    const receive = (event) => {
      receivedEvent = true;
      try {
        handleJob(JSON.parse(event.data));
      } catch (_) {
        // Ignore malformed network events and let the next persisted state replace them.
      }
    };
    source.addEventListener("update", receive);
    source.addEventListener("complete", receive);
    source.addEventListener("failed", receive);
    source.onerror = () => {
      source.close();
      if (state.eventSource === source) state.eventSource = null;
      if (state.currentJobStatus !== "done" && state.currentJobStatus !== "failed") {
        if (!receivedEvent) $("#progressMessage").textContent = tr("connectionError");
        schedulePoll(jobId, 900);
      }
    };
  }

  function schedulePoll(jobId, delay = 1800) {
    if (state.pollTimer || state.currentJobStatus === "done" || state.currentJobStatus === "failed") return;
    state.pollTimer = setTimeout(async () => {
      state.pollTimer = null;
      try {
        const response = await fetch(`/api/v2/jobs/${encodeURIComponent(jobId)}`, {
          headers: { Accept: "application/json" },
          cache: "no-store"
        });
        if (response.status === 404) {
          localStorage.removeItem("checkni-v2-job");
          return;
        }
        if (response.ok) handleJob(await response.json());
      } catch (_) {
        $("#progressMessage").textContent = tr("connectionError");
      }
      if (state.currentJobStatus !== "done" && state.currentJobStatus !== "failed") {
        schedulePoll(jobId, 1800);
      }
    }, delay);
  }

  function handleJob(job) {
    if (!job || typeof job !== "object") return;
    if (job.id) state.activeJobId = job.id;
    state.currentJobStatus = job.status || state.currentJobStatus;
    if (job.id) localStorage.setItem("checkni-v2-job", job.id);
    updateProgress(job);

    if (job.status === "done" && job.report) {
      closeTrackingConnections();
      renderReport(job.report, true);
    } else if (job.status === "failed") {
      closeTrackingConnections();
      renderFailure(job);
    }
  }

  function updateProgress(job) {
    const progress = clamp(job.progress || 0);
    $("#progressPercent").textContent = `${progress} %`;
    $("#progressBar").style.width = `${progress}%`;
    $("#progressMessage").textContent = job.message || tr("queued");
    updateStages(job.stage, progress);
    if (job.listing_preview) updatePreview(job.listing_preview);
  }

  function updateStages(stage, progress) {
    if (stage === "photos" || stage === "research") state.completedStages.add(stage);
    if (stage === "complete" || progress >= 100) {
      ["scraping", "normalizing", "photos", "research", "synthesis"].forEach((item) => state.completedStages.add(item));
    }

    const current = new Set();
    if (["queued", "starting", "scraping"].includes(stage)) current.add("scraping");
    if (stage === "normalizing") current.add("normalizing");
    if (stage === "analysis") {
      current.add("photos");
      current.add("research");
    }
    if (stage === "photos") current.add("research");
    if (stage === "research") current.add("photos");
    if (stage === "synthesis") current.add("synthesis");

    if (progress >= 28) state.completedStages.add("scraping");
    if (progress >= 42) state.completedStages.add("normalizing");
    if (progress >= 72) {
      state.completedStages.add("photos");
      state.completedStages.add("research");
    }

    $$('[data-stage]').forEach((node) => {
      const name = node.dataset.stage;
      node.classList.toggle("done", state.completedStages.has(name));
      node.classList.toggle("active", !state.completedStages.has(name) && current.has(name));
    });
  }

  function updatePreview(preview) {
    const price = asObject(preview.price);
    const quality = asObject(preview.data_quality);
    $("#previewTitle").textContent = preview.title || tr("waiting");
    $("#previewPrice").textContent = formatCurrency(price.amount, price.currency);
    $("#previewYear").textContent = preview.year || "—";
    $("#previewMileage").textContent = preview.mileage_km
      ? `${formatInteger(preview.mileage_km)} km`
      : "—";
    $("#previewCompleteness").textContent = Number.isFinite(Number(quality.score))
      ? `${clamp(quality.score)} %`
      : "—";
  }

  function renderFailure(job) {
    $("#progressSection").classList.remove("hidden");
    $(".progress-card").classList.add("failed");
    $("#progressPercent").textContent = "—";
    $("#progressBar").style.width = "100%";
    $("#progressMessage").textContent = job.error?.message || tr("genericError");
    const heading = $("#progressSection h2");
    heading.textContent = tr("failedTitle");
    showFormError(tr("failedAction"));
  }

  function severityLabel(value) {
    return tr({
      info: "severityInfo",
      watch: "severityWatch",
      risk: "severityRisk",
      critical: "severityCritical"
    }[value] || "severityWatch");
  }

  function confidenceLabel(value) {
    return tr({ high: "confidenceHigh", medium: "confidenceMedium", low: "confidenceLow" }[value] || "confidenceLow");
  }

  function evidenceLabel(value) {
    return tr({
      listing: "evidenceListing",
      photo: "evidencePhoto",
      web: "evidenceWeb",
      general_knowledge: "evidenceGeneral",
      estimate: "evidenceEstimate",
      manual_check: "evidenceManual"
    }[value] || "evidenceManual");
  }

  function urgencyLabel(value) {
    return tr({
      now: "urgencyNow",
      soon: "urgencySoon",
      reserve: "urgencyReserve",
      unknown: "urgencyUnknown"
    }[value] || "urgencyUnknown");
  }

  function priceStatusLabel(value) {
    return tr({
      good: "priceGood",
      fair: "priceFair",
      high: "priceHigh",
      low: "priceLow",
      unknown: "priceUnknown"
    }[value] || "priceUnknown");
  }

  function detailRow(label, value) {
    const row = createElement("div");
    row.append(createElement("dt", "", label), createElement("dd", "", value || "—"));
    return row;
  }

  function renderReport(report, scroll = true) {
    state.report = report;
    state.currentJobStatus = "done";
    const vehicle = asObject(report.vehicle);
    const verdict = asObject(report.verdict);
    const quality = asObject(report.data_quality);
    const photo = asObject(report.photo_analysis);
    const costs = asObject(report.ownership_costs);

    $("#progressSection").classList.add("hidden");
    $("#reportSection").classList.remove("hidden");
    $("#reportTitle").textContent = vehicle.title || report.headline || tr("reportDefaultTitle");
    $("#verdictCard").dataset.level = verdict.level || "yellow";
    $("#verdictLabel").textContent = report.verdict_label || String(verdict.level || "").toUpperCase();
    $("#verdictSentence").textContent = verdict.one_sentence || report.headline || "—";
    $("#verdictRecommendation").textContent = verdict.recommendation || "—";
    const safety = clamp(verdict.safety_score);
    $("#scoreRing").style.setProperty("--score", String(safety));
    $("#safetyScore").textContent = String(safety);

    renderVehicleFacts(vehicle);
    $("#confidenceMetric").textContent = `${clamp(verdict.confidence)} %`;
    $("#completenessMetric").textContent = `${clamp(quality.score)} %`;
    const missing = asArray(quality.missing_critical);
    $("#completenessText").textContent = missing.length
      ? tr("missingCount", { count: missing.length })
      : tr("completeData");
    $("#costMetric").textContent = formatRange(costs.total_min_eur, costs.total_max_eur, "EUR");
    const reviewed = Number(photo.images_reviewed || 0);
    $("#photosMetric").textContent = reviewed > 0 ? String(reviewed) : "—";
    $("#photosText").textContent = reviewed > 0 ? tr("reviewed") : tr("notReviewed");
    $("#executiveSummary").textContent = report.executive_summary || report.headline || "—";

    renderFindings(asArray(report.top_findings));
    renderPrice(asObject(report.price_assessment));
    renderQuality(quality);
    renderPhotos(photo);
    renderCosts(costs);
    renderQuestions(asArray(report.seller_questions));
    renderChecklist(asArray(report.inspection_checklist));
    renderSources(asObject(report.research), asArray(report.limitations));
    $("#disclaimer").textContent = report.disclaimer || "";

    if (scroll) $("#reportSection").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function renderVehicleFacts(vehicle) {
    const price = asObject(vehicle.price);
    const facts = [
      vehicle.year ? `${tr("yearWord")}: ${vehicle.year}` : null,
      vehicle.mileage_km ? `${tr("mileageWord")}: ${formatInteger(vehicle.mileage_km)} km` : null,
      price.amount ? `${tr("priceWord")}: ${formatCurrency(price.amount, price.currency)}` : null,
      vehicle.engine ? `${tr("engine")}: ${vehicle.engine}` : null,
      vehicle.power_kw ? `${tr("power")}: ${formatInteger(vehicle.power_kw)} kW` : null,
      vehicle.fuel ? `${tr("fuel")}: ${vehicle.fuel}` : null,
      vehicle.transmission ? `${tr("transmission")}: ${vehicle.transmission}` : null,
      vehicle.drivetrain ? `${tr("drivetrain")}: ${vehicle.drivetrain}` : null,
      vehicle.vin ? `${tr("vin")}: ${vehicle.vin}` : null
    ].filter(Boolean);
    setChildren($("#vehicleStrip"), facts.map((fact) => createElement("span", "", fact)));
  }

  function renderFindings(findings) {
    $("#findingsCount").textContent = String(findings.length);
    if (!findings.length) {
      setChildren($("#findingsGrid"), [createElement("p", "", tr("noFindings"))]);
      return;
    }
    const cards = findings.map((finding) => {
      const card = createElement("article", "finding");
      card.dataset.severity = finding.severity || "watch";
      const top = createElement("div", "finding-top");
      top.append(
        createElement("span", "pill", finding.category || tr("findings")),
        createElement("span", "pill", severityLabel(finding.severity))
      );
      const title = createElement("h4", "", finding.title || tr("noData"));
      const summary = createElement("p", "", finding.summary || "");
      const details = createElement("dl");
      const references = asArray(finding.evidence_refs).filter(Boolean).join(", ");
      details.append(
        detailRow(tr("evidenceLabel"), `${evidenceLabel(finding.evidence_type)}${references ? ` — ${references}` : ""}`),
        detailRow(tr("confidenceLabel"), confidenceLabel(finding.confidence)),
        detailRow(tr("actionLabel"), finding.action || tr("noData")),
        detailRow(tr("impactLabel"), formatRange(finding.cost_min_eur, finding.cost_max_eur, "EUR"))
      );
      card.append(top, title, summary, details);
      return card;
    });
    setChildren($("#findingsGrid"), cards);
  }

  function renderPrice(price) {
    $("#priceStatus").textContent = priceStatusLabel(price.status);
    $("#priceSummary").textContent = price.summary || tr("priceUnknown");
    const currency = price.currency || "EUR";
    const values = [
      [tr("marketMin"), price.market_min],
      [tr("marketMax"), price.market_max],
      [tr("recommendedMax"), price.recommended_max]
    ];
    setChildren(
      $("#priceRange"),
      values.map(([label, value]) => {
        const box = createElement("div");
        box.append(createElement("span", "", label), createElement("strong", "", formatCurrency(value, currency)));
        return box;
      })
    );
    const points = asArray(price.negotiation_points).filter(Boolean);
    setChildren(
      $("#negotiationList"),
      points.length ? points.map((point) => createElement("li", "", point)) : [createElement("li", "", tr("noData"))]
    );
  }

  function renderQuality(quality) {
    const score = clamp(quality.score);
    $("#qualityMeter").style.width = `${score}%`;
    const missing = asArray(quality.missing).filter(Boolean);
    setChildren(
      $("#missingList"),
      missing.length
        ? missing.map((item) => createElement("li", "", item))
        : [createElement("li", "", tr("completeData"))]
    );
  }

  function renderPhotos(photo) {
    const findings = asArray(photo.findings);
    $("#photoSummary").textContent = photo.summary || tr("noPhotos");
    setChildren(
      $("#photoFindings"),
      findings.length
        ? findings.map((finding) => {
            const card = createElement("article", "photo-finding");
            card.dataset.severity = finding.severity || "watch";
            const top = createElement("div", "finding-top");
            top.append(
              createElement("span", "pill", severityLabel(finding.severity)),
              createElement("span", "pill", confidenceLabel(finding.confidence))
            );
            const title = createElement("h4", "", finding.title || tr("photoTitle"));
            const observation = createElement("p", "", finding.observation || "");
            card.append(top, title, observation);
            if (finding.interpretation) card.append(createElement("p", "", finding.interpretation));
            const refs = asArray(finding.photo_refs).filter(Boolean);
            if (refs.length) card.append(createElement("small", "", `${tr("photoRefs")}: ${refs.join(", ")}`));
            if (finding.action) card.append(createElement("p", "", `${tr("actionLabel")}: ${finding.action}`));
            return card;
          })
        : [createElement("p", "", tr("noFindings"))]
    );

    const gaps = asArray(photo.coverage_gaps).filter(Boolean);
    $("#coverageBox").classList.toggle("hidden", gaps.length === 0);
    setChildren($("#coverageList"), gaps.map((gap) => createElement("li", "", gap)));
  }

  function renderCosts(costs) {
    $("#costsTotal").textContent = formatRange(costs.total_min_eur, costs.total_max_eur, "EUR");
    $("#costsSummary").textContent = costs.summary || tr("costUnavailable");
    const rows = asArray(costs.items).map((item) => {
      const row = createElement("tr");
      row.append(
        createElement("td", "", item.item || "—"),
        createElement("td", "", item.reason || "—"),
        createElement("td", "", urgencyLabel(item.urgency)),
        createElement("td", "", formatRange(item.min_eur, item.max_eur, "EUR"))
      );
      return row;
    });
    if (!rows.length) {
      const row = createElement("tr");
      const cell = createElement("td", "", tr("costUnavailable"));
      cell.colSpan = 4;
      row.append(cell);
      rows.push(row);
    }
    setChildren($("#costTableBody"), rows);
  }

  function renderQuestions(questions) {
    const items = questions.map((item) => {
      const li = createElement("li");
      li.append(createElement("strong", "", item.question || "—"));
      if (item.why_it_matters) li.append(createElement("small", "", `${tr("whyLabel")}: ${item.why_it_matters}`));
      if (item.red_flag_answer) li.append(createElement("small", "", `${tr("redFlagLabel")}: ${item.red_flag_answer}`));
      return li;
    });
    setChildren($("#questionList"), items.length ? items : [createElement("li", "", tr("noData"))]);
    $("#copyQuestionsButton").disabled = questions.length === 0;
  }

  function renderChecklist(groups) {
    const blocks = groups.map((group) => {
      const wrapper = createElement("section", "check-group");
      wrapper.append(createElement("h4", "", group.group || tr("checklist")));
      asArray(group.items).forEach((text) => {
        const label = createElement("label");
        const input = document.createElement("input");
        input.type = "checkbox";
        label.append(input, createElement("span", "", text));
        wrapper.append(label);
      });
      return wrapper;
    });
    setChildren($("#checklistGroups"), blocks.length ? blocks : [createElement("p", "", tr("noData"))]);
  }

  function safeUrl(value) {
    try {
      const parsed = new URL(value);
      return ["http:", "https:"].includes(parsed.protocol) ? parsed.href : null;
    } catch (_) {
      return null;
    }
  }

  function renderSources(research, limitations) {
    const sources = asArray(research.sources);
    const sourceItems = sources.map((source) => {
      const li = createElement("li");
      const href = safeUrl(source.url);
      if (href) {
        const link = createElement("a", "", source.title || href);
        link.href = href;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        li.append(link);
      } else {
        li.append(createElement("span", "", source.title || tr("noData")));
      }
      if (source.supports) li.append(createElement("small", "", `${tr("sourceSupports")}: ${source.supports}`));
      return li;
    });
    setChildren(
      $("#sourceList"),
      sourceItems.length ? sourceItems : [createElement("li", "", tr("noSources"))]
    );
    setChildren(
      $("#limitationsList"),
      limitations.length
        ? limitations.map((item) => createElement("li", "", item))
        : [createElement("li", "", tr("noLimitations"))]
    );
  }

  async function restoreLastJob() {
    const jobId = localStorage.getItem("checkni-v2-job");
    if (!jobId || !/^[0-9a-f]{32}$/.test(jobId)) return;
    try {
      const response = await fetch(`/api/v2/jobs/${encodeURIComponent(jobId)}`, {
        headers: { Accept: "application/json" },
        cache: "no-store"
      });
      if (response.status === 404) {
        localStorage.removeItem("checkni-v2-job");
        return;
      }
      if (!response.ok) return;
      const job = await response.json();
      state.activeJobId = job.id;
      state.currentJobStatus = job.status;
      if (job.status === "done" && job.report) {
        renderReport(job.report, false);
      } else if (job.status === "failed") {
        $("#progressSection").classList.remove("hidden");
        updateProgress(job);
        renderFailure(job);
      } else {
        beginTracking(job);
      }
    } catch (_) {
      // The analyzer remains usable when restoration is temporarily unavailable.
    }
  }

  function resetForNewAnalysis() {
    if (state.currentJobStatus === "running" && !window.confirm(tr("newAnalysisConfirm"))) return;
    closeTrackingConnections();
    state.activeJobId = null;
    state.currentJobStatus = null;
    state.report = null;
    state.completedStages.clear();
    localStorage.removeItem("checkni-v2-job");
    $("#reportSection").classList.add("hidden");
    $("#progressSection").classList.add("hidden");
    $(".progress-card").classList.remove("failed");
    $("#progressSection h2").textContent = tr("progressTitle");
    $("#progressPercent").textContent = "0 %";
    $("#progressBar").style.width = "0%";
    $("#progressMessage").textContent = tr("queued");
    $("#listingUrl").value = "";
    $("#manualForm").reset();
    renderSelectedFiles();
    setMode("url");
    $("#analyzer").scrollIntoView({ behavior: "smooth", block: "center" });
    setTimeout(() => $("#listingUrl").focus(), 350);
  }

  async function copyQuestions() {
    const questions = asArray(state.report?.seller_questions);
    if (!questions.length) return;
    const text = questions.map((item, index) => `${index + 1}. ${item.question}`).join("\n");
    try {
      await navigator.clipboard.writeText(text);
      const button = $("#copyQuestionsButton");
      const previous = button.textContent;
      button.textContent = tr("copied");
      setTimeout(() => {
        button.textContent = previous;
      }, 1500);
    } catch (_) {
      // Clipboard access can be blocked; report data remains visible.
    }
  }

  function renderSelectedFiles() {
    const input = $("#manualImages");
    if (!input) return;
    const files = Array.from(input.files || []);
    const target = $("#fileList");
    if (!files.length) {
      target.textContent = "";
      return;
    }
    const summary = createElement("strong", "", tr("filesSelected", { count: files.length }));
    const chips = createElement("div", "file-chips");
    files.forEach((file) => chips.append(createElement("span", "file-chip", file.name)));
    setChildren(target, [summary, chips]);
  }

  function enforceFileLimit() {
    const input = $("#manualImages");
    const files = Array.from(input.files || []);
    const limit = Number(state.config.max_manual_images || 10);
    if (files.length <= limit) {
      renderSelectedFiles();
      return true;
    }
    const transfer = new DataTransfer();
    files.slice(0, limit).forEach((file) => transfer.items.add(file));
    input.files = transfer.files;
    showFormError(tr("maxFiles", { count: limit }));
    renderSelectedFiles();
    return false;
  }

  function setupUploadZone() {
    const zone = $("#uploadZone");
    const input = $("#manualImages");
    input.addEventListener("change", enforceFileLimit);
    ["dragenter", "dragover"].forEach((name) => {
      zone.addEventListener(name, (event) => {
        event.preventDefault();
        zone.classList.add("dragging");
      });
    });
    ["dragleave", "drop"].forEach((name) => {
      zone.addEventListener(name, (event) => {
        event.preventDefault();
        zone.classList.remove("dragging");
      });
    });
    zone.addEventListener("drop", (event) => {
      const files = Array.from(event.dataTransfer?.files || []);
      const transfer = new DataTransfer();
      files
        .filter((file) => /^image\//.test(file.type))
        .slice(0, Number(state.config.max_manual_images || 10))
        .forEach((file) => transfer.items.add(file));
      input.files = transfer.files;
      enforceFileLimit();
    });
  }

  function bindEvents() {
    $("#languageSelect").addEventListener("change", (event) => applyLanguage(event.target.value));
    $("#urlTab").addEventListener("click", () => setMode("url"));
    $("#manualTab").addEventListener("click", () => setMode("manual"));
    $("#urlForm").addEventListener("submit", createUrlJob);
    $("#manualForm").addEventListener("submit", createManualJob);
    $("#newAnalysisButton").addEventListener("click", resetForNewAnalysis);
    $("#printButton").addEventListener("click", () => window.print());
    $("#downloadJsonButton").addEventListener("click", () => {
      if (state.activeJobId) {
        window.location.assign(`/api/v2/jobs/${encodeURIComponent(state.activeJobId)}/report`);
      }
    });
    $("#copyQuestionsButton").addEventListener("click", copyQuestions);
    setupUploadZone();
  }

  async function init() {
    applyLanguage(state.language);
    bindEvents();
    await loadConfig();
    await restoreLastJob();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
