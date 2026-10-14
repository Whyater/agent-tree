// Thread view: collapsible agent tree, row-aligned timeline, node detail panel.
import {
  h, s, clear, icon, fmtTokens, fmtInt, fmtAbs, fmtRel, fmtDur, fmtClock, fmtDate, costEl, costText, prettyModel, modelBadge,
  flagGlyph, sourceChain, store, parseTs, famClass, FAMILY_LABEL, FAMILY_ORDER, SOURCE_LABEL, copyButton, kbd,
} from "../util.js";
import { api, threadPath, exportPath } from "../api.js";
import { VirtualList, loadingState, errorState, emptyState, showTooltip, hideTooltip, ttRow, familyLegend, observeWidth } from "../ui.js";

const ROW_H = 32;
const HEADER_H = 56;
const TIMELINE_MIN = 140; // narrowest the timeline pane gets before the tree gives up width
const LINK_TEXT = {
  exact: ["Exact", "Matched by tool_use id: the parent session records launching this agent."],
  inferred: ["Inferred", "Matched by time window: no direct id link was found, so the parent running at that moment was chosen. This link may be wrong."],
  orphan: ["Orphan", "Parent not found: the session that launched this agent is missing or was not scanned."],
};
const KIND_LABEL = { root: "Root session", subagent: "Subagent", wrapper: "Wrapper", "t3-thread": "T3 thread", "t3-task": "T3 task" };
const TICK_STEPS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400].map((x) => x * 1000);

export function mount(ctx) {
  const wrap = h("div", { class: "tv" });
  ctx.main.append(wrap);
  let view = null;
  let currentId = ctx.route.params.id;

  const load = async () => {
    clear(wrap).append(loadingState("Loading thread…", 10));
    try {
      const data = await api(threadPath(currentId));
      clear(wrap);
      if (!data || !data.nodes || !data.nodes.length) {
        wrap.append(emptyState("This thread has no agents", "The index has a thread record but no nodes. Try a full rescan.", [{ label: "Back to threads", href: "#/threads" }]));
        return;
      }
      view = new ThreadView(wrap, data, ctx);
      view.selectFromQuery(ctx.route.query.node, true);
    } catch (e) {
      clear(wrap).append(e.status === 404
        ? emptyState("Thread not found", "It may have been removed from the index, or the link is out of date.", [{ label: "Back to threads", href: "#/threads" }])
        : errorState(e, load));
    }
  };
  load();

  return {
    fill: true,
    update(route) {
      if (view && route.query.node) view.selectFromQuery(route.query.node, true);
    },
    onKey: (e) => (view ? view.onKey(e) : false),
    focusSearch: null,
    destroy() { if (view) view.destroy(); hideTooltip(); },
  };
}

class ThreadView {
  constructor(root, data, ctx) {
    this.root = root;
    this.ctx = ctx;
    this.thread = data.thread;
    this.nodes = data.nodes;
    this.flags = data.flags || [];
    this.now = Date.now();
    this.index();
    this.showTimeline = store.get("timeline", true);
    this.mode = store.get("totals", "self");
    this.flaggedOnly = false;
    // Below 1000px the details panel floats over the tree, so it starts closed there;
    // opening it is an explicit choice (Details button, Enter, or a link to a node).
    this.narrow = window.matchMedia("(max-width: 999px)");
    this.panelOpen = this.narrow.matches ? false : store.get("panel", true);
    this.treeW = store.get("treeW", 800);
    this.selected = this.roots[0] ? this.roots[0].id : null;
    // default expansion: everything for small trees, top two levels for big ones
    this.expanded = new Set();
    const big = this.nodes.length > 150;
    for (const n of this.nodes) if (this.kids.get(n.id).length && (!big || n.depth < 2)) this.expanded.add(n.id);
    document.title = (this.thread.title || this.thread.t3_title || "Thread") + " · Agent Tree";
    this.build();
    this.computeVisible();
    this.vlist.setCount(this.visible.length);
    this.renderPanel();
  }

