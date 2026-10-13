"""JSON, CSV and Markdown exports."""

from __future__ import annotations

import csv
import io
import json

CSV_COLUMNS = [
    "tree_id", "id", "parent_id", "depth", "source", "kind", "agent_type", "role", "description", "model", "family",
    "effort", "status", "started", "ended", "wall_ms", "self_input", "self_cached_input", "self_cache_write",
    "self_output", "self_reasoning", "self_total", "self_cost_estimate", "self_covered_cost_estimate",
    "subtree_total", "subtree_cost_estimate", "link_confidence", "flags", "raw_file", "raw_offset", "parser_version",
]


def to_json(payload) -> str:
    return json.dumps(payload, indent=2, default=str)


def nodes_csv(nodes: list[dict], tree_id: str | None = None) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    for n in nodes:
        st, sm = n["self"]["tokens"], n["self"]["money"]
        w.writerow([
            tree_id or n.get("tree_id") or "", n["id"], n["parent_id"] or "", n["depth"], n["source"], n["kind"],
            n["agent_type"] or "", n["role"] or "", n["description"] or "", n["model"] or "", n["family"] or "",
            n["effort"] or "", n["status"] or "", n["started"] or "", n["ended"] or "", n["wall_ms"] if n["wall_ms"] is not None else "",
            st["input"], st["cached_input"], st["cache_write"], st["output"], st["reasoning"], st["total"],
            f"{sm['cost']:.6f}", f"{sm['covered_cost']:.6f}",
            n["subtree"]["tokens"]["total"], f"{n['subtree']['money']['cost']:.6f}",
            n["link_confidence"], ";".join(n.get("flags", [])), n["raw_file"] or "", n["raw_offset"] if n["raw_offset"] is not None else "",
            n["parser_version"] or "",
        ])
    return buf.getvalue()


def fmt_tokens(n: int) -> str:
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1000:.0f}k"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def fmt_cost(m: dict, covered: bool = False) -> str:
    cost = m["cost"] + (m["covered_cost"] if covered else 0)
    if not m["cost_known"] and cost == 0:
        return "?"
    prefix = "≥" if not m["cost_known"] else ""
    if cost == 0:
        return "$0"
    if cost < 0.01:
        return f"{prefix}${cost:.3f}"
    return f"{prefix}${cost:,.2f}"


def fmt_ms(ms: int | None) -> str:
    if ms is None:
        return "–"
    s = ms // 1000
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {(s % 3600) // 60:02d}m"


def node_label(n: dict) -> str:
    return n.get("description") or n.get("role") or n.get("agent_type") or n["kind"]


def thread_markdown(detail: dict) -> str:
    t = detail["thread"]
    lines = [
        f"### Agent tree: {t['title'] or t['id']}",
        "",
        f"- Thread: `{t['id']}` ({' → '.join(t['sources'])})",
        f"- Project: `{t['project_path'] or '—'}`",
        f"- When: {t['started'] or '?'} → {t['ended'] or '?'} ({fmt_ms(t['wall_ms'])})",
        f"- Agents: {t['node_count']}, max depth {t['max_depth']}",
        f"- Tokens: {fmt_tokens(t['tokens']['total'])} (in {fmt_tokens(t['tokens']['input'])}, cached {fmt_tokens(t['tokens']['cached_input'])}, "
        f"cache write {fmt_tokens(t['tokens']['cache_write'])}, out {fmt_tokens(t['tokens']['output'])})",
        f"- Est. cost: {fmt_cost(t['money'])}" + (f" (+ {fmt_cost({**t['money'], 'cost': t['money']['covered_cost']})} covered by plan)" if t['money']['covered_cost'] else ""),
        "",
        "```text",
    ]
    for n in detail["nodes"]:
        indent = "  " * n["depth"]
        bits = [n["model"] or "?", fmt_ms(n["wall_ms"]), fmt_tokens(n["self"]["tokens"]["total"]) + " tok", fmt_cost(n["self"]["money"], covered=True)]
        if n["link_confidence"] != "exact":
            bits.append(n["link_confidence"])
        if n.get("flags"):
            bits.append("flags: " + ",".join(n["flags"]))
        lines.append(f"{indent}- {node_label(n)}  [{' | '.join(bits)}]")
    lines.append("```")
    if detail["flags"]:
        lines += ["", "Flags:"]
        for f in detail["flags"]:
            lines.append(f"- **{f['rule']}** ({f['severity']}{', heuristic' if f['heuristic'] else ''}): {f['message']}")
    lines += ["", "_Costs are estimates from prices.toml._", ""]
    return "\n".join(lines)
