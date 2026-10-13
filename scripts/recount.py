#!/usr/bin/env python3
"""Independent recount for one real session, compared with what Agent Tree indexed.

This deliberately shares no code with the parsers. It re-reads the raw files
with the simplest possible logic and prints both numbers side by side.

    python3 scripts/recount.py claude <sessionId>
    python3 scripts/recount.py codex <threadId>
    python3 scripts/recount.py pick            # suggest good candidates

Reads ~/.claude and ~/.codex read-only. Reads the index from ~/.agent-tree
(or $AGENT_TREE_HOME). Prints only ids and numbers, never content.
"""

import glob
import json
import os
import sqlite3
import sys
from pathlib import Path

HOME = Path(os.environ.get("AGENT_TREE_HOME", Path.home() / ".agent-tree"))
CLAUDE = Path(os.environ.get("AGENT_TREE_CLAUDE_DIR", Path.home() / ".claude")) / "projects"
CODEX = Path(os.environ.get("AGENT_TREE_CODEX_DIR", Path.home() / ".codex"))


def lines(path):
    with open(path, "rb") as fh:
        for raw in fh:
            if not raw.endswith(b"\n"):
                break
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if isinstance(obj, dict):
                yield obj


def index():
    return sqlite3.connect(f"file:{HOME / 'index.sqlite'}?mode=ro", uri=True)


def row(label, mine, tool):
    ok = "match" if mine == tool else "DIFF"
    print(f"  {label:<28} {mine:>16,} {tool:>16,}   {ok}")
    return mine == tool


# --- Claude Code -----------------------------------------------------------------

def claude(session_id):
    mains = glob.glob(str(CLAUDE / "*" / f"{session_id}.jsonl"))
    if not mains:
        sys.exit(f"no transcript for {session_id}")
    main = Path(mains[0])
    sub_dir = main.parent / session_id / "subagents"
    metas = sorted(sub_dir.glob("*.meta.json")) if sub_dir.is_dir() else []
    files = [main] + (sorted(sub_dir.glob("agent-*.jsonl")) if sub_dir.is_dir() else [])

    usage = {}  # requestId -> usage (last wins)
    spawn_parent = {}  # tool_use id -> file stem
    for f in files:
        for o in lines(f):
            if o.get("type") != "assistant":
                continue
            msg = o.get("message") or {}
            for b in msg.get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") in ("Agent", "Task"):
                    spawn_parent[b.get("id")] = f.stem
            u = msg.get("usage")
            if not isinstance(u, dict) or msg.get("model") == "<synthetic>":
                continue
            key = o.get("requestId") or msg.get("id") or o.get("uuid")
            usage[key] = u
    mine = {
        "input": sum(int(u.get("input_tokens") or 0) for u in usage.values()),
        "cached_input": sum(int(u.get("cache_read_input_tokens") or 0) for u in usage.values()),
        "cache_write": sum(int(u.get("cache_creation_input_tokens") or 0) for u in usage.values()),
        "output": sum(int(u.get("output_tokens") or 0) for u in usage.values()),
    }
    expected_parent = {}
    for m in metas:
        meta = json.loads(m.read_text())
        agent = m.name[len("agent-"):-len(".meta.json")]
        p = spawn_parent.get(meta.get("toolUseId"))
        expected_parent[f"cc-agent:{agent}"] = (
            f"cc:{session_id}" if p == session_id else f"cc-agent:{p[len('agent-'):]}" if p else None)

    conn = index()
    nodes = {r[0]: r[1] for r in conn.execute(
        "SELECT id, parent_id FROM nodes WHERE source='claude-code' AND session_id=?", (f"cc:{session_id}",))}
    tool = conn.execute(
        """SELECT COALESCE(SUM(u.input),0), COALESCE(SUM(u.cached_input),0), COALESCE(SUM(u.cache_write),0), COALESCE(SUM(u.output),0)
           FROM usage u JOIN nodes n ON n.id = u.node_id WHERE n.source='claude-code' AND n.session_id=?""",
        (f"cc:{session_id}",)).fetchone()

    print(f"Claude Code session {session_id}")
    print(f"  {'':<28} {'hand recount':>16} {'agent tree':>16}")
    ok = row("nodes (1 + meta files)", 1 + len(metas), len(nodes))
    for i, k in enumerate(("input", "cached_input", "cache_write", "output")):
        ok &= row(f"{k} tokens", mine[k], tool[i])
    ok &= row("total tokens", sum(mine.values()), sum(tool))
    wrong = [a for a, p in expected_parent.items() if nodes.get(a) != p]
    ok &= row("children with right parent", len(expected_parent), len(expected_parent) - len(wrong))
    for a in wrong[:5]:
        print(f"    {a}: expected parent {expected_parent[a]}, index has {nodes.get(a)}")
    return ok


# --- Codex -----------------------------------------------------------------------

def codex_files():
    for root in (CODEX / "sessions", CODEX / "archived_sessions"):
        yield from sorted(root.rglob("rollout-*.jsonl")) if root.is_dir() else []


