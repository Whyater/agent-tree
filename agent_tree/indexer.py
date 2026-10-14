"""Scan orchestration and tree resolution.

``scan()`` runs every source parser, then ``resolve()`` turns the raw rows into
trees: it links children to parents, attaches T3 threads and wrapper runs,
assigns depth and tree ids, prices usage, rolls totals up the tree and
evaluates the flag rules.
"""

from __future__ import annotations

import fcntl
import importlib
import json
import sqlite3
import time
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from . import db
from .config import Config
from .pricing import PriceBook, load_prices
from .sources.base import SourceStats, model_family, now_iso

SOURCES = ("claude_code", "codex", "t3", "wrapper", "events")  # order matters: later sources look up nodes from earlier ones
RUNNING_WINDOW = timedelta(minutes=10)
FUTURE_SKEW = timedelta(minutes=2)


class ScanBusy(RuntimeError):
    """Another process is already scanning this index."""


@contextmanager
def scan_lock(cfg: Config, wait: bool = True):
    """Cross-process lock so the CLI, the server and the app never scan the same index at once."""
    cfg.home.mkdir(parents=True, exist_ok=True)
    with open(cfg.home / "scan.lock", "a+") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError as exc:
            raise ScanBusy("another scan is running") from exc
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def scan_in_progress(cfg: Config) -> bool:
    """True when any process (CLI, server, app) holds the scan lock."""
    try:
        with scan_lock(cfg, wait=False):
            return False
    except ScanBusy:
        return True


def scan(conn: sqlite3.Connection, cfg: Config, full: bool = False, only: list[str] | None = None,
         wait: bool = True) -> list[SourceStats]:
    with scan_lock(cfg, wait=wait):
        return _scan(conn, cfg, full, only)


def _scan(conn: sqlite3.Connection, cfg: Config, full: bool, only: list[str] | None) -> list[SourceStats]:
    if full:
        db.wipe(conn)
        conn.commit()
    results: list[SourceStats] = []
    for name in SOURCES:
        if only and name not in only:
            continue
        try:
            module = importlib.import_module(f".sources.{name}", __package__)
        except ImportError as exc:
            results.append(SourceStats(source=name, errors=[f"parser unavailable: {exc}"]))
            continue
        started = time.perf_counter()
        try:
            stats = module.scan(conn, cfg, full=full)
            conn.commit()
        except Exception as exc:  # one broken source must not stop the others
            conn.rollback()
            stats = SourceStats(source=name, errors=[f"{type(exc).__name__}: {exc}"])
        stats.notes.append(f"{time.perf_counter() - started:.2f}s")
        results.append(stats)
    resolve(conn, cfg)
    db.set_meta(conn, "last_scan", now_iso())
    db.set_meta(conn, "last_scan_stats", json.dumps([s.as_dict() for s in results]))
    conn.commit()
    from .live import write as write_live  # snapshot for apps that show Agent Tree (see live.py)
    write_live(conn, cfg)
    return results


# --- tree resolution -----------------------------------------------------------

