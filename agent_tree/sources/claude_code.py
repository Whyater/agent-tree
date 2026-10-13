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


def _abs_prompt_path(value: Any, cwd: str | None) -> str | None:
    if not isinstance(value, str) or not value or "$" in value or value.startswith("-"):
        return None
    try:
        path = Path(value).expanduser()
        if not path.is_absolute():
            if not cwd:
                return None
            path = Path(cwd) / path
        return str(path.absolute())
    except (TypeError, ValueError, OSError):
        return None


_SEPARATORS = {"&&", "||", ";", "|", "&"}
_SHELLS = {"sh", "bash", "zsh", "exec", "env", "nohup", "time"}


def _wrapper_link(conn: sqlite3.Connection, command: Any, node_id: str, ts: str | None, cwd: str | None) -> None:
    """Record a caller link when a Bash command *runs* ``codex-agent.sh``.

    The script must be in command position (start of a command, after a
    separator, env assignments or a shell/exec prefix); commands that merely
    mention it (cat, grep, sed ...) are ignored.
    """
    if not isinstance(command, str) or "codex-agent.sh" not in command:
        return
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        parts = list(lexer)
    except ValueError:
        return
    script_index = None
    at_start = True
    for index, token in enumerate(parts):
        if token in _SEPARATORS:
            at_start = True
            continue
        if at_start and ("=" in token and not token.startswith("=") and "/" not in token.split("=", 1)[0]):
            continue  # VAR=value prefix
        if at_start and token in _SHELLS:
            continue
        if at_start and Path(token).name == "codex-agent.sh":
            script_index = index
            break
        at_start = False
    if script_index is None:
        return
    args = []
    for token in parts[script_index + 1:]:
        if token in _SEPARATORS or token.startswith((">", "<", "2>")):
            break
        args.append(token)
    if len(args) < 4:
        return
    prompt_file = _abs_prompt_path(args[3], cwd)
    if prompt_file is None:
        return
    effort = _scalar(args[4], 40) if len(args) > 4 else None
    detail = json.dumps({"tier": _scalar(args[0], 40), "effort": effort, "ts": ts}, sort_keys=True)
    db.put_link(conn, "wrapper-caller", prompt_file, node_id, "exact", detail)


def _scalar(value: Any, limit: int = 160) -> str | None:
    """Identifiers from transcripts (ids, types, statuses, model names).

    Formats drift: a field that used to be a string may become a dict. Bind
    only plain scalars, capped, so one odd record can never break a scan.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (str, int, float)):
        text = str(value)
        return text[:limit] if text else None
    return None


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
        "request_shape": _scalar(meta.get("requestShape"), 40),
        "requested_model": _scalar(meta.get("model"), 80),
    }
    db.upsert_node(
        conn,
        node_id,
        session_id=f"cc:{session_id}",
        parent_hint=_scalar(meta.get("toolUseId")),
        source=SOURCE,
        kind="subagent",
        agent_type=_scalar(meta.get("agentType"), 80),
        description=clean_text(meta.get("description"), cfg.metadata_only, cfg.text_limit),
        model=_non_synthetic_model(_scalar(meta.get("model"), 80)),
        family=model_family(_non_synthetic_model(_scalar(meta.get("model"), 80))),
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
    session_id = _scalar(record.get("sessionId"), 80)
    if session_id and not state.get("session_id"):
        state["session_id"] = session_id
    cwd = record.get("cwd")
    if isinstance(cwd, str) and cwd and not state.get("cwd"):
        state["cwd"] = cwd[:1024]

    typ = record.get("type")
    if typ == "ai-title" and "aiTitle" in record:
        previous_ts = state.get("title_ts")
        if not previous_ts or not ts or ts >= previous_ts:
            state["title"] = clean_text(record.get("aiTitle"), cfg.metadata_only, cfg.text_limit)
            if ts:
                state["title_ts"] = ts
    if typ == "cost-state" and "totalCostUSD" in record:
        state["reported_cost_usd"] = record.get("totalCostUSD")

    effort = _scalar(record.get("effort"), 40)
    if effort:
        previous_ts = state.get("effort_ts")
        if not previous_ts or not ts or ts >= previous_ts:
            state["effort"] = _scalar(effort, 40)
            if ts:
                state["effort_ts"] = ts

    message = record.get("message")
    if not isinstance(message, dict):
        return
    model = _non_synthetic_model(_scalar(message.get("model"), 80))
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
        meta_model = _non_synthetic_model(_scalar(meta.get("model"), 80))
        extra = {
            "spawn_depth": meta.get("spawnDepth"),
            "request_shape": _scalar(meta.get("requestShape"), 40),
            "requested_model": _scalar(meta.get("model"), 80),
        }
        db.upsert_node(
            conn,
            node_id,
            session_id=f"cc:{session_id}",
            parent_hint=_scalar(meta.get("toolUseId")),
            source=SOURCE,
            kind="subagent",
            agent_type=_scalar(meta.get("agentType"), 80),
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
    cwd: str | None = None,
) -> None:
    typ = record.get("type")
    message = record.get("message")
    if typ == "assistant" and isinstance(message, dict):
        model = _non_synthetic_model(_scalar(message.get("model"), 80))
        usage = message.get("usage")
        if model and isinstance(usage, dict):
            request_id = _scalar(record.get("requestId"))
            message_id = _scalar(message.get("id"))
            uuid = _scalar(record.get("uuid"))
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
            tool_id = _scalar(block.get("id"))
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
                    requested_model=_scalar(tool_input.get("model"), 80),
                    ts=ts,
                )
            elif name == "Bash":
                _wrapper_link(conn, tool_input.get("command"), node_id, ts, cwd)

    if typ != "user" or not isinstance(record.get("toolUseResult"), dict):
        return
    result = record["toolUseResult"]
    agent_id = _scalar(result.get("agentId"))
    if not agent_id:
        return  # results of Bash, Read, etc. are not agent spawns
    result_blocks = [block for block in _blocks(message) if block.get("type") == "tool_result"]
    for block in result_blocks:
        tool_id = block.get("tool_use_id")
        if not tool_id:
            continue
        db.put_spawn(
            conn,
            str(tool_id),
            node_id,
            child_hint=agent_id,
            resolved_model=_scalar(result.get("resolvedModel"), 80),
            status=_scalar(result.get("status"), 40),
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
        record_cwd = record.get("cwd") if isinstance(record.get("cwd"), str) else state.get("cwd")
        _process_record(conn, cfg, record, ts, record_node_id, path, record_cwd)
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
        conn.execute("SAVEPOINT cc_file")
        try:
            _process_transcript(conn, cfg, path, projects_root, full, stats, touched, metas)
            conn.execute("RELEASE SAVEPOINT cc_file")
        except Exception as exc:  # one odd file must not stop the scan
            conn.execute("ROLLBACK TO SAVEPOINT cc_file")
            conn.execute("RELEASE SAVEPOINT cc_file")
            stats.errors.append(f"{path.name}: {type(exc).__name__}: {exc}")
    conn.commit()
    stats.nodes = len(touched)
    return stats
