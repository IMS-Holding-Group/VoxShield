"use strict";
(() => {
  const running = document.getElementById("training-running");
  if (running) { setTimeout(() => { window.location.reload(); }, 5000); }
})();