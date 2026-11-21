"use strict";

const fm = document.querySelector("#file-manager");
if (fm) {
  const $ = (id) => document.getElementById("fm-" + id);
  const node = (tag, text) => {
    const item = document.createElement(tag);
    if (text !== undefined) item.textContent = text;
    return item;
  };
  const button = (label, action) => {
    const item = node("button", label);
    item.className = "secondary";
    item.type = "button";
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

  async function run(action) {
    $("message").textContent = "";
    try { await action(); }
    catch (error) { $("message").textContent = error.message || "Operațiunea a eșuat."; }
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

  async function refresh() {
    const id = ++generation;
    $("entries").replaceChildren(node("p", "Se încarcă…"));
    selected = new Set();
    $("back").disabled = position <= 0;
    $("forward").disabled = position >= history.length - 1;
    fileRoots = await api("roots");
    if (state.mode === "files") {
      locations = fileRoots;
      if (!locations.some((row) => row.id === state.root)) state.root = locations[0]?.id;
    } else {
      locations = await api("repositories");
    }
    if (id !== generation) return;
    $("roots").replaceChildren();
    for (const item of locations) {
      const name = item.name ?? (item.id === "home" ? "Home" : item.id.slice(6));
      const details = [item.client, item.state, item.size ? Number(item.size).toLocaleString() + " B" : "", item.latest]
        .filter(Boolean).map((value) => " · " + value).join("");
      $("roots").append(button(name + details, () => route(
        state.mode === "files" ? {root: item.id, path: ""} : {repo: item.name, snapshot: null, path: ""},
      )));
    }
    $("breadcrumb").replaceChildren();
    const anchor = state.mode === "files" ? (state.root === "home" ? "Home" : state.root?.slice(6)) : state.repo;
    if (anchor) $("breadcrumb").append(button(anchor, () => route({path: ""})));
    let prefix = "";
    for (const part of state.path.split("/").filter(Boolean)) {
      prefix = prefix ? prefix + "/" + part : part;
      const target = prefix;
      $("breadcrumb").append(button(part, () => route({path: target})));
    }
    let data = {entries: [], more: false};
    if (state.mode === "files" && state.root) {
      data = await api("list", {...baseFields(), offset: state.offset, sort: $("sort").value, q: search});
      data.more = state.offset + limit < data.total;
    } else if (state.repo) {
      snapshots = await api("snapshots", {repo: state.repo});
      if (id !== generation) return;
      const picker = node("select");
      picker.setAttribute("aria-label", "Snapshot");
      for (const snapshot of snapshots) {
        const option = node("option", snapshot.time + " · " + (snapshot.hostname || snapshot.id.slice(0, 8)));
        option.value = snapshot.id;
        picker.append(option);
      }
      if (!state.snapshot || !snapshots.some((s) => s.id === state.snapshot)) state.snapshot = snapshots[0]?.id;
      if (state.snapshot) {
        picker.value = state.snapshot;
        picker.addEventListener("change", () => run(() => route({snapshot: picker.value, path: ""})));
        $("breadcrumb").append(picker, node("span", snapshots.length + " snapshoturi"));
        data = await api("tree", {...backupFields(), offset: state.offset, q: search});
      }
    }
    if (id !== generation) return;
    list = data.entries;
    $("prev").disabled = state.offset === 0;
    $("next").disabled = !data.more;
    render();
    actions();
  }

  function downloadUrl(entry) {
    const fields = state.mode === "files"
      ? {...baseFields(), name: entry.name}
      : {...backupFields(), path: fullPath(entry), kind: entry.type};
    return fm.dataset.download + "?" + new URLSearchParams(fields);
  }

  function download(entry) { window.location.assign(downloadUrl(entry)); }

  function details(entry) {
    const numeric = Number(entry.size);
    const size = entry.size === null || entry.size === undefined ? "" : " · " + numeric.toLocaleString() + " B";
    const date = entry.mtime ? " · " + new Date(typeof entry.mtime === "number" ? entry.mtime * 1000 : entry.mtime).toLocaleString() : "";
    return (entry.type === "dir" ? "Director" : "Fișier") + size + date;
  }

  function render() {
    const container = $("entries");
    container.classList.toggle("fm-grid", grid);
    container.replaceChildren();
    if (!list.length) container.append(node("p", state.mode === "backup" && !state.repo
      ? "Selectează un repository autorizat." : "Niciun rezultat."));
    for (const entry of list) {
      const item = node("article");
      item.className = "fm-entry";
      const select = node("input");
      select.type = "checkbox";
      select.setAttribute("aria-label", "Selectează " + entry.name);
      select.checked = selected.has(entry);
      select.addEventListener("change", () => {
        if (select.checked) selected.add(entry); else selected.delete(entry);
        actions();
      });
      const label = node("div");
      const open = () => entry.type === "dir" ? route({path: fullPath(entry)}) : preview(entry);
      const title = button((entry.type === "dir" ? "▸ " : "") + entry.name, open);
      title.addEventListener("dblclick", () => run(open));
      label.append(title, node("small", details(entry)));
      const menu = node("details");
      menu.append(node("summary", "Acțiuni"));
      const items = node("div");
      items.append(button("Proprietăți", () => show(node("p", entry.name + " · " + details(entry)))));
      if (entry.type === "file" || state.mode === "backup") items.append(button("Descarcă", () => download(entry)));
      if (entry.type === "file") items.append(button("Previzualizează", () => preview(entry)));
      if (state.mode === "backup") items.append(button("Restaurează", () => restore(entry)));
      if (state.mode === "files" && writeAccess()) {
        items.append(button("Redenumește", () => mutate("rename", entry)));
        items.append(button("Mută", () => mutate("move", entry)));
        items.append(button("Șterge", () => mutate("delete", entry)));
      }
      if (state.mode === "files") items.append(button("Copiază", () => mutate("copy", entry)));
      menu.append(items);
      item.addEventListener("contextmenu", (event) => { event.preventDefault(); menu.open = true; });
      item.append(select, label, menu);
      container.append(item);
    }
  }

  function locationPrompt() {
    const writable = fileRoots.filter((item) => item.access === "write").map((item) => item.id);
    return prompt("Destinație: " + writable.join(", "), "home");
  }

  async function mutate(action, entry, update = true) {
    const data = {...baseFields(), name: entry.name};
    if (action === "delete") {
      data.confirm = prompt("Pentru ștergere definitivă, introdu numele exact. Un director va fi șters cu tot conținutul.");
      if (data.confirm !== entry.name) return;
    } else {
      data.target = prompt("Nume la destinație", entry.name);
      if (!data.target) return;
      if (action !== "rename") {
        data.dest_root = locationPrompt();
        if (!data.dest_root) return;
        data.dest = prompt("Subdirector la destinație (gol pentru rădăcină)", "");
        if (data.dest === null) return;
      }
    }
    await api(action, data);
    if (update) await refresh();
  }

  async function restore(entry) {
    const root = locationPrompt();
    if (!root) return;
    const target = prompt("Numele unui director nou", "restore-" + new Date().toISOString().slice(0, 10));
    if (!target || !confirm("Restaurare în " + root + "/" + target + "?")) return;
    await api("restore", {...backupFields(), path: fullPath(entry), kind: entry.type, root, target, confirm: target});
    await jobs();
  }

  async function jobs() {
    if (state.mode !== "backup") return;
    try {
      const records = await api("jobs");
      $("jobs").replaceChildren(...records.map((job) =>
        node("p", job.target + " · " + job.message + " · " + job.restored.toLocaleString() + " B")));
    } catch (_) {
      $("jobs").replaceChildren(node("p", "Starea restaurărilor este momentan indisponibilă."));
    }
  }

  function show(content) {
    $("preview-content").replaceChildren(content);
    $("preview").showModal();
  }

  async function preview(entry) {
    if (entry.size > 2 * 1024 * 1024) {
      show(node("p", "Fișier prea mare pentru previzualizare."));
      return;
    }
    const image = {png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif", webp: "image/webp"};
    const extension = entry.name.split(".").pop().toLowerCase();
    if (!image[extension] && !["txt", "log", "csv", "json", "md", "yaml", "yml"].includes(extension)) {
      show(node("p", "Previzualizare indisponibilă pentru acest tip de fișier."));
      return;
    }
    const response = await fetch(downloadUrl(entry));
    if (!response.ok || response.redirected) throw new Error("Fișierul nu a putut fi citit.");
    if (image[extension]) {
      const img = node("img");
      previewUrl = URL.createObjectURL(new Blob([await response.arrayBuffer()], {type: image[extension]}));
      img.src = previewUrl;
      img.alt = entry.name;
      show(img);
    } else {
      show(node("pre", await response.text()));
    }
  }

  function actions() {
    const bar = $("actions");
    bar.replaceChildren();
    if (state.mode === "files" && writeAccess()) {
      bar.append(button("Director nou", async () => {
        const name = prompt("Numele directorului");
        if (name) { await api("mkdir", {...baseFields(), name}); await refresh(); }
      }));
      bar.append(button("Încarcă fișiere", () => $("upload").click()));
    }
    if (selected.size) {
      if (state.mode === "files" && writeAccess()) {
        bar.append(button("Șterge selecția", async () => {
          for (const entry of selected) await mutate("delete", entry, false);
          await refresh();
        }));
      }
      if (state.mode === "backup") {
        bar.append(button("Descarcă selecția", () => {
          const entries = [...selected];
          if (entries.length === 1) download(entries[0]);
          else {
            const fields = {...backupFields(), paths: JSON.stringify(entries.map((entry) => fullPath(entry)))};
            window.location.assign(fm.dataset.download + "?" + new URLSearchParams(fields));
          }
        }));
      }
    }
    if (state.mode === "backup" && state.snapshot && snapshots.length > 1) {
      bar.append(button("Compară snapshoturi", async () => {
        const items = snapshots.filter((item) => item.id !== state.snapshot);
        const options = items.slice(0, 40).map((item, i) => (i + 1) + ". " + item.time);
        const other = items[Number(prompt(options.join("\n"), "1")) - 1];
        if (other) show(node("pre", await api("diff", {...backupFields(), other: other.id})));
      }));
    }
  }

  $("filter").addEventListener("submit", (event) => {
    event.preventDefault();
    search = $("query").value;
    run(() => route({offset: 0}, false));
  });
  $("files").onclick = () => run(() => route({mode: "files", root: "home", path: ""}));
  $("backups").onclick = () => run(() => route({mode: "backup", repo: null, snapshot: null, path: ""}));
  $("prev").onclick = () => run(() => route({offset: Math.max(0, state.offset - limit)}, false));
  $("next").onclick = () => run(() => route({offset: state.offset + limit}, false));
  $("back").onclick = () => run(() => { state = {...history[--position]}; return refresh(); });
  $("forward").onclick = () => run(() => { state = {...history[++position]}; return refresh(); });
  $("view").onclick = () => { grid = !grid; $("view").textContent = grid ? "Listă" : "Grilă"; render(); };
  $("preview").addEventListener("close", () => { if (previewUrl) URL.revokeObjectURL(previewUrl); previewUrl = undefined; });
  $("upload").addEventListener("change", (event) => run(async () => {
    for (const file of event.target.files) {
      const params = new URLSearchParams({...baseFields(), name: file.name});
      const result = await fetch(fm.dataset.upload + "?" + params, {
        method: "POST", credentials: "same-origin", headers: {"X-CSRF-Token": fm.dataset.csrf}, body: file,
      });
      if (!result.ok) throw new Error((await result.json()).error || "Upload eșuat.");
    }
    event.target.value = "";
    await refresh();
  }));
  setInterval(jobs, 3000);
  run(() => route({}));
}
