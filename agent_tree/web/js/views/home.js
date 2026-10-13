// Overview: period totals, daily activity, top threads, flags.
import { h, clear, fmtTokens, fmtRel, fmtAbs, costEl, fmtUSD, modelBadge, sourceChain, famClass, FAMILY_LABEL, flagGlyph, icon } from "../util.js";
import { api } from "../api.js";
import { loadingState, errorState, firstRunState, dailyStackedChart, familyLegend, shareBar, RowNav, emptyState } from "../ui.js";

export function mount(ctx) {
  const root = h("div", { class: "page" });
  ctx.main.append(root);
  let nav = null;

  const load = async () => {
    clear(root).append(head(ctx), loadingState("Loading overview…"));
    const meta = ctx.meta();
    if (meta && !meta.last_scan) {
      clear(root).append(head(ctx), h("div", { class: "card" }, firstRunState(() => ctx.scan())));
      return;
    }
    try {
      const [sum, fl] = await Promise.all([api("/api/summary"), api("/api/flags", { since: "30d" }).catch(() => null)]);
      clear(root).append(head(ctx), body(sum, fl, ctx, (n) => { nav = n; }));
    } catch (e) {
      clear(root).append(head(ctx), h("div", { class: "card" }, errorState(e, load)));
    }
  };
  load();
  return {
    onKey: (e) => nav && nav.onKey(e),
  };
}

function head(ctx) {
  const m = ctx.meta();
  return h("div", { class: "page-head" },
    h("div", null, h("h1", null, "Overview"),
      h("div", { class: "sub" }, "Token use and estimated cost across your agent trees" + (m && m.timezone ? ` · days in ${m.timezone}` : ""))));
}

function body(sum, fl, ctx, setNav) {
  const p = sum.periods || {};
  const hasAny = (p.month && p.month.nodes > 0) || (sum.top_threads || []).length > 0;
  const wrap = h("div", { class: "stack" });
  wrap.append(h("div", { class: "grid-3" },
    periodCard("Today", p.today), periodCard("Last 7 days", p.week), periodCard("Last 30 days", p.month)));
  if (!hasAny) {
    wrap.append(h("div", { class: "card" }, emptyState("No activity in the last 30 days",
      "Agent Tree found no agent sessions in this range. Run a scan after using Claude Code or Codex, or check the sources in the status bar.",
      [{ label: "Scan now", onclick: () => ctx.scan() }, { label: "All threads", href: "#/threads?since=all" }])));
    return wrap;
  }

  const daily = sum.daily || [];
  const chartCard = h("section", { class: "card", "aria-labelledby": "act-h" },
    h("div", { class: "card-head" }, h("h2", { id: "act-h" }, "Daily tokens"), h("span", { class: "label" }, "last 30 days, by model family"),
      h("div", { class: "meta" }, familyLegend())),
    h("div", { class: "card-body" }, dailyStackedChart(daily, { height: 188 })));

  wrap.append(h("div", { class: "grid-main" }, chartCard, flagsCard(sum, fl)));

  // Top threads
  const rows = (sum.top_threads || []).map((t) => h("tr", { "aria-selected": "false" },
    h("td", { class: "title-cell" },
      h("a", { href: ctx.buildHash("/thread/" + encodeURIComponent(t.id)), class: "cell-ellipsis", style: { color: "var(--text)" } }, t.title || t.t3_title || untitled(t))),
    h("td", null, sourceChain(t.sources)),
    h("td", { class: "dim" }, t.project_name || "–"),
    h("td", { class: "dim", title: fmtAbs(t.started) }, fmtRel(t.started)),
    h("td", { class: "num" }, t.node_count),
    h("td", { class: "num" }, fmtTokens(t.tokens && t.tokens.total)),
    h("td", { class: "num" }, costEl(t.money)),
  ));
  const topCard = h("section", { class: "card", "aria-labelledby": "top-h" },
    h("div", { class: "card-head" }, h("h2", { id: "top-h" }, "Top threads by est. cost"), h("span", { class: "label" }, "last 30 days"),
      h("div", { class: "meta" }, h("a", { href: "#/threads?sort=cost&since=30d" }, "All threads by cost"))),
    rows.length
      ? h("table", { class: "table" },
        h("thead", null, h("tr", null, h("th", null, "Thread"), h("th", null, "Sources"), h("th", null, "Project"), h("th", null, "Started"),
          h("th", { class: "num" }, "Agents"), h("th", { class: "num" }, "Tokens"), h("th", { class: "num" }, "Est. cost"))),
        h("tbody", null, rows))
      : emptyState("No threads in the last 30 days", "Widen the range on the Threads page."));
  setNav(new RowNav(rows, (i) => ctx.navigate(ctx.buildHash("/thread/" + encodeURIComponent(sum.top_threads[i].id)))));
  wrap.append(topCard);
  return wrap;
}

