"use strict";
(() => {
  const config = VS.readJson("analyze-config") || { max_mb: 15, max_seconds: 60 };
  const MAX_BYTES = config.max_mb * 1024 * 1024;
  const MAX_RECORD_SECONDS = 30;
  const ACCEPTED = ["wav", "mp3", "m4a", "ogg", "flac"];
  const STAGE_NAMES = ["upload", "extract", "classify", "report"];
  const dropzone = document.getElementById("dropzone");
  const fileInput = document.getElementById("file-input");
  const fileLabel = document.getElementById("file-label");
  const message = document.getElementById("analyze-message");
  const recStart = document.getElementById("rec-start");
  const recStop = document.getElementById("rec-stop");
  const recTimer = document.getElementById("rec-timer");
  const recStatus = document.getElementById("rec-status");
  let busy = false;
  let recorder = null;
  let recordTimer = null;
  let recordStarted = 0;
  function iconNode(name) {
    const template = document.getElementById("icon-templates");
    const source = template.content.querySelector("[data-icon='" + name + "']");
    return source.firstElementChild.cloneNode(true);
  }
  function setStage(name, state, text, timeMs) {
    const node = document.querySelector(".stage[data-stage='" + name + "']");
    node.classList.remove("active", "done", "error");
    if (state) { node.classList.add(state); }
    const iconName = { active: "spinner", done: "check", error: "alert" }[state] || "dot";
    const holder = node.querySelector(".stage-icon");
    holder.textContent = "";
    holder.appendChild(iconNode(iconName));
    node.querySelector(".stage-state").textContent = text;
    node.querySelector(".stage-time").textContent = timeMs === undefined ? "" : timeMs + " ms";
  }
  function resetStages() { STAGE_NAMES.forEach((name, i) => { setStage(name, "", i === 0 ? "بانتظار الملف" : "بالانتظار"); }); }
  function showMessage(text, kind) {
    message.className = "notice notice-" + kind;
    message.textContent = text;
  }
  function hideMessage() { message.className = "notice hidden"; }
  function extensionOf(name) {
    const parts = (name || "").toLowerCase().split(".");
    return parts.length > 1 ? parts.pop() : "";
  }
  async function isWavFile(file) {
    const header = new Uint8Array(await file.slice(0, 12).arrayBuffer());
    const text = String.fromCharCode.apply(null, Array.from(header));
    return text.slice(0, 4) === "RIFF" && text.slice(8, 12) === "WAVE";
  }
  function encodeWav(audioBuffer) {
    const channels = Math.min(audioBuffer.numberOfChannels, 2);
    const rate = audioBuffer.sampleRate;
    const frames = audioBuffer.length;
    const dataSize = frames * channels * 2;
    const buffer = new ArrayBuffer(44 + dataSize);
    const view = new DataView(buffer);
    const writeText = (offset, text) => {
      for (let i = 0; i < text.length; i += 1) { view.setUint8(offset + i, text.charCodeAt(i)); }
    };
    writeText(0, "RIFF");
    view.setUint32(4, 36 + dataSize, true);
    writeText(8, "WAVE");
    writeText(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, channels, true);
    view.setUint32(24, rate, true);
    view.setUint32(28, rate * channels * 2, true);
    view.setUint16(32, channels * 2, true);
    view.setUint16(34, 16, true);
    writeText(36, "data");
    view.setUint32(40, dataSize, true);
    const data = [];
    for (let c = 0; c < channels; c += 1) { data.push(audioBuffer.getChannelData(c)); }
    let offset = 44;
    for (let i = 0; i < frames; i += 1) {
      for (let c = 0; c < channels; c += 1) {
        const sample = Math.max(-1, Math.min(1, data[c][i]));
        view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
        offset += 2;
      }
    }
    return new Blob([buffer], { type: "audio/wav" });
  }
  async function toWav(blob) {
    const Context = window.AudioContext || window.webkitAudioContext;
    if (!Context) { throw new Error("المتصفح لا يدعم فك ترميز الملفات الصوتية."); }
    const context = new Context();
    try {
      const decoded = await context.decodeAudioData(await blob.arrayBuffer());
      if (decoded.duration > config.max_seconds) { throw new Error("مدة المقطع تتجاوز الحد المسموح (" + config.max_seconds + " ثانية)."); }
      return encodeWav(decoded);
    } finally {
      context.close();
    }
  }
  async function prepareFile(file) {
    const ext = extensionOf(file.name);
    if (ACCEPTED.indexOf(ext) === -1) { throw new Error("صيغة الملف غير مدعومة. الصيغ المقبولة: WAV وMP3 وM4A وOGG وFLAC."); }
    if (file.size > MAX_BYTES) { throw new Error("حجم الملف يتجاوز الحد المسموح (" + config.max_mb + " ميجابايت)."); }
    if (await isWavFile(file)) { return file; }
    try {
      return await toWav(file);
    } catch (error) {
      throw new Error(error && error.message && error.message.indexOf("مدة") === 0 ? error.message : "تعذر فك ترميز الملف الصوتي في المتصفح، جرّب ملف WAV.");
    }
  }
  function delay(ms) { return new Promise((resolve) => { setTimeout(resolve, ms); }); }
  async function submit(blob, displayName) {
    if (busy) { return; }
    busy = true;
    hideMessage();
    resetStages();
    setStage("upload", "active", "جارٍ الرفع والتحقق");
    if (blob.size > MAX_BYTES) {
      setStage("upload", "error", "مرفوض");
      showMessage("حجم الملف بعد التحويل يتجاوز الحد المسموح (" + config.max_mb + " ميجابايت).", "error");
      busy = false;
      return;
    }
    const form = new FormData();
    form.append("audio", blob, "audio.wav");
    form.append("file_name", displayName);
    const result = await VS.request("/api/analyze", { method: "POST", body: form });
    if (!result.ok) {
      setStage("upload", "error", "تعذر الفحص");
      showMessage(result.data.error || "تعذر إكمال الفحص.", "error");
      busy = false;
      return;
    }
    const timings = result.data.timings || {};
    const doneText = { upload: "اكتمل الرفع", extract: "اكتمل الاستخلاص", classify: "اكتمل التصنيف", report: "صدرت النتيجة" };
    for (const name of STAGE_NAMES) {
      setStage(name, "done", doneText[name], timings[name]);
      await delay(180);
    }
    showMessage("النتيجة: " + VS.LABELS.verdict[result.data.verdict] + " بنسبة ثقة " + VS.percent(result.data.confidence) + ". جارٍ فتح التقرير...", "info");
    await delay(900);
    window.location.assign(result.data.redirect);
  }
  async function handleFile(file) {
    if (!file || busy) { return; }
    fileLabel.textContent = "الملف المحدد: " + file.name;
    try {
      const blob = await prepareFile(file);
      await submit(blob, file.name);
    } catch (error) {
      setStage("upload", "error", "مرفوض");
      showMessage(error.message, "error");
      busy = false;
    }
  }
  function updateTimer() {
    const elapsed = Math.floor((Date.now() - recordStarted) / 1000);
    recTimer.textContent = Math.floor(elapsed / 60) + ":" + String(elapsed % 60).padStart(2, "0") + " / 0:30";
    if (elapsed >= MAX_RECORD_SECONDS) { stopRecording(); }
  }
  async function startRecording() {
    if (busy || recorder) { return; }
    if (!navigator.mediaDevices || !window.MediaRecorder) {
      recStatus.textContent = "المتصفح لا يدعم التسجيل من الميكروفون.";
      return;
    }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (error) {
      recStatus.textContent = "تعذر الوصول إلى الميكروفون، تأكد من منح الإذن للمتصفح.";
      return;
    }
    const chunks = [];
    recorder = new MediaRecorder(stream);
    recorder.addEventListener("dataavailable", (event) => { if (event.data.size) { chunks.push(event.data); } });
    recorder.addEventListener("stop", async () => {
      stream.getTracks().forEach((track) => { track.stop(); });
      clearInterval(recordTimer);
      recorder = null;
      recStart.disabled = false;
      recStop.disabled = true;
      recStatus.textContent = "جارٍ تجهيز التسجيل للفحص...";
      try {
        const wav = await toWav(new Blob(chunks, { type: chunks.length ? chunks[0].type : "audio/webm" }));
        recStatus.textContent = "";
        await submit(wav, "تسجيل مباشر.wav");
      } catch (error) {
        recStatus.textContent = "تعذر معالجة التسجيل، حاول مرة أخرى.";
      }
    });
    recorder.start();
    recordStarted = Date.now();
    recordTimer = setInterval(updateTimer, 250);
    recStart.disabled = true;
    recStop.disabled = false;
    recStatus.textContent = "التسجيل جارٍ...";
  }
  function stopRecording() { if (recorder && recorder.state !== "inactive") { recorder.stop(); } }
  document.getElementById("pick-file").addEventListener("click", (event) => {
    event.stopPropagation();
    fileInput.click();
  });
  dropzone.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { fileInput.click(); } });
  fileInput.addEventListener("change", () => {
    handleFile(fileInput.files[0]);
    fileInput.value = "";
  });
  ["dragenter", "dragover"].forEach((type) => {
    dropzone.addEventListener(type, (event) => {
      event.preventDefault();
      dropzone.classList.add("dragover");
    });
  });
  ["dragleave", "drop"].forEach((type) => {
    dropzone.addEventListener(type, (event) => {
      event.preventDefault();
      dropzone.classList.remove("dragover");
    });
  });
  dropzone.addEventListener("drop", (event) => { handleFile(event.dataTransfer.files[0]); });
  recStart.addEventListener("click", startRecording);
  recStop.addEventListener("click", stopRecording);
})();