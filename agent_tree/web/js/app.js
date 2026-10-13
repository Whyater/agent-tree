// Agent Tree web UI: shell, hash router, keyboard, theme, scan, status bar.
import { h, clear, icon, logo, fmtRel, fmtAbs, store, setTimeZone, kbd, SOURCE_LABEL } from "./util.js";
import { api, postScan } from "./api.js";
import { toast, hideTooltip } from "./ui.js";
import * as home from "./views/home.js";
import * as threads from "./views/threads.js";
import * as thread from "./views/thread.js";
import * as projects from "./views/projects.js";
import * as models from "./views/models.js";
import * as flags from "./views/flags.js";

const VIEWS = { home, threads, thread, projects, models, flags };
const NAV = [
  ["home", "#/", "Overview", "h"],
  ["threads", "#/threads", "Threads", "t"],
  ["projects", "#/projects", "Projects", "p"],
  ["models", "#/models", "Models", "m"],
  ["flags", "#/flags", "Flags", "f"],
];

const main = document.getElementById("main");
const topbar = document.getElementById("topbar");
const statusbar = document.getElementById("statusbar");

const state = {
  meta: null,
  metaError: null,
  scanning: false,
  current: null, // { name, instance, route }
};

// ----------------------------------------------------------------- routing

export function parseRoute(hash = location.hash) {
  const raw = hash.replace(/^#/, "") || "/";
  const qi = raw.indexOf("?");
  const path = qi >= 0 ? raw.slice(0, qi) : raw;
  const query = Object.fromEntries(new URLSearchParams(qi >= 0 ? raw.slice(qi + 1) : ""));
  const parts = path.split("/").filter(Boolean);
  let name = "home";
  const params = {};
  if (parts[0] === "threads") name = "threads";
  else if (parts[0] === "thread" && parts[1]) {
    name = "thread";
    params.id = decodeURIComponent(parts.slice(1).join("/"));
  } else if (parts[0] === "projects") name = "projects";
  else if (parts[0] === "models") name = "models";
  else if (parts[0] === "flags") name = "flags";
  else if (parts.length) name = "notfound";
  return { name, params, query, path };
}

export function buildHash(path, query = {}) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) if (v !== undefined && v !== null && v !== "" && v !== false) q.set(k, v === true ? "1" : v);
  const qs = q.toString();
  return "#" + path + (qs ? "?" + qs : "");
}

function navigate(hash, { replace = false } = {}) {
  if (replace) {
    history.replaceState(null, "", hash);
    route();
  } else {
    location.hash = hash;
  }
}

function ctx(r) {
  return {
    main,
    route: r,
    meta: () => state.meta,
    navigate,
    buildHash,
    setQuery(query, opts = {}) {
      navigate(buildHash(r.path === "/" ? "/" : r.path, query), opts);
    },
    scan: runScan,
    reload: () => mountCurrent(true),
  };
}

function route() {
  hideTooltip();
  const r = parseRoute();
  const cur = state.current;
  if (cur && cur.name === r.name && cur.instance.update && sameParams(cur.route, r)) {
    cur.route = r;
    cur.instance.update(r, ctx(r));
    renderTopbar();
    return;
  }
  mount(r);
}

function sameParams(a, b) {
  return JSON.stringify(a.params) === JSON.stringify(b.params);
}

function mount(r) {
  const cur = state.current;
  if (cur && cur.instance.destroy) cur.instance.destroy();
  clear(main);
  main.className = "";
  main.scrollTop = 0;
  if (r.name === "notfound") {
    main.append(h("div", { class: "state" }, h("h2", null, "Page not found"), h("p", null, "There is nothing at this address."),
      h("div", { class: "actions" }, h("a", { class: "btn", href: "#/" }, "Go to overview"))));
    state.current = { name: r.name, instance: {}, route: r };
  } else {
    const view = VIEWS[r.name];
    const instance = view.mount(ctx(r)) || {};
    if (instance.fill) main.className = "fill";
    state.current = { name: r.name, instance, route: r };
  }
  renderTopbar();
  const title = NAV.find((n) => n[0] === r.name);
  if (r.name !== "thread") document.title = (title ? title[2] + " · " : "") + "Agent Tree";
}