  // ------------------------------------------------------------- data prep
  index() {
    this.byId = new Map(this.nodes.map((n) => [n.id, n]));
    this.kids = new Map(this.nodes.map((n) => [n.id, []]));
    this.roots = [];
    for (const n of this.nodes) {
      const p = n.parent_id && this.byId.get(n.parent_id);
      if (p) this.kids.get(p.id).push(n);
      else this.roots.push(n);
    }
    // times
    this.t = new Map();
    let T0 = Infinity;
    let T1 = -Infinity;
    for (const n of this.nodes) {
      const a = parseTs(n.started);
      let b = parseTs(n.ended);
      const running = !n.ended && a !== null && (n.status === "running" || !n.status || n.status === "unknown");
      if (b === null) b = a !== null ? (running ? this.now : a + (n.wall_ms || 0)) : null;
      this.t.set(n.id, { a, b, running: !n.ended && a !== null });
      if (a !== null) T0 = Math.min(T0, a);
      if (b !== null) T1 = Math.max(T1, b);
    }
    if (!Number.isFinite(T0)) { T0 = this.now; T1 = this.now + 1000; }
    // 1.5% breathing room on both ends so bars never touch the pane edges
    const pad = Math.max(T1 - T0, 1000) * 0.015;
    this.T0 = T0 - pad;
    this.T1 = Math.max(T1, T0 + 1000) + pad;
    // subtree spans (reverse DFS order: children before parents)
    this.span = new Map();
    for (let i = this.nodes.length - 1; i >= 0; i--) {
      const n = this.nodes[i];
      const tt = this.t.get(n.id);
      let a = tt.a;
      let b = tt.b;
      for (const c of this.kids.get(n.id)) {
        const cs = this.span.get(c.id);
        if (cs.a !== null && (a === null || cs.a < a)) a = cs.a;
        if (cs.b !== null && (b === null || cs.b > b)) b = cs.b;
      }
      this.span.set(n.id, { a, b });
    }
    // flags per node
    this.flagsByNode = new Map();
    for (const f of this.flags) {
      if (!this.flagsByNode.has(f.node_id)) this.flagsByNode.set(f.node_id, []);
      this.flagsByNode.get(f.node_id).push(f);
    }
    // nodes to keep for "flagged only": flagged nodes and their ancestors
    this.flagKeep = new Set();
    for (const n of this.nodes) {
      if ((n.flags && n.flags.length) || this.flagsByNode.has(n.id)) {
        let cur = n;
        while (cur && !this.flagKeep.has(cur.id)) {
          this.flagKeep.add(cur.id);
          cur = cur.parent_id ? this.byId.get(cur.parent_id) : null;
        }
      }
    }
    this.families = FAMILY_ORDER.filter((f) => this.nodes.some((n) => n.model && (n.family || "other") === f));
  }

  computeVisible() {
    const out = [];
    // guides[j] tells the row whether the branch line in indent column j continues
    // below it (the ancestor at that level, or the node itself for the last column,
    // still has siblings after it).
    const walk = (list, level, guides) => {
      const shown = this.flaggedOnly ? list.filter((n) => this.flagKeep.has(n.id)) : list;
      shown.forEach((n, i) => {
        const mine = level ? [...guides, i < shown.length - 1] : [];
        out.push({ n, pos: i + 1, size: shown.length, level, guides: mine });
        if (this.expanded.has(n.id)) walk(this.kids.get(n.id), level + 1, mine);
      });
    };
    walk(this.roots, 0, []);
    this.visible = out;
    this.rowOf = new Map(out.map((r, i) => [r.n.id, i]));
  }

  nodeFlags(n) {
    const list = this.flagsByNode.get(n.id);
    if (list && list.length) return list;
    return (n.flags || []).map((rule) => ({ rule, severity: "warn", heuristic: false, message: rule }));
  }

  label(n) {
    return n.description || n.agent_type || KIND_LABEL[n.kind] || n.kind || n.id;
  }

