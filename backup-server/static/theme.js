"use strict";
// Loaded synchronously in <head> so the saved theme applies before first paint (CSP forbids inline scripts).
(function () {
  const root = document.documentElement;
  root.classList.remove("no-js");
  try {
    const saved = localStorage.getItem("bitx-theme");
    if (saved === "light" || saved === "dark") root.dataset.theme = saved;
  } catch (_) { /* storage unavailable: follow the system */ }
})();
