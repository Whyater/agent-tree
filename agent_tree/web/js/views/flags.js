// Flags: rule legend + filterable list, each linking to its node.
import { h, clear, fmtRel, fmtAbs, flagGlyph } from "../util.js";
import { api } from "../api.js";
import { loadingState, errorState, emptyState, firstRunState, RowNav } from "../ui.js";

const SEV = [["", "All"], ["alert", "Alert"], ["warn", "Warn"]];
const RANGES = [["7d", "7 days"], ["30d", "30 days"], ["all", "All time"]];

export function mount(ctx) {
  const root = h("div", { class: "page" });
  ctx.main.append(root);
  let q = { ...ctx.route.query };
  let nav = null;
  let data = null;
  let rulesData = null;

  const head = () => {
    const range = q.since || "all";
    return h("div", { class: "page-head" },
      h("div", null, h("h1", null, "Flags"), h("div", { class: "sub" }, "Rule hits across your agent trees. Heuristic rules guess from short task descriptions.")),
      h("div", { class: "actions" },
        h("div", { class: "seg", role: "group", "aria-label": "Severity" },
          SEV.map(([k, l]) => h("button", { type: "button", "aria-pressed": String((q.severity || "") === k), onclick: () => ctx.setQuery({ ...q, severity: k }) }, l))),
        h("div", { class: "seg", role: "group", "aria-label": "Date range" },
          RANGES.map(([k, l]) => h("button", { type: "button", "aria-pressed": String(range === k), onclick: () => ctx.setQuery({ ...q, since: k === "all" ? "" : k }) }, l)))));
  };

  const render = () => {
    clear(root).append(head());
    if (!data) { root.append(h("div", { class: "card" }, loadingState("Loading flags…"))); return; }
    const rules = rulesData || data.rules || [];
    const total = rules.reduce((a, r) => a + (r.count || 0), 0);
    const ruleBtn = (id, label, extra, count, sev, r) => h("li", null, h("button", {
      type: "button", "aria-pressed": String((q.rule || "") === id), onclick: () => ctx.setQuery({ ...q, rule: id }, { replace: false }),
    },
      sev ? flagGlyph(sev, null, { label: false }) : h("span"),
      h("span", { class: "rl" + (r && r.enabled === false ? " off" : "") }, h("span", null, label),
        extra ? h("small", null, extra) : null),
      h("span", { class: "num muted" }, String(count))));
    const legend = h("section", { class: "card", "aria-labelledby": "rules-h" },
      h("div", { class: "card-head" }, h("h2", { id: "rules-h" }, "Rules")),
      h("ul", { class: "rule-list" },
        ruleBtn("", "All rules", null, total),
        rules.map((r) => ruleBtn(r.id, r.label, [r.id, r.heuristic ? h("span", { class: "chip dashed", title: "Guessed from short task descriptions; may be wrong" }, "heuristic") : null, r.enabled === false ? h("span", { class: "chip" }, "off") : null], r.count || 0, r.severity, r))));

    const flags = data.flags || [];
    const ruleLabel = Object.fromEntries(rules.map((r) => [r.id, r.label]));
    const href = (f) => ctx.buildHash("/thread/" + encodeURIComponent(f.tree_id), { node: f.node_id });
    let listCard;
    if (!flags.length) {
      listCard = h("section", { class: "card" }, emptyState(q.rule || q.severity ? "No flags match" : "No flags",
        q.rule || q.severity ? "Nothing matches these filters in this range." : "No rule fired in this range. Your agent trees look tidy.",
        q.rule || q.severity || q.since ? [{ label: "Clear filters", onclick: () => ctx.setQuery({}) }] : []));
      nav = null;
    } else {
      const rows = flags.map((f) => h("tr", { "aria-selected": "false" },
        h("td", null, flagGlyph(f.severity)),
        h("td", null, h("span", null, ruleLabel[f.rule] || f.rule), f.heuristic ? [" ", h("span", { class: "chip dashed", title: "Heuristic" }, "heuristic")] : null),
        h("td", { class: "title-cell dim" }, h("span", { class: "cell-ellipsis", title: f.message }, f.message)),
        h("td", { style: { maxWidth: "260px" } }, h("a", { href: href(f), class: "cell-ellipsis" }, f.thread_title || f.tree_id)),
        h("td", { class: "dim", title: fmtAbs(f.ts, { seconds: true }) }, fmtRel(f.ts))));
      listCard = h("section", { class: "card", "aria-labelledby": "fl-h" },
        h("div", { class: "card-head" }, h("h2", { id: "fl-h" }, `${flags.length.toLocaleString("en-US")} flag${flags.length === 1 ? "" : "s"}`),
          q.rule ? h("span", { class: "chip" }, ruleLabel[q.rule] || q.rule) : null,
          h("div", { class: "meta" }, "Click a flag to open its agent in the tree")),
        h("table", { class: "table" },
          h("thead", null, h("tr", null, h("th", null, "Severity"), h("th", null, "Rule"), h("th", null, "Message"), h("th", null, "Thread"), h("th", null, "When"))),
          h("tbody", null, rows)));
      nav = new RowNav(rows, (i) => ctx.navigate(href(flags[i])));
    }
    root.append(h("div", { class: "flags-layout" }, legend, listCard));
  };

  const load = async () => {
    data = null;
    render();
    const meta = ctx.meta();
    if (meta && !meta.last_scan) {
      clear(root).append(head(), h("div", { class: "card" }, firstRunState(() => ctx.scan())));
      return;
    }
    try {
      const params = { rule: q.rule, severity: q.severity, since: q.since };
      // rule counts for the legend come from the unfiltered range
      const [d, all] = await Promise.all([api("/api/flags", params), q.rule || q.severity ? api("/api/flags", { since: q.since }) : null]);
      data = d;
      rulesData = all ? all.rules : d.rules;
      render();
    } catch (e) {
      clear(root).append(head(), h("div", { class: "card" }, errorState(e, load)));
    }
  };
  load();
  return {
    update(route) { q = { ...route.query }; load(); },
    onKey: (e) => nav && nav.onKey(e),
  };
}
