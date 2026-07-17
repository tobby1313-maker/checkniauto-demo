(function () {
  "use strict";

  const C = window.Checkni;
  const $ = (selector, root = document) => root.querySelector(selector);
  const all = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const slug = C.slugFromPath();
  let payload;

  const l = (lang, sk, cs, en) => C.localize({ sk, cs, en }, lang);
  const na = (lang) => l(lang, "Nie je k dispozícii", "Není k dispozici", "Not available");
  const scalar = (value, lang) => {
    if (value === null || value === undefined || value === "") return na(lang);
    if (typeof value !== "object") return String(value);
    const label = value.value || value.label || value.marketing_name || value.name || value.type || value.family || value.status || "";
    const details = [value.code && value.code !== label ? value.code : "", value.resolution, value.confidence].filter(Boolean);
    return [label, details.length ? `(${details.join(" · ")})` : ""].filter(Boolean).join(" ") || na(lang);
  };
  const grid = (rows, lang) => rows.map(([label, value]) => `<div><dt>${C.escapeHtml(label)}</dt><dd>${C.escapeHtml(scalar(value, lang))}</dd></div>`).join("");
  const list = (items, empty) => {
    if (!Array.isArray(items) || !items.length) return `<p class="empty-state">${C.escapeHtml(empty)}</p>`;
    return `<ul class="evidence-list">${items.map((item) => {
      if (typeof item === "string") return `<li>${C.escapeHtml(item)}</li>`;
      const title = item.title || item.label || item.name || item.finding || item.issue || item.claim || "";
      const detail = item.detail || item.description || item.reason || item.evidence || item.buyer_impact || "";
      return `<li>${title ? `<strong>${C.escapeHtml(title)}</strong>` : ""}${detail ? `<span>${C.escapeHtml(detail)}</span>` : ""}</li>`;
    }).join("")}</ul>`;
  };

  function render(model) {
    payload = model;
    const lang = ["sk", "cs", "en"].includes(model.language) ? model.language : "sk";
    const empty = na(lang);
    const listing = model.listing || {};
    const verdict = model.verdict || {};
    const identity = model.identity || {};
    const vin = model.vin || {};
    const market = model.market || {};
    const photos = Array.isArray(listing.images) ? listing.images : [];
    const mileage = listing.mileage_km !== null && listing.mileage_km !== undefined ? `${C.formatNumber(listing.mileage_km, lang)} km` : empty;

    document.title = `${listing.title || "Technická analýza"} | Checkni Auto`;
    $("[data-report-title]").textContent = listing.title || empty;
    $("[data-report-meta]").textContent = [listing.year, mileage, listing.location].filter(Boolean).join(" · ");
    $("[data-verdict-label]").textContent = verdict.label || empty;
    $("[data-verdict-summary]").textContent = verdict.summary || empty;
    $("[data-verdict-banner]").classList.add(verdict.tone || "warn");
    $("[data-summary-link]").href = `/analysis/${encodeURIComponent(slug)}`;

    const facts = [
      [l(lang, "Cena", "Cena", "Price"), C.formatMoney(listing.price_eur, lang)],
      [l(lang, "Nájazd", "Nájezd", "Mileage"), mileage],
      [l(lang, "Palivo", "Palivo", "Fuel"), listing.fuel],
      [l(lang, "Prevodovka", "Převodovka", "Transmission"), listing.transmission]
    ];
    $("[data-fact-strip]").innerHTML = facts.map(([label, value]) => `<div><small>${C.escapeHtml(label)}</small><strong>${C.escapeHtml(value || empty)}</strong></div>`).join("");
    $("[data-pros]").innerHTML = list(model.pros, empty);
    $("[data-priority-findings]").innerHTML = list(model.priority_findings, empty);

    $("[data-listing-data]").innerHTML = grid([
      [l(lang, "Názov", "Název", "Title"), listing.title],
      [l(lang, "Cena", "Cena", "Price"), C.formatMoney(listing.price_eur, lang)],
      [l(lang, "Rok", "Rok", "Year"), listing.year],
      [l(lang, "Nájazd", "Nájezd", "Mileage"), mileage],
      [l(lang, "Palivo", "Palivo", "Fuel"), listing.fuel],
      [l(lang, "Motor", "Motor", "Engine"), listing.engine],
      [l(lang, "Prevodovka", "Převodovka", "Transmission"), listing.transmission],
      [l(lang, "Pohon", "Pohon", "Drivetrain"), listing.drivetrain],
      [l(lang, "Lokalita", "Lokalita", "Location"), listing.location],
      [l(lang, "Zdroj", "Zdroj", "Source"), listing.source_url]
    ], lang);
    $("[data-identity]").innerHTML = grid([
      [l(lang, "Značka", "Značka", "Make"), identity.make],
      ["Model", identity.model],
      [l(lang, "Generácia", "Generace", "Generation"), identity.generation],
      [l(lang, "Motor", "Motor", "Engine"), identity.engine],
      [l(lang, "Prevodovka", "Převodovka", "Transmission"), identity.transmission],
      [l(lang, "Pohon", "Pohon", "Drivetrain"), identity.drivetrain],
      [l(lang, "Spoľahlivosť", "Spolehlivost", "Confidence"), identity.confidence_label]
    ], lang);
    const identityNotes = [
      ...(identity.notes || []),
      ...(identity.candidate_variants || []).map((item) => [
        [item.engine_code, item.transmission_code].filter(Boolean).join(" / "),
        item.reason
      ].filter(Boolean).join(": "))
    ].filter(Boolean);
    const identityNoteCard = $("[data-identity-notes]");
    identityNoteCard.hidden = !identityNotes.length;
    identityNoteCard.innerHTML = identityNotes.length ? `<h3>${l(lang, "Limity identity a možné varianty", "Omezení identity a možné varianty", "Identity limitations and alternatives")}</h3>${list(identityNotes, empty)}` : "";
    $("[data-vin]").innerHTML = list([
      { title: "VIN", detail: listing.vin || empty },
      { title: l(lang, "Kontrola formátu", "Kontrola formátu", "Format check"), detail: vin.format_check || empty },
      { title: l(lang, "Online história", "Online historie", "Online history"), detail: vin.online_history || empty },
      { title: l(lang, "Dekódované údaje", "Rozpoznané údaje", "Decoded information"), detail: vin.decoded_information || vin.notes || empty }
    ], empty);
    const safety = model.safety_and_recall || {};
    $("[data-safety]").innerHTML = list(Object.entries(safety).filter(([, value]) => typeof value === "string" && value).map(([key, value]) => ({ title: key.replaceAll("_", " "), detail: value })), empty);

    $("[data-research-findings]").innerHTML = list(model.research_findings, empty);
    const sources = Array.isArray(model.sources) ? model.sources : [];
    $("[data-sources]").innerHTML = sources.length ? sources.map((source) => `<a class="source-card" href="${C.escapeHtml(source.url)}" target="_blank" rel="noopener noreferrer"><span>${C.escapeHtml(source.name || source.url)}<small>${C.escapeHtml([source.type, source.reliability, source.used_for].filter(Boolean).join(" · "))}</small></span><b>↗</b></a>`).join("") : `<p class="empty-state">${C.escapeHtml(empty)}</p>`;

    const risks = Array.isArray(model.technical_risks) ? model.technical_risks : [];
    $("[data-risks]").innerHTML = risks.length ? risks.map((risk, index) => `<article class="risk-card ${String(risk.risk_level || "").toLowerCase()}"><span class="risk-index">${String(index + 1).padStart(2, "0")}</span><div><div class="risk-head"><span>${C.escapeHtml([risk.risk_level, risk.evidence_category, risk.confidence].filter(Boolean).join(" · "))}</span></div><h3>${C.escapeHtml([risk.component, risk.issue].filter(Boolean).join(" — ") || empty)}</h3><div class="risk-fields"><div><span>${l(lang, "Dopad na kupujúceho", "Dopad na kupujícího", "Buyer impact")}</span><p>${C.escapeHtml(risk.buyer_impact || empty)}</p></div><div><span>${l(lang, "Dôkaz pre toto vozidlo", "Důkaz pro toto vozidlo", "Vehicle-specific evidence")}</span><p>${C.escapeHtml(risk.specific_vehicle_evidence || empty)}</p></div><div><span>${l(lang, "Ako overiť", "Jak ověřit", "How to verify")}</span><p>${C.escapeHtml(risk.verification_action || empty)}</p></div></div>${risk.low_eur !== null || risk.high_eur !== null ? `<small class="risk-estimate">${C.escapeHtml(C.formatRange(risk.low_eur, risk.high_eur, lang))}</small>` : ""}</div></article>`).join("") : `<p class="empty-state">${C.escapeHtml(empty)}</p>`;

    const marketFacts = [
      [l(lang, "Cena inzerátu", "Cena inzerátu", "Asking price"), C.formatMoney(market.advertised_price_eur ?? listing.price_eur, lang)],
      [l(lang, "Overený medián", "Ověřený medián", "Verified median"), C.formatMoney(market.median_eur, lang)],
      [l(lang, "Pozícia na trhu", "Pozice na trhu", "Market position"), market.price_view],
      [l(lang, "Kvalita podkladov", "Kvalita podkladů", "Evidence quality"), market.available ? C.confidenceLabel(market.confidence, lang) : empty]
    ];
    $("[data-market-summary]").innerHTML = marketFacts.map(([label, value]) => `<div class="metric-card"><small>${C.escapeHtml(label)}</small><strong>${C.escapeHtml(value || empty)}</strong></div>`).join("");
    $("[data-market-limitations]").innerHTML = market.limitations?.length ? `<strong>${l(lang, "Limity:", "Omezení:", "Limitations:")}</strong> ${C.escapeHtml(market.limitations.join(" "))}` : "";
    const comparables = Array.isArray(market.comparables) ? market.comparables : [];
    $("[data-comparables]").innerHTML = comparables.length ? comparables.map((item) => `<tr><td><a href="${C.escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">${C.escapeHtml(item.title || empty)}</a></td><td>${C.escapeHtml(C.formatMoney(item.price_eur, lang))}</td><td>${C.escapeHtml(item.year || empty)}</td><td>${C.escapeHtml(item.mileage_km !== null && item.mileage_km !== undefined ? `${C.formatNumber(item.mileage_km, lang)} km` : empty)}</td></tr>`).join("") : `<tr><td colspan="4">${C.escapeHtml(empty)}</td></tr>`;
    const costs = Array.isArray(model.costs?.items) ? model.costs.items : [];
    $("[data-costs]").innerHTML = costs.length ? costs.map((cost) => `<tr><td>${C.escapeHtml(cost.item || empty)}</td><td>${C.escapeHtml(C.formatRange(cost.low_eur, cost.high_eur, lang))}</td><td>${C.escapeHtml(cost.why || empty)}</td></tr>`).join("") : `<tr><td colspan="3">${C.escapeHtml(empty)}</td></tr>`;

    $("[data-photo-gallery]").innerHTML = photos.length ? photos.map((photo, index) => `<button class="technical-photo" type="button" data-photo-index="${index}"><img src="${C.escapeHtml(photo.url)}" alt="${C.escapeHtml(l(lang, "Fotografia vozidla ", "Fotografie vozidla ", "Vehicle photo ") + (index + 1))}" loading="lazy"><span>${String(index + 1).padStart(2, "0")}</span></button>`).join("") : `<p class="empty-state">${C.escapeHtml(empty)}</p>`;
    const vision = model.vision || {};
    const observations = [vision.visual_verdict, ...(vision.supported_observations || []), ...(vision.exterior_observations || []), ...(vision.interior_observations || []), ...(vision.warning_lights || []), ...(vision.visible_red_flags || [])].filter(Boolean);
    const visionLimits = [...(vision.photo_limitations || []), ...(vision.missing_views || []).map((item) => `${l(lang, "Chýbajúci pohľad", "Chybějící pohled", "Missing view")}: ${item}`)];
    $("[data-vision]").innerHTML = `<h3>${l(lang, "Vizuálne zistenia", "Vizuální zjištění", "Visual findings")}</h3>${list(observations, empty)}${visionLimits.length ? `<p class="method-note">${C.escapeHtml(visionLimits.join(" "))}</p>` : ""}`;
    C.wireLightbox(photos);

    const fallbackActions = lang === "en"
      ? ["Verify VIN and ownership documents.", "Arrange an independent mechanical inspection.", "Confirm service history and repair invoices."]
      : lang === "cs"
        ? ["Ověřte VIN a doklady k vozidlu.", "Domluvte nezávislou mechanickou prohlídku.", "Potvrďte servisní historii a faktury za opravy."]
        : ["Overte VIN a doklady k vozidlu.", "Dohodnite nezávislú mechanickú prehliadku.", "Potvrďte servisnú históriu a faktúry za opravy."];
    const actions = Array.isArray(model.buyer_actions) && model.buyer_actions.length ? model.buyer_actions : fallbackActions;
    const checklistKey = `checkni-checklist-${slug}`;
    let checked = {};
    try { checked = JSON.parse(localStorage.getItem(checklistKey) || "{}"); } catch (_) { checked = {}; }
    $("[data-checklist]").innerHTML = actions.map((action, index) => `<label class="checklist-item"><input type="checkbox" data-check-item="${index}" ${checked[index] ? "checked" : ""}><span>${C.escapeHtml(action)}</span></label>`).join("");
    all("[data-check-item]").forEach((input) => input.addEventListener("change", () => {
      checked[input.dataset.checkItem] = input.checked;
      localStorage.setItem(checklistKey, JSON.stringify(checked));
    }));
    $("[data-markdown-report]").innerHTML = model.report_markdown ? C.markdownToHtml(model.report_markdown) : `<p class="empty-state">${C.escapeHtml(empty)}</p>`;
    C.rememberAnalysis(slug);
  }

  function wireActions() {
    $("[data-print]").addEventListener("click", () => window.print());
    $("[data-copy-report]").addEventListener("click", () => C.copyText($("[data-report-main]").innerText));
    $("[data-share]").addEventListener("click", () => C.sharePage(payload?.listing?.title));
    if ("IntersectionObserver" in window) {
      const links = all(".technical-nav a");
      const observer = new IntersectionObserver((entries) => entries.filter((entry) => entry.isIntersecting).forEach((entry) => links.forEach((link) => link.classList.toggle("active", link.hash === `#${entry.target.id}`))), { rootMargin: "-20% 0px -70%" });
      all(".report-section, #summary").forEach((section) => observer.observe(section));
    }
  }

  document.addEventListener("DOMContentLoaded", async () => {
    wireActions();
    try {
      const model = await C.fetchPresentation(slug);
      C.setLanguage(model.language);
      render(model);
      $("[data-loading]").hidden = true;
      $("[data-content]").hidden = false;
    } catch (error) {
      $("[data-loading]").hidden = true;
      $("[data-error]").hidden = false;
      $("[data-error-message]").textContent = error.message;
    }
  });
})();
