"use strict";
// Progressive enhancements for every console page. The console works without this file.
(function () {
  const root = document.documentElement;

  // Theme: auto (system) / light / dark, stored per browser only.
  const buttons = document.querySelectorAll("[data-theme-set]");
  const current = () => root.dataset.theme || "auto";
  const paint = () => buttons.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.themeSet === current())));
  buttons.forEach((b) => b.addEventListener("click", () => {
    const value = b.dataset.themeSet;
    if (value === "auto") delete root.dataset.theme; else root.dataset.theme = value;
    try { if (value === "auto") localStorage.removeItem("bitx-theme"); else localStorage.setItem("bitx-theme", value); } catch (_) { /* ignore */ }
    paint();
  }));
  paint();

  // Mobile navigation drawer.
  const toggle = document.getElementById("nav-toggle");
  const scrim = document.querySelector(".scrim");
  const setNav = (open) => {
    document.body.classList.toggle("nav-open", open);
    if (toggle) toggle.setAttribute("aria-expanded", String(open));
  };
  if (toggle) toggle.addEventListener("click", () => setNav(!document.body.classList.contains("nav-open")));
  if (scrim) scrim.addEventListener("click", () => setNav(false));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") setNav(false); });

  // Localised timestamps: <time datetime="..."> rendered server-side in UTC.
  const format = new Intl.DateTimeFormat(document.documentElement.lang || undefined, {dateStyle: "medium", timeStyle: "short"});
  document.querySelectorAll("time[datetime]").forEach((el) => {
    const value = new Date(el.getAttribute("datetime").replace(/(\.\d{3})\d+/, "$1"));
    if (!Number.isNaN(value.getTime())) {
      el.title = el.getAttribute("datetime");
      el.textContent = format.format(value);
    }
  });

  // Confirmation for destructive submit buttons (server-side checks remain authoritative).
  document.querySelectorAll("button[data-confirm]").forEach((b) => b.addEventListener("click", (event) => {
    if (!window.confirm(b.dataset.confirm)) event.preventDefault();
  }));

  // Client-side filter for tables marked data-filterable (Activity).
  document.querySelectorAll("[data-filter-for]").forEach((tools) => {
    const table = document.getElementById(tools.dataset.filterFor);
    if (!table) return;
    const text = tools.querySelector("input[type=search]");
    const result = tools.querySelector("select");
    const counter = tools.querySelector("[data-filter-count]");
    const rows = [...table.tBodies[0].rows].filter((r) => !r.querySelector("td[colspan]"));
    const apply = () => {
      const q = (text?.value || "").trim().toLowerCase();
      const r = result?.value || "";
      let shown = 0;
      rows.forEach((row) => {
        const visible = (!q || row.textContent.toLowerCase().includes(q)) && (!r || row.dataset.result === r);
        row.hidden = !visible;
        if (visible) shown++;
      });
      if (counter) counter.textContent = shown + " / " + rows.length;
    };
    text?.addEventListener("input", apply);
    result?.addEventListener("change", apply);
    tools.hidden = false;
    apply();
  });
})();
