"use strict";
const VS = (() => {
  const RIYADH_OFFSET_MS = 3 * 60 * 60 * 1000;
  const COLORS = { navy: "#10204f", teal: "#007a73", fake: "#b42318", warn: "#8a4b00", grid: "#dde5ef", text: "#16223f", muted: "#56657c" };
  const LABELS = {
    verdict: { genuine: "أصلي", fake: "مزيف" },
    risk: { low: "منخفضة", medium: "متوسطة", high: "مرتفعة" },
    attack: { replay: "إعادة تشغيل", synthetic: "توليد بالذكاء الاصطناعي" },
    classes: { live: "صوت حي", replay: "إعادة تشغيل", synthetic: "مولد بالذكاء الاصطناعي" }
  };
  function csrf() {
    const meta = document.querySelector("meta[name='csrf-token']");
    return meta ? meta.getAttribute("content") : "";
  }
  function readJson(id) {
    const node = document.getElementById(id);
    if (!node) { return null; }
    try {
      return JSON.parse(node.textContent);
    } catch (error) {
      return null;
    }
  }
  function span(text) {
    const node = document.createElement("span");
    node.textContent = text;
    return node;
  }
  function formatDate(iso) {
    const container = document.createElement("span");
    container.className = "dt";
    const parsed = new Date(iso);
    if (!iso || Number.isNaN(parsed.getTime())) {
      container.textContent = "-";
      return container;
    }
    const local = new Date(parsed.getTime() + RIYADH_OFFSET_MS);
    const hours = local.getUTCHours();
    const minutes = String(local.getUTCMinutes()).padStart(2, "0");
    container.appendChild(span(local.getUTCFullYear() + "/" + (local.getUTCMonth() + 1) + "/" + local.getUTCDate()));
    container.appendChild(span("م"));
    container.appendChild(document.createTextNode(" "));
    container.appendChild(span((hours % 12 || 12) + ":" + minutes));
    container.appendChild(span(hours < 12 ? "ص" : "م"));
    return container;
  }
  function percent(value, digits) { return (Number(value) * 100).toFixed(digits === undefined ? 1 : digits) + "%"; }
  async function request(url, options) {
    const settings = Object.assign({ credentials: "same-origin", headers: {} }, options || {});
    settings.headers["X-CSRF-Token"] = csrf();
    let response;
    try {
      response = await fetch(url, settings);
    } catch (error) {
      return { ok: false, status: 0, data: { error: "تعذر الاتصال بالخادم، تحقق من الشبكة وحاول مرة أخرى." } };
    }
    let data = {};
    try {
      data = await response.json();
    } catch (error) {
      data = { error: "استجابة غير متوقعة من الخادم." };
    }
    return { ok: response.ok, status: response.status, data: data };
  }
  function postJson(url, body) {
    return request(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  }
  function colorAt(value) {
    const stops = [[0, [255, 255, 255]], [0.35, [173, 222, 238]], [0.6, [27, 168, 224]], [0.8, [14, 127, 116]], [1, [10, 36, 99]]];
    let i = 1;
    while (i < stops.length - 1 && value > stops[i][0]) { i += 1; }
    const lower = stops[i - 1];
    const upper = stops[i];
    const t = Math.min(1, Math.max(0, (value - lower[0]) / (upper[0] - lower[0])));
    return lower[1].map((c, k) => Math.round(c + (upper[1][k] - c) * t));
  }
  function prepareCanvas(canvas) {
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.font = "14px 'IBM Plex Sans Arabic', Tahoma, sans-serif";
    return ctx;
  }
  function drawSpectrogram(canvas, spec) {
    if (!canvas || !spec || !spec.data || !spec.data.length) { return; }
    const ctx = prepareCanvas(canvas);
    const pad = { left: 56, right: 12, top: 12, bottom: 34 };
    const width = canvas.width - pad.left - pad.right;
    const height = canvas.height - pad.top - pad.bottom;
    const image = ctx.createImageData(spec.cols, spec.rows);
    spec.data.forEach((column, x) => {
      column.forEach((value, y) => {
        const rgb = colorAt(value / 255);
        const offset = ((spec.rows - 1 - y) * spec.cols + x) * 4;
        image.data[offset] = rgb[0];
        image.data[offset + 1] = rgb[1];
        image.data[offset + 2] = rgb[2];
        image.data[offset + 3] = 255;
      });
    });
    const buffer = document.createElement("canvas");
    buffer.width = spec.cols;
    buffer.height = spec.rows;
    buffer.getContext("2d").putImageData(image, 0, 0);
    ctx.imageSmoothingEnabled = true;
    ctx.drawImage(buffer, pad.left, pad.top, width, height);
    ctx.strokeStyle = COLORS.grid;
    ctx.strokeRect(pad.left, pad.top, width, height);
    ctx.fillStyle = COLORS.muted;
    ctx.textAlign = "right";
    for (let khz = 0; khz <= spec.max_freq / 1000; khz += 2) {
      const y = pad.top + height - (khz * 1000 / spec.max_freq) * height;
      ctx.fillText(khz + " kHz", pad.left - 6, y + 5);
    }
    ctx.textAlign = "center";
    const step = spec.duration > 20 ? 5 : 1;
    for (let s = 0; s <= spec.duration; s += step) {
      const x = pad.left + (s / spec.duration) * width;
      ctx.fillText(s + " s", x, canvas.height - 10);
    }
  }
  function applyWidths(root) {
    (root || document).querySelectorAll("[data-width]").forEach((node) => {
      const value = Math.min(100, Math.max(0, parseFloat(node.getAttribute("data-width")) || 0));
      node.style.width = value + "%";
    });
  }
  async function reviewAlert(button) {
    const id = button.getAttribute("data-review-id");
    button.disabled = true;
    const result = await postJson("/api/alerts/" + encodeURIComponent(id) + "/review");
    if (!result.ok) {
      button.disabled = false;
      window.alert(result.data.error || "تعذر تحديث التنبيه.");
      return;
    }
    const cell = document.querySelector("[data-alert-status='" + id + "']");
    if (cell) {
      cell.textContent = "";
      const badge = span("تمت المراجعة");
      badge.className = "badge badge-status-reviewed";
      const sub = span((result.data.reviewed_by || "-") + " ");
      sub.className = "cell-sub";
      sub.appendChild(formatDate(result.data.reviewed_at));
      cell.appendChild(badge);
      cell.appendChild(sub);
    }
    button.remove();
  }
  function init() {
    applyWidths(document);
    document.querySelectorAll("img.brand-logo, img.auth-logo").forEach((img) => {
      img.addEventListener("error", () => { img.classList.add("hidden"); });
    });
    document.querySelectorAll("[data-review-id]").forEach((button) => {
      button.addEventListener("click", () => { reviewAlert(button); });
    });
    document.querySelectorAll("form[data-confirm]").forEach((form) => {
      form.addEventListener("submit", (event) => {
        if (!window.confirm(form.getAttribute("data-confirm"))) { event.preventDefault(); }
      });
    });
  }
  document.addEventListener("DOMContentLoaded", init);
  return { COLORS: COLORS, LABELS: LABELS, csrf: csrf, readJson: readJson, formatDate: formatDate, percent: percent, request: request, postJson: postJson, drawSpectrogram: drawSpectrogram, prepareCanvas: prepareCanvas, applyWidths: applyWidths };
})();