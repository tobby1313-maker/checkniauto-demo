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

  function showError(form, message) {
    const target = form.querySelector("[data-form-error]");
    target.textContent = message || "";
    target.classList.toggle("hidden", !message);
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
      : (Checkni.language() === "en" ? "JPG, PNG, WebP, BMP or AVIF · max. 12 files" : "JPG, PNG, WebP, BMP alebo AVIF · max. 12 súborov");
  }

  function renderFilePreviews() {
    previewUrls.forEach((url) => URL.revokeObjectURL(url));
    previewUrls = [];
    const target = $("[data-file-previews]");
    target.innerHTML = selectedFiles.map((file, index) => {
      const url = URL.createObjectURL(file);
      previewUrls.push(url);
      const removeLabel = Checkni.language() === "en" ? "Remove photo" : "Odstrániť fotografiu";
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
    const en = Checkni.language() === "en";
    if (/ready|done|complete/.test(value)) return en
      ? { value: 100, message: "Your analysis is ready and we are opening the result." }
      : { value: 100, message: "Analýza je pripravená a otvárame tvoj výsledok." };
    if (/final|report|synthesis/.test(value)) return en
      ? { value: 92, message: "We are preparing a clear summary of everything we found." }
      : { value: 92, message: "Pripravujeme jasný prehľad všetkého, čo sme zistili." };
    if (/risk|score/.test(value)) return en
      ? { value: 82, message: "We are sorting the things worth checking with the seller." }
      : { value: 82, message: "Triedime veci, ktoré sa oplatí preveriť s predajcom." };
    if (/vision|photo|image/.test(value)) return en
      ? { value: 70, message: "We are checking the photos for visible details and missing views." }
      : { value: 70, message: "Na fotografiách hľadáme viditeľné detaily a chýbajúce zábery." };
    if (/market|compar/.test(value)) return en
      ? { value: 57, message: "We are comparing the asking price with similar cars on the market." }
      : { value: 57, message: "Porovnávame ponúkanú cenu s podobnými autami na trhu." };
    if (/research|web/.test(value)) return en
      ? { value: 44, message: "We are checking useful information about this model and listing." }
      : { value: 44, message: "Overujeme užitočné informácie k modelu a inzerátu." };
    if (/identity|component/.test(value)) return en
      ? { value: 28, message: "We are organising the vehicle details that matter before a viewing." }
      : { value: 28, message: "Triedime údaje o aute dôležité pred obhliadkou." };
    if (/scrap|listing|manual/.test(value)) return en
      ? { value: 14, message: "We are collecting the details and photos from the listing." }
      : { value: 14, message: "Zbierame údaje a fotografie z inzerátu." };
    return en
      ? { value: 5, message: "We are getting everything ready to start the analysis." }
      : { value: 5, message: "Chystáme všetko, aby sme mohli začať s analýzou." };
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
          if (!slug) throw new Error("Analysis finished without a saved result.");
          Checkni.rememberAnalysis(slug);
          await completeJourney();
          location.assign(`/analysis/${encodeURIComponent(slug)}`);
          return;
        }
        const data = JSON.parse(raw);
        if (data.error) throw new Error(data.error);
        if (data.slug) slug = data.slug;
        updateProgress(data.status);
      }
    }
    throw new Error("Connection ended before the analysis completed.");
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
      if (/manual|not supported|mobile\.de/i.test(error.message)) {
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
      showError(form, Checkni.language() === "en" ? "Select at most 12 images." : "Vyber najviac 12 fotografií.");
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
        ? (Checkni.language() === "en" ? "Only the first 12 images were selected." : "Vybraných bolo iba prvých 12 fotografií.")
        : "");
    });
    $("[data-cancel-analysis]").addEventListener("click", () => {
      $("[data-cancel-analysis]").disabled = true;
      abortController?.abort();
      closeProgress();
    });
  });
})();