function untitled(t) {
  return h("span", { class: "muted" }, "Untitled · " + t.id);
}

function periodCard(label, per) {
  const card = h("section", { class: "card period", "aria-label": label });
  if (!per) return card;
  const fams = (per.by_family || []).filter((f) => f.tokens.total > 0);
  const covered = per.money && per.money.covered_cost > 0 ? per.money.covered_cost : 0;
  card.append(
    h("div", { class: "card-head" }, h("h2", null, label),
      h("div", { class: "meta" }, `${per.threads} thread${per.threads === 1 ? "" : "s"} · ${per.nodes} agent${per.nodes === 1 ? "" : "s"}`)),
    h("div", { class: "card-body" },
      h("div", null,
        h("div", { class: "headline" },
          h("span", { class: "big" }, costEl(per.money, { compact: true })), h("span", { class: "est" }, "est. cost"),
          h("span", { style: { marginLeft: "auto" }, class: "mid" }, fmtTokens(per.tokens.total)), h("span", { class: "est" }, "tokens")),
        covered ? h("div", { class: "covered-note", title: "Usage from families marked covered by plan is excluded from the estimate" },
          `+ ${fmtUSD(covered)} covered by plan, not included`) : null),
      per.nodes === 0
        ? h("div", { class: "muted" }, "No agent activity yet.")
        : [
          shareBar(fams.map((f) => ({ family: f.family, value: f.tokens.total })), "Token share by family: " + fams.map((f) => `${FAMILY_LABEL[f.family] || f.family} ${fmtTokens(f.tokens.total)}`).join(", ")),
          h("table", { class: "mini-table" },
            h("tbody", null, fams.map((f) => h("tr", null,
              h("td", null, h("span", { style: { display: "inline-flex", gap: "6px", alignItems: "center" } }, h("span", { class: "sw " + famClass(f.family) }), FAMILY_LABEL[f.family] || f.family)),
              h("td", { class: "num dim" }, fmtTokens(f.tokens.total)),
              h("td", { class: "num" }, costEl(f.money, { compact: true })))))),
          h("div", null,
            h("div", { class: "section-label" }, "Top models"),
            h("table", { class: "mini-table" },
              h("tbody", null, (per.by_model || []).slice(0, 3).map((m) => h("tr", null,
                h("td", null, modelBadge(m.model, m.family)),
                h("td", { class: "num dim" }, fmtTokens(m.tokens.total)),
                h("td", { class: "num" }, costEl(m.money, { compact: true }))))))),
        ]));
  return card;
}

function flagsCard(sum, fl) {
  const counts = sum.flag_counts || {};
  const rules = fl && fl.rules ? fl.rules.filter((r) => r.count > 0).sort((a, b) => b.count - a.count) : [];
  return h("section", { class: "card", "aria-labelledby": "flags-h" },
    h("div", { class: "card-head" }, h("h2", { id: "flags-h" }, "Flags"),
      h("div", { class: "meta" }, h("a", { href: "#/flags?since=30d" }, "View all"))),
    h("div", { class: "card-body stack" },
      h("div", { class: "headline", style: { display: "flex", alignItems: "baseline", gap: "16px" } },
        h("a", { href: "#/flags?since=30d", class: "big", style: { color: "var(--text)" }, "aria-label": `${sum.flag_count || 0} flags, view all` }, String(sum.flag_count || 0)),
        h("a", { href: "#/flags?since=30d&severity=alert", "aria-label": `${counts.alert || 0} alerts` }, flagGlyph("alert", (counts.alert || 0) + " alert")),
        h("a", { href: "#/flags?since=30d&severity=warn", "aria-label": `${counts.warn || 0} warnings` }, flagGlyph("warn", (counts.warn || 0) + " warn"))),
      rules.length
        ? h("table", { class: "mini-table" },
          h("tbody", null, rules.slice(0, 6).map((r) => h("tr", null,
            h("td", null, h("a", { href: "#/flags?since=30d&rule=" + encodeURIComponent(r.id), style: { color: "var(--text)" } }, r.label),
              r.heuristic ? [" ", h("span", { class: "chip dashed", title: "Guessed from short task descriptions" }, "heuristic")] : null),
            h("td", { class: "num" }, flagGlyph(r.severity, r.count))))))
        : h("div", { class: "muted" }, sum.flag_count ? "" : "No rules fired in the last 30 days."),
      null));
}