def first_meta(path):
    with open(path, "rb") as fh:
        try:
            o = json.loads(fh.readline())
        except ValueError:
            return None
    return o.get("payload") if o.get("type") == "session_meta" else None


def codex(thread_id):
    metas, files_of = {}, {}
    for f in codex_files():
        m = first_meta(f)
        if m and m.get("id"):
            metas.setdefault(m["id"], (f, m))
            files_of.setdefault(m["id"], []).append(f)  # long threads continue in "<id>_<suffix>" files
    children = {}
    for tid, (_, m) in metas.items():
        spawn = ((m.get("source") or {}) if isinstance(m.get("source"), dict) else {}).get("subagent", {}).get("thread_spawn") or {}
        parent = spawn.get("parent_thread_id") or (m.get("parent_thread_id") if m.get("thread_source") == "subagent" else None)
        if parent:
            children.setdefault(parent, []).append(tid)
    tree, stack = [], [thread_id]
    while stack:
        t = stack.pop()
        if t in metas and t not in tree:
            tree.append(t)
            stack.extend(children.get(t, []))
    totals = {}
    for t in tree:
        last, last_ts = None, ""
        for f in files_of[t]:
            cutoff = (first_meta(f) or {}).get("subagent_history_start_ordinal")
            for o in lines(f):
                if cutoff is not None and isinstance(o.get("ordinal"), int) and o["ordinal"] < cutoff:
                    continue
                p = o.get("payload") or {}
                if o.get("type") == "event_msg" and p.get("type") == "token_count" and p.get("info"):
                    if (o.get("timestamp") or "") >= last_ts and p["info"].get("total_token_usage"):
                        last, last_ts = p["info"]["total_token_usage"], o.get("timestamp") or ""
        totals[t] = last or {}  # the counter is cumulative across continuation files
    mine_total = sum(int(v.get("input_tokens") or 0) + int(v.get("output_tokens") or 0) for v in totals.values())
    mine_out = sum(int(v.get("output_tokens") or 0) for v in totals.values())
    mine_cached = sum(int(v.get("cached_input_tokens") or 0) for v in totals.values())

    conn = index()
    ids = [f"cx:{t}" for t in tree]
    marks = ",".join("?" * len(ids))
    tool_nodes = conn.execute(f"SELECT COUNT(*) FROM nodes WHERE id IN ({marks})", ids).fetchone()[0]
    tool = conn.execute(
        f"SELECT COALESCE(SUM(input+cached_input+output),0), COALESCE(SUM(output),0), COALESCE(SUM(cached_input),0) FROM usage WHERE node_id IN ({marks})",
        ids).fetchone()
    parents_ok = 0
    for t in tree[1:]:
        _, m = metas[t]
        spawn = (m.get("source") or {}).get("subagent", {}).get("thread_spawn") if isinstance(m.get("source"), dict) else None
        expected = f"cx:{(spawn or {}).get('parent_thread_id') or m.get('parent_thread_id')}"
        got = conn.execute("SELECT parent_id FROM nodes WHERE id=?", (f"cx:{t}",)).fetchone()
        parents_ok += 1 if got and got[0] == expected else 0

    print(f"Codex thread {thread_id}")
    print(f"  {'':<28} {'hand recount':>16} {'agent tree':>16}")
    ok = row("threads in tree", len(tree), tool_nodes)
    ok &= row("total tokens (in + out)", mine_total, tool[0])
    ok &= row("cached input tokens", mine_cached, tool[2])
    ok &= row("output tokens", mine_out, tool[1])
    ok &= row("children with right parent", len(tree) - 1, parents_ok)
    return ok


def pick():
    best = []
    for d in CLAUDE.glob("*/*/subagents"):
        metas = list(d.glob("*.meta.json"))
        nested = sum(1 for m in metas if (json.loads(m.read_text()).get("spawnDepth") or 1) > 1)
        best.append((nested, len(metas), d.parent.name))
    best.sort(reverse=True)
    print("Claude sessions with the most nesting / subagents:")
    for nested, n, sid in best[:5]:
        print(f"  {sid}  subagents={n} nested={nested}")
    kids = {}
    for f in codex_files():
        m = first_meta(f)
        if m and m.get("thread_source") == "subagent":
            spawn = ((m.get("source") or {}).get("subagent") or {}).get("thread_spawn") or {} if isinstance(m.get("source"), dict) else {}
            p = spawn.get("parent_thread_id") or m.get("parent_thread_id")
            kids[p] = kids.get(p, 0) + 1
    print("Codex threads with the most subagents:")
    for p, n in sorted(kids.items(), key=lambda x: -x[1])[:5]:
        print(f"  {p}  children={n}")


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("claude", "codex", "pick"):
        sys.exit(__doc__)
    if sys.argv[1] == "pick":
        pick()
    else:
        ok = claude(sys.argv[2]) if sys.argv[1] == "claude" else codex(sys.argv[2])
        sys.exit(0 if ok else 1)
