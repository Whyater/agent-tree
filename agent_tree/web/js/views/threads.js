// Threads list: filters in the URL, virtualised rows, paged fetching.
import { h, clear, icon, fmtTokens, fmtRel, fmtAbs, fmtDur, costEl, costText, modelBadges, flagGlyph, sourceChain, debounce, todayISO, kbd, SOURCE_LABEL } from "../util.js";
import { api } from "../api.js";
import { VirtualList, loadingState, errorState, emptyState, firstRunState } from "../ui.js";

const PAGE = 200;
const ROW_H = 48;
const PRESETS = [["today", "Today"], ["7d", "7d"], ["30d", "30d"], ["all", "All"], ["custom", "Custom"]];
const DEFAULT_RANGE = "30d";
const SORTS = { started: "Newest", cost: "Est. cost", tokens: "Tokens" };

let optionCache = null;
async function loadOptions() {
  if (optionCache) return optionCache;
  const [p, m] = await Promise.all([
    api("/api/projects", { since: "3650d" }).catch(() => ({ projects: [] })),
    api("/api/models", { since: "3650d" }).catch(() => ({ models: [] })),
  ]);
  optionCache = {
    projects: [...new Set((p.projects || []).map((x) => x.name))].sort((a, b) => a.localeCompare(b)),
    models: (m.models || []).map((x) => x.model).sort(),
  };
  return optionCache;
}

function rangeOf(q) {
  if (q.since && !PRESETS.some((p) => p[0] === q.since)) return "custom";
  if (q.until) return "custom";
  return q.since || DEFAULT_RANGE;
}

function apiParams(q) {
  const range = rangeOf(q);
  const p = { project: q.project, source: q.source, model: q.model, flagged: q.flagged === "1" ? 1 : null, q: q.q, sort: q.sort || "started" };
  if (range === "today") p.since = todayISO();
  else if (range === "7d" || range === "30d") p.since = range;
  else if (range === "custom") {
    p.since = q.since;
    p.until = q.until;
  }
  return p;
}

