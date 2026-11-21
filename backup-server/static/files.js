"use strict";

// File Manager and Backup Explorer. API contract unchanged (see docs/FILE-MANAGER.md):
// POST data-api {action, ...} with X-CSRF-Token; GET data-download; POST data-upload.
const fm = document.querySelector("#file-manager");
if (fm) {
  const $ = (id) => document.getElementById("fm-" + id);
  const SVG = "http://www.w3.org/2000/svg";
  const node = (tag, text, className) => {
    const item = document.createElement(tag);
    if (text !== undefined && text !== null) item.textContent = text;
    if (className) item.className = className;
    return item;
  };
  const icon = (name, cls = "icon icon-s") => {
    const svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("class", cls);
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS(SVG, "use");
    use.setAttribute("href", "#i-" + name);
    svg.append(use);
    return svg;
  };
  const button = (label, action, {cls = "secondary small", iconName, title} = {}) => {
    const item = node("button", null, cls);
    item.type = "button";
    if (iconName) item.append(icon(iconName));
    if (label) item.append(document.createTextNode(label));
    if (title) { item.title = title; item.setAttribute("aria-label", title); }
    item.addEventListener("click", () => run(action));
    return item;
  };
  const limit = 100;
  let state = {mode: "files", root: "home", path: "", offset: 0};
  let history = [];
  let position = -1;
  let list = [];
  let locations = [];
  let fileRoots = [];
  let snapshots = [];
  let selected = new Set();
  let grid = false;
  let search = "";
  let generation = 0;
  let previewUrl;
  let total = 0;
  let more = false;
  const compact = window.matchMedia("(max-width: 900px)");
  // On small screens the location list starts collapsed; the current location stays visible in its summary.
  if (compact.matches) document.querySelector(".fm-sidebar").open = false;

  // ---------- formatting ----------
  const lang = document.documentElement.lang || undefined;
  const dateFormat = new Intl.DateTimeFormat(lang, {dateStyle: "medium", timeStyle: "short"});
  const numberFormat = new Intl.NumberFormat(lang, {maximumFractionDigits: 1});
  function bytes(value) {
    if (value === null || value === undefined || value === "") return "";
    let n = Number(value);
    const units = ["B", "KiB", "MiB", "GiB", "TiB"];
    let unit = 0;
    while (n >= 1024 && unit < units.length - 1) { n /= 1024; unit++; }
    return numberFormat.format(n) + " " + units[unit];
  }
  function toDate(value) {
    if (!value) return null;
    const date = new Date(typeof value === "number" ? value * 1000 : String(value).replace(/(\.\d{3})\d+/, "$1"));
    return Number.isNaN(date.getTime()) ? null : date;
  }
  const when = (value) => { const d = toDate(value); return d ? dateFormat.format(d) : ""; };
  const rootLabel = (id) => id === "home" ? "Home" : (id || "").replace(/^share:/, "");
  const accessLabel = (access) => access === "write" ? "Citire și scriere" : "Doar citire";
  function kindOf(entry) {
    if (entry.type === "dir") return "dir";
    const ext = entry.name.includes(".") ? entry.name.split(".").pop().toLowerCase() : "";
    if (["png", "jpg", "jpeg", "gif", "webp", "svg", "heic", "bmp"].includes(ext)) return "img";
    if (["zip", "tar", "gz", "tgz", "7z", "rar", "xz", "bz2", "zst"].includes(ext)) return "arc";
    if (["txt", "md", "pdf", "doc", "docx", "odt", "csv", "xls", "xlsx", "json", "log", "yaml", "yml", "conf"].includes(ext)) return "doc";
    return "file";
  }
  const kindIcon = {dir: "folder", img: "image", arc: "archive", doc: "doc", file: "file"};

  // ---------- transport ----------
  async function api(action, fields = {}) {
    const result = await fetch(fm.dataset.api, {
      method: "POST", credentials: "same-origin",
      headers: {"Content-Type": "application/json", "X-CSRF-Token": fm.dataset.csrf},
      body: JSON.stringify({action, ...fields}),
    });
    if (result.redirected) {
      window.location.assign(result.url);
      throw new Error("Sesiunea a expirat.");
    }
    const data = await result.json();
    if (!result.ok) throw new Error(data.error || "Operațiunea a eșuat.");
    return data;
  }

  function message(text, kind = "error") {
    const box = $("message");
    box.className = "notice " + kind;
    box.textContent = text || "";
  }

  async function run(action) {
    message("");
    try { await action(); }
    catch (error) { message(error.message || "Operațiunea a eșuat."); }
  }

  function route(fields, push = true) {
    state = {...state, ...fields, offset: fields.offset || 0};
    if (push) {
      history = history.slice(0, position + 1).concat([{...state}]);
      position = history.length - 1;
      search = "";
      $("query").value = "";
    }
    return refresh();
  }

  function writeAccess() { return fileRoots.find((root) => root.id === state.root)?.access === "write"; }
  function fullPath(entry) { return entry.path || [state.path, entry.name].filter(Boolean).join("/"); }
  const baseFields = () => ({root: state.root, path: state.path});
  const backupFields = () => ({repo: state.repo, snapshot: state.snapshot, path: state.path});

  // ---------- dialogs ----------
  // ask() replaces prompt()/confirm(). Resolves to {field: value} or null when cancelled.
  function ask({title, intro, fields = [], ok = "Confirmă", danger = false}) {
    const dialog = $("dialog");
    return new Promise((resolve) => {
      const form = node("form");
      form.method = "dialog";
      const head = node("div", null, "dialog-head");
      head.append(node("h2", title));
      const body = node("div", null, "dialog-body");
      if (intro) (Array.isArray(intro) ? intro : [intro]).forEach((p) => body.append(typeof p === "string" ? node("p", p, "muted") : p));
      const inputs = {};
      for (const field of fields) {
        const label = node("label", field.label);
        let input;
        if (field.options) {
          input = node("select");
          for (const [value, text] of field.options) {
            const option = node("option", text);
            option.value = value;
            input.append(option);
          }
          if (field.value !== undefined) input.value = field.value;
        } else {
          input = node("input");
          input.value = field.value || "";
          input.autocomplete = "off";
          input.spellcheck = false;
          if (field.placeholder) input.placeholder = field.placeholder;
          if (field.maxlength) input.maxLength = field.maxlength;
        }
        input.name = field.name;
        if (field.required) input.required = true;
        label.append(input);
        if (field.hint) label.append(node("span", field.hint, "hint"));
        body.append(label);
        inputs[field.name] = input;
      }
      const foot = node("div", null, "dialog-foot");
      const cancel = node("button", "Anulează", "secondary");
      cancel.type = "button";
      const submit = node("button", ok, danger ? "danger" : "");
      submit.value = "ok";
      const validate = () => {
        submit.disabled = fields.some((f) => (f.required && !inputs[f.name].value) || (f.match !== undefined && inputs[f.name].value !== f.match));
      };
      Object.values(inputs).forEach((input) => input.addEventListener("input", validate));
      validate();
      foot.append(cancel, submit);
      form.append(head, body, foot);
      dialog.replaceChildren(form);
      let answered = false;
      cancel.addEventListener("click", () => dialog.close());
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        if (submit.disabled) return;
        answered = true;
        const values = {};
        for (const [name, input] of Object.entries(inputs)) values[name] = input.value;
        dialog.close();
        resolve(values);
      });
      dialog.addEventListener("close", () => { if (!answered) resolve(null); }, {once: true});
      dialog.showModal();
      const first = Object.values(inputs)[0];
      if (first) { first.focus(); if (first.select) first.select(); } else submit.focus();
    });
  }

  function writableRoots() {
    return fileRoots.filter((item) => item.access === "write").map((item) => [item.id, rootLabel(item.id)]);
  }

  // ---------- data ----------
  async function refresh() {
    const id = ++generation;
    fm.dataset.mode = state.mode;
    $("files").setAttribute("aria-pressed", String(state.mode === "files"));
    $("backups").setAttribute("aria-pressed", String(state.mode === "backup"));
    $("entries").replaceChildren(loading());
    selected = new Set();
    $("back").disabled = position <= 0;
    $("forward").disabled = position >= history.length - 1;
    $("sort").closest("label").hidden = state.mode !== "files";
    fileRoots = await api("roots");
    if (state.mode === "files") {
      locations = fileRoots;
      if (!locations.some((row) => row.id === state.root)) state.root = locations[0]?.id;
    } else {
      locations = await api("repositories");
    }
    if (id !== generation) return;
    renderRoots();
    renderBreadcrumb();
    let data = {entries: [], more: false};
    $("snapbar").hidden = true;
    if (state.mode === "files" && state.root) {
      data = await api("list", {...baseFields(), offset: state.offset, sort: $("sort").value, q: search});
      data.more = state.offset + limit < data.total;
      total = data.total;
    } else if (state.repo) {
      snapshots = await api("snapshots", {repo: state.repo});
      if (id !== generation) return;
      if (!state.snapshot || !snapshots.some((s) => s.id === state.snapshot)) state.snapshot = snapshots[0]?.id;
      renderSnapbar();
      if (state.snapshot) data = await api("tree", {...backupFields(), offset: state.offset, q: search});
      total = null;
    }
    if (id !== generation) return;
    list = data.entries;
    more = data.more;
    $("prev").disabled = state.offset === 0;
    $("next").disabled = !data.more;
    renderContext();
    render();
    actions();
    jobs();
  }

  function loading() {
    const box = node("div", null, "fm-loading");
    box.setAttribute("aria-label", "Se încarcă…");
    for (let i = 0; i < 5; i++) box.append(node("span"));
    return box;
  }

  // ---------- chrome ----------
  function renderRoots() {
    const heading = $("roots-title");
    heading.replaceChildren(document.createTextNode(state.mode === "files" ? "Locații" : "Repository-uri"));
    const current = state.mode === "files" ? rootLabel(state.root) : state.repo;
    if (current && compact.matches) heading.append(node("span", " · " + current, "current"));
    $("roots").replaceChildren();
    if (!locations.length) {
      $("roots").append(node("p", state.mode === "files" ? "Nicio locație disponibilă." : "Niciun repository autorizat. Cere acces administratorului.", "muted small"));
    }
    for (const item of locations) {
      const isFiles = state.mode === "files";
      const name = item.name ?? rootLabel(item.id);
      const meta = isFiles ? accessLabel(item.access)
        : [item.client, item.latest ? when(item.latest) : "", item.size ? bytes(item.size) : ""].filter(Boolean).join(" · ") || item.state || "";
      const entry = node("button");
      entry.type = "button";
      entry.append(icon(isFiles ? (item.id === "home" ? "home" : "shares") : "repositories", "icon"),
        node("span", name, "root-name"));
      if (isFiles && item.access !== "write") entry.append(icon("lock", "icon icon-s"));
      else entry.append(node("span"));
      if (meta) entry.append(node("span", meta, "root-meta"));
      const current = isFiles ? state.root === item.id : state.repo === item.name;
      if (current) entry.setAttribute("aria-current", "true");
      entry.addEventListener("click", () => run(() => (compact.matches && (document.querySelector(".fm-sidebar").open = false), route(
        isFiles ? {root: item.id, path: ""} : {repo: item.name, snapshot: null, path: ""},
      ))));
      $("roots").append(entry);
    }
  }

  function renderBreadcrumb() {
    const crumbs = $("breadcrumb");
    crumbs.replaceChildren();
    const anchor = state.mode === "files" ? rootLabel(state.root) : state.repo;
    if (!anchor) { crumbs.append(node("span", state.mode === "files" ? "" : "Alege un repository", "muted small")); return; }
    crumbs.append(button(anchor, () => route({path: ""}), {cls: "", iconName: state.mode === "files" ? (state.root === "home" ? "home" : "shares") : "repositories"}));
    let prefix = "";
    for (const part of state.path.split("/").filter(Boolean)) {
      prefix = prefix ? prefix + "/" + part : part;
      const target = prefix;
      crumbs.append(node("span", "/", "sep"), button(part, () => route({path: target}), {cls: ""}));
    }
  }

  function renderSnapbar() {
    const bar = $("snapbar");
    bar.replaceChildren();
    bar.hidden = false;
    if (!snapshots.length) {
      bar.append(node("span", "Repository-ul nu are snapshoturi disponibile.", "muted small"));
      return;
    }
    const label = node("label");
    label.append(icon("history"), node("span", "Snapshot"));
    const picker = node("select");
    picker.setAttribute("aria-label", "Snapshot");
    for (const snapshot of snapshots) {
      const option = node("option", [when(snapshot.time) || snapshot.time, snapshot.hostname, snapshot.id.slice(0, 8)].filter(Boolean).join(" · "));
      option.value = snapshot.id;
      picker.append(option);
    }
    picker.value = state.snapshot;
    picker.addEventListener("change", () => run(() => route({snapshot: picker.value, path: ""})));
    label.append(picker);
    bar.append(label, node("span", snapshots.length + " snapshoturi", "pill plain"));
  }

  function renderContext() {
    const box = $("context");
    box.replaceChildren();
    if (state.mode === "files") {
      box.className = "fm-context live";
      const access = writeAccess();
      box.append(node("span", "Live", "pill info"), node("span", access ? "Citire și scriere" : "Doar citire", "pill " + (access ? "ok" : "plain")),
        node("p", access ? "Fișierele de pe disc. Modificările se aplică imediat." : "Poți descărca și copia fișiere, dar nu le poți modifica aici."));
    } else {
      box.className = "fm-context snapshot";
      const snap = snapshots.find((s) => s.id === state.snapshot);
      box.append(node("span", "Snapshot", "pill snapshot"), node("span", "Doar citire", "pill plain"),
        node("p", snap ? "Copie din " + when(snap.time) + ". Nu se poate modifica. Restaurarea creează un director nou și nu suprascrie nimic."
          : "Backupurile Restic autorizate. Alege un repository, apoi un snapshot."));
    }
  }

  // ---------- entries ----------
  function downloadUrl(entry) {
    const fields = state.mode === "files"
      ? {...baseFields(), name: entry.name}
      : {...backupFields(), path: fullPath(entry), kind: entry.type};
    return fm.dataset.download + "?" + new URLSearchParams(fields);
  }

  function download(entry) { window.location.assign(downloadUrl(entry)); }

  function details(entry) {
    const size = entry.size === null || entry.size === undefined ? "" : bytes(entry.size);
    return [entry.type === "dir" ? "Director" : "Fișier", size, when(entry.mtime)].filter(Boolean).join(" · ");
  }

  function closeMenus(except) {
    fm.querySelectorAll(".fm-menu[open]").forEach((menu) => { if (menu !== except) menu.open = false; });
  }

  function menuFor(entry) {
    const menu = node("details", null, "fm-menu");
    const summary = node("summary");
    summary.setAttribute("aria-label", "Acțiuni pentru " + entry.name);
    summary.append(icon("more"));
    menu.append(summary);
    const items = node("div");
    items.setAttribute("role", "menu");
    const add = (label, iconName, action, danger = false) => {
      const item = button(label, async () => { menu.open = false; await action(); }, {cls: danger ? "danger-item" : "", iconName});
      item.setAttribute("role", "menuitem");
      items.append(item);
    };
    if (entry.type === "dir") add("Deschide", "folder", () => route({path: fullPath(entry)}));
    if (entry.type === "file") add("Previzualizează", "eye", () => preview(entry));
    if (entry.type === "file" || state.mode === "backup") add(entry.type === "dir" ? "Descarcă (TAR)" : "Descarcă", "download", () => download(entry));
    if (state.mode === "backup") add("Restaurează…", "restore", () => restore(entry));
    if (state.mode === "files") add("Copiază…", "copy", () => mutate("copy", entry));
    if (state.mode === "files" && writeAccess()) {
      add("Redenumește…", "edit", () => mutate("rename", entry));
      add("Mută…", "move", () => mutate("move", entry));
    }
    items.append(node("hr"));
    add("Proprietăți", "info", () => properties(entry));
    if (state.mode === "files" && writeAccess()) add("Șterge…", "trash", () => mutate("delete", entry), true);
    menu.append(items);
    menu.addEventListener("toggle", () => {
      if (!menu.open) return;
      closeMenus(menu);
      items.classList.toggle("up", menu.getBoundingClientRect().bottom + 320 > window.innerHeight && menu.getBoundingClientRect().top > 340);
    });
    return menu;
  }

  function render() {
    const container = $("entries");
    container.classList.toggle("fm-grid", grid);
    container.replaceChildren();
    if (!list.length) {
      const empty = node("div", null, "empty fm-empty");
      let title = "Folder gol";
      let hint = state.mode === "files" && writeAccess() ? "Trage fișiere aici sau folosește Încarcă." : "";
      if (search) { title = "Niciun rezultat"; hint = "Încearcă alt termen de căutare."; }
      else if (state.mode === "backup" && !state.repo) { title = "Selectează un repository autorizat."; hint = "Repository-urile apar în coloana din stânga."; }
      else if (state.mode === "backup" && !state.snapshot) { title = "Niciun snapshot"; hint = ""; }
      empty.append(icon(state.mode === "backup" ? "history" : "folder", "icon"), node("strong", title));
      if (hint) empty.append(node("span", hint));
      container.append(empty);
    } else if (!grid) {
      const head = node("div", null, "fm-head");
      const all = node("input");
      all.type = "checkbox";
      all.setAttribute("aria-label", "Selectează tot");
      all.addEventListener("change", () => {
        selected = all.checked ? new Set(list) : new Set();
        render();
        actions();
      });
      all.checked = selected.size > 0 && selected.size === list.length;
      head.append(all, node("span", "Nume"), node("span", "Dimensiune", "num"), node("span", "Modificat", "date"), node("span"));
      container.append(head);
    }
    for (const entry of list) {
      const item = node("article", null, "fm-entry" + (selected.has(entry) ? " selected" : ""));
      const select = node("input");
      select.type = "checkbox";
      select.setAttribute("aria-label", "Selectează " + entry.name);
      select.checked = selected.has(entry);
      select.addEventListener("change", () => {
        if (select.checked) selected.add(entry); else selected.delete(entry);
        item.classList.toggle("selected", select.checked);
        actions();
      });
      const open = () => entry.type === "dir" ? route({path: fullPath(entry)}) : preview(entry);
      const wrap = node("div", null, "open-wrap");
      const title = node("button", null, "open");
      title.type = "button";
      const kind = kindOf(entry);
      const badge = node("span", null, "ficon " + kind);
      badge.append(icon(kindIcon[kind], "icon"));
      title.append(badge, node("span", entry.name));
      title.title = entry.name;
      title.addEventListener("click", () => run(open));
      wrap.append(title, node("small", details(entry), "meta-line"));
      item.addEventListener("contextmenu", (event) => { event.preventDefault(); menu.open = true; });
      const menu = menuFor(entry);
      item.append(select, wrap, node("span", entry.type === "dir" ? "—" : bytes(entry.size), "size"), node("span", when(entry.mtime), "date"), menu);
      container.append(item);
    }
    const count = list.length ? (total !== null && total !== undefined ? total + " elemente" : list.length + (more ? "+" : "") + " elemente") : "";
    $("count").textContent = count + (state.offset ? " · de la " + (state.offset + 1) : "");
  }

  // ---------- operations ----------
  async function mutate(action, entry, update = true) {
    const data = {...baseFields(), name: entry.name};
    if (action === "delete") {
      const answer = await ask({
        title: "Ștergi „" + entry.name + "”?",
        intro: [entry.type === "dir" ? "Directorul va fi șters definitiv cu tot conținutul." : "Fișierul va fi șters definitiv.",
          "Pentru confirmare, introdu numele exact."],
        fields: [{name: "confirm", label: "Nume", placeholder: entry.name, match: entry.name, required: true}],
        ok: "Șterge definitiv", danger: true,
      });
      if (!answer || answer.confirm !== entry.name) return;
      data.confirm = answer.confirm;
    } else {
      const titles = {rename: "Redenumește", move: "Mută", copy: "Copiază"};
      const fields = [{name: "target", label: action === "rename" ? "Nume nou" : "Nume la destinație", value: entry.name, required: true, maxlength: 255}];
      if (action !== "rename") {
        const roots = writableRoots();
        if (!roots.length) throw new Error("Nu ai nicio locație cu drept de scriere.");
        fields.push({name: "dest_root", label: "Locație destinație", options: roots, value: state.root && roots.some((r) => r[0] === state.root) ? state.root : roots[0][0]});
        fields.push({name: "dest", label: "Subdirector la destinație", value: "", placeholder: "gol pentru rădăcină", hint: "Ex. Documente/2026. Un fișier existent nu este înlocuit."});
      }
      const answer = await ask({title: titles[action] + " „" + entry.name + "”", fields, ok: titles[action]});
      if (!answer || !answer.target) return;
      Object.assign(data, answer);
    }
    await api(action, data);
    if (update) {
      await refresh();
      message({delete: "Element șters.", rename: "Redenumit.", move: "Mutat.", copy: "Copiat."}[action], "ok");
    }
  }

  async function restore(entry) {
    const roots = writableRoots();
    if (!roots.length) throw new Error("Nu ai nicio locație cu drept de scriere pentru restaurare.");
    const snap = snapshots.find((s) => s.id === state.snapshot);
    const answer = await ask({
      title: "Restaurează „" + entry.name + "”",
      intro: ["Din snapshotul " + (snap ? when(snap.time) + " · " + snap.id.slice(0, 8) : state.snapshot) + ".",
        "Se creează un director nou; fișierele existente nu sunt suprascrise. Restaurarea rulează în fundal."],
      fields: [
        {name: "root", label: "Locație destinație", options: roots, value: roots[0][0]},
        {name: "target", label: "Numele unui director nou", value: "restore-" + new Date().toISOString().slice(0, 10), required: true, maxlength: 255},
      ],
      ok: "Pornește restaurarea",
    });
    if (!answer || !answer.target) return;
    await api("restore", {...backupFields(), path: fullPath(entry), kind: entry.type, root: answer.root, target: answer.target, confirm: answer.target});
    message("Restaurare pusă în coadă către " + rootLabel(answer.root) + "/" + answer.target + ".", "ok");
    await jobs();
  }

  const jobStyle = {queued: ["plain", "În așteptare"], running: ["info", "În curs"], complete: ["ok", "Finalizat"], failed: ["danger", "Eșuat"], interrupted: ["warn", "Întrerupt"]};
  async function jobs() {
    if (state.mode !== "backup") { $("jobs").replaceChildren(); return; }
    try {
      const records = await api("jobs");
      if (!records.length) { $("jobs").replaceChildren(); return; }
      const rows = records.map((job) => {
        const row = node("div", null, "job");
        const [cls, label] = jobStyle[job.status] || ["plain", job.status];
        const target = job.target.replace(/^share:/, "");
        row.append(node("span", label, "pill " + cls), node("span", target, "job-target"), node("span", bytes(job.restored), "job-bytes"), node("span", job.message, "job-msg"));
        if (job.status === "running") { const bar = node("progress"); bar.setAttribute("aria-label", "Restaurare în curs"); row.append(bar); }
        return row;
      });
      $("jobs").replaceChildren(node("h3", "Restaurări"), ...rows);
    } catch (_) {
      $("jobs").replaceChildren(node("p", "Starea restaurărilor este momentan indisponibilă.", "muted small"));
    }
  }

  function show(title, ...content) {
    $("preview-title").textContent = title;
    $("preview-content").replaceChildren(...content);
    $("preview").showModal();
  }

  function properties(entry) {
    const list = node("dl", null, "kv");
    const rows = [["Nume", entry.name], ["Tip", entry.type === "dir" ? "Director" : "Fișier"],
      ["Dimensiune", entry.type === "dir" ? "—" : bytes(entry.size) + " (" + Number(entry.size).toLocaleString(lang) + " B)"],
      ["Modificat", when(entry.mtime) || "—"],
      ["Locație", state.mode === "files" ? rootLabel(state.root) + "/" + fullPath(entry) : state.repo + " · " + (state.snapshot || "").slice(0, 8) + " · /" + fullPath(entry)]];
    for (const [k, v] of rows) list.append(node("dt", k), node("dd", v));
    show("Proprietăți", list);
  }

  async function preview(entry) {
    const downloadButton = button("Descarcă", () => download(entry), {cls: "", iconName: "download"});
    if (entry.size > 2 * 1024 * 1024) {
      show(entry.name, node("p", "Fișier prea mare pentru previzualizare (" + bytes(entry.size) + ").", "muted"), downloadButton);
      return;
    }
    const image = {png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif", webp: "image/webp"};
    const extension = entry.name.split(".").pop().toLowerCase();
    if (!image[extension] && !["txt", "log", "csv", "json", "md", "yaml", "yml"].includes(extension)) {
      show(entry.name, node("p", "Previzualizare indisponibilă pentru acest tip de fișier.", "muted"), downloadButton);
      return;
    }
    const response = await fetch(downloadUrl(entry));
    if (!response.ok || response.redirected) throw new Error("Fișierul nu a putut fi citit.");
    if (image[extension]) {
      const img = node("img");
      previewUrl = URL.createObjectURL(new Blob([await response.arrayBuffer()], {type: image[extension]}));
      img.src = previewUrl;
      img.alt = entry.name;
      show(entry.name, img);
    } else {
      show(entry.name, node("pre", await response.text()));
    }
  }

  async function upload(files) {
    files = [...files];
    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      message("Se încarcă " + (i + 1) + " din " + files.length + ": " + file.name + " (" + bytes(file.size) + ")…", "info");
      const params = new URLSearchParams({...baseFields(), name: file.name});
      const result = await fetch(fm.dataset.upload + "?" + params, {
        method: "POST", credentials: "same-origin", headers: {"X-CSRF-Token": fm.dataset.csrf}, body: file,
      });
      if (!result.ok) throw new Error((await result.json().catch(() => ({}))).error || "Upload eșuat.");
    }
    await refresh();
    message(files.length === 1 ? "Fișier încărcat." : files.length + " fișiere încărcate.", "ok");
  }

  function actions() {
    const bar = $("actions");
    bar.replaceChildren();
    if (state.mode === "files" && writeAccess()) {
      bar.append(button("Director nou", async () => {
        const answer = await ask({title: "Director nou", fields: [{name: "name", label: "Numele directorului", required: true, maxlength: 255}], ok: "Creează"});
        if (answer?.name) { await api("mkdir", {...baseFields(), name: answer.name}); await refresh(); message("Director creat.", "ok"); }
      }, {iconName: "folder-plus"}));
      bar.append(button("Încarcă", () => $("upload").click(), {cls: "small", iconName: "upload"}));
    }
    if (state.mode === "backup" && state.snapshot && snapshots.length > 1) {
      bar.append(button("Compară snapshoturi", async () => {
        const items = snapshots.filter((item) => item.id !== state.snapshot);
        const answer = await ask({
          title: "Compară snapshoturi",
          intro: "Afișează fișierele adăugate, eliminate și modificate față de snapshotul curent.",
          fields: [{name: "other", label: "Compară cu", options: items.map((s) => [s.id, [when(s.time) || s.time, s.hostname, s.id.slice(0, 8)].filter(Boolean).join(" · ")])}],
          ok: "Compară",
        });
        if (answer?.other) show("Diferențe între snapshoturi", node("pre", await api("diff", {...backupFields(), other: answer.other})));
      }, {iconName: "diff"}));
    }
    const sel = $("selection");
    sel.replaceChildren();
    sel.hidden = !selected.size;
    if (selected.size) {
      sel.append(node("span", selected.size + (selected.size === 1 ? " element selectat" : " elemente selectate")));
      if (state.mode === "files" && writeAccess()) {
        sel.append(button("Șterge selecția", async () => {
          for (const entry of [...selected]) await mutate("delete", entry, false);
          await refresh();
        }, {cls: "danger small", iconName: "trash"}));
      }
      if (state.mode === "backup") {
        sel.append(button("Descarcă selecția", () => {
          const entries = [...selected];
          if (entries.length === 1) download(entries[0]);
          else {
            const fields = {...backupFields(), paths: JSON.stringify(entries.map((entry) => fullPath(entry)))};
            window.location.assign(fm.dataset.download + "?" + new URLSearchParams(fields));
          }
        }, {iconName: "download"}));
        if (selected.size > 1) sel.append(node("span", "Arhivă TAR: maximum 20 de fișiere, 8 GiB", "muted small"));
      }
      sel.append(button("Anulează selecția", () => { selected = new Set(); render(); actions(); }, {cls: "ghost small"}));
    }
  }

  // ---------- wiring ----------
  $("filter").addEventListener("submit", (event) => {
    event.preventDefault();
    search = $("query").value;
    run(() => route({offset: 0}, false));
  });
  $("sort").addEventListener("change", () => run(() => route({offset: 0}, false)));
  $("files").onclick = () => run(() => route({mode: "files", root: "home", path: ""}));
  $("backups").onclick = () => run(() => route({mode: "backup", repo: null, snapshot: null, path: ""}));
  $("prev").onclick = () => run(() => route({offset: Math.max(0, state.offset - limit)}, false));
  $("next").onclick = () => run(() => route({offset: state.offset + limit}, false));
  $("back").onclick = () => run(() => { state = {...history[--position]}; return refresh(); });
  $("forward").onclick = () => run(() => { state = {...history[++position]}; return refresh(); });
  $("view").onclick = () => {
    grid = !grid;
    $("view").replaceChildren(icon(grid ? "list" : "grid"));
    $("view").title = grid ? "Listă" : "Grilă";
    $("view").setAttribute("aria-label", grid ? "Vizualizare listă" : "Vizualizare grilă");
    try { localStorage.setItem("bitx-fm-grid", grid ? "1" : ""); } catch (_) { /* ignore */ }
    render();
  };
  try { if (localStorage.getItem("bitx-fm-grid")) { grid = true; $("view").replaceChildren(icon("list")); $("view").title = "Listă"; } } catch (_) { /* ignore */ }
  $("preview").addEventListener("close", () => { if (previewUrl) URL.revokeObjectURL(previewUrl); previewUrl = undefined; });
  $("upload").addEventListener("change", (event) => run(async () => {
    try { await upload(event.target.files); } finally { event.target.value = ""; }
  }));
  const entries = $("entries");
  entries.addEventListener("dragover", (event) => {
    if (state.mode !== "files" || !writeAccess() || !event.dataTransfer?.types.includes("Files")) return;
    event.preventDefault();
    entries.classList.add("dragging");
  });
  entries.addEventListener("dragleave", (event) => { if (!entries.contains(event.relatedTarget)) entries.classList.remove("dragging"); });
  entries.addEventListener("drop", (event) => {
    entries.classList.remove("dragging");
    if (state.mode !== "files" || !writeAccess() || !event.dataTransfer?.files.length) return;
    event.preventDefault();
    run(() => upload(event.dataTransfer.files));
  });
  document.addEventListener("click", (event) => { if (!event.target.closest(".fm-menu")) closeMenus(); });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeMenus(); });
  setInterval(jobs, 3000);
  run(() => route({}));
}
