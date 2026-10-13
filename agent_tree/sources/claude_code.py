"""Parser for Claude Code JSONL transcripts.

The parser is deliberately conservative.  Transcript files are streamed through
``iter_jsonl`` and only the normalized fields needed by the index are written.
Free text is passed through the shared privacy helper before it reaches SQLite.
"""

from __future__ import annotations

import json
import shlex
import sqlite3
from pathlib import Path
from typing import Any

from .. import db
from ..config import Config
from ..privacy import clean_text
from .base import (
    SourceStats,
    as_int,
    iter_jsonl,
    model_family,
    norm_ts,
    open_cursor,
    safe_files,
    save_cursor,
)

PARSER_VERSION = "cc-1"
SOURCE = "claude-code"


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


def _non_synthetic_model(value: Any) -> str | None:
    if value is None:
        return None
    model = str(value)
    if not model or model.lower() == "<synthetic>":
        return None
    return model


def _blocks(message: Any) -> list[dict[str, Any]]:
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if isinstance(content, dict):
        content = [content]
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, dict)]


def _abs_prompt_path(value: Any) -> str | None:
    if value is None:
        return None
    try:
        path = Path(str(value)).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        return str(path.absolute())
    except (TypeError, ValueError, OSError):
        return None


def _wrapper_link(conn: sqlite3.Connection, command: Any, node_id: str, ts: str | None) -> None:
    """Record a caller link for a ``codex-agent.sh`` invocation."""
    if not isinstance(command, str) or "codex-agent.sh" not in command:
        return
    try:
        parts = shlex.split(command)
    except ValueError:
        return
    script_index = None
    for index, token in enumerate(parts):
        try:
            if Path(token).name == "codex-agent.sh":
                script_index = index
                break
        except (TypeError, ValueError):
            continue
    if script_index is None or len(parts) <= script_index + 4:
        return
    tier = parts[script_index + 1]
    prompt_file = _abs_prompt_path(parts[script_index + 4])
    if prompt_file is None:
        return
    effort = parts[script_index + 5] if len(parts) > script_index + 5 else None
    detail = json.dumps({"tier": tier, "effort": effort, "ts": ts}, sort_keys=True)
    db.put_link(conn, "wrapper-caller", prompt_file, node_id, "exact", detail)


def _old_nodes(conn: sqlite3.Connection, path: Path) -> list[tuple[str, str | None, str | None]]:
    rows = conn.execute("SELECT id, session_id, kind FROM nodes WHERE raw_file=?", (str(path),)).fetchall()
    return [(row[0], row[1], row[2]) for row in rows]


def _cleanup_restarted_file(conn: sqlite3.Connection, node_ids: list[str]) -> None:
    """Remove rows that ``forget_file`` cannot identify by raw_file."""
    if not node_ids:
        return
    marks = ",".join("?" for _ in node_ids)
    conn.execute(f"DELETE FROM spawns WHERE parent_node_id IN ({marks})", node_ids)
    conn.execute(f"DELETE FROM links WHERE kind='wrapper-caller' AND to_id IN ({marks})", node_ids)


def _meta_identity(path: Path, projects_root: Path) -> tuple[str, str] | None:
    """Return ``(session_id, agent_id)`` for a subagent metadata path."""
    try:
        rel = path.relative_to(projects_root)
    except ValueError:
        return None
    parts = rel.parts
    if len(parts) < 4 or parts[-2] != "subagents":
        return None
    name = parts[-1]
    if not name.endswith(".meta.json") or not name.startswith("agent-"):
        return None
    agent_id = name[len("agent-") : -len(".meta.json")]
    if not agent_id:
        return None
    return parts[-3], agent_id


def _transcript_identity(path: Path, projects_root: Path) -> tuple[str, str | None] | None:
    """Classify a transcript and return ``(fallback_session, agent_id)``."""
    try:
        rel = path.relative_to(projects_root)
    except ValueError:
        return None
    parts = rel.parts
    if "tool-results" in parts:
        return None
    if len(parts) >= 4 and parts[-2] == "subagents" and path.name.startswith("agent-"):
        agent_id = path.stem[len("agent-") :]
        if agent_id:
            return parts[-3], agent_id
        return None
    if len(parts) == 2:
        return path.stem, None
    return None