  // ------------------------------------------------------------- layout
  build() {
    const th = this.thread;
    const rootNode = this.roots[0];
    const running = this.nodes.some((n) => this.t.get(n.id).running);
    const title = th.title || th.t3_title;
    const flagCount = this.flags.length || th.flag_count || 0;

    // header
    this.flagStatBtn = h("button", { class: "btn small ghost", type: "button", "aria-pressed": "false", onclick: () => this.toggleFlagged(), title: "Show only flagged agents (f)", disabled: !flagCount },
      flagCount ? flagGlyph(this.flags.some((f) => f.severity === "alert") ? "alert" : "warn", flagCount) : h("span", { class: "muted" }, "0"));
    const exportBtn = (ext, label) => h("a", { class: "btn small", href: exportPath(th.id, ext), download: "", "aria-label": `Export thread as ${label}` }, label);
    const head = h("header", { class: "tv-head" },
      h("div", { class: "info" },
        h("div", { class: "crumbs" }, h("a", { href: "#/threads" }, "Threads"), h("span", { "aria-hidden": "true" }, "/"),
          th.project_name ? h("a", { href: "#/threads?project=" + encodeURIComponent(th.project_name), title: th.project_path || "" }, th.project_name) : h("span", null, "No project")),
        h("h1", { title: title || th.id }, title || h("span", { class: "muted" }, "Untitled thread")),
        h("div", { class: "metaline" },
          sourceChain(th.sources),
          th.t3_title && th.title && th.t3_title !== th.title ? h("span", null, "T3: " + th.t3_title) : null,
          h("span", { title: fmtAbs(th.started, { seconds: true }) }, "Started " + fmtAbs(th.started)),
          h("span", null, running ? "Running for " + fmtDur(this.now - this.T0) : "Ran " + fmtDur(th.wall_ms)),
          h("span", { class: "mono muted", title: "Thread id" }, th.id))),
      h("div", { class: "tv-stats" },
        h("div", { class: "stat" }, h("span", { class: "label" }, "Est. cost"), h("span", { class: "v" }, costEl(th.money))),
        h("div", { class: "stat" }, h("span", { class: "label" }, "Tokens"), h("span", { class: "v", title: fmtInt(th.tokens.total) }, fmtTokens(th.tokens.total))),
        h("div", { class: "stat" }, h("span", { class: "label" }, "Agents"), h("span", { class: "v" }, String(th.node_count || this.nodes.length), h("span", { class: "est" }, "depth " + th.max_depth))),
        h("div", { class: "stat" }, h("span", { class: "label" }, "Flags"), h("span", { class: "v" }, this.flagStatBtn)),
        h("div", { class: "exports" }, h("span", { class: "label" }, "Export"), h("div", { class: "row" }, exportBtn("json", "JSON"), exportBtn("csv", "CSV"), exportBtn("md", "Markdown")))));

    // toolbar
    this.modeSeg = h("div", { class: "seg small", role: "group", "aria-label": "Token and cost totals" });
    this.flagBtn = h("button", { class: "btn small", type: "button", "aria-pressed": "false", onclick: () => this.toggleFlagged(), disabled: !flagCount }, icon("filter", 12), "Flagged only", kbd("f"));
    this.tlBtn = h("button", { class: "btn small", type: "button", "aria-pressed": String(this.showTimeline), onclick: () => this.toggleTimeline() }, icon("timeline", 12), "Timeline", kbd("t"));
    this.panelBtn = h("button", { class: "btn small", type: "button", "aria-pressed": String(this.panelOpen), onclick: () => this.togglePanel() }, "Details");
    const toolbar = h("div", { class: "tv-toolbar" },
      h("span", { class: "label" }, "Totals"), this.modeSeg,
      h("button", { class: "btn small ghost", type: "button", onclick: () => this.expandAll(true) }, "Expand all"),
      h("button", { class: "btn small ghost", type: "button", onclick: () => this.expandAll(false) }, "Collapse all"),
      this.flagBtn,
      h("span", { class: "spacer" }),
      familyLegend(this.families.length ? this.families : ["other"]),
      this.tlBtn, this.panelBtn);
    this.renderModeSeg();

    // tree + timeline
    this.axis = h("div", { class: "tl-axis", "aria-hidden": "true" });
    this.colsHead = h("div", { class: "tree-cols", "aria-hidden": "true" });
    this.axisNote = h("div", { class: "axis-note" });
    this.header = h("div", { class: "tv-header" }, h("div", { class: "tree-head" }, this.axisNote, this.colsHead), this.axis);
    this.bg = h("div", { class: "tv-bg", "aria-hidden": "true" });
    this.rowsEl = h("div", { class: "tv-rows" }, this.bg);
    this.resizer = h("div", { class: "resizer", role: "separator", "aria-orientation": "vertical", "aria-label": "Resize tree and timeline", tabindex: "-1" });
    this.grid = h("div", { class: "tv-grid" }, this.header, this.rowsEl, this.resizer);
    this.scroller = h("div", {
      class: "tv-scroll", role: "tree", tabindex: "0",
      "aria-label": "Agent tree. Use j and k or arrow keys to move, enter or arrows to expand.",
    }, this.grid);
    this.scroller.addEventListener("focus", () => this.updateActive());
    this.panel = h("aside", { class: "tv-panel", "aria-label": "Agent details" });
    const mainCol = h("div", { class: "tv-main" }, this.scroller);
    const body = h("div", { class: "tv-body" }, mainCol, this.panel);
    this.root.append(head, toolbar, body);
    this.root.classList.toggle("no-timeline", !this.showTimeline);
    this.applyTreeWidth();
    this.renderColsHead();
    this.setupResizer();

    this.vlist = new VirtualList({ scroller: this.scroller, spacer: this.rowsEl, rowHeight: ROW_H, renderRow: (i) => this.renderRow(i), overscan: 14, offsetTop: HEADER_H });
    this.stopObserving = observeWidth(this.axis, (w) => { this.axisW = w; this.renderAxis(); });
  }

  renderModeSeg() {
    clear(this.modeSeg).append(...[["self", "Self"], ["subtree", "Subtree"]].map(([k, l]) =>
      h("button", { type: "button", "aria-pressed": String(this.mode === k), title: k === "self" ? "Each agent's own usage (s)" : "Agent plus everything it spawned (s)", onclick: () => this.setMode(k) }, l)));
  }

  renderColsHead() {
    const m = this.mode === "self" ? "self" : "subtree";
    clear(this.colsHead).append(
      h("span", null, "Agent"), h("span", null, "Model"), h("span", { class: "num", title: "Depth" }, "D"), h("span", { title: "Status" }, ""),
      h("span", { class: "num" }, "Time"), h("span", { class: "num", title: `Tokens (${m})` }, "Tokens"),
      h("span", { class: "num", title: `Estimated cost (${m})` }, "Est. cost"), h("span", { class: "num" }, "Flags"));
  }

  applyTreeWidth() {
    const max = Math.max(460, (this.scroller.clientWidth || window.innerWidth) - TIMELINE_MIN);
    this.treeW = Math.max(440, Math.min(this.treeW, max));
    this.grid.style.setProperty("--tree-w", this.showTimeline ? this.treeW + "px" : "100%");
    this.grid.style.minWidth = this.showTimeline ? `calc(${this.treeW}px + ${TIMELINE_MIN}px)` : "";
  }

