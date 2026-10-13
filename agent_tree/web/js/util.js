// Shared helpers: DOM building (never innerHTML with data), formatting, icons.

const SVG_NS = "http://www.w3.org/2000/svg";

/** Build an element. attrs: class, text, style (object), dataset, on<event>, aria-*, anything else as attribute. */
export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  applyAttrs(el, attrs);
  append(el, children);
  return el;
}

/** Build an SVG element. */
export function s(tag, attrs, ...children) {
  const el = document.createElementNS(SVG_NS, tag);
  applyAttrs(el, attrs, true);
  append(el, children);
  return el;
}

function applyAttrs(el, attrs, svg = false) {
  if (!attrs) return;
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.setAttribute("class", v);
    else if (k === "text") el.textContent = v;
    else if (k === "style" && typeof v === "object") {
      for (const [sk, sv] of Object.entries(v)) {
        if (sk.startsWith("--")) el.style.setProperty(sk, sv);
        else el.style[sk] = sv;
      }
    }
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "value" && !svg) el.value = v;
    else if (k === "checked" && !svg) el.checked = !!v;
    else el.setAttribute(k, v === true ? "" : String(v));
  }
}

function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false || c === "") continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

// ----------------------------------------------------------------- numbers

function trimZero(str) {
  return str.replace(/\.0$/, "");
}

/** 940, 4.2k, 340k, 1.2M, 3.4B */
export function fmtTokens(n) {
  if (n === null || n === undefined) return "–";
  const a = Math.abs(n);
  if (a < 1000) return String(Math.round(n));
  if (a < 10_000) return trimZero((n / 1e3).toFixed(1)) + "k";
  if (a < 999_500) return Math.round(n / 1e3) + "k";
  if (a < 999.5e6) return trimZero((n / 1e6).toFixed(1)) + "M";
  return trimZero((n / 1e9).toFixed(1)) + "B";
}

export function fmtInt(n) {
  if (n === null || n === undefined) return "–";
  return Math.round(n).toLocaleString("en-US");
}

/** $12.34, $0.42, $0.004, $1,234 */
export function fmtUSD(v) {
  if (v === null || v === undefined) return "?";
  if (v === 0) return "$0.00";
  const a = Math.abs(v);
  if (a < 0.001) return "<$0.001";
  if (a < 0.01) return "$" + v.toFixed(3);
  if (a < 1000) return "$" + v.toFixed(2);
  if (a < 100_000) return "$" + Math.round(v).toLocaleString("en-US");
  return "$" + trimZero((v / 1e3).toFixed(1)) + "k";
}

/** Plain-text cost for aria labels and tooltips. */
export function costText(money) {
  if (!money) return "no cost";
  const { cost, cost_known, covered_cost } = money;
  if (cost === null || cost === undefined || (cost === 0 && cost_known === false && !covered_cost)) return covered_cost ? `${fmtUSD(covered_cost)} covered by plan` : "unknown cost (no price)";
  if (cost === 0 && covered_cost > 0) return `${fmtUSD(covered_cost)} covered by plan`;
  let t = (cost_known === false ? "at least " : "") + fmtUSD(cost) + " estimated";
  if (covered_cost > 0) t += `, plus ${fmtUSD(covered_cost)} covered by plan`;
  return t;
}

/**
 * Cost cell. Rules:
 *  cost null            -> "?"  (no price for the model)
 *  cost_known false     -> "≥ $x"
 *  only plan-covered    -> greyed "$x", tooltip "covered by plan"
 *  some plan-covered    -> "$x" + small greyed "+$y"
 */
export function costEl(money, { compact = false } = {}) {
  if (!money) return h("span", { class: "muted", text: "–" });
  const { cost, cost_known, covered_cost } = money;
  if (cost === null || cost === undefined || (cost === 0 && cost_known === false && !covered_cost)) {
    return h("span", { class: "unknown", title: "No price for this model. Tokens are still counted.", "aria-label": "unknown cost, no price for this model", text: "?" });
  }
  if (cost === 0 && covered_cost > 0) {
    const t = `Covered by plan: about ${fmtUSD(covered_cost)} at API prices, excluded from totals`;
    return h("span", { class: "covered", title: t, "aria-label": `${fmtUSD(covered_cost)}, covered by plan` }, fmtUSD(covered_cost));
  }
  const main = (cost_known === false ? "≥ " : "") + fmtUSD(cost);
  const wrap = h("span", {
    title: cost_known === false ? "Some usage has no price, so this is a lower bound" : null,
    "aria-label": costText(money),
  }, main);
  if (covered_cost > 0 && !compact) {
    wrap.append(h("span", { class: "covered-extra", title: `Plus ${fmtUSD(covered_cost)} covered by plan (excluded)` }, "+" + fmtUSD(covered_cost)));
  }
  return wrap;
}

