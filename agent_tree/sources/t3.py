"""Read-only parser for the T3 Code desktop application's state database."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ..db import (
    put_link,
    touch_project,
    upsert_node,
    upsert_session,
)
from ..privacy import clean_text
from .base import (
    FileCursor,
    SourceStats,
    as_int,
    model_family,
    norm_ts,
    readonly_sqlite,
    save_cursor,
)

PARSER_VERSION = "t3-1"
MIN_RESCAN_SECONDS = 180
SOURCE = "t3"
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _json(value: Any) -> dict[str, Any] | list[Any] | None:
    if isinstance(value, (dict, list)):
        return value
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, (dict, list)) else None


def _ts(value: Any) -> str | None:
    return norm_ts(value)


def _min_ts(a: str | None, b: str | None) -> str | None:
    if not a:
        return b
    if not b:
        return a
    return min(a, b)


def _max_ts(a: str | None, b: str | None) -> str | None:
    if not a:
        return b
    if not b:
        return a
    return max(a, b)


def _bool(value: Any) -> bool:
    return value not in (None, "", 0, False, "0", "false", "False")


def _row_value(row: sqlite3.Row | dict[str, Any], key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _table(conn: sqlite3.Connection, name: str) -> list[sqlite3.Row]:
    return list(conn.execute(f"SELECT * FROM {name}"))


def _model_selection(value: Any) -> tuple[str | None, str | None, str | None]:
    obj = _json(value)
    if not isinstance(obj, dict):
        return None, None, None
    model = obj.get("model")
    provider = obj.get("instanceId") or obj.get("provider")
    effort = None
    options = obj.get("options")
    if isinstance(options, list):
        for option in options:
            if isinstance(option, dict) and str(option.get("id", "")).lower() in {"effort", "reasoning_effort"}:
                effort = option.get("value")
                break
    if effort is None:
        effort = obj.get("effort") or obj.get("reasoning_effort")
    return (str(model) if model is not None else None,
            str(provider) if provider is not None else None,
            str(effort) if effort is not None else None)


def _ident(value: Any) -> str | None:
    """Identifiers (model, effort, status) are kept in metadata-only mode but capped."""
    return str(value)[:80] if isinstance(value, (str, int, float)) and str(value) else None


def _provider_prefix(provider: Any) -> str:
    return "cc" if str(provider or "").lower() in {"claudeagent", "claude", "anthropic"} else "cx"


def _session_target(provider: Any, value: Any) -> str | None:
    if value is None or value == "":
        return None
    return f"{_provider_prefix(provider)}:{value}"


def _thread_project(rows: list[sqlite3.Row]) -> dict[str, sqlite3.Row]:
    return {str(_row_value(r, "project_id")): r for r in rows if _row_value(r, "project_id") is not None}


def _deleted(value: Any) -> bool:
    return value not in (None, "", 0, False, "0", "false", "False")


def _thread_status(thread: sqlite3.Row, turns: list[sqlite3.Row]) -> str:
    """active = a turn is running right now; otherwise archived or settled."""
    if turns:
        latest = max(turns, key=lambda t: (_ts(_row_value(t, "requested_at")) or _ts(_row_value(t, "started_at")) or ""))
        if str(_row_value(latest, "state") or "").lower() == "running" and not _row_value(latest, "completed_at"):
            return "active"
    if _deleted(_row_value(thread, "archived_at")):
        return "archived"
    return "settled"


def _turn_times(thread_id: str, turns: list[sqlite3.Row]) -> tuple[str | None, str | None]:
    matching = [t for t in turns if str(_row_value(t, "thread_id")) == thread_id]
    started = ended = None
    for turn in matching:
        started = _min_ts(started, _ts(_row_value(turn, "requested_at")) or _ts(_row_value(turn, "started_at")))
        ended = _max_ts(ended, _ts(_row_value(turn, "completed_at")))
    return started, ended


def _runtime_links(thread_id: str, provider_sessions: list[sqlite3.Row], runtimes: list[sqlite3.Row]) -> set[tuple[str, str]]:
    links: set[tuple[str, str]] = set()
    # Imported IDs are stable and are always exact.
    if thread_id.startswith("import:"):
        bits = thread_id.split(":", 2)
        if len(bits) == 3 and bits[2]:
            links.add(("exact", _session_target(bits[1], bits[2])))
    for row in provider_sessions:
        if str(_row_value(row, "thread_id")) != thread_id:
            continue
        provider = _row_value(row, "provider_name")
        ident = _row_value(row, "provider_session_id") or _row_value(row, "provider_thread_id")
        target = _session_target(provider, ident)
        if target:
            links.add(("exact", target))
    for row in runtimes:
        if str(_row_value(row, "thread_id")) != thread_id:
            continue
        provider = _row_value(row, "provider_name")
        cursor = _json(_row_value(row, "resume_cursor_json"))
        if isinstance(cursor, dict):
            # Claude threads keep the Claude Code session id in "resume"; their
            # "threadId" is the T3 thread's own id, so never treat that as a session.
            for key in ("resume", "threadId", "thread_id", "sessionId", "session_id", "providerSessionId"):
                value = cursor.get(key)
                if not isinstance(value, str) or value == thread_id or value.startswith("import:"):
                    continue
                target = _session_target(provider, value)
                if target:
                    links.add(("exact", target))
        payload = _json(_row_value(row, "runtime_payload_json"))
        if isinstance(payload, dict):
            transcripts = payload.get("importedTranscripts")
            if isinstance(transcripts, list):
                for item in transcripts:
                    if not isinstance(item, dict):
                        continue
                    p = item.get("provider") or item.get("providerName") or provider
                    ident = item.get("providerSessionId") or item.get("sessionId") or item.get("threadId")
                    target = _session_target(p, ident)
                    if target:
                        links.add(("exact", target))
    return {(confidence, target) for confidence, target in links if target and ":" in target}


def _infer_claude(conn: sqlite3.Connection, project_path: str | None, started: str | None, ended: str | None) -> list[str]:
    if not project_path:
        return []
    candidates: list[str] = []
    for row in conn.execute("SELECT id, started, ended, project_path FROM nodes WHERE source='claude-code' AND kind='root'"):
        if row["project_path"] != project_path:
            continue
        cstart, cend = row["started"], row["ended"]
        # A missing endpoint is treated as a point. Keep a generous two-minute
        # boundary for native T3 rows written a little before the transcript.
        if started and cend and cend < started:
            continue
        if ended and cstart and cstart > ended:
            continue
        if (started and cstart) and abs(_epoch(started) - _epoch(cstart)) > 120 and not (cstart <= (ended or started) and (cend or started) >= started):
            continue
        candidates.append(str(row["id"]))
    return candidates


def _epoch(value: str) -> float:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, OSError):
        return 0.0


def _activity_payload(row: sqlite3.Row) -> dict[str, Any]:
    payload = _json(_row_value(row, "payload_json"))
    return payload if isinstance(payload, dict) else {}


def _activity_text(payload: dict[str, Any], row: sqlite3.Row, key: str) -> Any:
    value = payload.get(key)
    if value is None:
        value = _row_value(row, key)
    return value


def _signature(path: Path) -> tuple[int, int, int, int]:
    st = path.stat()
    wal = Path(str(path) + "-wal")
    try:
        ws = wal.stat()
        return st.st_size, st.st_mtime_ns, ws.st_size, ws.st_mtime_ns
    except OSError:
        return st.st_size, st.st_mtime_ns, 0, 0


def _unchanged(conn: sqlite3.Connection, path: Path, full: bool) -> bool:
    if full:
        return False
    try:
        sig = _signature(path)
    except OSError:
        return True
    row = conn.execute("SELECT size, mtime_ns, parser_version, state_json FROM files WHERE path=?", (str(path),)).fetchone()
    if not row or row["parser_version"] != PARSER_VERSION:
        return False
    state = _json(row["state_json"]) or {}
    if (row["size"], row["mtime_ns"], as_int(state.get("wal_size")), as_int(state.get("wal_mtime_ns"))) == sig:
        return True
    # T3 writes its WAL constantly while open, and reading it means snapshotting
    # the database. Thread metadata changes slowly, so re-read at most every few minutes.
    last = conn.execute("SELECT last_scan FROM files WHERE path=?", (str(path),)).fetchone()
    try:
        age = time.time() - datetime.fromisoformat(str(last[0]).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return False
    return age < MIN_RESCAN_SECONDS


def _clear_previous(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM links WHERE kind IN ('t3-session','t3-task')")
    conn.execute("DELETE FROM nodes WHERE source=?", (SOURCE,))
    conn.execute("DELETE FROM sessions WHERE source=?", (SOURCE,))


def scan(conn: sqlite3.Connection, cfg: Any, full: bool = False) -> SourceStats:
    root = Path(cfg.t3_dir)
    path = root / "userdata" / "state.sqlite"
    stats = SourceStats(source=SOURCE, root=str(root), present=path.is_file())
    if not path.is_file():
        return stats
    stats.files_seen = 1
    if _unchanged(conn, path, full):
        return stats

    try:
        sig = _signature(path)
    except OSError as exc:
        stats.errors.append(str(exc))
        return stats
    stats.files_parsed = 1
    try:
        with readonly_sqlite(path) as t3:
            projects = _thread_project(_table(t3, "projection_projects"))
            threads = _table(t3, "projection_threads")
            turns = _table(t3, "projection_turns")
            sessions = _table(t3, "projection_thread_sessions")
            runtimes = _table(t3, "provider_session_runtime")
            # Only the kinds we use; the full table carries large payloads.
            activities = t3.execute(
                "SELECT thread_id, kind, sequence, created_at, payload_json FROM projection_thread_activities "
                "WHERE kind = 'context-window.updated' OR kind LIKE 'task.%'"
            ).fetchall()
    except Exception as exc:
        stats.errors.append(f"state.sqlite: {type(exc).__name__}: {exc}")
        return stats

    # Once the snapshot has been opened successfully, replace all rows derived
    # from the previous T3 snapshot. This keeps deleted threads from lingering
    # while ensuring a read error never destroys the last good index.
    _clear_previous(conn)

    turns_by_thread: dict[str, list[sqlite3.Row]] = {}
    for turn in turns:
        turns_by_thread.setdefault(str(_row_value(turn, "thread_id")), []).append(turn)
    sessions_by_thread: dict[str, list[sqlite3.Row]] = {}
    for row in sessions:
        sessions_by_thread.setdefault(str(_row_value(row, "thread_id")), []).append(row)
    runtimes_by_thread: dict[str, list[sqlite3.Row]] = {}
    for row in runtimes:
        runtimes_by_thread.setdefault(str(_row_value(row, "thread_id")), []).append(row)
    activities_by_thread: dict[str, list[sqlite3.Row]] = {}
    for row in activities:
        activities_by_thread.setdefault(str(_row_value(row, "thread_id")), []).append(row)

    thread_fields: dict[str, dict[str, Any]] = {}
    for thread in threads:
        thread_id = str(_row_value(thread, "thread_id") or "")
        if not thread_id or _deleted(_row_value(thread, "deleted_at")):
            continue
        project = projects.get(str(_row_value(thread, "project_id")))
        project_path = _row_value(project, "workspace_root") if project is not None else None
        title = clean_text(_row_value(thread, "title"), cfg.metadata_only, cfg.text_limit)
        model, provider, effort = _model_selection(_row_value(thread, "model_selection_json"))
        created = _ts(_row_value(thread, "created_at"))
        updated = _ts(_row_value(thread, "updated_at"))
        turn_started, turn_ended = _turn_times(thread_id, turns_by_thread.get(thread_id, []))
        # T3's thread creation timestamp is the canonical start; turn timing
        # is only a fallback for partially migrated rows.
        started = created or turn_started
        ended = _max_ts(updated, turn_ended)
        status = _thread_status(thread, turns_by_thread.get(thread_id, []))
        context: dict[str, int] | None = None
        for activity in sorted(activities_by_thread.get(thread_id, []), key=lambda a: (as_int(_row_value(a, "sequence")), _ts(_row_value(a, "created_at")) or "")):
            if _row_value(activity, "kind") != "context-window.updated":
                continue
            payload = _activity_payload(activity)
            context = {
                "usedTokens": as_int(payload.get("usedTokens", payload.get("used_tokens"))),
                "maxTokens": as_int(payload.get("maxTokens", payload.get("max_tokens"))),
            }
        extra: dict[str, Any] = {
            "t3_project": clean_text(_row_value(project, "title"), cfg.metadata_only, cfg.text_limit) if project is not None else None,
            "archived": _deleted(_row_value(thread, "archived_at")),
        }
        if context is not None:
            extra["t3_context"] = context
        extra = {k: v for k, v in extra.items() if v is not None}
        node_id = f"t3:{thread_id}"
        upsert_node(
            conn, node_id, session_id=node_id, source=SOURCE, kind="t3-thread",
            agent_type=_ident(provider) or "t3", description=title,
            model=_ident(model), family=model_family(model),
            effort=_ident(effort), status=status,
            started=started, ended=ended, project_path=str(project_path) if project_path else None,
            raw_file=str(path), raw_offset=0, parser_version=PARSER_VERSION, extra_json=extra,
        )
        upsert_session(
            conn, node_id, source=SOURCE, project_path=str(project_path) if project_path else None,
            title=title, started=started, ended=ended, root_node_id=node_id,
            raw_file=str(path), parser_version=PARSER_VERSION,
        )
        touch_project(conn, str(project_path) if project_path else None, started or ended)
        thread_fields[thread_id] = {"node_id": node_id, "project_path": str(project_path) if project_path else None, "started": started, "ended": ended, "status": status}
        stats.nodes += 1

        targets = _runtime_links(thread_id, sessions_by_thread.get(thread_id, []), runtimes_by_thread.get(thread_id, []))
        for confidence, target in targets:
            put_link(conn, "t3-session", node_id, target, confidence, "T3 imported/provider session id")
        if not targets and str(provider or "").lower() in {"claudeagent", "claude"}:
            candidates = _infer_claude(conn, str(project_path) if project_path else None, started, ended)
            if len(candidates) == 1:
                put_link(conn, "t3-session", node_id, candidates[0], "inferred", "same workspace and overlapping time window")

    task_rows: dict[tuple[str, str], dict[str, Any]] = {}
    ordered_activities = sorted(
        activities,
        key=lambda a: (
            str(_row_value(a, "thread_id") or ""),
            as_int(_row_value(a, "sequence")),
            _ts(_row_value(a, "created_at")) or "",
        ),
    )
    for activity in ordered_activities:
        thread_id = str(_row_value(activity, "thread_id") or "")
        if thread_id not in thread_fields:
            continue
        kind = str(_row_value(activity, "kind") or "")
        if not kind.startswith("task."):
            continue
        payload = _activity_payload(activity)
        task_id = _activity_text(payload, activity, "taskId") or _activity_text(payload, activity, "task_id")
        if task_id is None:
            continue
        task_id = str(task_id)
        key = (thread_id, task_id)
        agent_kind = _activity_text(payload, activity, "agentKind") or _activity_text(payload, activity, "agent_kind")
        # Completions occasionally omit agentKind. A task that was previously
        # identified as an agent remains eligible for its later updates.
        known_agent = key in task_rows
        if str(agent_kind or "").lower() != "agent" and not known_agent:
            continue
        item = task_rows.setdefault(key, {"thread_id": thread_id, "task_id": task_id, "started": None, "ended": None, "status": None, "payload": {}})
        ts = _ts(_row_value(activity, "created_at"))
        if kind == "task.started":
            item["started"] = _min_ts(item["started"], ts)
            item["status"] = "active"
        elif kind == "task.completed":
            item["ended"] = _max_ts(item["ended"], ts)
            item["status"] = str(_activity_text(payload, activity, "status") or "completed")
        else:
            item["started"] = _min_ts(item["started"], ts)
        item["payload"].update({key: value for key, value in payload.items() if value is not None})

    for (thread_id, task_id), item in task_rows.items():
        payload = item["payload"]
        node_id = f"t3-task:{thread_id}:{task_id}"
        title = _activity_text(payload, {}, "title") or _activity_text(payload, {}, "detail")
        role = _activity_text(payload, {}, "role")
        model = _activity_text(payload, {}, "model")
        effort = _activity_text(payload, {}, "effort")
        agent_path = _activity_text(payload, {}, "agentPath") or _activity_text(payload, {}, "agent_path")
        task_type = _activity_text(payload, {}, "taskType") or _activity_text(payload, {}, "task_type")
        tool_use_id = _activity_text(payload, {}, "toolUseId") or _activity_text(payload, {}, "tool_use_id")
        thread = thread_fields[thread_id]
        # A task that never reported completion is only "active" while its thread is.
        if item["status"] == "active" and thread["status"] != "active":
            item["status"] = "stopped"
        extra = {"agent_path": clean_text(agent_path, cfg.metadata_only, cfg.text_limit), "task_type": clean_text(task_type, cfg.metadata_only, cfg.text_limit), "tool_use_id": clean_text(tool_use_id, cfg.metadata_only, cfg.text_limit)}
        extra = {k: v for k, v in extra.items() if v is not None}
        upsert_node(
            conn, node_id, session_id=thread["node_id"], parent_hint=thread["node_id"], source=SOURCE,
            kind="t3-task", agent_type="t3-task", role=clean_text(role, cfg.metadata_only, cfg.text_limit),
            description=clean_text(title, cfg.metadata_only, cfg.text_limit), model=_ident(model),
            family=model_family(model), effort=_ident(effort), status=_ident(item["status"]),
            started=item["started"], ended=item["ended"], project_path=thread["project_path"], raw_file=str(path), raw_offset=0,
            parser_version=PARSER_VERSION, extra_json=extra,
        )
        stats.nodes += 1
        if _UUID_RE.match(task_id):
            put_link(conn, "t3-task", node_id, f"cx:{task_id}", "exact", "taskId is a Codex thread id")
        if tool_use_id:
            put_link(conn, "t3-task", node_id, f"toolu:{tool_use_id}", "exact", "tool_use id")

    try:
        st = path.stat()
        cur = FileCursor(str(path), 0, {"wal_size": sig[2], "wal_mtime_ns": sig[3]}, True,
                         st.st_size, st.st_mtime_ns, st.st_ino)
        save_cursor(conn, cur, SOURCE, PARSER_VERSION)
    except OSError as exc:
        stats.errors.append(f"cursor: {exc}")
    return stats