  setupResizer() {
    let startX = 0;
    let startW = 0;
    const move = (e) => {
      this.treeW = startW + (e.clientX - startX);
      this.applyTreeWidth();
    };
    const up = () => {
      this.endDrag = null;
      this.resizer.classList.remove("dragging");
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      store.set("treeW", this.treeW);
      this.renderAxis();
    };
    this.resizer.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      startX = e.clientX;
      startW = this.treeW;
      this.resizer.classList.add("dragging");
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
      this.endDrag = up;
    });
  }

  // ------------------------------------------------------------- timeline axis
  pct(t) {
    return ((t - this.T0) / (this.T1 - this.T0)) * 100;
  }

  renderAxis() {
    this.axisNote.textContent = "";
    if (!this.showTimeline) { clear(this.axis); clear(this.bg); return; }
    const w = this.axisW || 400;
    const span = this.T1 - this.T0;
    const want = Math.max(3, Math.floor(w / (span > 86400000 ? 110 : 72)));
    const step = TICK_STEPS.find((st) => span / st <= want) || 86400000 * Math.ceil(span / want / 86400000);
    clear(this.axis);
    for (const el of [...this.bg.children]) el.remove();
    const withSec = step < 60000;
    const DAY = 86400000;
    const multiDay = span > DAY;
    // Label: dates for day-sized steps, date + time when the span crosses days, else time.
    const label = (t) => step >= DAY ? fmtDate(t) : multiDay ? `${fmtDate(t)} ${fmtClock(t)}` : fmtClock(t, withSec);
    let first;
    if (step >= DAY) {
      // Day ticks sit on local midnight, not UTC midnight.
      const d = new Date(this.T0);
      d.setHours(0, 0, 0, 0);
      first = d.getTime() < this.T0 ? d.getTime() + DAY : d.getTime();
    } else {
      // Sub-day steps align to the local clock (handles zones with :30 offsets too).
      const offset = new Date(this.T0).getTimezoneOffset() * 60000;
      first = Math.ceil((this.T0 - offset) / step) * step + offset;
    }
    const stepAt = (t) => step >= DAY ? (() => { const d = new Date(t); d.setDate(d.getDate() + Math.round(step / DAY)); return d.getTime(); })() : t + step;
    for (let t = first; t <= this.T1; t = stepAt(t)) {
      const p = this.pct(t);
      if (p > 97) break;
      this.axis.append(h("span", { class: "tick", style: { left: p + "%" } }, label(t)));
      this.bg.append(h("span", { class: "vline", style: { left: p + "%" } }));
    }
    // Start label only when there is room before the first tick.
    if ((this.pct(first) * w) / 100 > (multiDay ? 110 : 64)) this.axis.append(h("span", { class: "tick first", style: { left: "4px" } }, label(this.T0)));

    // concurrency strip + idle gaps (subagent activity only; top-level sessions span everything)
    const iv = [];
    for (const n of this.nodes) {
      if (!n.parent_id || !this.byId.has(n.parent_id)) continue;
      const tt = this.t.get(n.id);
      if (tt.a === null) continue;
      if ((tt.b - tt.a) / span > 0.95) continue;
      iv.push([tt.a, Math.max(tt.b, tt.a + 1)]);
    }
    if (!iv.length) return;
    const B = 240;
    const buckets = new Array(B).fill(0);
    const ev = [];
    for (const [a, b] of iv) { ev.push([a, 1], [b, -1]); }
    ev.sort((x, y) => x[0] - y[0] || x[1] - y[1]);
    let cur = 0;
    let peak = 0;
    let k = 0;
    for (let bi = 0; bi < B; bi++) {
      const bEnd = this.T0 + ((bi + 1) / B) * span;
      let mx = cur;
      while (k < ev.length && ev[k][0] < bEnd) {
        cur += ev[k][1];
        mx = Math.max(mx, cur);
        k++;
      }
      buckets[bi] = mx;
      peak = Math.max(peak, mx);
    }
    if (peak > 0) {
      let d = `M0,${peak}`;
      buckets.forEach((v, i) => { d += `L${i},${peak - v}L${i + 1},${peak - v}`; });
      d += `L${B},${peak}Z`;
      this.axis.append(s("svg", { class: "conc", viewBox: `0 0 ${B} ${peak}`, preserveAspectRatio: "none" }, s("path", { d })));
      this.axisNote.textContent = `Activity strip: peak ${peak} agents running at once`;
    }
    // idle gaps between merged intervals
    iv.sort((x, y) => x[0] - y[0]);
    const merged = [];
    for (const it of iv) {
      const last = merged[merged.length - 1];
      if (last && it[0] <= last[1]) last[1] = Math.max(last[1], it[1]);
      else merged.push([...it]);
    }
    const minGap = Math.max(60000, span * 0.03);
    for (let i = 1; i < merged.length; i++) {
      const a = merged[i - 1][1];
      const b = merged[i][0];
      if (b - a < minGap) continue;
      const l = this.pct(a);
      const wd = this.pct(b) - l;
      this.bg.prepend(h("span", { class: "idle", style: { left: l + "%", width: wd + "%" } }));
      if (wd > 6) this.axis.append(h("span", { class: "idle-label", style: { left: l + wd / 2 + "%" }, title: `No subagents running for ${fmtDur(b - a)}` }, "idle " + fmtDur(b - a)));
    }
  }

  // ------------------------------------------------------------- rows
  renderRow(i) {
    const { n, pos, size, level, guides } = this.visible[i];
    const kids = this.kids.get(n.id);
    const hasKids = kids.length > 0;
    const open = this.expanded.has(n.id);
    const sel = n.id === this.selected;
    const totals = this.mode === "self" ? n.self : n.subtree;
    const fl = this.nodeFlags(n);
    const parent = n.parent_id ? this.byId.get(n.parent_id) : null;

    const indent = h("span", { class: "indent", "aria-hidden": "true" });
    for (let j = 0; j < level; j++) {
      const own = j === level - 1;
      indent.append(h("i", { class: own ? (guides[j] ? "tee" : "elbow") : (guides[j] ? "pipe" : null) }));
    }
    const caret = hasKids
      ? h("span", { class: "caret", "aria-hidden": "true", onclick: (e) => { e.stopPropagation(); this.toggle(n.id); } }, icon("caret", 12))
      : h("span", { class: "caret leaf " + famClass(n.model ? n.family : "other"), "aria-hidden": "true" });

    const tags = h("span", { class: "tags" });
    if (n.link_confidence && n.link_confidence !== "exact") {
      tags.append(h("span", { class: "chip dashed", title: LINK_TEXT[n.link_confidence] ? LINK_TEXT[n.link_confidence][1] : n.link_confidence }, n.link_confidence));
    }
    if (parent && parent.source !== n.source) tags.append(h("span", { class: "chip src", title: "Source changes here" }, SOURCE_LABEL[n.source] || n.source));
    const srcShown = parent && parent.source !== n.source && SOURCE_LABEL[n.source] === KIND_LABEL[n.kind];
    if ((n.kind === "wrapper" || n.kind === "t3-thread" || n.kind === "t3-task") && this.label(n) !== KIND_LABEL[n.kind] && !srcShown) tags.append(h("span", { class: "chip" }, KIND_LABEL[n.kind]));

    const status = n.status || "unknown";
    // Parsers report several finished/stopped vocabularies; fold them into four glyphs.
    const done = ["completed", "done", "settled", "archived"].includes(status);
    const live = ["running", "active", "async_launched"].includes(status);
    const bad = ["failed", "error", "aborted", "killed"].includes(status);
    const stGlyph = done ? icon("check", 12)
      : live ? s("svg", { class: "pulse", width: 10, height: 10, viewBox: "0 0 10 10", "aria-hidden": "true" }, s("circle", { cx: 5, cy: 5, r: 3.5, fill: "currentColor" }))
        : bad ? icon("x", 12)
          : status === "stopped" ? h("span", { "aria-hidden": "true" }, "–") : h("span", { "aria-hidden": "true" }, "?");

    const desc = n.description;
    // The role wraps onto a hidden second line when it can't show whole (see .trow .txt).
    const text = h("span", { class: "txt", title: this.label(n) }, h("span", { class: "nm" }, this.label(n)),
      desc && n.agent_type && n.agent_type !== desc ? h("span", { class: "at" }, n.agent_type) : null);

    const cells = h("div", { class: "cells" },
      h("div", { class: "label" }, indent, caret, text, tags),
      h("div", { class: "model" }, n.model ? modelBadge(n.model, n.family) : h("span", { class: "muted" }, "–")),
      h("div", { class: "dep" }, String(n.depth)),
      h("div", { class: "st " + status, title: status }, stGlyph),
      h("div", { class: "num dim" }, fmtDur(this.t.get(n.id).running ? this.now - this.t.get(n.id).a : n.wall_ms)),
      h("div", { class: "num" }, fmtTokens(totals && totals.tokens ? totals.tokens.total : null)),
      h("div", { class: "num" }, costEl(totals && totals.money, { compact: true })),
      h("div", { class: "fl" }, fl.length ? flagGlyph(fl.some((f) => f.severity === "alert") ? "alert" : "warn", fl.length) : null));

    const row = h("div", {
      class: "trow", role: "treeitem", id: "tn-" + i,
      "aria-level": n.depth + 1, "aria-setsize": size, "aria-posinset": pos,
      "aria-selected": String(sel), "aria-expanded": hasKids ? String(open) : null,
      "aria-label": `${this.label(n)}, ${n.model ? prettyModel(n.model) : "no model"}, ${status}, ${fmtDur(n.wall_ms)}, ${fmtTokens(totals.tokens.total)} tokens, ${costText(totals.money)}${fl.length ? `, ${fl.length} flag${fl.length > 1 ? "s" : ""}` : ""}${n.link_confidence !== "exact" ? ", link " + n.link_confidence : ""}`,
    }, cells);
    if (this.showTimeline) row.append(this.renderBar(n, hasKids && !open));
    row.addEventListener("click", () => { this.select(n.id, { openPanel: !this.narrow.matches }); this.scroller.focus({ preventScroll: true }); });
    row.addEventListener("dblclick", () => hasKids && this.toggle(n.id));
    return row;
  }

  renderBar(n, collapsedWithKids) {
    const cell = h("div", { class: "tl", "aria-hidden": "true" });
    const tt = this.t.get(n.id);
    if (tt.a === null) return cell;
    if (collapsedWithKids) {
      // summary bar: union of descendant intervals
      const sp = this.span.get(n.id);
      if (sp.a !== null) cell.append(h("span", { class: "sumbar", style: { left: this.pct(sp.a) + "%", width: Math.max(0.2, this.pct(sp.b) - this.pct(sp.a)) + "%" } }));
    }
    const cls = ["bar", famClass(n.family)];
    if (!n.model) cls.push("nomodel");
    if (n.status === "failed") cls.push("failed");
    if (tt.running) cls.push("running");
    const bar = h("span", { class: cls.join(" "), style: { left: this.pct(tt.a) + "%", width: Math.max(0, this.pct(tt.b) - this.pct(tt.a)) + "%" } });
    bar.addEventListener("mousemove", (e) => {
      showTooltip(h("div", null,
        h("div", { class: "tt-title" }, this.label(n)),
        n.model ? ttRow(prettyModel(n.model), FAMILY_LABEL[n.family] || "", n.family) : null,
        ttRow("Start", fmtAbs(tt.a, { seconds: true })),
        ttRow(tt.running ? "Running for" : "Wall time", fmtDur(tt.b - tt.a)),
        ttRow("Tokens (self)", fmtTokens(n.self.tokens.total)),
        ttRow("Est. cost (self)", costText(n.self.money).replace(" estimated", "")),
        collapsedWithKids ? ttRow("Subtree", `${n.subtree.nodes} agents, ${fmtDur(this.span.get(n.id).b - this.span.get(n.id).a)}`) : null), e.clientX, e.clientY);
    });
    bar.addEventListener("mouseleave", hideTooltip);
    cell.append(bar);
    return cell;
  }

  refreshRows(keepSel = true) {
    this.computeVisible();
    this.vlist.setCount(this.visible.length);
    if (keepSel && this.selected && !this.rowOf.has(this.selected)) {
      // selected node got hidden: move selection to nearest visible ancestor
      let cur = this.byId.get(this.selected);
      while (cur && !this.rowOf.has(cur.id)) cur = cur.parent_id ? this.byId.get(cur.parent_id) : null;
      this.selected = cur ? cur.id : this.visible[0] && this.visible[0].n.id;
      this.renderPanel();
    }
    this.updateActive();
  }

  updateActive() {
    const i = this.rowOf.get(this.selected);
    if (i !== undefined) this.scroller.setAttribute("aria-activedescendant", "tn-" + i);
    else this.scroller.removeAttribute("aria-activedescendant");
  }

  // ------------------------------------------------------------- actions
  select(id, { openPanel = false, center = false, syncUrl = true } = {}) {
    if (!this.byId.has(id)) return;
    const prev = this.rowOf.get(this.selected);
    this.selected = id;
    if (openPanel && !this.panelOpen) this.togglePanel(true);
    if (prev !== undefined) this.vlist.rerender(prev);
    const i = this.rowOf.get(id);
    if (i !== undefined) {
      this.vlist.scrollToIndex(i, center ? "center" : "nearest");
      this.vlist.rerender(i);
    }
    this.updateActive();
    this.renderPanel();
    if (syncUrl) {
      const hash = this.ctx.buildHash("/thread/" + encodeURIComponent(this.thread.id), { node: id === (this.roots[0] && this.roots[0].id) ? "" : id });
      if (hash !== location.hash) history.replaceState(null, "", hash);
    }
  }

  selectFromQuery(nodeId, center) {
    if (!nodeId || !this.byId.has(nodeId)) return;
    let cur = this.byId.get(nodeId);
    let changed = false;
    while (cur && cur.parent_id && this.byId.has(cur.parent_id)) {
      if (!this.expanded.has(cur.parent_id)) { this.expanded.add(cur.parent_id); changed = true; }
      cur = this.byId.get(cur.parent_id);
    }
    if (this.flaggedOnly && !this.flagKeep.has(nodeId)) { this.flaggedOnly = false; changed = true; this.syncFlagButtons(); }
    if (changed) this.refreshRows(false);
    requestAnimationFrame(() => this.select(nodeId, { openPanel: true, center, syncUrl: false }));
  }

  toggle(id, force) {
    if (!this.kids.get(id).length) return;
    const open = force !== undefined ? force : !this.expanded.has(id);
    if (open) this.expanded.add(id);
    else this.expanded.delete(id);
    this.refreshRows();
  }

  expandAll(open) {
    this.expanded.clear();
    // "collapse all" keeps the top level open so the tree never shows a single row
    const list = open ? this.nodes : this.roots;
    for (const n of list) {
      if (this.kids.get(n.id).length) this.expanded.add(n.id);
    }
    this.refreshRows();
  }

  setMode(m) {
    this.mode = m;
    store.set("totals", m);
    this.renderModeSeg();
    this.renderColsHead();
    this.vlist.refresh();
  }

  toggleTimeline() {
    this.showTimeline = !this.showTimeline;
    store.set("timeline", this.showTimeline);
    this.root.classList.toggle("no-timeline", !this.showTimeline);
    this.tlBtn.setAttribute("aria-pressed", String(this.showTimeline));
    this.applyTreeWidth();
    this.renderAxis();
    this.vlist.refresh();
  }

  toggleFlagged() {
    if (!this.flagKeep.size) return;
    this.flaggedOnly = !this.flaggedOnly;
    if (this.flaggedOnly) for (const id of this.flagKeep) if (this.kids.get(id).length) this.expanded.add(id);
    this.syncFlagButtons();
    this.refreshRows();
  }

  syncFlagButtons() {
    this.flagBtn.setAttribute("aria-pressed", String(this.flaggedOnly));
    this.flagStatBtn.setAttribute("aria-pressed", String(this.flaggedOnly));
  }

  togglePanel(force) {
    this.panelOpen = force !== undefined ? force : !this.panelOpen;
    if (!this.narrow.matches) store.set("panel", this.panelOpen); // the narrow default stays closed
    this.panelBtn.setAttribute("aria-pressed", String(this.panelOpen));
    this.renderPanel();
    requestAnimationFrame(() => { this.applyTreeWidth(); this.renderAxis(); });
  }

  move(delta) {
    const i = this.rowOf.get(this.selected);
    const next = Math.max(0, Math.min(this.visible.length - 1, (i === undefined ? -1 : i) + delta));
    if (this.visible[next]) this.select(this.visible[next].n.id);
  }

  onKey(e) {
    const ae = document.activeElement;
    const onControl = ae && (ae.tagName === "BUTTON" || ae.tagName === "A");
    const sel = this.selected && this.byId.get(this.selected);
    switch (e.key) {
      case "j": case "ArrowDown": this.move(1); return true;
      case "k": case "ArrowUp": this.move(-1); return true;
      case "Home": this.move(-Infinity); return true;
      case "End": this.move(Infinity); return true;
      case "Enter":
        if (onControl || !sel) return false;
        if (this.kids.get(sel.id).length) this.toggle(sel.id);
        else this.togglePanel(true);
        return true;
      case "ArrowRight": case "l":
        if (!sel) return false;
        if (this.kids.get(sel.id).length && !this.expanded.has(sel.id)) this.toggle(sel.id, true);
        else this.move(1);
        return true;
      case "ArrowLeft": case "h":
        if (!sel) return false;
        if (this.expanded.has(sel.id)) this.toggle(sel.id, false);
        else if (sel.parent_id && this.rowOf.has(sel.parent_id)) this.select(sel.parent_id);
        return true;
      case "t": this.toggleTimeline(); return true;
      case "f": this.toggleFlagged(); return true;
      case "s": this.setMode(this.mode === "self" ? "subtree" : "self"); return true;
      case "Escape":
        if (this.panelOpen) { this.togglePanel(false); this.scroller.focus({ preventScroll: true }); return true; }
        if (this.flaggedOnly) { this.toggleFlagged(); return true; }
        return false;
      default: return false;
    }
  }

  // ------------------------------------------------------------- detail panel
  renderPanel() {
    this.panel.hidden = !this.panelOpen;
    if (!this.panelOpen) return;
    const n = this.selected && this.byId.get(this.selected);
    clear(this.panel);
    if (!n) {
      this.panel.append(h("div", { class: "state" }, h("p", null, "Select an agent to see its details.")));
      return;
    }
    const tt = this.t.get(n.id);
    const parent = n.parent_id ? this.byId.get(n.parent_id) : null;
    const fl = this.nodeFlags(n);
    const hasKids = this.kids.get(n.id).length > 0;
    const isTop = !n.parent_id && n.link_confidence !== "orphan";
    const link = isTop ? ["None", "This is the top of the tree, so it has no parent to link to."] : (LINK_TEXT[n.link_confidence] || [n.link_confidence || "unknown", ""]);
    const kv = (pairs) => h("dl", { class: "kv" }, pairs.filter(Boolean).map(([k, v]) => [h("dt", null, k), h("dd", null, v === null || v === undefined || v === "" ? h("span", { class: "muted" }, "–") : v)]));

    // A synthetic top node (a T3 thread with no model and no usage of its own) would
    // show a page of dashes; summarise the work under it instead.
    const summary = hasKids && !n.model && !(n.self.tokens && n.self.tokens.total > 0);
    const tokCols = summary ? ["subtree"] : hasKids ? ["self", "subtree"] : ["self"];
    const tokRow = (label, key, sub = false) => h("tr", null, h("td", { class: sub ? "sub" : "" }, label),
      tokCols.map((c) => h("td", { class: "num" }, fmtInt(n[c].tokens[key]))));
    const levels = (k) => `${k} level${k === 1 ? "" : "s"}`;
    const stat = (label, value, cap) => h("div", null, h("div", { class: "label" }, label), h("div", { class: "v" }, value), cap ? h("div", { class: "cap" }, cap) : null);

    const statSec = h("section", { class: "panel-sec" },
      h("div", { class: "two-stat" },
        summary
          ? [stat("Subtree est. cost", costEl(n.subtree.money), `${n.subtree.nodes} agents`),
            stat("Subtree tokens", h("span", { title: fmtInt(n.subtree.tokens.total) }, fmtTokens(n.subtree.tokens.total)), levels(n.subtree.max_depth))]
          : [stat("Self est. cost", costEl(n.self.money)),
            hasKids ? stat("Subtree est. cost", costEl(n.subtree.money), `${n.subtree.nodes} agents`) : stat("Tokens", fmtTokens(n.self.tokens.total))]),
      !summary && n.covered ? h("p", { class: "explain" }, `${FAMILY_LABEL[n.family] || n.family} usage is marked covered by plan. The greyed figure is what it would cost at API prices; it is not in totals.`) : null,
      !summary && n.self.money && n.self.money.cost === null ? h("p", { class: "explain" }, `No price is set for ${n.model}. Tokens are counted, cost shows "?". Add it to prices.toml to estimate it.`) : null);

    const factsSec = summary
      ? h("section", { class: "panel-sec" }, h("h3", null, "Subtree"),
        kv([
          ["Models", this.subtreeModels(n.id)],
          ["Source", SOURCE_LABEL[n.source] || n.source],
          ["Status", n.status],
          ["Started", h("span", { title: n.started || "" }, fmtAbs(tt.a, { seconds: true }))],
          ["Ended", n.ended ? h("span", { title: n.ended }, fmtAbs(parseTs(n.ended), { seconds: true })) : (tt.running ? "still running" : null)],
          ["Wall time", fmtDur(tt.running ? this.now - tt.a : n.wall_ms)],
          ["Children", `${this.kids.get(n.id).length} direct, ${n.subtree.nodes - 1} total, ${levels(n.subtree.max_depth)}`],
        ]))
      : h("section", { class: "panel-sec" }, h("h3", null, "Agent"),
        kv([
          ["Model", n.model ? h("span", { style: { display: "inline-flex", flexWrap: "wrap", gap: "4px 6px", alignItems: "center" } }, modelBadge(n.model, n.family), h("span", { class: "mono muted" }, n.model)) : null],
          ["Family", FAMILY_LABEL[n.family] || n.family],
          ["Effort", n.effort],
          ["Role", n.role],
          ["Status", n.status],
          ["Source", SOURCE_LABEL[n.source] || n.source],
          ["Started", h("span", { title: n.started || "" }, fmtAbs(tt.a, { seconds: true }))],
          ["Ended", n.ended ? h("span", { title: n.ended }, fmtAbs(parseTs(n.ended), { seconds: true })) : (tt.running ? "still running" : null)],
          ["Wall time", fmtDur(tt.running ? this.now - tt.a : n.wall_ms)],
          hasKids ? ["Children", `${this.kids.get(n.id).length} direct, ${n.subtree.nodes - 1} total, ${levels(n.subtree.max_depth)}`] : null,
        ]));

    this.panel.append(
      h("div", { class: "panel-head" },
        h("div", { class: "ttl" },
          h("h2", null, this.label(n)),
          h("div", { class: "sub" }, h("span", null, KIND_LABEL[n.kind] || n.kind), n.agent_type ? h("span", null, "· " + n.agent_type) : null, h("span", null, "· depth " + n.depth))),
        h("button", { class: "btn small icon ghost", type: "button", "aria-label": "Close details (Esc)", title: "Close (Esc)", onclick: () => this.togglePanel(false) }, icon("close"))),

      statSec,
      factsSec,

      h("section", { class: "panel-sec" }, h("h3", null, "Tokens"),
        h("table", { class: "tok-table" },
          h("thead", null, h("tr", null, h("th", null, ""), tokCols.map((c) => h("th", null, c === "self" ? "Self" : "Subtree")))),
          h("tbody", null,
            tokRow("Input", "input"), tokRow("Cached input", "cached_input"), tokRow("Cache write", "cache_write"),
            tokRow("Output", "output"), tokRow("of which reasoning", "reasoning", true),
            h("tr", { class: "total" }, h("td", null, "Total"), tokCols.map((c) => h("td", { class: "num" }, fmtInt(n[c].tokens.total))))))),

      h("section", { class: "panel-sec" }, h("h3", null, "Link to parent"),
        kv([
          ["Confidence", h("span", null, isTop || n.link_confidence === "exact" ? link[0] : h("span", { class: "chip dashed" }, link[0]))],
          ["Parent", parent ? h("a", { href: "#", onclick: (e) => { e.preventDefault(); this.select(parent.id); } }, this.label(parent)) : n.parent_id ? h("span", { class: "mono" }, n.parent_id) : null],
        ]),
        link[1] ? h("p", { class: "explain" }, link[1]) : null),

      fl.length ? h("section", { class: "panel-sec" }, h("h3", null, "Flags"),
        fl.map((f) => h("div", { class: "flag-item" },
          h("div", { class: "top" }, flagGlyph(f.severity), h("span", { class: "mono" }, f.rule),
            f.heuristic ? h("span", { class: "chip dashed", title: "Guessed from short task descriptions; may be wrong" }, "heuristic") : null),
          f.message && f.message !== f.rule ? h("div", { class: "msg" }, f.message) : null))) : null,

      h("section", { class: "panel-sec" }, h("h3", null, "Identity and source"),
        h("dl", { class: "kv" },
          h("dt", null, "Node id"), h("dd", null, h("div", { class: "copyline" }, h("code", null, n.id), copyButton(n.id, "Copy node id"))),
          h("dt", null, "Parent id"), h("dd", null, n.parent_id ? h("code", null, n.parent_id) : h("span", { class: "muted" }, "none")),
          h("dt", null, "Raw file"), h("dd", null, n.raw_file ? h("div", { class: "copyline" }, h("code", null, n.raw_file), copyButton(n.raw_file, "Copy file path")) : h("span", { class: "muted" }, "–")),
          h("dt", null, "Offset"), h("dd", null, h("code", null, n.raw_offset !== null && n.raw_offset !== undefined ? fmtInt(n.raw_offset) + " bytes" : "–")),
          h("dt", null, "Parser"), h("dd", null, h("code", null, n.parser_version || "–")))));
  }

  /** Model badges with agent counts for everything below a node, most used first. */
  subtreeModels(id) {
    const count = new Map();
    const stack = [...this.kids.get(id)];
    while (stack.length) {
      const c = stack.pop();
      if (c.model) {
        const e = count.get(c.model) || { family: c.family, n: 0 };
        e.n++;
        count.set(c.model, e);
      }
      stack.push(...this.kids.get(c.id));
    }
    if (!count.size) return null;
    return h("span", { class: "model-list" }, [...count.entries()].sort((a, b) => b[1].n - a[1].n).map(([m, e]) =>
      h("span", { class: "model-count" }, modelBadge(m, e.family), h("span", { class: "muted" }, "×" + e.n))));
  }

  destroy() {
    this.vlist.destroy();
    if (this.stopObserving) this.stopObserving();
    if (this.endDrag) this.endDrag();   // leaving the page mid-drag
  }
}
