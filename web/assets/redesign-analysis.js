(function () {
  "use strict";
  const CA = window.Checkni;
  const $ = (selector) => document.querySelector(selector);
  const all = (selector) => Array.from(document.querySelectorAll(selector));
  let model = null;
  const l = (sk, cs, en) => CA.localize({ sk, cs, en }, model?.language);

  function text(selector, value) { all(selector).forEach((element) => { element.textContent = value || CA.t("unavailable", model?.language); }); }
  function setImage(selector, image) { const target = $(selector); if (!target || !image) return; target.src = image.url; target.alt = image.filename || model.listing.title; target.classList.remove("hidden"); }
  function itemTone(item) { return ["good", "warn", "risk"].includes(item?.tone) ? item.tone : "warn"; }

  function renderFindings() {
    const findings = model.priority_findings.slice(0, 3);
    $("[data-findings-count]").textContent = `${findings.length} ${l("prioritné body", "prioritní body", "priority items")}`;
    $("[data-findings]").innerHTML = findings.length ? findings.map((item) => `<article class="finding ${itemTone(item)}"><div class="finding-dot"></div><h4>${CA.escapeHtml(item.title)}</h4><p>${CA.escapeHtml(item.detail || CA.t("noData", model.language))}</p>${item.action ? `<small>${CA.escapeHtml(item.action)}</small>` : ""}</article>`).join("") : `<div class="empty-state">${CA.escapeHtml(CA.t("noData", model.language))}</div>`;
  }

  function renderMarket() {
    const market = model.market;
    $("[data-market-confidence]").textContent = market.available ? CA.confidenceLabel(market.confidence, model.language) : CA.t("unavailable", model.language);
    if (!market.available || market.median_eur === null) {
      $("[data-market]").innerHTML = `<div class="empty-state">${CA.escapeHtml(CA.t("marketUnavailable", model.language))}</div>`;
      return;
    }
    const delta = market.price_delta_percent === null ? CA.t("unavailable", model.language) : `${market.price_delta_percent > 0 ? "+" : ""}${market.price_delta_percent}%`;
    $("[data-market]").innerHTML = `<div class="market-layout"><div class="metric-box"><span>${l("Cena inzerátu", "Cena inzerátu", "Advertised price")}</span><strong>${CA.formatMoney(market.advertised_price_eur, model.language)}</strong><p>${l("Rozdiel oproti overenému mediánu", "Rozdíl oproti ověřenému mediánu", "Difference from verified median")}: ${CA.escapeHtml(delta)}</p></div><div class="metric-box"><span>${l("Overený medián", "Ověřený medián", "Verified median")}</span><strong>${CA.formatMoney(market.median_eur, model.language)}</strong><p>${market.comparables.length} ${l("zobrazené porovnateľné ponuky", "zobrazené srovnatelné nabídky", "customer-visible comparables")}</p></div></div>`;
  }

  function costBox(label, group, description) {
    return group.available ? `<div class="metric-box"><span>${CA.escapeHtml(label)}</span><strong>${CA.formatRange(group.low_eur, group.high_eur, model.language)}</strong><p>${CA.escapeHtml(description)}</p></div>` : `<div class="metric-box"><span>${CA.escapeHtml(label)}</span><strong>${CA.escapeHtml(CA.t("unavailable", model.language))}</strong><p>${CA.escapeHtml(CA.t("noCosts", model.language))}</p></div>`;
  }

  function renderCosts() {
    $("[data-costs]").innerHTML = costBox(CA.t("initialService", model.language), model.costs.initial_service, l("Pravdepodobný servis a diagnostika po kúpe.", "Pravděpodobný servis a diagnostika po koupi.", "Likely service and diagnostics after purchase.")) + costBox(CA.t("conditionalRepairs", model.language), model.costs.conditional_repairs, l("Iba ak kontrola potvrdí konkrétnu chybu.", "Pouze pokud kontrola potvrdí konkrétní závadu.", "Only if an inspection confirms the fault."));
  }

  function renderGallery() {
    const images = model.listing.images.slice(0, 5);
    $("[data-photo-count]").textContent = `${model.listing.images.length} ${CA.t("photos", model.language)}`;
    $("[data-gallery]").innerHTML = images.length ? images.map((image, index) => `<button type="button" data-photo-index="${index}"><img src="${CA.escapeHtml(image.url)}" alt="${CA.escapeHtml(image.filename || "")}"></button>`).join("") : `<div class="empty-state">${CA.escapeHtml(CA.t("noPhotos", model.language))}</div>`;
    CA.wireLightbox(model.listing.images);
  }

  function renderRisks() {
    const target = $("[data-risk-accordion]");
    target.innerHTML = model.technical_risks.length ? model.technical_risks.slice(0, 5).map((risk, index) => `<div class="accordion-item ${index === 0 ? "open" : ""}"><button class="accordion-button" type="button">${CA.escapeHtml([risk.component, risk.issue].filter(Boolean).join(" — "))} ⌄</button><div class="accordion-body"><strong>${l("Dopad", "Dopad", "Buyer impact")}:</strong> ${CA.escapeHtml(risk.buyer_impact || CA.t("noData", model.language))}<br><strong>${l("Ako overiť", "Jak ověřit", "How to verify")}:</strong> ${CA.escapeHtml(risk.verification_action || CA.t("noData", model.language))}${risk.low_eur !== null || risk.high_eur !== null ? `<br><strong>${l("Podmienený odhad", "Podmíněný odhad", "Conditional estimate")}:</strong> ${CA.formatRange(risk.low_eur, risk.high_eur, model.language)}` : ""}</div></div>`).join("") : `<div class="empty-state">${CA.escapeHtml(CA.t("noRisks", model.language))}</div>`;
    target.querySelectorAll(".accordion-button").forEach((button) => button.addEventListener("click", () => button.parentElement.classList.toggle("open")));
  }

  function renderActions() {
    $("[data-actions]").innerHTML = model.buyer_actions.length ? model.buyer_actions.map((action, index) => `<div class="todo"><span class="todo-number">${index + 1}</span><div><strong>${CA.escapeHtml(action)}</strong></div></div>`).join("") : `<div class="empty-state">${CA.escapeHtml(CA.t("noData", model.language))}</div>`;
    text("[data-seller-message]", model.seller_message);
  }

  function renderConfidence() {
    const rows = [
      [l("Kvalita dôkazov", "Kvalita důkazů", "Evidence quality"), CA.confidenceLabel(model.verdict.evidence_quality, model.language)],
      ["VIN", model.listing.vin || CA.t("missing", model.language)],
      [l("Trhové porovnanie", "Tržní srovnání", "Market benchmark"), model.market.available ? CA.confidenceLabel(model.market.confidence, model.language) : CA.t("unavailable", model.language)],
      [l("Analýza fotografií", "Analýza fotografií", "Photo analysis"), model.vision.photos_provided ? `${model.listing.images.length} ${CA.t("photos", model.language)}` : CA.t("unavailable", model.language)],
      [l("Overené zdroje", "Ověřené zdroje", "Verified sources"), String(model.sources.length)],
    ];
    $("[data-confidence-rows]").innerHTML = rows.map(([label, value]) => `<div class="source-row"><span>${CA.escapeHtml(label)}</span><strong>${CA.escapeHtml(value)}</strong></div>`).join("");
  }

  function render() {
    const listing = model.listing;
    all("[data-analysis-language]").forEach((element) => {
      element.textContent = model.language === "cs" ? "CZ" : model.language.toUpperCase();
    });
    document.title = `${listing.title} — Checkni Auto`;
    text("[data-analysis-title]", listing.title);
    text("[data-price]", CA.formatMoney(listing.price_eur, model.language));
    text("[data-mileage]", listing.mileage_km === null ? CA.t("unavailable", model.language) : `${CA.formatNumber(listing.mileage_km, model.language)} km`);
    text("[data-year]", listing.year ? String(listing.year) : CA.t("unavailable", model.language));
    text("[data-vin]", listing.vin || CA.t("missing", model.language));
    text("[data-source]", listing.source_name || CA.t("unavailable", model.language));
    const sourceLink = $("[data-source-link]");
    if (listing.source_url?.startsWith("http://") || listing.source_url?.startsWith("https://")) {
      sourceLink.href = listing.source_url;
      sourceLink.target = "_blank";
      sourceLink.rel = "noopener noreferrer";
    } else {
      sourceLink.removeAttribute("href");
    }
    const subtitle = [listing.year, listing.fuel, listing.transmission, listing.drivetrain].filter(Boolean).join(" · ");
    text("[data-listing-subtitle]", subtitle);
    setImage("[data-hero-image]", listing.images[0]);
    $("[data-verdict-badge]").className = `verdict-badge ${model.verdict.tone}`;
    $("[data-verdict-badge]").textContent = `● ${model.verdict.label}`;
    text("[data-verdict-label]", model.verdict.label);
    text("[data-verdict-summary]", model.verdict.summary);
    text("[data-confidence]", `${CA.t("confidence", model.language)}: ${CA.confidenceLabel(model.verdict.evidence_quality, model.language)}`);
    const alerts = model.priority_findings.slice(0, 3);
    $("[data-alerts]").innerHTML = alerts.map((item) => `<div class="alert ${itemTone(item)}">● ${CA.escapeHtml(item.title)}</div>`).join("");
    text("[data-strength]", model.pros[0] || model.vision.visual_verdict || CA.t("unavailable", model.language));
    text("[data-priority]", model.priority_findings[0]?.title || CA.t("unavailable", model.language));
    text("[data-initial-cost]", model.costs.initial_service.available ? CA.formatRange(model.costs.initial_service.low_eur, model.costs.initial_service.high_eur, model.language) : CA.t("unavailable", model.language));
    all("[data-technical-link]").forEach((link) => { link.href = `/analysis/${encodeURIComponent(listing.slug)}/technical`; });
    renderFindings(); renderMarket(); renderCosts(); renderGallery(); renderRisks(); renderActions(); renderConfidence();
    all("[data-copy-seller]").forEach((button) => button.addEventListener("click", () => CA.copyText(model.seller_message)));
    all("[data-share]").forEach((button) => button.addEventListener("click", () => CA.sharePage(listing.title)));
  }

  document.addEventListener("DOMContentLoaded", async () => {
    const slug = CA.slugFromPath();
    try {
      model = await CA.fetchPresentation(slug);
      CA.setLanguage(model.language, { persist: false });
      CA.rememberAnalysis(slug);
      $("[data-loading]").classList.add("hidden");
      $("[data-content]").classList.remove("hidden");
      render();
    } catch (error) {
      $("[data-loading]").classList.add("hidden");
      $("[data-error]").classList.remove("hidden");
      $("[data-error-message]").textContent = error.message;
    }
  });
})();
