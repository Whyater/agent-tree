// Projects: per-project totals with a daily activity sparkline.
import { h, clear, fmtTokens, fmtRel, fmtAbs, costEl, moneySortValue, fmtDayShort, threadsSince } from "../util.js";
import { api } from "../api.js";
import { loadingState, errorState, emptyState, firstRunState, sparkBars, RowNav } from "../ui.js";

const RANGES = [["30d", "30 days"], ["90d", "90 days"], ["all", "All time"]];
const COLS = [
  ["name", "Project", false],
  ["threads", "Threads", true],
  ["nodes", "Agents", true],
  ["tokens", "Tokens", true],
  ["cost", "Est. cost", true],
  ["last", "Last active", false],
];

export function mount(ctx) {
  const root = h("div", { class: "page" });
  ctx.main.append(root);
  let q = { ...ctx.route.query };
  let nav = null;
  let data = null;

  const sortKey = () => q.sort || "cost";
  const sorters = {
    name: (a, b) => a.name.localeCompare(b.name),
    threads: (a, b) => b.threads - a.threads,
    nodes: (a, b) => b.nodes - a.nodes,
    tokens: (a, b) => b.tokens.total - a.tokens.total,
    cost: (a, b) => moneySortValue(b.money) - moneySortValue(a.money),
    last: (a, b) => (b.last_ts || "").localeCompare(a.last_ts || ""),
  };

  const render = () => {
    const range = q.range || "90d";
    const head = h("div", { class: "page-head" },
      h("div", null, h("h1", null, "Projects"), h("div", { class: "sub" }, "Where your agent tokens and estimated spend go")),
      h("div", { class: "actions" }, h("div", { class: "seg", role: "group", "aria-label": "Date range" },
        RANGES.map(([k, l]) => h("button", { type: "button", "aria-pressed": String(range === k), onclick: () => ctx.setQuery({ ...q, range: k === "90d" ? "" : k }) }, l)))));
    clear(root).append(head);
    if (!data) return root.append(h("div", { class: "card" }, loadingState("Loading projects…")));
    const list = [...(data.projects || [])].sort(sorters[sortKey()] || sorters.cost);
    if (!list.length) {
      root.append(h("div", { class: "card" }, emptyState("No projects in this range", "Widen the range, or run a scan to pick up new sessions.",
        range !== "all" ? [{ label: "Show all time", onclick: () => ctx.setQuery({ ...q, range: "all" }) }] : [])));
      return;
    }
    const days = (list[0].daily || []);
    const sparkLabel = days.length ? `${fmtDayShort(days[0].day)} to ${fmtDayShort(days[days.length - 1].day)}` : "";
    const threadsLink = (p) => ctx.buildHash("/threads", { project: p.path || p.name, since: threadsSince(range) });
    const rows = list.map((p) => h("tr", { "aria-selected": "false" },
      h("td", { class: "title-cell" },
        h("a", { href: threadsLink(p), style: { color: "var(--text)", fontWeight: 500 } }, p.name || "(no project)"),
        h("div", { class: "mono muted cell-ellipsis", title: p.path }, p.path)),
      h("td", { class: "num" }, p.threads),
      h("td", { class: "num" }, p.nodes),
      h("td", { class: "num" }, fmtTokens(p.tokens.total)),
      h("td", { class: "num" }, costEl(p.money)),
      h("td", { class: "dim", title: fmtAbs(p.last_ts) }, fmtRel(p.last_ts)),
      h("td", null, sparkBars((p.daily || []).map((d) => d.total), { width: 132, height: 22, label: `${p.name}: daily tokens, ${sparkLabel}` }))));
    const sortBtn = (key, label, num) => {
      const active = sortKey() === key;
      return h("th", { class: num ? "num" : "", "aria-sort": active ? (key === "name" ? "ascending" : "descending") : null },
        h("button", { class: "sort", type: "button", "aria-sort-active": String(active), onclick: () => ctx.setQuery({ ...q, sort: key === "cost" ? "" : key }, { replace: true }) },
          label, active ? h("span", { "aria-hidden": "true" }, key === "name" ? "↑" : "↓") : null));
    };
    root.append(h("div", { class: "card" },
      h("table", { class: "table" },
        h("thead", null, h("tr", null, COLS.map(([k, l, n]) => sortBtn(k, l, n)), h("th", null, "Tokens per day", h("span", { class: "muted", style: { fontWeight: 400 } }, " · last 30 days")))),
        h("tbody", null, rows))));
    nav = new RowNav(rows, (i) => ctx.navigate(threadsLink(list[i])));
  };

  const load = async () => {
    data = null;
    render();
    const meta = ctx.meta();
    if (meta && !meta.last_scan) {
      root.lastChild.replaceWith(h("div", { class: "card" }, firstRunState(() => ctx.scan())));
      return;
    }
    const range = q.range || "90d";
    try {
      data = await api("/api/projects", { since: range === "all" ? "3650d" : range });
      render();
    } catch (e) {
      clear(root).append(h("div", { class: "card" }, errorState(e, load)));
    }
  };
  load();

  return {
    update(route) {
      const prevRange = q.range;
      q = { ...route.query };
      if (q.range !== prevRange) load();
      else render();
    },
    onKey: (e) => nav && nav.onKey(e),
  };
}
