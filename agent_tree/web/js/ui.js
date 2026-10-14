// Reusable UI pieces: states, tooltip, toast, charts, virtual list.
import { h, s, clear, icon, fmtTokens, fmtUSD, fmtDayShort, fmtDayLong, famClass, FAMILY_LABEL, FAMILY_ORDER, niceStep, costText } from "./util.js";

// ----------------------------------------------------------------- states

export function loadingState(label = "Loading…", rows = 6) {
  const widths = [72, 54, 88, 61, 79, 46, 67, 58];
  return h("div", { class: "loading", role: "status", "aria-live": "polite" },
    h("div", { class: "loading-label" }, h("span", { class: "spinner", "aria-hidden": "true" }), label),
    h("div", { class: "loading-rows", "aria-hidden": "true" },
      Array.from({ length: rows }, (_, i) => h("span", { style: { width: widths[i % widths.length] + "%" } }))));
}

export function errorState(err, retry) {
  return h("div", { class: "state", role: "alert" },
    stateIcon("alertCircle", "error"),
    h("h2", null, "Could not load this view"),
    h("p", null, err && err.message ? err.message : String(err)),
    h("div", { class: "actions" }, retry ? h("button", { class: "btn", type: "button", onclick: retry }, icon("refresh"), "Retry") : null));
}

function stateIcon(name, tone = "") {
  return h("span", { class: "state-tile" + (tone ? " " + tone : ""), "aria-hidden": "true" }, icon(name, 20));
}

/** Empty state. hint may be a string or nodes. actions: [{label, onclick, primary}] */
export function emptyState(title, hint, actions = [], iconName = "empty") {
  return h("div", { class: "state" },
    stateIcon(iconName),
    h("h2", null, title),
    hint ? h("p", null, hint) : null,
    actions.length ? h("div", { class: "actions" }, actions.map((a) =>
      a.href ? h("a", { class: "btn" + (a.primary ? " primary" : ""), href: a.href }, a.label)
        : h("button", { class: "btn" + (a.primary ? " primary" : ""), type: "button", onclick: a.onclick }, a.label))) : null);
}

export function firstRunState(onScan) {
  return emptyState("No scan yet",
    [h("span", null, "Agent Tree reads your local Claude Code, Codex and T3 Code logs. Run "), h("code", null, "att scan"),
      h("span", null, " in a terminal, or press Scan now. Nothing leaves this machine.")],
    [{ label: "Scan now", onclick: onScan, primary: true }], "plug");
}

// ----------------------------------------------------------------- tooltip

let ttEl;
export function showTooltip(content, x, y) {
  ttEl = ttEl || document.getElementById("tooltip");
  clear(ttEl).append(content);
  ttEl.hidden = false;
  const pad = 12;
  const r = ttEl.getBoundingClientRect();
  let left = x + pad;
  let top = y + pad;
  if (left + r.width > window.innerWidth - 8) left = x - r.width - pad;
  if (top + r.height > window.innerHeight - 8) top = y - r.height - pad;
  ttEl.style.left = Math.max(8, left) + "px";
  ttEl.style.top = Math.max(8, top) + "px";
}
export function hideTooltip() {
  ttEl = ttEl || document.getElementById("tooltip");
  ttEl.hidden = true;
}
export function ttRow(label, value, fam) {
  return h("div", { class: "tt-row" },
    h("span", null, fam ? h("span", { class: "sw " + famClass(fam) }) : null, label),
    h("span", { class: "num" }, value));
}

// ----------------------------------------------------------------- toast

let toastTimer;
export function toast(msg, ms = 3500) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, ms);
}

// ----------------------------------------------------------------- charts

function topRoundedRect(x, y, w, hgt, r) {
  r = Math.min(r, w / 2, hgt);
  return `M${x},${y + hgt}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + hgt}Z`;
}

/** Legend for families, text in text colours, swatch carries identity. */
export function familyLegend(fams = FAMILY_ORDER) {
  return h("div", { class: "legend", "aria-hidden": "true" },
    fams.map((f) => h("span", null, h("span", { class: "sw " + famClass(f) }), FAMILY_LABEL[f] || f)));
}

/**
 * Daily stacked bars by family.
 * rows: [{ day, by_family: {anthropic, openai, other}, money? }]
 */