export function mount(ctx) {
  let q = { ...ctx.route.query };
  let rows = [];
  let total = 0;
  let selected = -1;
  let gen = 0; // request generation, to drop stale responses
  const pending = new Set();

  const page = h("div", { class: "list-page" });
  ctx.main.append(page);

  // ---------------- controls
  const search = h("input", { type: "search", placeholder: "Search titles", "aria-label": "Search thread titles", value: q.q || "" });
  const searchWrap = h("label", { class: "search" }, icon("search", 13), search, kbd("/"));
  const pushQ = (patch, replace = false) => {
    const next = { ...q, ...patch };
    for (const k of Object.keys(next)) if (next[k] === "" || next[k] === null || next[k] === undefined) delete next[k];
    ctx.setQuery(next, { replace });
  };
  search.addEventListener("input", debounce(() => pushQ({ q: search.value.trim() }, true), 220));
  search.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === "ArrowDown") {
      e.preventDefault();
      search.blur();
      select(0);
    }
  });

  const seg = h("div", { class: "seg", role: "group", "aria-label": "Date range" });
  const customBox = h("span", { class: "field" });
  const sinceIn = h("input", { type: "date", "aria-label": "From date" });
  const untilIn = h("input", { type: "date", "aria-label": "To date" });
  sinceIn.addEventListener("change", () => pushQ({ since: sinceIn.value || undefined }));
  untilIn.addEventListener("change", () => pushQ({ until: untilIn.value || undefined }));
  customBox.append(sinceIn, h("span", null, "to"), untilIn);

  const selProject = h("select", { "aria-label": "Project" });
  const selSource = h("select", { "aria-label": "Source" });
  const selModel = h("select", { "aria-label": "Model" });
  selProject.addEventListener("change", () => pushQ({ project: selProject.value }));
  selSource.addEventListener("change", () => pushQ({ source: selSource.value }));
  selModel.addEventListener("change", () => pushQ({ model: selModel.value }));
  const flagged = h("input", { type: "checkbox" });
  flagged.addEventListener("change", () => pushQ({ flagged: flagged.checked ? "1" : "" }));
  const clearBtn = h("button", { class: "btn small ghost", type: "button", onclick: () => ctx.setQuery({}) }, "Clear filters");

  const totalEl = h("span", { class: "total" });
  const exportLink = h("a", { class: "btn small", download: "" }, icon("download", 13), "Export CSV");

  const fillSelect = (sel, label, values, cur, fmt = (x) => x) => {
    clear(sel).append(h("option", { value: "" }, "All " + label));
    for (const v of values) sel.append(h("option", { value: v }, fmt(v)));
    if (cur && !values.includes(cur)) sel.append(h("option", { value: cur }, fmt(cur)));
    sel.value = cur || "";
  };

  const head = h("div", { class: "list-head" },
    h("div", { class: "row title-row" }, h("h1", null, "Threads"), totalEl, h("span", { class: "spacer" }), exportLink),
    h("div", { class: "row" }, searchWrap, seg, customBox, selProject, selSource, selModel,
      h("label", { class: "check", title: "Shortcut: f" }, flagged, "Has flags"), clearBtn));

  // ---------------- table
  const cols = "minmax(240px, 1fr) 136px 84px 80px 96px 64px 84px 196px 44px";
  const sortBtn = (key, label) => {
    const active = (q.sort || "started") === key;
    return h("button", { class: "sort", type: "button", "aria-sort-active": active ? "true" : "false", "aria-label": `Sort by ${label}`, onclick: () => pushQ({ sort: key === "started" ? "" : key }) },
      label, active ? h("span", { "aria-hidden": "true" }, "↓") : null);
  };
  const vhead = h("div", { class: "vhead", role: "presentation" });
  const renderHead = () => {
    clear(vhead).append(
      h("div", null, "Thread"), h("div", null, "Project"), h("div", null, sortBtn("started", "Started")), h("div", { class: "num" }, "Duration"),
      h("div", { class: "num", title: "Agents in the tree / deepest level" }, "Agents / depth"), h("div", { class: "num" }, sortBtn("tokens", "Tokens")),
      h("div", { class: "num" }, sortBtn("cost", "Est. cost")), h("div", null, "Models"), h("div", { class: "num" }, "Flags"));
  };
  const spacer = h("div", { class: "vspacer" });
  const scroller = h("div", { class: "vscroll", "aria-label": "Threads", role: "region" }, spacer);
  const foot = h("div", { class: "list-foot" });
  const body = h("div", { class: "vtable", style: { "--cols": cols } }, vhead, scroller, foot);
  const stateBox = h("div");
  page.append(head, body);

  const href = (t) => ctx.buildHash("/thread/" + encodeURIComponent(t.id));

  const renderRow = (i) => {
    const t = rows[i];
    if (!t) {
      return h("div", { class: "vrow", "aria-hidden": "true" }, h("div", { class: "muted" }, "Loading…"));
    }
    const title = t.title || t.t3_title;
    const el = h("a", {
      class: "vrow", href: href(t), "aria-selected": i === selected ? "true" : "false", "data-i": i,
      "aria-label": `${title || "Untitled thread"}, ${t.project_name || "no project"}, started ${fmtRel(t.started)}, ${t.node_count} agents, ${fmtTokens(t.tokens.total)} tokens, ${costText(t.money)}${t.flag_count ? `, ${t.flag_count} flags` : ""}`,
    },
      h("div", { class: "tl-title" },
        h("span", { class: "t" + (title ? "" : " untitled") }, title || "Untitled thread"),
        h("span", { class: "s" }, sourceChain(t.sources), t.t3_title && t.title && t.t3_title !== t.title ? h("span", { class: "t3" }, "· T3: " + t.t3_title) : null)),
      h("div", { class: "dim cell-ellipsis", title: t.project_path || "" }, t.project_name || "–"),
      h("div", { class: "dim", title: fmtAbs(t.started, { seconds: true }) }, fmtRel(t.started)),
      h("div", { class: "num dim" }, t.ended ? fmtDur(t.wall_ms) : h("span", { title: "Still running" }, fmtDur(t.wall_ms) + "+")),
      h("div", { class: "num" }, String(t.node_count), h("span", { class: "muted" }, " / " + t.max_depth)),
      h("div", { class: "num" }, fmtTokens(t.tokens.total)),
      h("div", { class: "num" }, costEl(t.money, { compact: true })),
      h("div", null, modelBadges(t.models, t.families, 2)),
      h("div", { class: "num" }, t.flag_count ? flagGlyph("warn", t.flag_count) : h("span", { class: "muted", "aria-hidden": "true" }, "·")));
    el.addEventListener("focus", () => { if (selected !== i) select(i, false); });
    return el;
  };

  const vlist = new VirtualList({
    scroller, spacer, rowHeight: ROW_H, renderRow, overscan: 12,
    onRange: (start, end) => {
      for (let i = start; i < end; i++) {
        if (!rows[i] && i < total) {
          fetchPage(Math.floor(i / PAGE) * PAGE);
          break;
        }
      }
    },
  });

  function select(i, focus = true) {
    if (!total) return;
    i = Math.max(0, Math.min(total - 1, i));
    const prev = selected;
    selected = i;
    vlist.rerender(prev);
    vlist.scrollToIndex(i);
    vlist.rerender(i);
    if (focus) {
      const el = spacer.querySelector(`[data-i="${i}"]`);
      if (el) el.focus({ preventScroll: true });
    }
  }

  async function fetchPage(offset) {
    if (pending.has(offset)) return;
    pending.add(offset);
    const my = gen;
    try {
      const res = await api("/api/threads", { ...apiParams(q), limit: PAGE, offset });
      if (my !== gen) return;
      total = res.total;
      res.threads.forEach((t, j) => { rows[offset + j] = t; });
      updateFoot();
      vlist.setCount(total);
    } catch (e) {
      if (my === gen && offset === 0) showError(e);
    } finally {
      pending.delete(offset);
    }
  }

  function updateFoot() {
    const loaded = rows.filter(Boolean).length;
    totalEl.textContent = `${total.toLocaleString("en-US")} thread${total === 1 ? "" : "s"}`;
    clear(foot).append(
      h("span", null, loaded < total ? `Loaded ${loaded.toLocaleString("en-US")} of ${total.toLocaleString("en-US")}` : `${total.toLocaleString("en-US")} shown`),
      h("span", null, kbd("j"), " ", kbd("k"), " move  ", kbd("enter"), " open  ", kbd("f"), " has flags"),
      h("span", { style: { marginLeft: "auto" } }, "Est. cost excludes plan-covered usage"));
  }

  function showError(e) {
    body.hidden = true;
    clear(stateBox).append(errorState(e, () => reload()));
    if (!stateBox.isConnected) page.append(stateBox);
  }

  function showEmpty() {
    const range = rangeOf(q);
    const filtered = q.project || q.source || q.model || q.flagged || q.q;
    body.hidden = true;
    clear(stateBox).append(emptyState(
      filtered ? "No threads match these filters" : "No threads in this range",
      filtered ? "Try clearing a filter or widening the date range." : range === "all" ? "Run a scan to index your agent sessions." : "Widen the range to see older threads, or run a scan to pick up new sessions.",
      [
        range !== "all" ? { label: "Show all time", onclick: () => pushQ({ since: "all", until: "" }) } : null,
        filtered ? { label: "Clear filters", onclick: () => ctx.setQuery({ since: q.since }) } : null,
        { label: "Scan now", onclick: () => ctx.scan() },
      ].filter(Boolean)));
    if (!stateBox.isConnected) page.append(stateBox);
  }

  function syncControls() {
    const range = rangeOf(q);
    clear(seg).append(...PRESETS.map(([key, label]) => h("button", {
      type: "button", "aria-pressed": range === key ? "true" : "false",
      onclick: () => {
        if (key === "custom") pushQ({ since: q.since && rangeOf(q) === "custom" ? q.since : todayISO(), until: "" });
        else pushQ({ since: key === DEFAULT_RANGE ? "" : key, until: "" });
      },
    }, label)));
    customBox.hidden = range !== "custom";
    sinceIn.value = range === "custom" ? (q.since || "") : "";
    untilIn.value = q.until || "";
    if (document.activeElement !== search) search.value = q.q || "";
    flagged.checked = q.flagged === "1";
    fillSelect(selSource, "sources", ["claude-code", "codex", "t3"], q.source, (x) => SOURCE_LABEL[x] || x);
    const opts = optionCache || { projects: [], models: [] };
    fillSelect(selProject, "projects", opts.projects, q.project);
    fillSelect(selModel, "models", opts.models, q.model);
    const p = apiParams(q);
    exportLink.href = "/api/export/range.csv?" + new URLSearchParams(Object.fromEntries(Object.entries({ since: p.since || "", until: p.until || "" }).filter(([, v]) => v)));
    exportLink.setAttribute("aria-label", "Export every agent in this date range as CSV");
    clearBtn.hidden = !Object.keys(q).length;
    renderHead();
  }

  async function reload() {
    gen++;
    rows = [];
    total = 0;
    selected = -1;
    pending.clear();
    syncControls();
    body.hidden = false;
    stateBox.remove();
    vlist.setCount(0);
    clear(foot).append(h("span", null, "Loading…"));
    const meta = ctx.meta();
    if (meta && !meta.last_scan) {
      body.hidden = true;
      clear(stateBox).append(firstRunState(() => ctx.scan()));
      page.append(stateBox);
      return;
    }
    const my = gen;
    const loadingBox = loadingState("Loading threads…", 8);
    spacer.append(loadingBox);
    try {
      const res = await api("/api/threads", { ...apiParams(q), limit: PAGE, offset: 0 });
      if (my !== gen) return;
      loadingBox.remove();
      total = res.total;
      rows = new Array(total);
      res.threads.forEach((t, j) => { rows[j] = t; });
      if (!total) return showEmpty();
      updateFoot();
      vlist.setCount(total);
    } catch (e) {
      loadingBox.remove();
      if (my === gen) showError(e);
    }
  }

  loadOptions().then(() => syncControls());
  reload();

  return {
    fill: true,
    update(route) {
      const before = JSON.stringify(q);
      q = { ...route.query };
      if (JSON.stringify(q) !== before) reload();
    },
    focusSearch() {
      search.focus();
      search.select();
    },
    onKey(e) {
      if (e.key === "j" || e.key === "ArrowDown") { select(selected + 1); return true; }
      if (e.key === "k" || e.key === "ArrowUp") { select(selected <= 0 ? 0 : selected - 1); return true; }
      if (e.key === "Enter" && selected >= 0 && rows[selected]) { ctx.navigate(href(rows[selected])); return true; }
      if (e.key === "f") { pushQ({ flagged: q.flagged === "1" ? "" : "1" }); return true; }
      if (e.key === "Escape" && selected >= 0) { const p = selected; selected = -1; vlist.rerender(p); return true; }
      return false;
    },
    destroy() { vlist.destroy(); },
  };
}