def resolve(conn: sqlite3.Connection, cfg: Config, prices: PriceBook | None = None) -> None:
    prices = prices or load_prices(cfg)
    nodes = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM nodes")}
    parent: dict[str, str | None] = {}
    confidence: dict[str, str] = {}
    hidden: set[str] = set()  # nodes that duplicate another node (merged away)

    spawns = {r["key"]: dict(r) for r in conn.execute("SELECT * FROM spawns")}
    spawn_by_child = {r["child_hint"]: r for r in spawns.values() if r.get("child_hint")}
    links = defaultdict(list)
    for r in conn.execute("SELECT * FROM links"):
        links[r["kind"]].append(dict(r))

    def set_parent(node_id: str, parent_id: str | None, conf: str) -> None:
        if parent_id == node_id:
            parent_id, conf = None, "orphan"
        parent[node_id] = parent_id
        confidence[node_id] = conf

    # 1. Per-source parent hints.
    for nid, n in nodes.items():
        hint = n.get("parent_hint")
        kind = n["kind"]
        if n["source"] == "claude-code" and kind == "subagent":
            agent_id = nid.split(":", 1)[1] if ":" in nid else None
            spawn = spawns.get(hint) if hint else None
            if spawn is None and agent_id:
                spawn = spawn_by_child.get(agent_id)
            if spawn and spawn["parent_node_id"] in nodes:
                set_parent(nid, spawn["parent_node_id"], "exact")
            else:
                root = n.get("session_id")  # cc:<sessionId> is also the root node id
                extra = json.loads(n["extra_json"]) if n.get("extra_json") else {}
                if root in nodes and (extra.get("spawn_depth") in (None, 1)):
                    set_parent(nid, root, "inferred")
                elif root in nodes:
                    set_parent(nid, root, "orphan")
                else:
                    set_parent(nid, None, "orphan")
        elif n["source"] == "codex" and hint:
            set_parent(nid, hint, "exact") if hint in nodes else set_parent(nid, None, "orphan")
        elif kind == "t3-task":
            set_parent(nid, hint if hint in nodes else None, "exact" if hint in nodes else "orphan")
        elif kind == "wrapper":
            set_parent(nid, None, "orphan")  # replaced below when a caller link exists
        else:
            set_parent(nid, None, "exact")

    # 2. Wrapper runs: caller (Claude node that ran codex-agent.sh) and the Codex session they created.
    callers = {l["from_id"]: l for l in links["wrapper-caller"]}
    for nid, n in nodes.items():
        if n["kind"] != "wrapper":
            continue
        task_path = (n.get("parent_hint") or "").removeprefix("wrapper-caller:")
        link = callers.get(task_path)
        if link and link["to_id"] in nodes:
            set_parent(nid, link["to_id"], link["confidence"])
    # Calls whose task path was a shell variable: attach still-orphaned runs to the
    # nearest such call made within 10 minutes after the task file was written.
    unresolved = []
    for l in links["wrapper-call-unresolved"]:
        ts = _epoch(json.loads(l["detail"] or "{}").get("ts"))
        if ts is not None and l["to_id"] in nodes:
            unresolved.append((ts, l["to_id"]))
    claimed = set()
    for nid, n in sorted(nodes.items(), key=lambda kv: kv[1].get("started") or ""):
        if n["kind"] != "wrapper" or parent.get(nid) is not None:
            continue
        start = _epoch(n.get("started"))
        if start is None:
            continue
        best = min(((ts - start, ts, caller) for ts, caller in unresolved
                    if 0 <= ts - start <= 600 and (ts, caller) not in claimed), default=None)
        if best:
            claimed.add((best[1], best[2]))
            set_parent(nid, best[2], "inferred")
    for l in links["wrapper-codex"]:
        child, wrapper = l["to_id"], l["from_id"]
        if child in nodes and wrapper in nodes and parent.get(child) is None:
            set_parent(child, wrapper, l["confidence"])

    # 3. T3 threads wrap the Claude/Codex session they drive.
    for l in sorted(links["t3-session"], key=lambda x: x["confidence"] != "exact"):
        t3_node, session_root = l["from_id"], l["to_id"]
        if t3_node in nodes and session_root in nodes and parent.get(session_root) is None:
            set_parent(session_root, t3_node, l["confidence"])

    # 4. T3 task rows duplicate agents we already see in the transcripts; hide those.
    tool_use_to_node = {}
    for nid, n in nodes.items():
        if n["source"] == "claude-code" and n["kind"] == "subagent" and n.get("parent_hint"):
            tool_use_to_node[n["parent_hint"]] = nid
    for l in links["t3-task"]:
        target = l["to_id"]
        if target.startswith("toolu:"):
            target = tool_use_to_node.get(target.removeprefix("toolu:"))
        if target and target in nodes and l["from_id"] in nodes:
            hidden.add(l["from_id"])
    # Unlinked T3 tasks are only useful when the thread has no transcript behind it.
    backed = {parent.get(c) for c, n in nodes.items() if n["kind"] != "t3-task" and parent.get(c)}
    for nid, n in nodes.items():
        if n["kind"] == "t3-task" and parent.get(nid) in backed:
            hidden.add(nid)

    # 5. Depth and tree ids (iterative, cycle-safe).
    children: dict[str | None, list[str]] = defaultdict(list)
    for nid in nodes:
        if nid in hidden:
            continue
        p = parent.get(nid)
        if p in hidden:
            p = None
        children[p].append(nid)
    for kids in children.values():
        kids.sort(key=lambda c: (nodes[c].get("started") or "", c))
    tree_of: dict[str, str] = {}
    depth_of: dict[str, int] = {}
    stack = [(root, root, 0) for root in children[None]]
    while stack:
        nid, tree, depth = stack.pop()
        if nid in tree_of:
            continue
        tree_of[nid], depth_of[nid] = tree, depth
        stack.extend((c, tree, depth + 1) for c in children.get(nid, []))
    for nid in sorted(nodes):  # anything left is in a cycle: break it there and walk it as a new root
        if nid in tree_of or nid in hidden:
            continue
        old_parent = parent.get(nid)
        if old_parent in children and nid in children[old_parent]:
            children[old_parent].remove(nid)
        parent[nid], confidence[nid] = None, "orphan"
        children[None].append(nid)
        stack = [(nid, nid, 0)]
        while stack:
            cur, tree, depth = stack.pop()
            if cur in tree_of:
                continue
            tree_of[cur], depth_of[cur] = tree, depth
            stack.extend((c, tree, depth + 1) for c in children.get(cur, []))

    # 6. Status. Time-based guesses ("running" = touched in the last 10 minutes)
    #    are recomputed on every resolve so they can't go stale; exact
    #    SubagentStart/Stop hook events win when present.
    now = datetime.now(timezone.utc)
    hook = _hook_states(conn)
    statuses = {}
    for nid, n in nodes.items():
        status = n.get("status")
        if nid in hook:
            status = "completed" if hook[nid].get("ended") else "running"
        elif n["source"] == "claude-code" and status in (None, "", "unknown", "done", "running"):
            spawn = spawns.get(n.get("parent_hint") or "")
            if spawn and spawn.get("status") in ("completed", "failed", "killed", "error"):
                status = spawn["status"]
            else:
                status = "running" if _recent(n.get("ended"), now) else "done"
        elif status == "running" and not _recent(n.get("ended") or n.get("started"), now):
            status = "stopped"
        statuses[nid] = status or "unknown"

    conn.execute("UPDATE nodes SET tree_id=NULL, depth=NULL")
    conn.executemany(
        "UPDATE nodes SET parent_id=?, link_confidence=?, tree_id=?, depth=?, status=? WHERE id=?",
        [
            (parent.get(nid), confidence.get(nid, "exact"), tree_of.get(nid), depth_of.get(nid), statuses[nid], nid)
            for nid in nodes
        ],
    )
    price_usage(conn, prices)
    rollup(conn, cfg, nodes, parent, children, hidden)
    from .flags import evaluate  # late import: flags reads the resolved tables
    evaluate(conn, cfg)


