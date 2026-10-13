"""A small snapshot other apps can show: ~/.agent-tree/live.json.

Written atomically after every scan. It is the whole contract between Agent
Tree and anything that displays it (for example a card inside another menu bar
app), so the shape below is versioned and only grows by adding fields.

{
  "schema": 1,
  "generated_at": ISO,
  "totals": {"today": {"tokens": n, "cost": x|null}, "week": {...}, "month": {...}},
  "flags": {"total": n, "alert": n, "warn": n},
  "running": [ {thread_id, title, project_name, model, family, active_since, last_activity,
                agents_running, tokens, cost} ],
  "tree": {                                # the running thread, else the most recent one
    "thread_id", "title", "project_name", "live": bool, "node_count", "max_depth", "tokens", "cost",
    "nodes": [ {"id", "parent_id", "depth", "label", "model", "family", "status",
                "running": bool, "tokens", "cost"} ]   # depth-first, at most MAX_NODES
  } | null
}
Costs are estimates in US dollars (null when no price is known).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from . import queries
from .export import node_label

SCHEMA = 1
MAX_NODES = 60
RUNNING_WINDOW = timedelta(minutes=10)


def _cost(money: dict) -> float | None:
    if money.get("cost") is None:
        return None
    return round((money.get("cost") or 0) + (money.get("covered_cost") or 0), 4)


def build(conn, cfg) -> dict:
    summary = queries.summary(conn, cfg)
    totals = {name: {"tokens": p["tokens"]["total"], "cost": _cost(p["money"])}
              for name, p in summary["periods"].items()}
    running = [
        {k: r[k] for k in ("thread_id", "title", "project_name", "model", "family", "active_since",
                           "last_activity", "agents_running")}
        | {"tokens": r["tokens"]["total"], "cost": _cost(r["money"])}
        for r in queries.now(conn, cfg)["running"]
    ]
    focus = running[0]["thread_id"] if running else None
    if focus is None:
        latest = queries.list_threads(conn, cfg, limit=1)["threads"]
        focus = latest[0]["id"] if latest else None
    tree = None
    if focus:
        detail = queries.thread_detail(conn, cfg, focus)
        if detail:
            cutoff = (datetime.now(timezone.utc) - RUNNING_WINDOW).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            nodes = []
            for n in detail["nodes"][:MAX_NODES]:
                nodes.append({
                    "id": n["id"], "parent_id": n["parent_id"], "depth": n["depth"],
                    "label": node_label(n), "model": n["model"], "family": n["family"], "status": n["status"],
                    "running": n["status"] in ("running", "active") or bool(n["ended"] and n["ended"] >= cutoff
                                                                            and n["kind"] not in ("root", "t3-thread")),
                    "tokens": n["subtree"]["tokens"]["total"], "cost": _cost(n["subtree"]["money"]),
                })
            t = detail["thread"]
            tree = {"thread_id": t["id"], "title": t["title"], "project_name": t["project_name"],
                    "live": bool(running), "node_count": t["node_count"], "max_depth": t["max_depth"],
                    "tokens": t["tokens"]["total"], "cost": _cost(t["money"]), "nodes": nodes}
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "totals": totals,
        "flags": {"total": summary["flag_count"], **summary["flag_counts"]},
        "running": running,
        "tree": tree,
    }


def write(conn, cfg) -> None:
    """Best effort: a failure here must never break a scan."""
    try:
        data = build(conn, cfg)
        path = cfg.home / "live.json"
        tmp = path.with_name("live.json.tmp")
        tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        pass
