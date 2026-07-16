(function () {
  "use strict";

  const STORAGE = {
    theme: "checkni-auto-theme",
    language: "checkni-auto-language",
    recent: "checkni-auto-recent-analyses-v2",
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
    return stored === "en" ? "en" : "sk";
  }

  function setLanguage(value) {
    const next = value === "en" ? "en" : "sk";
    localStorage.setItem(STORAGE.language, next);
    document.documentElement.lang = next;
    document.querySelectorAll("[data-sk][data-en]").forEach((element) => {
      element.textContent = element.dataset[next] || element.textContent;
    });
    document.querySelectorAll("[data-language-toggle]").forEach((element) => {
      element.textContent = next === "sk" ? "SK / EN" : "EN / SK";
      element.setAttribute("aria-label", next === "sk" ? "Prepnúť na angličtinu" : "Switch to Slovak");
    });
    document.dispatchEvent(new CustomEvent("checkni:language", { detail: next }));
    return next;
  }

  function t(key, selectedLanguage) {
    const lang = selectedLanguage || language();
    return ui[lang]?.[key] || ui.sk[key] || key;
  }

  function setTheme(value) {
    const next = value === "light" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem(STORAGE.theme, next);
    document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
      button.textContent = next === "dark" ? "☾" : "☀";
      button.setAttribute("aria-label", next === "dark" ? "Prepnúť na svetlú tému" : "Prepnúť na tmavú tému");
    });
  }

  function initializeControls() {
    setTheme(localStorage.getItem(STORAGE.theme) || "dark");
    setLanguage(language());
    document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
      button.addEventListener("click", () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
    });
    document.querySelectorAll("[data-language-toggle]").forEach((button) => {
      button.addEventListener("click", () => setLanguage(language() === "sk" ? "en" : "sk"));
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
    const locale = (selectedLanguage || language()) === "en" ? "en-IE" : "sk-SK";
    return new Intl.NumberFormat(locale, { style: "currency", currency: "EUR", maximumFractionDigits: 0 }).format(Number(value));
  }

  function formatNumber(value, selectedLanguage) {
    if (value === null || value === undefined || value === "") return t("unavailable", selectedLanguage);
    return new Intl.NumberFormat((selectedLanguage || language()) === "en" ? "en-US" : "sk-SK").format(Number(value));
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
