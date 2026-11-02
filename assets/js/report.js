"use strict";
(() => {
  const data = VS.readJson("report-data");
  const SVG_NS = "http://www.w3.org/2000/svg";
  function svgNode(tag, attrs) {
    const node = document.createElementNS(SVG_NS, tag);
    Object.keys(attrs).forEach((key) => { node.setAttribute(key, attrs[key]); });
    return node;
  }
  function drawProbabilities(container, probabilities) {
    const classes = ["live", "replay", "synthetic"];
    const colors = { live: VS.COLORS.teal, replay: VS.COLORS.warn, synthetic: VS.COLORS.fake };
    const rowHeight = 56;
    const svg = svgNode("svg", { viewBox: "0 0 600 " + rowHeight * classes.length, role: "img", "aria-label": "احتمالات الفئات", direction: "ltr" });
    classes.forEach((name, i) => {
      const value = Number(probabilities[name] || 0);
      const y = i * rowHeight;
      const label = svgNode("text", { x: 600, y: y + 20, "text-anchor": "end", "font-size": 17, fill: VS.COLORS.text });
      label.textContent = VS.LABELS.classes[name];
      const valueText = svgNode("text", { x: 0, y: y + 20, "text-anchor": "start", "font-size": 17, fill: VS.COLORS.text, direction: "ltr" });
      valueText.textContent = VS.percent(value);
      svg.appendChild(label);
      svg.appendChild(valueText);
      svg.appendChild(svgNode("rect", { x: 0, y: y + 28, width: 600, height: 16, rx: 6, fill: "#eef1f5", stroke: "#d3dbe6" }));
      svg.appendChild(svgNode("rect", { x: 600 - 600 * value, y: y + 28, width: Math.max(0, 600 * value), height: 16, rx: 6, fill: colors[name] }));
    });
    container.appendChild(svg);
  }
  function drawEnvelope(canvas, envelope) {
    if (!canvas || !envelope || !envelope.db || !envelope.db.length) { return; }
    const ctx = VS.prepareCanvas(canvas);
    const pad = { left: 56, right: 12, top: 14, bottom: 34 };
    const width = canvas.width - pad.left - pad.right;
    const height = canvas.height - pad.top - pad.bottom;
    const minDb = -100;
    const maxFreq = 8000;
    ctx.strokeStyle = VS.COLORS.grid;
    ctx.fillStyle = VS.COLORS.muted;
    ctx.textAlign = "right";
    for (let db = 0; db >= minDb; db -= 20) {
      const y = pad.top + (db / minDb) * height;
      ctx.beginPath();
      ctx.moveTo(pad.left, y);
      ctx.lineTo(pad.left + width, y);
      ctx.stroke();
      ctx.fillText(db + " dB", pad.left - 6, y + 5);
    }
    ctx.textAlign = "center";
    for (let khz = 0; khz <= 8; khz += 1) { ctx.fillText(khz + " kHz", pad.left + (khz * 1000 / maxFreq) * width, canvas.height - 10); }
    ctx.strokeStyle = VS.COLORS.navy;
    ctx.lineWidth = 2;
    ctx.beginPath();
    envelope.db.forEach((value, i) => {
      const x = pad.left + (envelope.freqs[i] / maxFreq) * width;
      const y = pad.top + (Math.max(minDb, value) / minDb) * height;
      if (i === 0) { ctx.moveTo(x, y); } else { ctx.lineTo(x, y); }
    });
    ctx.stroke();
  }
  if (data) {
    drawProbabilities(document.getElementById("prob-bars"), data.probabilities || {});
    VS.drawSpectrogram(document.getElementById("spectrogram"), data.spectrogram);
    drawEnvelope(document.getElementById("envelope"), data.envelope);
  }
  document.getElementById("print-report").addEventListener("click", () => { window.print(); });
})();