function mountCurrent() {
  mount(parseRoute());
}

// ----------------------------------------------------------------- top bar

function renderTopbar() {
  // keep keyboard focus on the same control across re-renders
  const focusables = () => [...topbar.querySelectorAll("a, button")];
  const focusIdx = focusables().indexOf(document.activeElement);
  rebuildTopbar();
  if (focusIdx >= 0) {
    const el = focusables()[focusIdx];
    if (el) el.focus();
  }
}

function rebuildTopbar() {
  const r = state.current ? state.current.route : parseRoute();
  const active = r.name === "thread" ? "threads" : r.name;
  const theme = document.documentElement.getAttribute("data-theme");
  const m = state.meta;
  const scanning = state.scanning || (m && m.scan_running);
  clear(topbar).append(
    h("a", { class: "brand", href: "#/", "aria-label": "Agent Tree, overview" }, logo(18), h("span", null, "Agent Tree")),
    h("nav", { class: "nav", "aria-label": "Sections" },
      NAV.map(([name, href, label]) => h("a", { href, "aria-current": name === active ? "page" : null }, label))),
    h("div", { class: "topbar-right" },
      h("span", { class: "scan-label", title: m && m.last_scan ? "Last scan " + fmtAbs(m.last_scan, { seconds: true }) : "" },
        scanning ? "Scanning…" : m ? (m.last_scan ? "Scanned " + fmtRel(m.last_scan) : "Never scanned") : ""),
      h("button", { class: "btn small" + (m && !m.last_scan ? " primary" : ""), type: "button", disabled: scanning, onclick: () => runScan(), "aria-label": scanning ? "Scan running" : "Scan now" },
        scanning ? h("span", { class: "spinner", "aria-hidden": "true" }) : icon("refresh", 13), scanning ? "Scanning" : "Scan now"),
      h("button", { class: "btn small icon ghost", type: "button", onclick: toggleTheme, "aria-label": theme === "dark" ? "Switch to light theme" : "Switch to dark theme", title: theme === "dark" ? "Light theme" : "Dark theme" },
        icon(theme === "dark" ? "sun" : "moon")),
      h("button", { class: "btn small icon ghost", type: "button", onclick: showShortcuts, "aria-label": "Keyboard shortcuts", title: "Keyboard shortcuts (?)" }, icon("keyboard"))));
}

function renderStatus() {
  const m = state.meta;
  const parts = [];
  if (state.metaError) {
    parts.push(h("span", { style: { color: "var(--text)", fontWeight: 600 }, title: state.metaError.message }, state.metaError.status ? "API error" : "Server unreachable"));
  } else if (m) {
    parts.push(h("span", { title: m.last_scan ? fmtAbs(m.last_scan, { seconds: true }) : "" }, m.last_scan ? "Last scan " + fmtRel(m.last_scan) : "No scan yet"));
    const src = (m.sources || []).filter((x) => x.present);
    if (src.length) {
      parts.push(h("span", { title: src.map((x) => `${SOURCE_LABEL[x.source] || x.source}: ${x.root}, parser ${x.parser_version}, ${x.lines_skipped} lines skipped`).join("\n") },
        src.map((x) => `${SOURCE_LABEL[x.source] || x.source} ${x.files.toLocaleString("en-US")} files`).join(" · ")));
    }
    if (m.metadata_only) parts.push(h("span", { class: "pill", title: "No task descriptions, titles or prompts are stored. Only metadata." }, "Metadata-only mode"));
  }
  parts.push(h("span", null, "All costs are estimates"));
  parts.push(h("span", { class: "sep" }));
  if (m) parts.push(h("span", null, "v" + m.version));
  parts.push(h("span", null, kbd("?"), " shortcuts"));
  clear(statusbar).append(...parts);
}