export function moneySortValue(m) {
  if (!m) return 0;
  return (m.cost || 0) + (m.covered_cost || 0);
}

// ----------------------------------------------------------------- time

let TZ;
export function setTimeZone(tz) {
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: tz || undefined });
    TZ = tz || undefined;
  } catch {
    TZ = undefined;
  }
}
export function timeZone() { return TZ; }

const fmtCache = new Map();
function dtf(opts) {
  const key = JSON.stringify(opts) + TZ;
  if (!fmtCache.has(key)) fmtCache.set(key, new Intl.DateTimeFormat("en-US", { timeZone: TZ, hourCycle: "h23", ...opts }));
  return fmtCache.get(key);
}

export function parseTs(ts) {
  if (!ts) return null;
  const t = Date.parse(ts);
  return Number.isNaN(t) ? null : t;
}

/** "Oct 12, 2026, 21:04:11" */
export function fmtAbs(ts, { seconds = false } = {}) {
  const t = typeof ts === "number" ? ts : parseTs(ts);
  if (t === null) return "–";
  return dtf({ year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", ...(seconds ? { second: "2-digit" } : {}) }).format(t);
}

export function fmtClock(t, withSeconds = false) {
  return dtf({ hour: "2-digit", minute: "2-digit", ...(withSeconds ? { second: "2-digit" } : {}) }).format(t);
}

export function fmtDayShort(day) {
  // day = "2026-10-12" (already a local calendar day)
  const [y, m, d] = day.split("-").map(Number);
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" }).format(Date.UTC(y, m - 1, d));
}
export function fmtDayLong(day) {
  const [y, m, d] = day.split("-").map(Number);
  return new Intl.DateTimeFormat("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" }).format(Date.UTC(y, m - 1, d));
}

/** "just now", "4m ago", "3h ago", "2d ago", "Oct 3" */
export function fmtRel(ts, now = Date.now()) {
  const t = typeof ts === "number" ? ts : parseTs(ts);
  if (t === null) return "–";
  const s = Math.round((now - t) / 1000);
  if (s < 45) return "just now";
  if (s < 3600) return Math.max(1, Math.round(s / 60)) + "m ago";
  if (s < 86400) return Math.round(s / 3600) + "h ago";
  if (s < 7 * 86400) return Math.round(s / 86400) + "d ago";
  const sameYear = dtf({ year: "numeric" }).format(t) === dtf({ year: "numeric" }).format(now);
  return dtf(sameYear ? { month: "short", day: "numeric" } : { year: "numeric", month: "short", day: "numeric" }).format(t);
}

/** "<1s", "45s", "12m 04s", "2h 13m", "3d 4h" */
export function fmtDur(ms) {
  if (ms === null || ms === undefined) return "–";
  const s = Math.floor(ms / 1000);
  if (s < 1) return "<1s";
  if (s < 60) return s + "s";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, "0")}s`;
  const hh = Math.floor(m / 60);
  if (hh < 24) return `${hh}h ${String(m % 60).padStart(2, "0")}m`;
  return `${Math.floor(hh / 24)}d ${hh % 24}h`;
}

export function todayISO() {
  const parts = dtf({ year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(Date.now());
  const g = (t) => parts.find((p) => p.type === t).value;
  return `${g("year")}-${g("month")}-${g("day")}`;
}

// ----------------------------------------------------------------- models / families

export const FAMILY_LABEL = { anthropic: "Anthropic", openai: "OpenAI", other: "Other" };
export const FAMILY_ORDER = ["anthropic", "openai", "other"];
export const SOURCE_LABEL = { "claude-code": "Claude Code", codex: "Codex", t3: "T3", wrapper: "Wrapper" };
export const SOURCE_SHORT = { "claude-code": "CC", codex: "Codex", t3: "T3", wrapper: "Wrap" };

export function famClass(f) {
  return "fam-" + (f === "anthropic" || f === "openai" ? f : "other");
}

export function familyOf(model) {
  if (!model) return "other";
  if (/^claude|anthropic/i.test(model)) return "anthropic";
  if (/^(gpt|o\d|codex|openai)/i.test(model)) return "openai";
  return "other";
}

/** claude-opus-5-5 -> "Opus 5.5", gpt-6.1-sol -> "Sol 6.1", unknown -> raw */
export function prettyModel(model) {
  if (!model) return "–";
  let m = model.match(/^claude-([a-z]+)-(\d+)(?:-(\d+))?/i);
  if (m) return cap(m[1]) + " " + m[2] + (m[3] ? "." + m[3] : "");
  m = model.match(/^gpt-(\d+(?:\.\d+)?)-([a-z]+)$/i);
  if (m) return cap(m[2]) + " " + m[1];
  return model;
}
function cap(x) { return x.charAt(0).toUpperCase() + x.slice(1); }

export function modelBadge(model, family) {
  const fam = family || familyOf(model);
  return h("span", { class: "badge " + famClass(fam), title: `${model} (${FAMILY_LABEL[fam] || fam})` },
    h("span", { class: "sw", "aria-hidden": "true" }), h("span", { class: "cell-ellipsis" }, prettyModel(model)));
}

export function modelBadges(models, families, max = 3) {
  const wrap = h("div", { class: "badges" });
  (models || []).slice(0, max).forEach((m) => wrap.append(modelBadge(m, familyOf(m))));
  if ((models || []).length > max) {
    const rest = models.slice(max);
    wrap.append(h("span", { class: "more", title: rest.join(", ") }, "+" + rest.length));
  }
  return wrap;
}

export function sourceChain(sources) {
  const list = sources || [];
  const wrap = h("span", { class: "chain", "aria-label": "Sources: " + list.map((x) => SOURCE_LABEL[x] || x).join(" then ") });
  list.forEach((src, i) => {
    if (i) wrap.append(h("span", { "aria-hidden": "true" }, "→"));
    wrap.append(h("span", null, SOURCE_LABEL[src] || src));
  });
  return wrap;
}

// ----------------------------------------------------------------- icons (inline SVG, stroke = currentColor)

const ICONS = {
  search: "M7 12.5a5.5 5.5 0 1 0 0-11 5.5 5.5 0 0 0 0 11Zm4-1.5 3.5 3.5",
  caret: "M6 4l4 4-4 4",
  close: "M4 4l8 8M12 4l-8 8",
  sun: "M8 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM8 1v1.5M8 13.5V15M1 8h1.5M13.5 8H15M3 3l1 1M12 12l1 1M13 3l-1 1M4 12l-1 1",
  moon: "M13.5 9.5A5.5 5.5 0 0 1 6.5 2.5a5.5 5.5 0 1 0 7 7Z",
  refresh: "M13.5 8a5.5 5.5 0 1 1-1.6-3.9M13.5 2v3h-3",
  download: "M8 2v8m0 0-3-3m3 3 3-3M3 13h10",
  keyboard: "M2 4.5h12v7H2zM4.5 7h1M7.5 7h1M10.5 7h1M5 9.5h6",
  copy: "M5.5 5.5h7v7h-7zM3.5 10.5v-7h7",
  check: "M3.5 8.5 6.5 11.5 12.5 4.5",
  x: "M5 5l6 6M11 5l-6 6",
  run: "M8 3v5l3 2",
  alertCircle: "M8 1.75a6.25 6.25 0 1 0 0 12.5 6.25 6.25 0 0 0 0-12.5ZM8 5v3.5M8 11v.01",
  tree: "M4 2.5v11M4 6h5M4 12h5",
  timeline: "M2 4h7M5 8h9M3 12h6",
  empty: "M2.5 4.5h11v8h-11zM2.5 4.5l2-2h7l2 2M6 8.5h4",
  plug: "M6 2v3M10 2v3M4.5 5h7v3a3.5 3.5 0 0 1-7 0V5ZM8 11.5V14",
  filter: "M2.5 3.5h11l-4.2 5v4l-2.6 1.2V8.5z",
};

export function icon(name, size = 14) {
  const d = ICONS[name];
  return s("svg", { width: size, height: size, viewBox: "0 0 16 16", fill: "none", stroke: "currentColor", "stroke-width": 1.5, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", focusable: "false" },
    s("path", { d }));
}

export function logo(size = 18) {
  return s("svg", { width: size, height: size, viewBox: "0 0 20 20", "aria-hidden": "true", focusable: "false" },
    s("path", { d: "M6 6v9M6 10h4M6 15h4", fill: "none", stroke: "currentColor", "stroke-width": 1.6, "stroke-linecap": "round" }),
    s("rect", { x: 3.5, y: 2.5, width: 5, height: 5, rx: 1.3, fill: "currentColor" }),
    s("rect", { x: 10.5, y: 8, width: 7, height: 4, rx: 1.2, fill: "var(--fam-anthropic)" }),
    s("rect", { x: 10.5, y: 13, width: 7, height: 4, rx: 1.2, fill: "var(--fam-openai)" }));
}

/** Flag severity glyph: warn = triangle, alert = octagon. Shape + text, never colour alone. */
export function flagGlyph(severity, count, { label = true } = {}) {
  const alert = severity === "alert";
  const shape = alert
    ? s("path", { d: "M5.2 1.5h5.6l3.7 3.7v5.6l-3.7 3.7H5.2l-3.7-3.7V5.2z", fill: "currentColor" })
    : s("path", { d: "M8 1.8 15 14H1z", fill: "currentColor" });
  const mark = s("path", { d: alert ? "M8 4.6v4.2M8 10.9v.3" : "M8 6v4M8 11.7v.3", stroke: "var(--surface)", "stroke-width": 1.6, "stroke-linecap": "round" });
  const svgEl = s("svg", { width: 12, height: 12, viewBox: "0 0 16 16", "aria-hidden": "true", focusable: "false" }, shape, mark);
  const text = count !== undefined && count !== null ? String(count) : (label ? (alert ? "alert" : "warn") : "");
  return h("span", { class: "flag-glyph sev-" + (alert ? "alert" : "warn") }, svgEl, text ? h("span", null, text) : null);
}

export function kbd(...keys) {
  return keys.map((k) => h("kbd", null, k));
}

// ----------------------------------------------------------------- misc

export function debounce(fn, ms) {
  let t;
  return (...a) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...a), ms);
  };
}

export const store = {
  get(key, fallback) {
    try {
      const v = localStorage.getItem("agent-tree." + key);
      return v === null ? fallback : JSON.parse(v);
    } catch {
      return fallback;
    }
  },
  set(key, value) {
    try { localStorage.setItem("agent-tree." + key, JSON.stringify(value)); } catch { /* ignore */ }
  },
};

export function prefersReducedMotion() {
  return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = h("textarea", { style: { position: "fixed", opacity: "0" } });
    ta.value = text;
    document.body.append(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch { ok = false; }
    ta.remove();
    return ok;
  }
}

export function copyButton(text, label = "Copy") {
  const b = h("button", { class: "btn small copy", type: "button", "aria-label": label + " to clipboard" }, icon("copy", 12), "Copy");
  b.addEventListener("click", async () => {
    const ok = await copyText(text);
    b.lastChild.textContent = ok ? "Copied" : "Failed";
    setTimeout(() => { b.lastChild.textContent = "Copy"; }, 1400);
  });
  return b;
}

/** Nice tick step for an axis covering `range` with about `count` ticks. */
export function niceStep(range, count) {
  const raw = range / Math.max(1, count);
  const mag = 10 ** Math.floor(Math.log10(raw || 1));
  const norm = raw / mag;
  const step = norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10;
  return step * mag;
}

/** Translate a page's range ("7d", "30d", "90d", "all") into a Threads-view `since` value. */
export function threadsSince(range) {
  if (!range || range === "all") return "all";
  if (range === "7d" || range === "30d" || range === "today") return range;
  const m = /^(\d+)d$/.exec(range);
  if (!m) return range;
  const d = new Date();
  d.setDate(d.getDate() - (Number(m[1]) - 1));
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
