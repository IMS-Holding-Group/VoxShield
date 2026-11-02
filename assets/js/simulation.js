"use strict";
(() => {
  const page = VS.readJson("scenario-data");
  if (!page) { return; }
  const steps = Array.from(document.querySelectorAll(".step"));
  const dots = Array.from(document.querySelectorAll("[data-dot]"));
  const prev = document.getElementById("step-prev");
  const next = document.getElementById("step-next");
  const counter = document.getElementById("step-counter");
  let current = page.start;
  let furthest = page.start;
  function saveProgress(step) { VS.postJson("/api/simulation/progress", { scenario: page.key, step: step }); }
  function render() {
    steps.forEach((node, i) => { node.classList.toggle("hidden", i !== current); });
    dots.forEach((dot, i) => {
      dot.classList.toggle("current", i === current);
      dot.classList.toggle("seen", i <= furthest && i !== current);
    });
    counter.textContent = "الخطوة " + (current + 1) + " من " + page.steps;
    prev.disabled = current === 0;
    next.disabled = current === page.steps - 1;
  }
  function go(step) {
    current = Math.max(0, Math.min(page.steps - 1, step));
    furthest = Math.max(furthest, current);
    render();
    saveProgress(current);
  }
  async function loadSpectrogram(card) {
    const sample = card.getAttribute("data-sample");
    const result = await VS.request("/api/simulation/" + encodeURIComponent(page.key) + "/" + encodeURIComponent(sample) + "/spectrogram");
    if (result.ok) { VS.drawSpectrogram(card.querySelector("canvas"), result.data); }
  }
  function resultLine(label, value) {
    const line = document.createElement("p");
    line.textContent = label + ": " + value;
    return line;
  }
  async function analyze(card, button) {
    const box = card.querySelector(".sample-result");
    button.disabled = true;
    box.classList.remove("hidden");
    box.textContent = "جارٍ فحص المقطع...";
    const result = await VS.postJson("/api/simulation/analyze", { scenario: page.key, sample: card.getAttribute("data-sample") });
    button.disabled = false;
    box.textContent = "";
    if (!result.ok) {
      box.textContent = result.data.error || "تعذر فحص المقطع.";
      return;
    }
    const verdict = document.createElement("p");
    verdict.className = "result-verdict " + (result.data.verdict === "genuine" ? "text-genuine" : "text-fake");
    verdict.textContent = VS.LABELS.verdict[result.data.verdict];
    box.appendChild(verdict);
    box.appendChild(resultLine("نسبة الثقة", VS.percent(result.data.confidence)));
    box.appendChild(resultLine("نوع الهجوم", result.data.attack_type ? VS.LABELS.attack[result.data.attack_type] : "لا يوجد"));
    box.appendChild(resultLine("مستوى الخطورة", VS.LABELS.risk[result.data.risk_level]));
    const probs = result.data.probabilities || {};
    box.appendChild(resultLine("الاحتمالات", ["live", "replay", "synthetic"].map((c) => VS.LABELS.classes[c] + " " + VS.percent(probs[c] || 0)).join("، ")));
    if (result.data.inconclusive) { box.appendChild(resultLine("ملاحظة", "النتيجة غير حاسمة وتستدعي مراجعة بشرية")); }
  }
  prev.addEventListener("click", () => { go(current - 1); });
  next.addEventListener("click", () => { go(current + 1); });
  dots.forEach((dot, i) => { dot.addEventListener("click", () => { go(i); }); });
  document.querySelectorAll(".sample").forEach((card) => {
    loadSpectrogram(card);
    const button = card.querySelector("[data-analyze]");
    button.addEventListener("click", () => { analyze(card, button); });
  });
  render();
  saveProgress(current);
})();