def _hook_states(conn) -> dict:
    row = conn.execute("SELECT state_json FROM files WHERE source='events' LIMIT 1").fetchone()
    if not row or not row[0]:
        return {}
    return json.loads(row[0]).get("agents", {})


def _epoch(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _recent(ts: str | None, now: datetime) -> bool:
    if not ts:
        return False
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    age = now.astimezone(timezone.utc) - dt
    # Records far in the future are clock errors, not active work: treating them
    # as recent would keep a thread running indefinitely. A little skew between
    # tools is normal, so a record up to two minutes ahead still counts.
    return -FUTURE_SKEW <= age < RUNNING_WINDOW


def price_usage(conn: sqlite3.Connection, prices: PriceBook) -> None:
    """(Re)price every usage row. Cheap enough to do on each resolve, and it
    keeps estimates in step with prices.toml edits."""
    updates = []
    for r in conn.execute("SELECT source, key, model, input, cached_input, cache_write, output FROM usage"):
        cost = prices.cost(r["model"], r["input"], r["cached_input"], r["cache_write"], r["output"])
        updates.append((cost, model_family(r["model"]), r["source"], r["key"]))
    conn.executemany("UPDATE usage SET cost=?, family=? WHERE source=? AND key=?", updates)


def rollup(conn, cfg: Config, nodes, parent, children, hidden) -> None:
    fields = ("input", "cached", "cache_write", "output", "reasoning", "cost", "covered", "unpriced")
    self_t: dict[str, list] = {nid: [0, 0, 0, 0, 0, 0.0, 0.0, 0] for nid in nodes}
    for r in conn.execute(
        """SELECT u.node_id, u.family, SUM(u.input) i, SUM(u.cached_input) c, SUM(u.cache_write) w,
                  SUM(u.output) o, SUM(u.reasoning) r, SUM(u.cost) cost,
                  SUM(CASE WHEN u.cost IS NULL AND (u.input+u.cached_input+u.cache_write+u.output) > 0 THEN 1 ELSE 0 END) unpriced
           FROM usage u JOIN nodes n ON n.id = u.node_id
           WHERE n.counts_usage = 1
           GROUP BY u.node_id, u.family"""
    ):
        t = self_t.get(r["node_id"])
        if t is None:
            continue
        t[0] += r["i"] or 0
        t[1] += r["c"] or 0
        t[2] += r["w"] or 0
        t[3] += r["o"] or 0
        t[4] += r["r"] or 0
        if cfg.is_covered(r["family"]):
            t[6] += r["cost"] or 0.0
        else:
            t[5] += r["cost"] or 0.0
        t[7] += r["unpriced"] or 0

    sub: dict[str, list] = {}
    order: list[str] = []
    stack = list(children.get(None, []))
    while stack:  # pre-order, then walk it backwards for a post-order fold
        nid = stack.pop()
        order.append(nid)
        stack.extend(children.get(nid, []))
    for nid in reversed(order):
        acc = list(self_t[nid]) + [1, 0]  # + node count, max relative depth
        for c in children.get(nid, []):
            ct = sub.get(c)
            if ct is None:
                continue
            for i in range(len(fields)):
                acc[i] += ct[i]
            acc[8] += ct[8]
            acc[9] = max(acc[9], ct[9] + 1)
        sub[nid] = acc

    conn.execute("DELETE FROM node_totals")
    conn.executemany(
        """INSERT INTO node_totals VALUES (?, ?,?,?,?,?,?,?,?, ?,?,?,?,?,?,?,?, ?,?)""",
        [(nid, *self_t[nid], *sub[nid]) for nid in order if nid in sub],
    )