async function loadMeta() {
  try {
    state.meta = await api("/api/meta");
    state.metaError = null;
    setTimeZone(state.meta.timezone);
  } catch (e) {
    state.metaError = e;
  }
  renderStatus();
  renderTopbar();
}

async function runScan() {
  if (state.scanning) return;
  state.scanning = true;
  renderTopbar();
  try {
    const res = await postScan();
    toast(`Scan finished in ${res && res.seconds != null ? res.seconds.toFixed(1) : "?"}s`);
    state.scanning = false;
    await loadMeta();
    mountCurrent();
  } catch (e) {
    state.scanning = false;
    renderTopbar();
    toast("Scan failed: " + e.message, 6000);
  }
}

// ----------------------------------------------------------------- theme

function toggleTheme() {
  const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  try { localStorage.setItem("agent-tree.theme", next); } catch { /* ignore */ }
  renderTopbar();
}

// ----------------------------------------------------------------- shortcuts

let sheet;
function showShortcuts() {
  if (!sheet) {
    const row = (label, ...keys) => h("div", null, h("span", null, label), h("span", null, ...keys.map((k) => Array.isArray(k) ? [kbd(...k), " "] : [kbd(k), " "])));
    sheet = h("dialog", { class: "sheet", "aria-labelledby": "sheet-title" },
      h("div", { class: "sheet-head" }, h("h2", { id: "sheet-title" }, "Keyboard shortcuts"),
        h("button", { class: "btn small icon ghost", type: "button", "aria-label": "Close", onclick: () => sheet.close() }, icon("close"))),
      h("div", { class: "sheet-body" },
        h("div", null,
          h("h3", null, "Anywhere"),
          h("div", { class: "keys" },
            row("Go to overview", "g", "h"), row("Go to threads", "g", "t"), row("Go to projects", "g", "p"),
            row("Go to models", "g", "m"), row("Go to flags", "g", "f"), row("Search threads", "/"),
            row("This sheet", "?"), row("Close panel or dialog", "esc"))),
        h("div", null,
          h("h3", null, "Lists and trees"),
          h("div", { class: "keys" },
            row("Next / previous", "j", "k"), row("Open or expand", "enter"), row("Expand / collapse", "→", "←"),
            row("Toggle timeline", "t"), row("Flags filter", "f"), row("Self / subtree totals", "s")))));
    document.body.append(sheet);
  }
  if (!sheet.open) sheet.showModal();
}

let gPending = 0;
function isTyping(el) {
  return el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable);
}

document.addEventListener("keydown", (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  if (sheet && sheet.open) return; // dialog handles esc natively
  const typing = isTyping(document.activeElement);
  if (typing) {
    if (e.key === "Escape") document.activeElement.blur();
    return;
  }
  const inst = state.current && state.current.instance;
  if (gPending && Date.now() - gPending < 1500) {
    gPending = 0;
    const target = NAV.find((n) => n[3] === e.key.toLowerCase());
    if (target) {
      e.preventDefault();
      navigate(target[1]);
      return;
    }
  }
  gPending = 0;
  if (e.key === "g") {
    gPending = Date.now();
    return;
  }
  if (e.key === "?") {
    e.preventDefault();
    showShortcuts();
    return;
  }
  if (e.key === "/") {
    e.preventDefault();
    if (inst && inst.focusSearch) inst.focusSearch();
    else {
      navigate("#/threads");
      setTimeout(() => state.current.instance.focusSearch && state.current.instance.focusSearch(), 0);
    }
    return;
  }
  if (inst && inst.onKey && inst.onKey(e)) e.preventDefault();
});

// ----------------------------------------------------------------- boot

window.addEventListener("hashchange", route);
setInterval(() => {
  loadMeta();
}, 30000);
setInterval(() => {
  // keep relative times fresh without a refetch
  renderTopbar();
  renderStatus();
}, 60000);

renderTopbar();
renderStatus();
loadMeta().then(() => route());
