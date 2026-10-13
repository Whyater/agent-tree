"""Read-side queries shared by the CLI, the HTTP API and the exporters.

Shapes follow docs/API.md.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from . import __version__
from .config import Config
from .db import get_meta
from .flags import HEURISTIC_RULES, RULE_LABELS, load_rules

TOKEN_KEYS = ("input", "cached_input", "cache_write", "output", "reasoning")
TASK_TYPES = [
    ("review", r"critic|review|audit|verif|check|qa\b"),
    ("design", r"\bui\b|\bux\b|design|layout|css|style|visual|screen|front-?end"),
    ("test", r"\btest|pytest|fixture|regression"),
    ("research", r"research|investigat|explore|look ?up|read|docs|survey|analy"),
    ("scan", r"\bscan|grep|count|list|find|search|lint|relay|inventory"),
    ("build", r"build|implement|fix|write|refactor|create|add|port|migrat|parser|backend|feature"),
]


# --- time helpers ---------------------------------------------------------------

def tz(cfg: Config):
    names = [cfg.timezone, os.environ.get("TZ")]
    try:
        link = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in link:
            names.append(link.split("zoneinfo/", 1)[1])
    except OSError:
        pass
    for name in names:
        if name:
            try:
                return ZoneInfo(name)
            except Exception:
                continue
    return datetime.now().astimezone().tzinfo


def to_utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def local_midnight(cfg: Config, d: date) -> datetime:
    return datetime.combine(d, time(0), tzinfo=tz(cfg))


def parse_since(value: str | None, cfg: Config) -> str | None:
    """Accept '7d', '12h', '2w', 'today', an ISO date or an ISO timestamp; return a UTC ISO string."""
    if not value:
        return None
    v = value.strip().lower()
    now = datetime.now(tz(cfg))
    if v in ("all", ""):
        return None
    if v == "today":
        return to_utc_iso(local_midnight(cfg, now.date()))
    m = re.fullmatch(r"(\d+)\s*([hdwm])", v)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        if unit == "h":
            return to_utc_iso(now - timedelta(hours=n))
        days = {"d": 1, "w": 7, "m": 30}[unit] * n
        return to_utc_iso(local_midnight(cfg, now.date() - timedelta(days=days - 1)))
    try:
        if len(v) == 10:
            return to_utc_iso(local_midnight(cfg, date.fromisoformat(v)))
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz(cfg))
        return to_utc_iso(dt)
    except ValueError:
        raise ValueError(f"cannot parse date '{value}' (try 7d, today, or 2026-10-01)")


def parse_until(value: str | None, cfg: Config) -> str | None:
    if not value:
        return None
    v = value.strip()
    if len(v) == 10:  # whole day inclusive
        return to_utc_iso(local_midnight(cfg, date.fromisoformat(v) + timedelta(days=1)))
    return parse_since(v, cfg)


def wall_ms(started: str | None, ended: str | None) -> int | None:
    if not started or not ended:
        return None
    try:
        a = datetime.fromisoformat(started.replace("Z", "+00:00"))
        b = datetime.fromisoformat(ended.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, int((b - a).total_seconds() * 1000))


# --- shape helpers ----------------------------------------------------------------

def tokens(i=0, c=0, w=0, o=0, r=0) -> dict:
    i, c, w, o, r = (int(x or 0) for x in (i, c, w, o, r))
    return {"input": i, "cached_input": c, "cache_write": w, "output": o, "reasoning": r, "total": i + c + w + o}


def money(cost=0.0, covered=0.0, unpriced=0) -> dict:
    """cost is None when nothing in the aggregate has a price (shown as "?")."""
    cost, covered = float(cost or 0), float(covered or 0)
    if unpriced and not cost and not covered:
        return {"cost": None, "cost_known": False, "covered_cost": 0.0}
    return {"cost": round(cost, 6), "cost_known": not unpriced, "covered_cost": round(covered, 6)}


def spend(m: dict) -> float:
    """Sort key: everything the usage would cost at API prices."""
    return (m["cost"] or 0.0) + (m["covered_cost"] or 0.0)


def _covered_sql(cfg: Config) -> str:
    fams = [f for f, v in cfg.covered_by_plan.items() if v]
    if not fams:
        return "0"
    return "u.family IN (" + ",".join("'" + f.replace("'", "") + "'" for f in fams) + ")"


def _usage_select(cfg: Config) -> str:
    cov = _covered_sql(cfg)
    return f"""SUM(u.input) i, SUM(u.cached_input) c, SUM(u.cache_write) w, SUM(u.output) o, SUM(u.reasoning) r,
               SUM(CASE WHEN {cov} THEN 0 ELSE COALESCE(u.cost, 0) END) cost,
               SUM(CASE WHEN {cov} THEN COALESCE(u.cost, 0) ELSE 0 END) covered,
               SUM(CASE WHEN u.cost IS NULL AND (u.input + u.cached_input + u.cache_write + u.output) > 0 THEN 1 ELSE 0 END) unpriced"""


def _tm(row) -> tuple[dict, dict]:
    return tokens(row["i"], row["c"], row["w"], row["o"], row["r"]), money(row["cost"], row["covered"], row["unpriced"])


USAGE_JOIN = "FROM usage u JOIN nodes n ON n.id = u.node_id WHERE n.tree_id IS NOT NULL AND n.counts_usage = 1"


def task_type(n: dict) -> str:
    text = " ".join(str(n.get(k) or "") for k in ("description", "role", "agent_type")).lower()
    if n.get("agent_type") == "Explore":
        return "research"
    for name, pattern in TASK_TYPES:
        if re.search(pattern, text):
            return name
    return "other"


# --- meta / summary ------------------------------------------------------------

def meta(conn: sqlite3.Connection, cfg: Config, scan_running: bool = False) -> dict:
    stats = json.loads(get_meta(conn, "last_scan_stats") or "[]")
    files = {r["source"]: r for r in conn.execute(
        "SELECT source, COUNT(*) files, SUM(skipped_lines) skipped, MAX(parser_version) pv FROM files GROUP BY source")}
    sources = []
    name_map = {"claude_code": "claude-code"}
    roots = {"claude-code": cfg.claude_dir, "codex": cfg.codex_dir, "t3": cfg.t3_dir}
    for s in stats:
        src = name_map.get(s["source"], s["source"])
        f = files.get(src)
        sources.append({
            "source": src, "present": s.get("present", False),
            "root": str(roots.get(src, s.get("root") or "")).replace(str(cfg.home.parent), "~"),
            "files": f["files"] if f else s.get("files_seen", 0),
            "lines_skipped": f["skipped"] if f else s.get("lines_skipped", 0),
            "parser_version": f["pv"] if f else None,
            "errors": s.get("errors", []),
        })
    return {
        "version": __version__,
        "last_scan": get_meta(conn, "last_scan"),
        "scan_running": scan_running,
        "metadata_only": cfg.metadata_only,
        "covered_by_plan": cfg.covered_by_plan,
        "timezone": str(tz(cfg)),
        "sources": sources,
    }


def _period(conn, cfg, since_iso: str) -> dict:
    sel = _usage_select(cfg)
    total = conn.execute(f"SELECT {sel}, COUNT(DISTINCT u.node_id) nodes, COUNT(DISTINCT n.tree_id) threads {USAGE_JOIN} AND u.ts >= ?", (since_iso,)).fetchone()
    t, m = _tm(total)
    by_family = []
    for r in conn.execute(f"SELECT COALESCE(u.family,'other') fam, {sel} {USAGE_JOIN} AND u.ts >= ? GROUP BY fam ORDER BY SUM(u.input+u.cached_input+u.cache_write+u.output) DESC", (since_iso,)):
        ft, fm = _tm(r)
        by_family.append({"family": r["fam"], "tokens": ft, "money": fm})
    by_model = []
    for r in conn.execute(f"SELECT COALESCE(u.model,'unknown') mdl, COALESCE(u.family,'other') fam, {sel} {USAGE_JOIN} AND u.ts >= ? GROUP BY mdl ORDER BY cost DESC, SUM(u.input+u.cached_input+u.cache_write+u.output) DESC", (since_iso,)):
        mt, mm = _tm(r)
        by_model.append({"model": r["mdl"], "family": r["fam"], "tokens": mt, "money": mm})
    return {"tokens": t, "money": m, "nodes": total["nodes"] or 0, "threads": total["threads"] or 0,
            "by_family": by_family, "by_model": by_model}


def daily(conn, cfg, days: int = 30, where: str = "", params: tuple = ()) -> list[dict]:
    """Per-local-day totals for the last `days` days (oldest first)."""
    zone = tz(cfg)
    today = datetime.now(zone).date()
    start = today - timedelta(days=days - 1)
    buckets = {start + timedelta(days=i): {"tokens": [0] * 5, "cost": 0.0, "covered": 0.0, "unpriced": 0, "fam": defaultdict(int)} for i in range(days)}
    cov = {f for f, v in cfg.covered_by_plan.items() if v}
    q = f"""SELECT u.ts, u.family, u.input, u.cached_input, u.cache_write, u.output, u.reasoning, u.cost
            {USAGE_JOIN} AND u.ts >= ? {where}"""
    for r in conn.execute(q, (to_utc_iso(local_midnight(cfg, start)), *params)):
        try:
            d = datetime.fromisoformat(r["ts"].replace("Z", "+00:00")).astimezone(zone).date()
        except (AttributeError, ValueError):
            continue
        b = buckets.get(d)
        if b is None:
            continue
        vals = (r["input"], r["cached_input"], r["cache_write"], r["output"], r["reasoning"])
        for i, v in enumerate(vals):
            b["tokens"][i] += v or 0
        if r["cost"] is None:
            b["unpriced"] += 1 if sum(vals[:4]) else 0
        elif r["family"] in cov:
            b["covered"] += r["cost"]
        else:
            b["cost"] += r["cost"]
        b["fam"][r["family"] or "other"] += sum(v or 0 for v in vals[:4])
    return [
        {"day": d.isoformat(), "tokens": tokens(*b["tokens"]), "money": money(b["cost"], b["covered"], b["unpriced"]),
         "by_family": dict(b["fam"])}
        for d, b in sorted(buckets.items())
    ]


def summary(conn, cfg) -> dict:
    zone = tz(cfg)
    today = datetime.now(zone).date()
    periods = {
        "today": _period(conn, cfg, to_utc_iso(local_midnight(cfg, today))),
        "week": _period(conn, cfg, to_utc_iso(local_midnight(cfg, today - timedelta(days=6)))),
        "month": _period(conn, cfg, to_utc_iso(local_midnight(cfg, today - timedelta(days=29)))),
    }
    since = to_utc_iso(local_midnight(cfg, today - timedelta(days=29)))
    top = list_threads(conn, cfg, since=since, sort="cost", limit=10)["threads"]
    counts = dict(conn.execute("SELECT severity, COUNT(*) FROM flags WHERE COALESCE(ts, '') >= ? GROUP BY severity", (since,)).fetchall())
    return {"periods": periods, "daily": daily(conn, cfg, 30), "top_threads": top,
            "flag_count": sum(counts.values()), "flag_counts": {"alert": counts.get("alert", 0), "warn": counts.get("warn", 0)}}


# --- threads -------------------------------------------------------------------------

def _tree_stats(conn) -> dict[str, dict]:
    out = {}
    for r in conn.execute(
        """SELECT tree_id, MIN(started) s, MAX(COALESCE(ended, started)) e, COUNT(*) cnt, MAX(depth) md,
                  GROUP_CONCAT(DISTINCT model) models, GROUP_CONCAT(DISTINCT family) fams, GROUP_CONCAT(DISTINCT source) srcs
           FROM nodes WHERE tree_id IS NOT NULL GROUP BY tree_id"""
    ):
        out[r["tree_id"]] = dict(r)
    return out


def _titles(conn) -> dict[str, str | None]:
    return {r["id"]: r["title"] for r in conn.execute("SELECT id, title FROM sessions")}


def _thread_summary(root: dict, st: dict, totals: dict | None, titles: dict, flag_count: int, child_title: str | None) -> dict:
    title = titles.get(root["session_id"]) or root.get("description") or child_title
    t3_title = titles.get(root["session_id"]) if root["source"] == "t3" else None
    if root["source"] == "t3" and child_title:
        title = t3_title or child_title
    sources = [s for s in (st.get("srcs") or "").split(",") if s]
    order = ["t3", "claude-code", "wrapper", "codex"]
    sources.sort(key=lambda s: order.index(s) if s in order else 9)
    path = root.get("project_path")
    return {
        "id": root["id"],
        "source": root["source"],
        "title": title,
        "t3_title": t3_title,
        "project_path": path,
        "project_name": path.rstrip("/").rsplit("/", 1)[-1] if path else None,
        "started": st.get("s"), "ended": st.get("e"), "wall_ms": wall_ms(st.get("s"), st.get("e")),
        "node_count": st.get("cnt", 1), "max_depth": st.get("md") or 0,
        "tokens": tokens(*(totals[k] for k in ("sub_input", "sub_cached", "sub_cache_write", "sub_output", "sub_reasoning"))) if totals else tokens(),
        "money": money(totals["sub_cost"], totals["sub_covered"], totals["sub_unpriced"]) if totals else money(),
        "models": sorted(m for m in (st.get("models") or "").split(",") if m),
        "families": sorted(f for f in (st.get("fams") or "").split(",") if f),
        "flag_count": flag_count,
        "sources": sources,
    }


def list_threads(conn, cfg, project=None, source=None, model=None, since=None, until=None,
                 flagged=False, q=None, sort="started", limit=200, offset=0) -> dict:
    since_iso = parse_since(since, cfg) if since and not str(since).endswith("Z") else since
    until_iso = parse_until(until, cfg) if until and not str(until).endswith("Z") else until
    stats = _tree_stats(conn)
    titles = _titles(conn)
    totals = {r["node_id"]: dict(r) for r in conn.execute("SELECT * FROM node_totals")}
    flag_counts = dict(conn.execute("SELECT tree_id, COUNT(*) FROM flags GROUP BY tree_id").fetchall())
    roots = [dict(r) for r in conn.execute("SELECT * FROM nodes WHERE parent_id IS NULL AND tree_id = id")]
    # Title of the first child session for wrapper roots such as T3 threads.
    child_title = {}
    for r in conn.execute("SELECT n.parent_id, s.title FROM nodes n JOIN sessions s ON s.id = n.session_id WHERE n.parent_id IN (SELECT id FROM nodes WHERE kind='t3-thread') ORDER BY n.started"):
        child_title.setdefault(r["parent_id"], r["title"])
    if model:
        with_model = {r[0] for r in conn.execute("SELECT DISTINCT tree_id FROM nodes WHERE model = ? OR model LIKE ?", (model, f"%{model}%"))}
    out = []
    ql = (q or "").lower().strip()
    for root in roots:
        st = stats.get(root["id"], {})
        summ = _thread_summary(root, st, totals.get(root["id"]), titles, flag_counts.get(root["id"], 0), child_title.get(root["id"]))
        if project and project != summ["project_path"] and project != summ["project_name"]:
            continue
        if source and source not in summ["sources"]:
            continue
        if model and root["id"] not in with_model:
            continue
        if since_iso and (summ["ended"] or summ["started"] or "") < since_iso:
            continue
        if until_iso and (summ["started"] or "") >= until_iso:
            continue
        if flagged and not summ["flag_count"]:
            continue
        if ql and ql not in " ".join(str(x or "") for x in (summ["title"], summ["t3_title"], summ["project_name"], summ["id"])).lower():
            continue
        out.append(summ)
    keyfn = {
        "cost": lambda t: (spend(t["money"]), t["tokens"]["total"]),
        "tokens": lambda t: t["tokens"]["total"],
    }.get(sort, lambda t: t["started"] or "")
    out.sort(key=keyfn, reverse=True)
    return {"total": len(out), "threads": out[offset: offset + limit]}


def _node_out(n: dict, t: dict | None, cfg: Config, flags_by_node: dict, child_count: dict) -> dict:
    t = t or {}
    def tk(prefix):
        return tokens(*(t.get(f"{prefix}_{k}") for k in ("input", "cached", "cache_write", "output", "reasoning")))
    return {
        "id": n["id"], "parent_id": n["parent_id"], "depth": n["depth"] or 0,
        "source": n["source"], "kind": n["kind"], "agent_type": n["agent_type"], "role": n["role"],
        "description": n["description"], "model": n["model"], "family": n["family"], "effort": n["effort"],
        "status": n["status"], "started": n["started"], "ended": n["ended"], "wall_ms": wall_ms(n["started"], n["ended"]),
        "self": {"tokens": tk("self"), "money": money(t.get("self_cost"), t.get("self_covered"), t.get("self_unpriced"))},
        "subtree": {"tokens": tk("sub"), "money": money(t.get("sub_cost"), t.get("sub_covered"), t.get("sub_unpriced")),
                    "nodes": t.get("sub_nodes") or 1, "max_depth": t.get("sub_max_depth") or 0},
        "covered": cfg.is_covered(n["family"]),
        "link_confidence": n["link_confidence"] or "exact",
        "raw_file": n["raw_file"], "raw_offset": n["raw_offset"], "parser_version": n["parser_version"],
        "child_count": child_count.get(n["id"], 0),
        "flags": flags_by_node.get(n["id"], []),
        "project_path": n["project_path"],
        "extra": json.loads(n["extra_json"]) if n["extra_json"] else {},
    }


def resolve_thread_id(conn, thread_id: str) -> str | None:
    """Accept a tree id, any node id inside a tree, a bare session id, or a unique prefix."""
    row = conn.execute("SELECT tree_id FROM nodes WHERE id = ? AND tree_id IS NOT NULL", (thread_id,)).fetchone()
    if row:
        return row[0]
    for prefix in ("cc:", "cx:", "t3:", "cc-agent:"):
        row = conn.execute("SELECT tree_id FROM nodes WHERE id = ? AND tree_id IS NOT NULL", (prefix + thread_id,)).fetchone()
        if row:
            return row[0]
    rows_ = conn.execute("SELECT DISTINCT tree_id FROM nodes WHERE tree_id IS NOT NULL AND (id LIKE ? OR id LIKE ?) LIMIT 2",
                         (thread_id + "%", "%:" + thread_id + "%")).fetchall()
    return rows_[0][0] if len(rows_) == 1 else None


def thread_detail(conn, cfg, thread_id: str) -> dict | None:
    tree_id = resolve_thread_id(conn, thread_id)
    if not tree_id:
        return None
    nodes = [dict(r) for r in conn.execute("SELECT * FROM nodes WHERE tree_id = ?", (tree_id,))]
    totals = {r["node_id"]: dict(r) for r in conn.execute(
        "SELECT t.* FROM node_totals t JOIN nodes n ON n.id = t.node_id WHERE n.tree_id = ?", (tree_id,))}
    flags = [dict(r) for r in conn.execute("SELECT * FROM flags WHERE tree_id = ? ORDER BY ts", (tree_id,))]
    flags_by_node = defaultdict(list)
    for f in flags:
        flags_by_node[f["node_id"]].append(f["rule"])
        f["heuristic"] = bool(f["heuristic"])
    kids = defaultdict(list)
    for n in nodes:
        kids[n["parent_id"]].append(n)
    child_count = {k: len(v) for k, v in kids.items()}
    ordered = []
    root = next((n for n in nodes if n["id"] == tree_id), None)
    stack = [root] if root else []
    while stack:
        n = stack.pop()
        ordered.append(_node_out(n, totals.get(n["id"]), cfg, flags_by_node, child_count))
        stack.extend(sorted(kids.get(n["id"], []), key=lambda c: (c["started"] or "", c["id"]), reverse=True))
    titles = _titles(conn)
    child_t = next((titles.get(c["session_id"]) for c in sorted(kids.get(tree_id, []), key=lambda c: c["started"] or "") if titles.get(c["session_id"])), None)
    st = _tree_stats_one(conn, tree_id)
    summ = _thread_summary(root, st, totals.get(tree_id), titles, len(flags), child_t)
    title = summ["title"]
    for f in flags:
        f["thread_title"] = title
    return {"thread": summ, "nodes": ordered, "flags": flags}


def _tree_stats_one(conn, tree_id: str) -> dict:
    r = conn.execute(
        """SELECT MIN(started) s, MAX(COALESCE(ended, started)) e, COUNT(*) cnt, MAX(depth) md,
                  GROUP_CONCAT(DISTINCT model) models, GROUP_CONCAT(DISTINCT family) fams, GROUP_CONCAT(DISTINCT source) srcs
           FROM nodes WHERE tree_id = ?""", (tree_id,)).fetchone()
    return dict(r) if r else {}


# --- projects / models / flags ------------------------------------------------------

def projects(conn, cfg, since: str | None = "90d") -> dict:
    since_iso = parse_since(since, cfg) or "0000"
    sel = _usage_select(cfg)
    rows_ = conn.execute(
        f"""SELECT r.project_path path, {sel}, COUNT(DISTINCT n.tree_id) threads, COUNT(DISTINCT n.id) nodes
            FROM usage u JOIN nodes n ON n.id = u.node_id JOIN nodes r ON r.id = n.tree_id
            WHERE n.counts_usage = 1 AND u.ts >= ? GROUP BY r.project_path""", (since_iso,)).fetchall()
    meta_ = {r["path"]: dict(r) for r in conn.execute("SELECT * FROM projects")}
    days = 30
    per_day = defaultdict(lambda: defaultdict(lambda: [0, 0.0]))
    zone = tz(cfg)
    start = to_utc_iso(local_midnight(cfg, datetime.now(zone).date() - timedelta(days=days - 1)))
    cov = {f for f, v in cfg.covered_by_plan.items() if v}
    for r in conn.execute(
        """SELECT r.project_path path, u.ts, u.family, u.input + u.cached_input + u.cache_write + u.output tot, u.cost
           FROM usage u JOIN nodes n ON n.id = u.node_id JOIN nodes r ON r.id = n.tree_id
           WHERE n.counts_usage = 1 AND u.ts >= ?""", (start,)):
        try:
            d = datetime.fromisoformat(r["ts"].replace("Z", "+00:00")).astimezone(zone).date().isoformat()
        except (AttributeError, ValueError):
            continue
        cell = per_day[r["path"]][d]
        cell[0] += r["tot"] or 0
        if r["cost"] and r["family"] not in cov:
            cell[1] += r["cost"]
    today = datetime.now(zone).date()
    day_list = [(today - timedelta(days=days - 1 - i)).isoformat() for i in range(days)]
    out = []
    for r in rows_:
        t, m = _tm(r)
        path = r["path"]
        pm = meta_.get(path, {})
        out.append({
            "path": path, "name": (path.rstrip("/").rsplit("/", 1)[-1] if path else "(no project)"),
            "first_ts": pm.get("first_ts"), "last_ts": pm.get("last_ts"),
            "threads": r["threads"], "nodes": r["nodes"], "tokens": t, "money": m,
            "daily": [{"day": d, "total": per_day[path][d][0], "cost": round(per_day[path][d][1], 6)} for d in day_list],
        })
    out.sort(key=lambda p: (spend(p["money"]), p["tokens"]["total"]), reverse=True)
    return {"projects": out}


def models(conn, cfg, since: str | None = "30d") -> dict:
    since_iso = parse_since(since, cfg) or "0000"
    sel = _usage_select(cfg)
    out = []
    node_rows = {r["id"]: dict(r) for r in conn.execute("SELECT id, description, role, agent_type, model FROM nodes")}
    per_model_nodes = defaultdict(set)
    per_model_types = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for r in conn.execute(f"SELECT u.model, u.node_id, SUM(u.input+u.cached_input+u.cache_write+u.output) tot {USAGE_JOIN} AND u.ts >= ? GROUP BY u.model, u.node_id", (since_iso,)):
        per_model_nodes[r["model"]].add(r["node_id"])
        tt = task_type(node_rows.get(r["node_id"], {}))
        cell = per_model_types[r["model"]][tt]
        cell[0] += 1
        cell[1] += r["tot"] or 0
    for r in conn.execute(f"SELECT u.model, COALESCE(u.family,'other') fam, {sel}, COUNT(DISTINCT n.tree_id) threads {USAGE_JOIN} AND u.ts >= ? GROUP BY u.model", (since_iso,)):
        t, m = _tm(r)
        out.append({
            "model": r["model"] or "unknown", "family": r["fam"], "nodes": len(per_model_nodes[r["model"]]), "threads": r["threads"],
            "tokens": t, "money": m, "priced": not r["unpriced"],
            "by_task_type": sorted(({"task_type": k, "nodes": v[0], "total": v[1]} for k, v in per_model_types[r["model"]].items()),
                                   key=lambda x: -x["total"]),
        })
    out.sort(key=lambda x: (spend(x["money"]), x["tokens"]["total"]), reverse=True)
    fams = []
    for r in conn.execute(f"SELECT COALESCE(u.family,'other') fam, {sel}, COUNT(DISTINCT u.node_id) nodes {USAGE_JOIN} AND u.ts >= ? GROUP BY fam", (since_iso,)):
        t, m = _tm(r)
        fams.append({"family": r["fam"], "tokens": t, "money": m, "nodes": r["nodes"]})
    return {"models": out, "families": fams,
            "daily": [{"day": d["day"], "by_family": d["by_family"]} for d in daily(conn, cfg, 30)]}


def flags(conn, cfg, since=None, rule=None, severity=None) -> dict:
    rules_cfg = load_rules(cfg)
    where, params = ["1=1"], []
    if since:
        where.append("COALESCE(f.ts, '') >= ?")
        params.append(parse_since(since, cfg))
    if rule:
        where.append("f.rule = ?")
        params.append(rule)
    if severity:
        where.append("f.severity = ?")
        params.append(severity)
    titles = {}
    out = []
    for r in conn.execute(f"SELECT f.* FROM flags f WHERE {' AND '.join(where)} ORDER BY f.ts DESC", params):
        d = dict(r)
        d["heuristic"] = bool(d["heuristic"])
        if d["tree_id"] not in titles:
            titles[d["tree_id"]] = _thread_title(conn, d["tree_id"])
        d["thread_title"] = titles[d["tree_id"]]
        out.append(d)
    # Rule counts follow the same date/severity filter as the list (not the rule filter).
    cwhere = [w for w in where if w != "f.rule = ?"]
    cparams = [p for w, p in zip([w for w in where if "?" in w], params) if w != "f.rule = ?"]
    counts = dict(conn.execute(f"SELECT f.rule, COUNT(*) FROM flags f WHERE {' AND '.join(cwhere)} GROUP BY f.rule", cparams).fetchall())
    rules_out = []
    for rid, label in RULE_LABELS.items():
        rc = rules_cfg.get(rid, {}) if isinstance(rules_cfg.get(rid), dict) else {}
        rules_out.append({"id": rid, "label": label, "severity": rc.get("severity", "warn"),
                          "heuristic": rid in HEURISTIC_RULES, "enabled": rc.get("enabled", True), "count": counts.get(rid, 0)})
    return {"flags": out, "rules": rules_out}


def _thread_title(conn, tree_id: str) -> str | None:
    row = conn.execute("SELECT s.title, n.description FROM nodes n LEFT JOIN sessions s ON s.id = n.session_id WHERE n.id = ?", (tree_id,)).fetchone()
    if row and (row["title"] or row["description"]):
        return row["title"] or row["description"]
    row = conn.execute("SELECT s.title FROM nodes n JOIN sessions s ON s.id = n.session_id WHERE n.parent_id = ? AND s.title IS NOT NULL ORDER BY n.started LIMIT 1", (tree_id,)).fetchone()
    return row["title"] if row else None


def range_nodes(conn, cfg, since=None, until=None) -> list[dict]:
    """Nodes active in a date range, flattened for export."""
    since_iso = parse_since(since, cfg) if since else None
    until_iso = parse_until(until, cfg) if until else None
    where, params = ["n.tree_id IS NOT NULL"], []
    if since_iso:
        where.append("COALESCE(n.ended, n.started) >= ?")
        params.append(since_iso)
    if until_iso:
        where.append("n.started < ?")
        params.append(until_iso)
    totals = {r["node_id"]: dict(r) for r in conn.execute("SELECT * FROM node_totals")}
    rows_ = [dict(r) for r in conn.execute(f"SELECT n.* FROM nodes n WHERE {' AND '.join(where)} ORDER BY n.tree_id, n.depth, n.started", params)]
    return [_node_out(n, totals.get(n["id"]), cfg, {}, {}) for n in rows_]


def now(conn, cfg, window_minutes: int = 10) -> dict:
    """Trees with activity in the last few minutes, for the menu bar "Now" card."""
    cutoff = to_utc_iso(datetime.now(timezone.utc) - timedelta(minutes=window_minutes))
    active = conn.execute(
        """SELECT tree_id, MAX(COALESCE(ended, started)) last,
                  SUM(CASE WHEN kind NOT IN ('root', 't3-thread') AND COALESCE(ended, started) >= ? THEN 1 ELSE 0 END) busy
           FROM nodes WHERE tree_id IS NOT NULL GROUP BY tree_id HAVING last >= ? ORDER BY last DESC LIMIT 10""",
        (cutoff, cutoff)).fetchall()
    if not active:
        return {"running": []}
    titles = _titles(conn)
    flag_counts = dict(conn.execute("SELECT tree_id, COUNT(*) FROM flags GROUP BY tree_id").fetchall())
    out = []
    for row in active:
        tree_id = row["tree_id"]
        root = conn.execute("SELECT * FROM nodes WHERE id = ?", (tree_id,)).fetchone()
        if root is None:
            continue
        root = dict(root)
        totals = conn.execute("SELECT * FROM node_totals WHERE node_id = ?", (tree_id,)).fetchone()
        child_title = conn.execute(
            "SELECT s.title FROM nodes n JOIN sessions s ON s.id = n.session_id WHERE n.parent_id = ? AND s.title IS NOT NULL ORDER BY n.started LIMIT 1",
            (tree_id,)).fetchone()
        summ = _thread_summary(root, _tree_stats_one(conn, tree_id), dict(totals) if totals else None, titles,
                               flag_counts.get(tree_id, 0), child_title[0] if child_title else None)
        # The model doing the work: the most recently active node with a model.
        lead = conn.execute(
            "SELECT model, family FROM nodes WHERE tree_id = ? AND model IS NOT NULL ORDER BY COALESCE(ended, started) DESC LIMIT 1",
            (tree_id,)).fetchone()
        # When the current burst of work began: the earliest start among nodes still active
        # (the root counts only when it is the only thing running). Drives the elapsed timer.
        since = conn.execute(
            """SELECT MIN(started) FROM nodes WHERE tree_id = ? AND COALESCE(ended, started) >= ?
               AND kind NOT IN ('root', 't3-thread')""", (tree_id, cutoff)).fetchone()[0]
        if not since:
            # Only the main session is busy: walk its activity back to the last 10-minute gap.
            stamps = [r[0] for r in conn.execute(
                "SELECT u.ts FROM usage u JOIN nodes n ON n.id = u.node_id WHERE n.tree_id = ? AND u.ts IS NOT NULL "
                "ORDER BY u.ts DESC LIMIT 5000", (tree_id,))]
            since = stamps[0] if stamps else summ["started"]
            for newer, older in zip(stamps, stamps[1:]):
                if (datetime.fromisoformat(newer.replace("Z", "+00:00"))
                        - datetime.fromisoformat(older.replace("Z", "+00:00"))) > timedelta(minutes=window_minutes):
                    break
                since = older
        out.append({
            "active_since": since,
            "thread_id": tree_id, "title": summ["title"], "project_name": summ["project_name"],
            "model": lead["model"] if lead else None, "family": lead["family"] if lead else None,
            "started": summ["started"], "last_activity": row["last"], "agents_running": row["busy"] or 0,
            "tokens": summ["tokens"], "money": summ["money"],
        })
    return {"running": out}
