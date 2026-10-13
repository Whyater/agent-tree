"""Live events written by the opt-in Claude Code hooks (agent_tree/hook.py).

Hooks carry no tokens; they only make "running" vs "finished" exact for
subagents while a session is still going. Tokens always come from transcripts.
"""

from __future__ import annotations

from .base import SourceStats, iter_jsonl, open_cursor, save_cursor

PARSER_VERSION = "ev-1"


def scan(conn, cfg, full: bool = False) -> SourceStats:
    stats = SourceStats(source="events", root=str(cfg.events_path))
    path = cfg.events_path
    if not path.is_file():
        stats.notes.append("hooks not enabled (optional)")
        return stats
    stats.present = True
    stats.files_seen = 1
    cur = open_cursor(conn, path, "events", PARSER_VERSION, full)
    if cur is None:
        return stats
    stats.files_parsed = 1
    for _, ev in iter_jsonl(cur):
        agent_id, name = ev.get("agent_id"), ev.get("hook_event_name")
        if not agent_id or name not in ("SubagentStart", "SubagentStop"):
            continue
        key = f"cc-agent:{agent_id}"
        state = cur.state.setdefault("agents", {})
        if name == "SubagentStart":
            state.setdefault(key, {})["started"] = ev.get("ts")
        else:
            state.setdefault(key, {})["ended"] = ev.get("ts")
    # The indexer reads cur.state["agents"] when it resolves statuses.
    stats.lines_parsed = cur.parsed_lines
    stats.lines_skipped = cur.skipped_lines
    save_cursor(conn, cur, "events", PARSER_VERSION)
    return stats
