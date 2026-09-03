(() => {
  "use strict";

  const translations = {
    sk: {
      title: "Všetky fotografie z inzerátu",
      explanation: "Každá fotografia zostáva v reporte. Takmer rovnaké zábery sú iba zoskupené; detailnú vision kontrolu absolvujú vybrané rizikové zábery a rozložená kontrolná vzorka.",
      stats: "{total} celkom · {unique} odlišných · {duplicates} podobných · {detail} detailne",
      coverage: "Obrazové pokrytie: {coverage} %",
      detail: "detail",
      overview: "prehľad",
      duplicate: "podobná",
      inventory: "bez vision kontroly",
      duplicateOf: "Zoskupená s {photo}",
      open: "Otvoriť fotografiu",
      unavailable: "Fotografia nie je dostupná.",
      metric: "{coverage} % pokrytie · {detail} detailne",
      alt: "{photo} z inzerátu vozidla"
    },
    cs: {
      title: "Všechny fotografie z inzerátu",
      explanation: "Každá fotografie zůstává v reportu. Téměř stejné záběry jsou pouze seskupené; detailní vision kontrolou projdou vybrané rizikové záběry a rozložený kontrolní vzorek.",
      stats: "{total} celkem · {unique} odlišných · {duplicates} podobných · {detail} detailně",
      coverage: "Obrazové pokrytí: {coverage} %",
      detail: "detail",
      overview: "přehled",
      duplicate: "podobná",
      inventory: "bez vision kontroly",
      duplicateOf: "Seskupená s {photo}",
      open: "Otevřít fotografii",
      unavailable: "Fotografie není dostupná.",
      metric: "{coverage} % pokrytí · {detail} detailně",
      alt: "{photo} z inzerátu vozidla"
    }
  };

  let lastReport = null;
  let lastJobId = null;
  let observer = null;

  function language() {
    return document.querySelector("#languageSelect")?.value === "cs" ? "cs" : "sk";
  }

  function tr(key, values = {}) {
    let text = translations[language()][key] || translations.sk[key] || key;
    Object.entries(values).forEach(([name, value]) => {
      text = text.replaceAll(`{${name}}`, String(value));
    });
    return text;
  }

  function element(tag, className = "", text = "") {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }

  function asObject(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  function asArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function clamp(value) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? Math.max(0, Math.min(100, Math.round(parsed))) : 0;
  }

  function ensureShell() {
    let panel = document.querySelector("#allPhotosPanel");
    if (panel) return panel;

    const photoBlock = document.querySelector("#photoBlock");
    if (!photoBlock) return null;

    panel = element("details", "gallery-panel");
    panel.id = "allPhotosPanel";
    const summary = element("summary", "gallery-summary-row");
    const heading = element("span", "gallery-heading", tr("title"));
    const count = element("strong", "gallery-count", "0");
    count.id = "allPhotosCount";
    summary.append(heading, count);

    const explanation = element("p", "gallery-explanation", tr("explanation"));
    explanation.id = "allPhotosExplanation";
    const meta = element("div", "gallery-meta");
    const stats = element("span", "", "");
    stats.id = "allPhotosStats";
    const coverage = element("span", "", "");
    coverage.id = "allPhotosCoverage";
    meta.append(stats, coverage);

    const grid = element("div", "all-photo-grid");
    grid.id = "allPhotosGrid";
    panel.append(summary, explanation, meta, grid);
    panel.addEventListener("toggle", () => {
      panel.dataset.touched = "true";
    });
    photoBlock.append(panel);
    return panel;
  }

  function reviewLabel(level) {
    return tr({
      detail: "detail",
      overview: "overview",
      duplicate_reference: "duplicate",
      inventory: "inventory"
    }[level] || "inventory");
  }

  function validPhotoUrl(jobId, photoId) {
    if (!/^[0-9a-f]{32}$/.test(jobId || "")) return null;
    if (!/^photo-\d{3}$/.test(photoId || "")) return null;
    return `/api/v2/jobs/${encodeURIComponent(jobId)}/photos/${encodeURIComponent(photoId)}`;
  }

  function renderCard(item, jobId) {
    const card = element("figure", "all-photo-card");
    const level = String(item.review_level || "inventory");
    card.dataset.reviewLevel = level;

    const url = validPhotoUrl(jobId, String(item.id || ""));
    const imageFrame = url ? element("a", "all-photo-image") : element("div", "all-photo-image");
    if (url) {
      imageFrame.href = url;
      imageFrame.target = "_blank";
      imageFrame.rel = "noopener noreferrer";
      imageFrame.title = tr("open");
    }

    if (url) {
      const image = document.createElement("img");
      image.src = url;
      image.alt = tr("alt", { photo: item.label || "Foto" });
      image.loading = "lazy";
      image.decoding = "async";
      image.addEventListener("error", () => {
        image.remove();
        imageFrame.append(element("span", "photo-unavailable", tr("unavailable")));
      }, { once: true });
      imageFrame.append(image);
    } else {
      imageFrame.append(element("span", "photo-unavailable", tr("unavailable")));
    }

    const caption = element("figcaption");
    const top = element("div", "all-photo-caption-top");
    top.append(
      element("strong", "", String(item.label || item.id || "Foto")),
      element("span", "photo-review-badge", reviewLabel(level))
    );
    caption.append(top);

    if (item.original_name) {
      caption.append(element("small", "photo-filename", String(item.original_name)));
    }
    if (item.duplicate_of) {
      caption.append(
        element("small", "photo-duplicate-note", tr("duplicateOf", { photo: item.duplicate_of }))
      );
    }

    card.append(imageFrame, caption);
    return card;
  }

  function render(report, jobId) {
    lastReport = report;
    lastJobId = jobId;
    const photo = asObject(report?.photo_analysis);
    const gallery = asArray(photo.gallery).filter((item) => item && typeof item === "object");
    const panel = ensureShell();
    if (!panel) return;

    const total = Number(photo.gallery_total || gallery.length || 0);
    const unique = Number(photo.gallery_unique || total || 0);
    const duplicates = Number(photo.duplicate_count || Math.max(0, total - unique) || 0);
    const detail = Number(photo.detail_count || 0);
    const coverage = clamp(photo.visual_coverage_percent || 0);

    panel.hidden = gallery.length === 0;
    panel.querySelector(".gallery-heading").textContent = tr("title");
    document.querySelector("#allPhotosCount").textContent = String(total);
    document.querySelector("#allPhotosExplanation").textContent = tr("explanation");
    document.querySelector("#allPhotosStats").textContent = tr("stats", {
      total,
      unique,
      duplicates,
      detail
    });
    document.querySelector("#allPhotosCoverage").textContent = tr("coverage", { coverage });

    const grid = document.querySelector("#allPhotosGrid");
    grid.replaceChildren(...gallery.map((item) => renderCard(item, jobId)));

    if (!panel.dataset.touched) panel.open = gallery.length <= 16;

    const metric = document.querySelector("#photosMetric");
    const metricText = document.querySelector("#photosText");
    if (metric && total > 0) metric.textContent = String(total);
    if (metricText && total > 0) {
      metricText.textContent = tr("metric", { coverage, detail });
    }
  }

  async function refresh() {
    const reportSection = document.querySelector("#reportSection");
    if (!reportSection || reportSection.classList.contains("hidden")) return;

    const jobId = localStorage.getItem("checkni-v2-job") || "";
    if (!/^[0-9a-f]{32}$/.test(jobId)) return;

    try {
      const response = await fetch(`/api/v2/jobs/${encodeURIComponent(jobId)}`, {
        headers: { Accept: "application/json" },
        cache: "no-store"
      });
      if (!response.ok) return;
      const job = await response.json();
      if (job.status === "done" && job.report) render(job.report, jobId);
    } catch (_) {
      // The main report remains usable even if thumbnail loading is temporarily unavailable.
    }
  }

  function init() {
    ensureShell();
    const reportSection = document.querySelector("#reportSection");
    if (reportSection) {
      observer = new MutationObserver(() => {
        if (!reportSection.classList.contains("hidden")) refresh();
      });
      observer.observe(reportSection, { attributes: true, attributeFilter: ["class"] });
    }

    document.querySelector("#languageSelect")?.addEventListener("change", () => {
      setTimeout(() => {
        if (lastReport && lastJobId) render(lastReport, lastJobId);
      }, 0);
    });

    window.addEventListener("pageshow", refresh);
    window.addEventListener("beforeprint", () => {
      const panel = document.querySelector("#allPhotosPanel");
      if (panel && !panel.hidden) panel.open = true;
    });
    refresh();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