def _read_meta(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _upsert_meta_node(
    conn: sqlite3.Connection,
    cfg: Config,
    path: Path,
    projects_root: Path,
    meta: dict[str, Any],
    touched: set[str],
) -> tuple[str, str] | None:
    identity = _meta_identity(path, projects_root)
    if identity is None:
        return None
    session_id, agent_id = identity
    node_id = f"cc-agent:{agent_id}"
    extra = {
        "spawn_depth": meta.get("spawnDepth"),
        "request_shape": meta.get("requestShape"),
        "requested_model": meta.get("model"),
    }
    db.upsert_node(
        conn,
        node_id,
        session_id=f"cc:{session_id}",
        parent_hint=meta.get("toolUseId"),
        source=SOURCE,
        kind="subagent",
        agent_type=meta.get("agentType"),
        description=clean_text(meta.get("description"), cfg.metadata_only, cfg.text_limit),
        model=_non_synthetic_model(meta.get("model")),
        family=model_family(_non_synthetic_model(meta.get("model"))),
        extra_json=extra,
        raw_file=str(path),
        raw_offset=0,
        parser_version=PARSER_VERSION,
    )
    touched.add(node_id)
    return session_id, agent_id


def _node_state(
    cur_state: dict[str, Any],
    fallback_session: str,
    agent_id: str | None,
) -> tuple[str, str]:
    session_id = str(cur_state.get("session_id") or fallback_session)
    if agent_id is None:
        return session_id, f"cc:{session_id}"
    return session_id, f"cc-agent:{agent_id}"


def _record_state(state: dict[str, Any], record: dict[str, Any], ts: str | None, cfg: Config) -> None:
    if ts:
        state["started"] = _min_ts(state.get("started"), ts)
        state["ended"] = _max_ts(state.get("ended"), ts)
    session_id = record.get("sessionId")
    if session_id and not state.get("session_id"):
        state["session_id"] = str(session_id)
    cwd = record.get("cwd")
    if cwd and not state.get("cwd"):
        state["cwd"] = str(cwd)

    typ = record.get("type")
    if typ == "ai-title" and "aiTitle" in record:
        previous_ts = state.get("title_ts")
        if not previous_ts or not ts or ts >= previous_ts:
            state["title"] = clean_text(record.get("aiTitle"), cfg.metadata_only, cfg.text_limit)
            if ts:
                state["title_ts"] = ts
    if typ == "cost-state" and "totalCostUSD" in record:
        state["reported_cost_usd"] = record.get("totalCostUSD")

    effort = record.get("effort")
    if effort:
        previous_ts = state.get("effort_ts")
        if not previous_ts or not ts or ts >= previous_ts:
            state["effort"] = str(effort)
            if ts:
                state["effort_ts"] = ts

    message = record.get("message")
    if not isinstance(message, dict):
        return
    model = _non_synthetic_model(message.get("model"))
    if model:
        previous_ts = state.get("model_ts")
        if not previous_ts or not ts or ts >= previous_ts:
            state["model"] = model
            if ts:
                state["model_ts"] = ts


def _write_node(
    conn: sqlite3.Connection,
    cfg: Config,
    path: Path,
    fallback_session: str,
    agent_id: str | None,
    state: dict[str, Any],
    meta: dict[str, Any] | None,
) -> tuple[str, str]:
    session_id, node_id = _node_state(state, fallback_session, agent_id)
    model = _non_synthetic_model(state.get("model"))
    if agent_id is None:
        db.upsert_node(
            conn,
            node_id,
            session_id=f"cc:{session_id}",
            source=SOURCE,
            kind="root",
            agent_type="main",
            model=model,
            family=model_family(model),
            effort=state.get("effort"),
            status="unknown",
            started=state.get("started"),
            ended=state.get("ended"),
            project_path=state.get("cwd"),
            raw_file=str(path),
            raw_offset=0,
            parser_version=PARSER_VERSION,
        )
        extra = {}
        if "reported_cost_usd" in state:
            extra["reported_cost_usd"] = state.get("reported_cost_usd")
        db.upsert_session(
            conn,
            f"cc:{session_id}",
            source=SOURCE,
            project_path=state.get("cwd"),
            title=clean_text(state.get("title"), cfg.metadata_only, cfg.text_limit),
            started=state.get("started"),
            ended=state.get("ended"),
            root_node_id=node_id,
            raw_file=str(path),
            parser_version=PARSER_VERSION,
            extra_json=extra or None,
        )
    else:
        meta = meta or {}
        meta_model = _non_synthetic_model(meta.get("model"))
        extra = {
            "spawn_depth": meta.get("spawnDepth"),
            "request_shape": meta.get("requestShape"),
            "requested_model": meta.get("model"),
        }
        db.upsert_node(
            conn,
            node_id,
            session_id=f"cc:{session_id}",
            parent_hint=meta.get("toolUseId"),
            source=SOURCE,
            kind="subagent",
            agent_type=meta.get("agentType"),
            description=clean_text(meta.get("description"), cfg.metadata_only, cfg.text_limit),
            model=model or meta_model,
            family=model_family(model or meta_model),
            effort=state.get("effort"),
            started=state.get("started"),
            ended=state.get("ended"),
            project_path=state.get("cwd"),
            extra_json=extra,
            raw_file=str(path),
            raw_offset=0,
            parser_version=PARSER_VERSION,
        )
    return session_id, node_id


def _process_record(
    conn: sqlite3.Connection,
    cfg: Config,
    record: dict[str, Any],
    ts: str | None,
    node_id: str,
    path: Path,
) -> None:
    typ = record.get("type")
    message = record.get("message")
    if typ == "assistant" and isinstance(message, dict):
        model = _non_synthetic_model(message.get("model"))
        usage = message.get("usage")
        if model and isinstance(usage, dict):
            request_id = record.get("requestId")
            message_id = message.get("id")
            uuid = record.get("uuid")
            key = (
                f"cc:{request_id}" if request_id else
                f"cc:msg:{message_id}" if message_id else
                f"cc:uuid:{uuid}" if uuid else None
            )
            if key:
                details = usage.get("output_tokens_details")
                reasoning = details.get("thinking_tokens") if isinstance(details, dict) else 0
                db.put_usage(
                    conn,
                    SOURCE,
                    key,
                    node_id,
                    ts=ts,
                    model=model,
                    input=as_int(usage.get("input_tokens")),
                    cached_input=as_int(usage.get("cache_read_input_tokens")),
                    cache_write=as_int(usage.get("cache_creation_input_tokens")),
                    output=as_int(usage.get("output_tokens")),
                    reasoning=as_int(reasoning),
                    raw_file=str(path),
                )
        for block in _blocks(message):
            if block.get("type") != "tool_use":
                continue
            name = block.get("name")
            tool_id = block.get("id")
            if not tool_id:
                continue
            tool_input = block.get("input")
            if not isinstance(tool_input, dict):
                tool_input = {}
            if name in ("Agent", "Task"):
                db.put_spawn(
                    conn,
                    str(tool_id),
                    node_id,
                    description=clean_text(tool_input.get("description"), cfg.metadata_only, cfg.text_limit),
                    requested_model=tool_input.get("model"),
                    ts=ts,
                )
            elif name == "Bash":
                _wrapper_link(conn, tool_input.get("command"), node_id, ts)

    if typ != "user" or not isinstance(record.get("toolUseResult"), dict):
        return
    result = record["toolUseResult"]
    result_blocks = [block for block in _blocks(message) if block.get("type") == "tool_result"]
    for block in result_blocks:
        tool_id = block.get("tool_use_id")
        if not tool_id:
            continue
        db.put_spawn(
            conn,
            str(tool_id),
            node_id,
            child_hint=result.get("agentId"),
            resolved_model=result.get("resolvedModel"),
            status=result.get("status"),
        )


def _process_transcript(
    conn: sqlite3.Connection,
    cfg: Config,
    path: Path,
    projects_root: Path,
    full: bool,
    stats: SourceStats,
    touched: set[str],
    metas: dict[Path, dict[str, Any]],
) -> None:
    identity = _transcript_identity(path, projects_root)
    if identity is None:
        return
    fallback_session, agent_id = identity
    old_nodes = _old_nodes(conn, path)
    cur = open_cursor(conn, path, SOURCE, PARSER_VERSION, full)
    if cur is None:
        return
    if cur.restarted:
        _cleanup_restarted_file(conn, [node_id for node_id, _session, _kind in old_nodes])
        root_sessions = {
            session_id for node_id, session_id, kind in old_nodes
            if kind == "root" and session_id and node_id == session_id
        }
        if root_sessions:
            marks = ",".join("?" for _ in root_sessions)
            conn.execute(f"DELETE FROM sessions WHERE id IN ({marks})", tuple(root_sessions))
    stats.files_parsed += 1
    parsed_before, skipped_before = cur.parsed_lines, cur.skipped_lines
    state = dict(cur.state)
    meta_path = path.with_name(path.name.removesuffix(".jsonl") + ".meta.json")
    meta = metas.get(meta_path)
    for _offset, record in iter_jsonl(cur):
        ts = norm_ts(record.get("timestamp"))
        _record_state(state, record, ts, cfg)
        _session_id, record_node_id = _node_state(state, fallback_session, agent_id)
        _process_record(conn, cfg, record, ts, record_node_id, path)
    state["session_id"] = str(state.get("session_id") or fallback_session)
    session_id, node_id = _write_node(conn, cfg, path, fallback_session, agent_id, state, meta)
    touched.add(node_id)
    if state.get("cwd"):
        db.touch_project(conn, state.get("cwd"), state.get("started") or state.get("ended"))
        if state.get("ended") and state.get("ended") != state.get("started"):
            db.touch_project(conn, state.get("cwd"), state.get("ended"))
    cur.state = state
    stats.lines_parsed += cur.parsed_lines - parsed_before
    stats.lines_skipped += cur.skipped_lines - skipped_before
    save_cursor(conn, cur, SOURCE, PARSER_VERSION)
    conn.commit()


def scan(conn: sqlite3.Connection, cfg: Config, full: bool = False) -> SourceStats:
    projects_root = cfg.claude_dir / "projects"
    stats = SourceStats(source=SOURCE, root=str(cfg.claude_dir))
    if not projects_root.exists() or not projects_root.is_dir():
        return stats
    stats.present = True

    transcript_paths = [
        path for path in safe_files(projects_root, "**/*.jsonl")
        if _transcript_identity(path, projects_root) is not None
    ]
    meta_paths = [
        path for path in safe_files(projects_root, "**/*.meta.json")
        if _meta_identity(path, projects_root) is not None
    ]
    stats.files_seen = len(transcript_paths)

    metas: dict[Path, dict[str, Any]] = {}
    touched: set[str] = set()
    transcript_set = set(transcript_paths)
    for meta_path in meta_paths:
        meta = _read_meta(meta_path)
        if meta is None:
            continue
        metas[meta_path] = meta
        # A transcript parse below consumes the same metadata.  Upsert here
        # only when the transcript is absent, so an unchanged rescan remains
        # a no-op while meta-only agents still appear in the index.
        transcript_path = meta_path.with_name(meta_path.name.removesuffix(".meta.json") + ".jsonl")
        if transcript_path not in transcript_set:
            _upsert_meta_node(conn, cfg, meta_path, projects_root, meta, touched)
    conn.commit()

    for path in transcript_paths:
        _process_transcript(conn, cfg, path, projects_root, full, stats, touched, metas)
    stats.nodes = len(touched)
    return stats
