// Models: usage per family and model, daily split, task-type share (heuristic).
import { h, clear, fmtTokens, fmtInt, costEl, modelBadge, famClass, FAMILY_LABEL, FAMILY_ORDER, threadsSince } from "../util.js";
import { api } from "../api.js";
import { loadingState, errorState, emptyState, firstRunState, dailyStackedChart, familyLegend, RowNav } from "../ui.js";

const RANGES = [["7d", "7 days"], ["30d", "30 days"], ["90d", "90 days"]];
const TASKS = ["build", "review", "research", "scan", "design", "test", "other"];

export function mount(ctx) {
  const root = h("div", { class: "page" });
  ctx.main.append(root);
  let q = { ...ctx.route.query };
  let nav = null;

  const head = () => {
    const range = q.since || "30d";
    return h("div", { class: "page-head" },
      h("div", null, h("h1", null, "Models"), h("div", { class: "sub" }, "Usage per model and family, priced and unpriced")),
      h("div", { class: "actions" }, h("div", { class: "seg", role: "group", "aria-label": "Date range" },
        RANGES.map(([k, l]) => h("button", { type: "button", "aria-pressed": String(range === k), onclick: () => ctx.setQuery({ since: k === "30d" ? "" : k }) }, l)))));
  };

  const load = async () => {
    clear(root).append(head(), h("div", { class: "card" }, loadingState("Loading models…")));
    const meta = ctx.meta();
    if (meta && !meta.last_scan) {
      clear(root).append(head(), h("div", { class: "card" }, firstRunState(() => ctx.scan())));
      return;
    }
    try {
      const d = await api("/api/models", { since: q.since || "30d" });
      clear(root).append(head(), body(d));
    } catch (e) {
      clear(root).append(head(), h("div", { class: "card" }, errorState(e, load)));
    }
  };

  const body = (d) => {
    const models = d.models || [];
    if (!models.length) {
      return h("div", { class: "card" }, emptyState("No model usage in this range", "Pick a longer range, or run a scan.", [{ label: "Last 90 days", onclick: () => ctx.setQuery({ since: "90d" }) }]));
    }
    const fams = [...(d.families || [])].sort((a, b) => FAMILY_ORDER.indexOf(a.family) - FAMILY_ORDER.indexOf(b.family));
    const totalTok = fams.reduce((a, f) => a + f.tokens.total, 0) || 1;
    const wrap = h("div", { class: "stack" });

    wrap.append(h("div", { class: "grid-3" }, fams.map((f) => h("section", { class: "card period", "aria-label": FAMILY_LABEL[f.family] || f.family },
      h("div", { class: "card-head" }, h("span", { class: "sw " + famClass(f.family) }), h("h2", null, FAMILY_LABEL[f.family] || f.family),
        h("div", { class: "meta" }, `${fmtInt(f.nodes)} agents`)),
      h("div", { class: "card-body" },
        h("div", { class: "headline" },
          h("span", { class: "big" }, fmtTokens(f.tokens.total)), h("span", { class: "est" }, "tokens"),
          h("span", { style: { marginLeft: "auto" }, class: "mid" }, costEl(f.money, { compact: true })), h("span", { class: "est" }, "est. cost")),
        h("div", { class: "facts" }, h("span", null, h("b", null, Math.round((f.tokens.total / totalTok) * 100) + "%"), " of tokens"),
          f.money && f.money.covered_cost > 0 ? h("span", { class: "covered", title: "Covered by plan, excluded from totals" }, costEl({ cost: 0, cost_known: true, covered_cost: f.money.covered_cost }), " covered by plan") : null),
        h("div", { class: "share", role: "img", "aria-label": `${FAMILY_LABEL[f.family] || f.family}: ${Math.round((f.tokens.total / totalTok) * 100)}% of tokens` },
          h("span", { class: famClass(f.family), style: { width: (f.tokens.total / totalTok) * 100 + "%" } })))))));

    wrap.append(h("section", { class: "card", "aria-labelledby": "md-h" },
      h("div", { class: "card-head" }, h("h2", { id: "md-h" }, "Daily tokens by family, last 30 days"), h("div", { class: "meta" }, familyLegend(fams.map((f) => f.family)))),
      h("div", { class: "card-body" }, dailyStackedChart(d.daily || [], { height: 170 }))));

    const maxTok = Math.max(...models.map((m) => m.tokens.total), 1);
    const rows = models.map((m) => h("tr", { "aria-selected": "false" },
      h("td", null, modelBadge(m.model, m.family)),
      h("td", { class: "mono muted" }, m.model),
      h("td", { class: "num" }, m.threads),
      h("td", { class: "num" }, m.nodes),
      h("td", { class: "num" }, fmtTokens(m.tokens.total)),
      h("td", { style: { width: "18%" } }, h("div", { class: "share " + famClass(m.family), role: "img", "aria-label": `${Math.round((m.tokens.total / totalTok) * 100)}% of tokens` },
        h("span", { style: { width: (m.tokens.total / maxTok) * 100 + "%" } }))),
      h("td", { class: "num" }, costEl(m.money)),
      h("td", null, m.priced ? h("span", { class: "muted" }, "priced") : h("span", { class: "chip dashed", title: "No price in prices.toml, so cost shows ?" }, "no price"))));
    wrap.append(h("section", { class: "card", "aria-labelledby": "mt-h" },
      h("div", { class: "card-head" }, h("h2", { id: "mt-h" }, "Per model"), h("div", { class: "meta" }, `${models.filter((m) => !m.priced).length} unpriced`)),
      h("table", { class: "table" },
        h("thead", null, h("tr", null, h("th", null, "Model"), h("th", null, "Id"), h("th", { class: "num" }, "Threads"), h("th", { class: "num" }, "Agents"),
          h("th", { class: "num" }, "Tokens"), h("th", null, "Relative volume"), h("th", { class: "num" }, "Est. cost"), h("th", null, "Price"))),
        h("tbody", null, rows))));
    nav = new RowNav(rows, (i) => ctx.navigate(ctx.buildHash("/threads", { model: models[i].model, since: threadsSince(q.since || "30d") })));

    // task type matrix
    const usedTasks = TASKS.filter((t) => models.some((m) => (m.by_task_type || []).some((x) => x.task_type === t)));
    wrap.append(h("section", { class: "card", "aria-labelledby": "tt-h" },
      h("div", { class: "card-head" }, h("h2", { id: "tt-h" }, "Share of work by task type"),
        h("span", { class: "chip dashed", title: "Task type is guessed from short task descriptions and agent types" }, "heuristic"),
        h("div", { class: "meta" }, "Share of each model's tokens. Guessed from descriptions and agent types.")),
      h("table", { class: "table matrix" },
        h("thead", null, h("tr", null, h("th", null, "Model"), usedTasks.map((t) => h("th", { class: "num" }, t)))),
        h("tbody", null, models.map((m) => {
          const tot = (m.by_task_type || []).reduce((a, x) => a + x.total, 0) || 1;
          return h("tr", { style: { cursor: "default" } }, h("td", null, modelBadge(m.model, m.family)),
            usedTasks.map((t) => {
              const x = (m.by_task_type || []).find((y) => y.task_type === t);
              const pct = x ? (x.total / tot) * 100 : 0;
              return h("td", { class: "num m " + famClass(m.family), title: x ? `${t}: ${x.nodes} agents, ${fmtTokens(x.total)} tokens` : `${t}: none` },
                h("span", { class: "pct " + (pct ? "" : "muted") }, pct ? (pct < 1 ? "<1%" : Math.round(pct) + "%") : "–"),
                pct ? h("span", { class: "mbar", "aria-hidden": "true" }, h("span", { style: { width: pct + "%" } })) : null);
            }));
        })))));
    return wrap;
  };

  load();
  return {
    update(route) { q = { ...route.query }; load(); },
    onKey: (e) => nav && nav.onKey(e),
  };
}