export function dailyStackedChart(rows, { height = 170, caption = "Daily tokens by model family" } = {}) {
  const wrap = h("div", { class: "chart" });
  const fams = FAMILY_ORDER.filter((f) => rows.some((r) => (r.by_family || {})[f] > 0));
  const totals = rows.map((r) => fams.reduce((a, f) => a + ((r.by_family || {})[f] || 0), 0));
  const max = Math.max(1, ...totals);

  // Text equivalent: a hidden table.
  const table = h("table", { class: "sr-only" },
    h("caption", null, caption),
    h("thead", null, h("tr", null, h("th", null, "Day"), fams.map((f) => h("th", null, FAMILY_LABEL[f] + " tokens")), h("th", null, "Est. cost"))),
    h("tbody", null, rows.map((r) => h("tr", null, h("td", null, r.day), fams.map((f) => h("td", null, fmtTokens((r.by_family || {})[f] || 0))), h("td", null, r.money ? costText(r.money) : "–")))));
  wrap.append(table);

  const draw = (width) => {
    const old = wrap.querySelector("svg");
    if (old) old.remove();
    if (width < 50) return;
    const padL = 44, padB = 22, padT = 6;
    const ih = height - padB - padT;
    const iw = width - padL;
    const step = niceStep(max, 3);
    const top = Math.ceil(max / step) * step;
    const svg = s("svg", { width, height, viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": caption + ", last " + rows.length + " days" });
    for (let v = 0; v <= top + 1e-9; v += step) {
      const y = padT + ih - (v / top) * ih;
      svg.append(s("line", { class: "gridline", x1: padL, x2: width, y1: y, y2: y }));
      svg.append(s("text", { class: "axis-label", x: padL - 8, y: y + 4, "text-anchor": "end" }, fmtTokens(v)));
    }
    const n = rows.length;
    const slot = iw / n;
    const bw = Math.max(2, Math.min(22, slot - 3));
    rows.forEach((r, i) => {
      const x = padL + i * slot + (slot - bw) / 2;
      const g = s("g", { class: "col" });
      let y = padT + ih;
      const segs = fams.map((f) => ({ f, v: (r.by_family || {})[f] || 0 })).filter((d) => d.v > 0);
      segs.forEach((d, si) => {
        const hh = (d.v / top) * ih;
        const gap = si > 0 ? 2 : 0; // 2px surface gap between stacked segments
        const segH = Math.max(0, hh - gap);
        y -= hh;
        if (segH <= 0) return;
        const isTop = si === segs.length - 1;
        g.append(isTop
          ? s("path", { class: "seg-rect " + famClass(d.f), d: topRoundedRect(x, y, bw, segH, 3) })
          : s("rect", { class: "seg-rect " + famClass(d.f), x, y: y + gap, width: bw, height: segH }));
      });
      const hit = s("rect", { class: "hit", x: padL + i * slot, y: padT, width: slot, height: ih });
      hit.addEventListener("mousemove", (e) => {
        svg.querySelectorAll(".col").forEach((c, j) => c.classList.toggle("dim", j !== i));
        const tt = h("div", null, h("div", { class: "tt-title" }, fmtDayLong(r.day)),
          fams.map((f) => ttRow(FAMILY_LABEL[f], fmtTokens((r.by_family || {})[f] || 0), f)),
          ttRow("Total", fmtTokens(totals[i])),
          r.money ? ttRow("Est. cost", costText(r.money).replace(" estimated", "")) : null);
        showTooltip(tt, e.clientX, e.clientY);
      });
      hit.addEventListener("mouseleave", () => {
        svg.querySelectorAll(".col").forEach((c) => c.classList.remove("dim"));
        hideTooltip();
      });
      svg.append(g, hit);
      if (i % 7 === (n - 1) % 7) {
        svg.append(s("text", { class: "axis-label", x: x + bw / 2, y: height - 6, "text-anchor": "middle" }, fmtDayShort(r.day)));
      }
    });
    wrap.append(svg);
  };
  observeWidth(wrap, draw);
  return wrap;
}

/** Tiny per-day bar sparkline (single series, neutral ink). */
export function sparkBars(values, { width = 120, height = 22, label = "" } = {}) {
  const max = Math.max(...values, 0);
  const n = values.length;
  const slot = width / n;
  const bw = Math.max(1, slot - 1);
  const svg = s("svg", { class: "spark", width, height, viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": label });
  values.forEach((v, i) => {
    const hh = max > 0 ? Math.max(v > 0 ? 2 : 0, (v / max) * (height - 1)) : 0;
    svg.append(hh > 0
      ? s("rect", { x: i * slot, y: height - hh, width: bw, height: hh, rx: 0.5 })
      : s("rect", { class: "zero", x: i * slot, y: height - 1, width: bw, height: 1 }));
  });
  return svg;
}

/** Horizontal share bar across families. parts: [{family, value}] */
export function shareBar(parts, label) {
  const total = parts.reduce((a, p) => a + p.value, 0) || 1;
  return h("div", { class: "share", role: "img", "aria-label": label },
    parts.filter((p) => p.value > 0).map((p) => h("span", { class: famClass(p.family), style: { width: (p.value / total) * 100 + "%" } })));
}

/**
 * 5px capsule meter. pct is 0 to 100 (clamped for drawing). With thresholds the
 * fill turns yellow from 60% and red from 85%, the way plan-limit meters read;
 * otherwise it takes the family colour from `fam` or the accent.
 */
export function meter(pct, { label = "", fam = null, thresholds = false } = {}) {
  const v = Math.max(0, Math.min(100, Number(pct) || 0));
  const level = thresholds ? (v >= 85 ? " crit" : v >= 60 ? " warn" : "") : "";
  return h("div", { class: "meter" + (fam ? " " + famClass(fam) + " fam" : "") + level, role: "img", "aria-label": label },
    h("span", { style: { width: v + "%" } }));
}

/** Calls fn(width) when el's width changes. Returns a function that stops watching. */
export function observeWidth(el, fn) {
  let last = -1;
  const ro = new ResizeObserver((entries) => {
    const w = Math.floor(entries[0].contentRect.width);
    if (w !== last) {
      last = w;
      fn(w);
    }
  });
  ro.observe(el);
  return () => ro.disconnect();
}

// ----------------------------------------------------------------- virtual list

/**
 * Fixed-row-height virtual list. Only rows in view (+ overscan) are in the DOM.
 * renderRow(i) -> HTMLElement (absolutely positioned by the list).
 */
export class VirtualList {
  constructor({ scroller, spacer, rowHeight, renderRow, overscan = 10, onRange, offsetTop = 0 }) {
    Object.assign(this, { scroller, spacer, rowHeight, renderRow, overscan, onRange, offsetTop });
    this.count = 0;
    this.rows = new Map();
    this.raf = 0;
    this.onScroll = () => {
      if (!this.raf) this.raf = requestAnimationFrame(() => { this.raf = 0; this.update(); });
    };
    scroller.addEventListener("scroll", this.onScroll, { passive: true });
    this.ro = new ResizeObserver(() => this.update());
    this.ro.observe(scroller);
  }
  setCount(n) {
    this.count = n;
    this.spacer.style.height = n * this.rowHeight + "px";
    this.refresh();
  }
  refresh() {
    for (const el of this.rows.values()) el.remove();
    this.rows.clear();
    this.update();
  }
  /** Re-render one row in place (e.g. selection change). */
  rerender(i) {
    const old = this.rows.get(i);
    if (!old) return;
    const el = this.place(i);
    old.replaceWith(el);
    this.rows.set(i, el);
  }
  place(i) {
    const el = this.renderRow(i);
    el.style.top = i * this.rowHeight + "px";
    el.style.height = this.rowHeight + "px";
    return el;
  }
  update() {
    const top = Math.max(0, this.scroller.scrollTop - this.offsetTop);
    const vh = this.scroller.clientHeight;
    const start = Math.max(0, Math.floor(top / this.rowHeight) - this.overscan);
    const end = Math.min(this.count, Math.ceil((top + vh) / this.rowHeight) + this.overscan);
    for (const [i, el] of this.rows) {
      if (i < start || i >= end) {
        el.remove();
        this.rows.delete(i);
      }
    }
    const frag = document.createDocumentFragment();
    for (let i = start; i < end; i++) {
      if (!this.rows.has(i)) {
        const el = this.place(i);
        this.rows.set(i, el);
        frag.append(el);
      }
    }
    this.spacer.append(frag);
    if (this.onRange) this.onRange(start, end);
  }
  scrollToIndex(i, block = "nearest") {
    const y = i * this.rowHeight + this.offsetTop;
    const vt = this.scroller.scrollTop;
    const vh = this.scroller.clientHeight;
    const header = this.offsetTop;
    if (block === "center") this.scroller.scrollTop = Math.max(0, y - vh / 2);
    else if (y < vt + header) this.scroller.scrollTop = y - header;
    else if (y + this.rowHeight > vt + vh) this.scroller.scrollTop = y + this.rowHeight - vh;
    this.update();
  }
  destroy() {
    this.scroller.removeEventListener("scroll", this.onScroll);
    this.ro.disconnect();
    cancelAnimationFrame(this.raf);
  }
}

export { fmtUSD };

// ----------------------------------------------------------------- list keyboard navigation

/**
 * j/k selection over a set of row elements (table rows). Enter opens.
 * rows: HTMLElement[]; open(i) called on Enter / click.
 */
export class RowNav {
  constructor(rows, open) {
    this.rows = rows;
    this.open = open;
    this.index = -1;
    rows.forEach((r, i) => {
      r.addEventListener("click", (e) => {
        if (e.target.closest("a, button")) return;
        this.select(i);
        open(i);
      });
    });
  }
  select(i) {
    if (!this.rows.length) return;
    i = Math.max(0, Math.min(this.rows.length - 1, i));
    if (this.index >= 0 && this.rows[this.index]) this.rows[this.index].setAttribute("aria-selected", "false");
    this.index = i;
    const r = this.rows[i];
    r.setAttribute("aria-selected", "true");
    r.scrollIntoView({ block: "nearest" });
  }
  onKey(e) {
    if (!this.rows.length) return false;
    if (e.key === "j" || e.key === "ArrowDown") { this.select(this.index + 1); return true; }
    if (e.key === "k" || e.key === "ArrowUp") { this.select(this.index < 0 ? 0 : this.index - 1); return true; }
    if (e.key === "Enter" && this.index >= 0) { this.open(this.index); return true; }
    return false;
  }
}
