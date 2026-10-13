"""Incremental parser for Codex rollout JSONL files."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..db import put_usage, touch_project, upsert_node, upsert_session
from ..privacy import clean_text, is_secret_path
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

PARSER_VERSION = "cx-5"
SOURCE = "codex"
_TOTAL_FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens")


def _ts(value: Any) -> str | None:
    return norm_ts(value)


def _min_ts(a: str | None, b: str | None) -> str | None:
    return b if not a else a if not b else min(a, b)


def _max_ts(a: str | None, b: str | None) -> str | None:
    return b if not a else a if not b else max(a, b)


def _totals(value: Any) -> dict[str, int]:
    value = value if isinstance(value, dict) else {}
    return {key: as_int(value.get(key)) for key in _TOTAL_FIELDS}


def _state() -> dict[str, Any]:
    return {"thread_id": None, "meta": {}, "cutoff": None, "fallback_cutoff_ts": None,
            "fallback_active": True, "latest_model": None, "effort": None, "started": None,
            "ended": None, "last_status": None, "last_status_ts": None,
            "last_task_started_ts": None, "last_task_complete_ts": None,
            "last_total": {key: 0 for key in _TOTAL_FIELDS}, "seen_token_count": False,
            "fallback_records": {}, "fallback_total": 0, "last_token_offset": None,
            "last_token_ts": None}


def _meta_state(payload: dict[str, Any], cfg: Any) -> dict[str, Any]:
    source = payload.get("source")
    spawn: dict[str, Any] = {}
    if isinstance(source, dict):
        subagent = source.get("subagent")
        if isinstance(subagent, dict) and isinstance(subagent.get("thread_spawn"), dict):
            spawn = subagent["thread_spawn"]
    thread_id = payload.get("id") or payload.get("session_id")
    parent = spawn.get("parent_thread_id") or payload.get("parent_thread_id")
    parent = str(parent) if parent and str(parent) != str(thread_id) else None
    source_value = source if isinstance(source, str) else ("subagent" if isinstance(source, dict) else payload.get("thread_source"))

    def text(value: Any) -> str | None:
        return clean_text(value, cfg.metadata_only, cfg.text_limit)

    return {"id": str(thread_id) if thread_id else None, "timestamp": _ts(payload.get("timestamp")),
            "cwd": str(payload.get("cwd")) if payload.get("cwd") else None,
            "originator": text(payload.get("originator")), "cli_version": text(payload.get("cli_version")),
            "model_provider": text(payload.get("model_provider")), "thread_source": text(payload.get("thread_source")),
            "agent_nickname": text(payload.get("agent_nickname")), "agent_path": text(payload.get("agent_path")),
            "source": text(source_value), "forked_from_id": str(payload.get("forked_from_id")) if payload.get("forked_from_id") else None,
            "parent_thread_id": parent, "depth": spawn.get("depth"), "agent_role": text(spawn.get("agent_role")),
            "spawn_agent_path": text(spawn.get("agent_path")), "spawn_agent_nickname": text(spawn.get("agent_nickname")),
            "subagent_history_start_ordinal": payload.get("subagent_history_start_ordinal")}


def _eligible(obj: dict[str, Any], state: dict[str, Any]) -> bool:
    cutoff, ordinal = state.get("cutoff"), obj.get("ordinal")
    if cutoff is not None and isinstance(ordinal, int):
        return ordinal >= as_int(cutoff)
    # A record-only rollout has no task_started marker. Its per-response usage
    # is still the complete source of billing data when no child cutoff exists.
    if obj.get("type") == "token_usage_record" and cutoff is None:
        return True
    if ordinal is None and state.get("fallback_active", True):
        payload = obj.get("payload")
        if obj.get("type") == "event_msg" and isinstance(payload, dict) and payload.get("type") == "task_started":
            stamp, cutoff_stamp = _ts(payload.get("started_at") or obj.get("timestamp")), state.get("fallback_cutoff_ts")
            if stamp and (not cutoff_stamp or stamp >= cutoff_stamp):
                state["fallback_active"] = False
                return True
        return False
    return True


def _record_fallback(state: dict[str, Any], payload: dict[str, Any], timestamp: str | None) -> None:
    response_id = payload.get("response_id")
    if response_id is None or not isinstance(payload.get("usage"), dict):
        return
    state.setdefault("fallback_records", {})[str(response_id)] = {"usage": _totals(payload.get("usage")), "ts": timestamp}


def _title_index(path: Path, stats: SourceStats, cfg: Any) -> dict[str, str]:
    latest: dict[str, tuple[str | None, str]] = {}
    if not path.exists() or is_secret_path(path):
        return {}
    try:
        with path.open("rb") as fh:
            for raw in fh:
                if not raw.endswith(b"\n"):
                    break
                stripped = raw.strip()
                if not stripped:
                    continue
                try:
                    value = json.loads(stripped)
                except (ValueError, UnicodeDecodeError):
                    stats.lines_skipped += 1
                    continue
                if not isinstance(value, dict) or not value.get("id"):
                    stats.lines_skipped += 1
                    continue
                ident, updated = str(value["id"]), _ts(value.get("updated_at"))
                title = clean_text(value.get("thread_name"), cfg.metadata_only, cfg.text_limit)
                old = latest.get(ident)
                if old is None or (updated or "") >= (old[0] or ""):
                    latest[ident] = (updated, title or "")
    except OSError as exc:
        stats.errors.append(f"session_index.jsonl: {exc}")
    return {ident: title for ident, (_, title) in latest.items() if title}


def _ident(value: Any) -> str | None:
    """Model and effort names: identifiers, kept in metadata-only mode but capped."""
    return str(value)[:80] if isinstance(value, (str, int, float)) and str(value) else None


def _is_recent(path: Path) -> bool:
    try:
        return datetime.now(timezone.utc).timestamp() - path.stat().st_mtime < 600
    except OSError:
        return False


def _display(state: dict[str, Any], cfg: Any, path: Path) -> dict[str, Any] | None:
    meta = state.get("meta") or {}
    thread_id = state.get("thread_id") or meta.get("id")
    if not thread_id:
        return None
    parent = meta.get("parent_thread_id")
    agent_path = meta.get("spawn_agent_path") or meta.get("agent_path")
    nickname = meta.get("spawn_agent_nickname") or meta.get("agent_nickname")
    role = meta.get("agent_role") or (Path(str(agent_path)).name if agent_path else None)
    status = state.get("last_status") or "unknown"
    if status == "running" and not _is_recent(path):
        status = "unknown"
    extra = {"depth": as_int(meta.get("depth")) if meta.get("depth") is not None else None,
             "agent_path": clean_text(agent_path, cfg.metadata_only, cfg.text_limit),
             "nickname": clean_text(nickname, cfg.metadata_only, cfg.text_limit),
             "originator": clean_text(meta.get("originator"), cfg.metadata_only, cfg.text_limit),
             "cli_version": clean_text(meta.get("cli_version"), cfg.metadata_only, cfg.text_limit),
             "source": clean_text(meta.get("source") or meta.get("thread_source"), cfg.metadata_only, cfg.text_limit),
             "model_provider": clean_text(meta.get("model_provider"), cfg.metadata_only, cfg.text_limit)}
    if meta.get("forked_from_id"):
        extra["forked_from"] = clean_text(meta["forked_from_id"], cfg.metadata_only, cfg.text_limit)
    extra = {key: value for key, value in extra.items() if value is not None}
    return {"thread_id": str(thread_id), "parent": str(parent) if parent else None,
            "kind": "subagent" if parent else "root", "role": clean_text(role, cfg.metadata_only, cfg.text_limit),
            "description": clean_text(agent_path or nickname, cfg.metadata_only, cfg.text_limit),
            "model": _ident(state.get("latest_model")),
            "family": model_family(state.get("latest_model")), "effort": _ident(state.get("effort")),
            "status": status, "started": state.get("started") or meta.get("timestamp"),
            "ended": _max_ts(state.get("ended"), state.get("last_seen_ts")),
            "project_path": meta.get("cwd"), "extra": extra}


def _write_display(conn: sqlite3.Connection, fields: dict[str, Any], title: str | None, path: Path) -> None:
    node_id = f"cx:{fields['thread_id']}"
    upsert_node(conn, node_id, session_id=node_id, source=SOURCE, kind=fields["kind"],
                parent_hint=f"cx:{fields['parent']}" if fields.get("parent") else None, role=fields["role"],
                agent_type="codex-subagent" if fields.get("parent") else "codex", description=fields["description"],
                model=fields["model"], family=fields["family"], effort=fields["effort"], status=fields["status"],
                started=fields["started"], ended=fields["ended"], project_path=fields["project_path"], raw_file=str(path), raw_offset=0,
                parser_version=PARSER_VERSION, extra_json=fields["extra"])
    upsert_session(conn, node_id, source=SOURCE, project_path=fields["project_path"], title=title,
                   started=fields["started"], ended=fields["ended"], root_node_id=node_id, raw_file=str(path), parser_version=PARSER_VERSION)
    touch_project(conn, fields["project_path"], fields["ended"] or fields["started"])


def _saved_totals(conn: sqlite3.Connection, paths: list[Path]) -> dict[str, list[tuple[str, int | None]]]:
    allowed, result = {str(path) for path in paths}, {}
    for row in conn.execute("SELECT path,state_json FROM files WHERE source=?", (SOURCE,)):
        if row[0] not in allowed or not row[1]:
            continue
        try:
            saved = json.loads(row[1])
        except (TypeError, ValueError):
            continue
        thread_id = saved.get("thread_id")
        if not thread_id:
            continue
        total = saved.get("last_total", {}).get("total_tokens") if saved.get("seen_token_count") else saved.get("fallback_total")
        result.setdefault(str(thread_id), []).append((row[0], as_int(total) if total is not None else None))
    return result


def _saved_last_totals(conn: sqlite3.Connection) -> dict[str, list[tuple[str, dict[str, int]]]]:
    """thread id -> [(path, final cumulative totals)] from earlier scans."""
    result: dict[str, list[tuple[str, dict[str, int]]]] = {}
    for row in conn.execute("SELECT path, state_json FROM files WHERE source=?", (SOURCE,)):
        try:
            saved = json.loads(row[1]) if row[1] else {}
        except (TypeError, ValueError):
            continue
        if saved.get("thread_id") and saved.get("seen_token_count"):
            result.setdefault(str(saved["thread_id"]), []).append((row[0], _totals(saved.get("last_total"))))
    return result


def _is_continuation(path: Path, thread_id: str) -> bool:
    """Long Codex threads continue in rollout-...-<id>_<suffix>.jsonl, with the
    cumulative token counter carrying on from the previous file."""
    return f"{thread_id}_" in path.name


def _key_prefix(path: Path, thread_id: str) -> str:
    """Usage keys are unique per file of a thread (continuations get their suffix),
    while the live and archived copies of one file share keys and so dedupe."""
    if _is_continuation(path, thread_id):
        return f"cx:{thread_id}:{path.stem.split(f'{thread_id}_', 1)[1]}"
    return f"cx:{thread_id}"


def _has_other_larger(seen: dict[str, list[tuple[str, int | None]]], thread_id: str, path: Path, total: int | None) -> bool:
    return total is not None and any(other != str(path) and value is not None and value >= total for other, value in seen.get(thread_id, []))


def _merge_cumulative(previous: dict[str, int], raw: Any) -> tuple[dict[str, int], dict[str, int], bool]:
    """Turn a cumulative total into the change since the previous one.

    Codex counters occasionally step back by a few tokens (seen at continuation
    file boundaries). Only a large drop (total below half the previous one) is
    a real reset that starts a new baseline; a small dip is recorded as a small
    negative change, so the rows always sum exactly to the last reported total.
    """
    source = raw if isinstance(raw, dict) else {}
    current = {key: as_int(source[key]) if key in source else previous.get(key, 0) for key in _TOTAL_FIELDS}
    prev_total = previous.get("total_tokens", 0)
    if prev_total > 0 and current.get("total_tokens", 0) < prev_total * 0.5:
        return current, dict(current), True
    return current, {key: current[key] - previous.get(key, 0) for key in _TOTAL_FIELDS}, False


def scan(conn: sqlite3.Connection, cfg: Any, full: bool = False) -> SourceStats:
    root = Path(cfg.codex_dir)
    stats = SourceStats(source=SOURCE, root=str(root), present=root.exists())
    if not root.exists():
        conn.commit()
        return stats
    titles = _title_index(root / "session_index.jsonl", stats, cfg)
    # Order by file name (it starts with the timestamp), so a thread's files are read in order
    # even when some were moved to archived_sessions/.
    paths = sorted({*safe_files(root / "sessions", "**/*.jsonl"), *safe_files(root / "archived_sessions", "**/*.jsonl")},
                   key=lambda p: (p.name, str(p)))
    stats.files_seen = len(paths)
    # A full rebuild invalidates saved cursors and cumulative totals. Ignore
    # stale state when arbitrating duplicate rollout files in that run.
    seen_totals, seen_nodes = ({} if full else _saved_totals(conn, paths)), set()
    last_totals = {} if full else _saved_last_totals(conn)
    for path in paths:
        cur = open_cursor(conn, path, SOURCE, PARSER_VERSION, full)
        if cur is None:
            continue
        stats.files_parsed += 1
        old_parsed, old_skipped = cur.parsed_lines, cur.skipped_lines
        state = _state()
        state.update(cur.state or {})
        state["meta"] = dict((cur.state or {}).get("meta") or {})
        state["last_total"] = _totals(state.get("last_total"))
        state.setdefault("fallback_records", {})
        first_meta_seen = bool(state.get("thread_id"))
        pending: list[tuple[str, str | None, str | None, dict[str, int]]] = []
        had_token_count = bool(state.get("seen_token_count"))
        token_record_count = 0
        try:
            # response_item lines hold message and tool content we never use; don't decode them.
            for line_offset, obj in iter_jsonl(cur, skip_markers=(b'"type":"response_item"',)):
                obj_type, payload = obj.get("type"), obj.get("payload")
                if not first_meta_seen:
                    if obj_type == "session_meta" and isinstance(payload, dict):
                        meta = _meta_state(payload, cfg)
                        if meta.get("id"):
                            state.update({"meta": meta, "thread_id": meta["id"], "cutoff": meta.get("subagent_history_start_ordinal"),
                                          "fallback_cutoff_ts": meta.get("timestamp"), "fallback_active": True, "started": meta.get("timestamp")})
                            first_meta_seen = True
                            if _is_continuation(path, str(meta["id"])):
                                # Seed the counter from the previous file of this thread so this
                                # file only stores its own increases (keeps per-day and per-model splits).
                                # File names sort chronologically across sessions/ and archived_sessions/.
                                earlier = sorted(((Path(p_).name, t) for p_, t in last_totals.get(str(meta["id"]), [])
                                                  if Path(p_).name < path.name), key=lambda item: item[0])
                                if earlier:
                                    state["last_total"] = earlier[-1][1]  # the most recent earlier file
                                state["continuation"] = True
                    continue
                if not _eligible(obj, state) or not isinstance(payload, dict):
                    continue
                # Last activity of this thread (copied parent history is already excluded).
                state["last_seen_ts"] = _max_ts(state.get("last_seen_ts"), _ts(obj.get("timestamp")))
                if obj_type == "turn_context":
                    collaboration = payload.get("collaboration_mode")
                    settings = collaboration.get("settings") if isinstance(collaboration, dict) else None
                    model = payload.get("model") or (settings.get("model") if isinstance(settings, dict) else None)
                    effort = payload.get("effort") or (settings.get("reasoning_effort") if isinstance(settings, dict) else None)
                    if _ident(model):
                        state["latest_model"] = _ident(model)
                    if _ident(effort):
                        state["effort"] = _ident(effort)
                elif obj_type == "event_msg":
                    event_kind, event_stamp = payload.get("type"), _ts(payload.get("started_at") or obj.get("timestamp"))
                    if event_kind == "task_started":
                        state["started"] = state.get("started") or event_stamp
                        state["last_task_started_ts"], state["last_status"], state["last_status_ts"] = event_stamp, "running", event_stamp
                        state["fallback_active"] = False
                    elif event_kind == "task_complete":
                        complete_stamp = _ts(obj.get("timestamp"))
                        state["ended"], state["last_task_complete_ts"] = _max_ts(state.get("ended"), complete_stamp), complete_stamp
                        started_stamp = state.get("last_task_started_ts")
                        if not started_stamp or not complete_stamp or complete_stamp >= started_stamp:
                            state["last_status"], state["last_status_ts"] = "completed", complete_stamp
                    elif event_kind in {"turn_aborted", "error"}:
                        stamp = _ts(obj.get("timestamp"))
                        state["ended"] = _max_ts(state.get("ended"), stamp)
                        if not state.get("last_status_ts") or not stamp or stamp >= state["last_status_ts"]:
                            state["last_status"], state["last_status_ts"] = ("aborted" if event_kind == "turn_aborted" else "failed"), stamp
                    elif event_kind == "token_count":
                        info = payload.get("info")
                        raw_usage = info.get("total_token_usage") if isinstance(info, dict) else None
                        if isinstance(raw_usage, dict):
                            previous = _totals(state.get("last_total"))
                            current, delta, reset = _merge_cumulative(previous, raw_usage)
                            if reset:
                                pending.clear()
                                conn.execute("DELETE FROM usage WHERE source=? AND raw_file=? AND node_id=?", (SOURCE, str(path), f"cx:{state['thread_id']}"))
                            state["last_total"], state["last_token_offset"], state["last_token_ts"] = current, line_offset, _ts(obj.get("timestamp"))
                            state["seen_token_count"] = True
                            if any(delta.values()):
                                pending.append((f"{_key_prefix(path, state['thread_id'])}:{line_offset}", state["last_token_ts"], state.get("latest_model"), delta))
                elif obj_type == "token_usage_record":
                    token_record_count += 1
                    if payload.get("thread_id") == state.get("thread_id"):
                        _record_fallback(state, payload, _ts(obj.get("timestamp")))
        except Exception as exc:  # drift in one file must not stop the others
            stats.errors.append(f"{path.name}: {type(exc).__name__}: {exc}")
            continue  # cursor not saved: the file is retried on the next scan
        if token_record_count:
            stats.notes.append(f"token_usage_record seen: {token_record_count}")
        thread_id = state.get("thread_id")
        if thread_id:
            if state.get("seen_token_count") and not had_token_count:
                conn.execute("DELETE FROM usage WHERE source=? AND raw_file=? AND key LIKE ?", (SOURCE, str(path), f"cx:{thread_id}:record:%"))
            if not state.get("seen_token_count"):
                state["fallback_total"] = 0
                for record in (state.get("fallback_records") or {}).values():
                    usage = record.get("usage", record) if isinstance(record, dict) else {}
                    if isinstance(usage, dict):
                        state["fallback_total"] += as_int(usage.get("total_tokens")) or as_int(usage.get("input_tokens")) + as_int(usage.get("output_tokens"))
            final_total = state.get("last_total", {}).get("total_tokens") if state.get("seen_token_count") else state.get("fallback_total")
            total_int = as_int(final_total) if final_total is not None else None
            # Two kinds of same-thread files: continuations (rollout-...-<id>_<suffix>.jsonl, the
            # counter carries on, rows are increments) and copies (the same history saved twice,
            # e.g. live + archived). Only copies are deduplicated, keeping the most complete one.
            copies = [(o, v) for o, v in seen_totals.get(str(thread_id), [])
                      if o != str(path) and not _is_continuation(Path(o), str(thread_id))]
            continuation = bool(state.get("continuation"))
            skip = not continuation and _has_other_larger({str(thread_id): copies}, str(thread_id), path, total_int)
            if skip:
                conn.execute("DELETE FROM usage WHERE source=? AND raw_file=?", (SOURCE, str(path)))
                stats.notes.append(f"skipped duplicate usage for {thread_id} in {path.name}")
            elif not continuation:
                replaced = False
                for other, value in copies:
                    if value is not None and total_int is not None and value < total_int:
                        conn.execute("DELETE FROM usage WHERE source=? AND raw_file=?", (SOURCE, other))
                        stats.notes.append(f"replaced duplicate usage for {thread_id} from {Path(other).name}")
                        replaced = True
                existing = conn.execute("SELECT 1 FROM usage WHERE source=? AND raw_file=? LIMIT 1", (SOURCE, str(path))).fetchone()
                if replaced and existing is None and state.get("seen_token_count") and total_int is not None:
                    # This copy's earlier rows were dropped while the other copy was ahead; restate its total.
                    pending = [(f"cx:{thread_id}:{state.get('last_token_offset', 0)}", state.get("last_token_ts"),
                                state.get("latest_model"), dict(state.get("last_total") or {}))]
            if not skip:
                for key, stamp, model, delta in pending:
                    # Signed on purpose: see _merge_cumulative. OpenAI input includes cached input.
                    cached = as_int(delta.get("cached_input_tokens"))
                    put_usage(conn, SOURCE, key, f"cx:{thread_id}", ts=stamp, model=model,
                              input=as_int(delta.get("input_tokens")) - cached, cached_input=cached,
                              cache_write=as_int(delta.get("cache_write_input_tokens")), output=as_int(delta.get("output_tokens")),
                              reasoning=as_int(delta.get("reasoning_output_tokens")), raw_file=str(path))
                if not state.get("seen_token_count"):
                    for response_id, record in (state.get("fallback_records") or {}).items():
                        usage = record.get("usage", record) if isinstance(record, dict) else {}
                        if not isinstance(usage, dict):
                            continue
                        cached = max(0, as_int(usage.get("cached_input_tokens")))
                        put_usage(conn, SOURCE, f"cx:{thread_id}:record:{response_id}", f"cx:{thread_id}", ts=record.get("ts") if isinstance(record, dict) else None,
                                  model=state.get("latest_model"), input=max(0, as_int(usage.get("input_tokens")) - cached), cached_input=cached,
                                  cache_write=max(0, as_int(usage.get("cache_write_input_tokens"))), output=max(0, as_int(usage.get("output_tokens"))),
                                  reasoning=max(0, as_int(usage.get("reasoning_output_tokens"))), raw_file=str(path))
            seen_totals.setdefault(str(thread_id), []).append((str(path), total_int))
            if state.get("seen_token_count"):
                last_totals.setdefault(str(thread_id), []).append((str(path), _totals(state.get("last_total"))))
            fields = _display(state, cfg, path)
            if fields:
                origin = path
                if state.get("continuation"):
                    row = conn.execute("SELECT raw_file FROM nodes WHERE id=?", (f"cx:{thread_id}",)).fetchone()
                    origin = Path(row[0]) if row and row[0] else path  # node stays with the thread's first file
                _write_display(conn, fields, titles.get(str(thread_id)), origin)
                if str(thread_id) not in seen_nodes:
                    seen_nodes.add(str(thread_id))
                    stats.nodes += 1
        stats.lines_parsed += cur.parsed_lines - old_parsed
        stats.lines_skipped += cur.skipped_lines - old_skipped
        cur.state = state
        save_cursor(conn, cur, SOURCE, PARSER_VERSION)
    for thread_id, title in titles.items():
        if conn.execute("SELECT 1 FROM sessions WHERE id=?", (f"cx:{thread_id}",)).fetchone():
            upsert_session(conn, f"cx:{thread_id}", title=title)
    conn.commit()
    return stats
