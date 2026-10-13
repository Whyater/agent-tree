"""Parser for runs launched by the ``codex-agent.sh`` wrapper.

The wrapper leaves a task prompt (``.md``), a final response (``.out``), and
usually a log (``.log``).  This parser indexes only metadata from those files:
contents of prompts and final responses are deliberately never read or stored.
"""

from __future__ import annotations

import json
import re
import time
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..db import forget_file, put_link, put_usage, touch_project, upsert_node
from ..privacy import clean_text, is_secret_path
from .base import SourceStats, as_int, model_family, norm_ts, open_cursor, safe_files, save_cursor

PARSER_VERSION = "wr-1"
SOURCE = "wrapper"

# codex exec's header has historically used all of these spellings.  Keep the
# scan bounded to the first 200 lines since logs can contain arbitrary output.
_SESSION_RE = re.compile(r"(?i)\bsession\s+id\s*:\s*([0-9a-f][0-9a-f-]{15,})\b")
_MODEL_RE = re.compile(r"(?i)^\s*model\s*:\s*(.+?)\s*$")
_EFFORT_RE = re.compile(r"(?i)^\s*reasoning\s+effort\s*:\s*(.+?)\s*$")
_WORKDIR_RE = re.compile(r"(?i)^\s*workdir\s*:\s*(.+?)\s*$")


def _sibling(path: Path, suffix: str) -> Path:
    """Return ``foo.md.<suffix>`` rather than Path.with_suffix()."""
    return Path(str(path) + suffix)


def _stat_signature(path: Path) -> dict[str, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return {"size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns), "inode": int(stat.st_ino)}


def _fingerprint(paths: dict[str, Path]) -> dict[str, Any]:
    return {name: _stat_signature(path) for name, path in paths.items()}


def _read_meta(path: Path) -> dict[str, Any]:
    if not path.exists() or is_secret_path(path):
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            value = json.load(fh)
    except (OSError, ValueError, UnicodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _log_fields(path: Path) -> dict[str, str]:
    """Read at most 200 lines and extract codex's header fields."""
    fields: dict[str, str] = {}
    if not path.exists() or is_secret_path(path):
        return fields
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for number in range(200):
                line = fh.readline(65536)  # bounded: one huge line can't load a whole file
                if not line:
                    break
                text = line.rstrip("\r\n")
                match = _SESSION_RE.search(text)
                if match and "session_id" not in fields:
                    fields["session_id"] = match.group(1)
                for key, pattern in (
                    ("model", _MODEL_RE),
                    ("effort", _EFFORT_RE),
                    ("workdir", _WORKDIR_RE),
                ):
                    match = pattern.match(text)
                    if match and key not in fields:
                        fields[key] = match.group(1).strip()
    except OSError:
        return fields
    return fields


def _mtime(path: Path) -> str | None:
    try:
        return norm_ts(path.stat().st_mtime)
    except OSError:
        return None


def _first(value: Any, *fallbacks: Any) -> Any:
    if value not in (None, ""):
        return value
    for fallback in fallbacks:
        if fallback not in (None, ""):
            return fallback
    return None


def _status(meta: dict[str, Any], out: Path, log: Path) -> str:
    # A metadata exit code is authoritative whenever present, including a
    # non-integer value which is treated as an unsuccessful wrapper run.
    if "exit_code" in meta and meta.get("exit_code") not in (None, ""):
        try:
            return "completed" if int(meta["exit_code"]) == 0 else "failed"
        except (TypeError, ValueError):
            return "failed"
    try:
        if out.stat().st_size > 0:
            return "completed"
    except OSError:
        pass
    # No final message yet: still going if the log moved recently, else it died.
    try:
        st = log.stat()
    except OSError:
        return "unknown"
    if time.time() - st.st_mtime < 600:
        return "running"
    return "failed" if st.st_size > 0 else "unknown"


def _ident(value: Any) -> str | None:
    """Model, effort, tier, session ids: identifiers, kept in metadata-only mode but capped."""
    return str(value)[:80] if isinstance(value, (str, int, float)) and str(value) else None


def _as_clean(value: Any, cfg: Any) -> str | None:
    return clean_text(value, bool(getattr(cfg, "metadata_only", False)), int(getattr(cfg, "text_limit", 160)))


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _infer_codex(conn: sqlite3.Connection, started: str | None, workdir: str | None) -> list[str]:
    if not started or not workdir:
        return []
    start = _dt(started)
    if start is None:
        return []
    candidates: list[str] = []
    for row in conn.execute(
        "SELECT id, started FROM nodes WHERE source='codex' AND project_path=? AND started IS NOT NULL",
        (workdir,),
    ):
        candidate = _dt(row["started"])
        if candidate is not None and start <= candidate <= start + timedelta(seconds=120):
            candidates.append(str(row["id"]))
    return candidates


def _token_value(tokens: dict[str, Any], *names: str) -> int:
    for name in names:
        if name in tokens and tokens[name] not in (None, ""):
            return as_int(tokens[name])
    return 0


def _scan_one(conn: sqlite3.Connection, cfg: Any, task: Path, full: bool, stats: SourceStats) -> None:
    task = task.absolute()
    log = _sibling(task, ".log")
    out = _sibling(task, ".out")
    meta_path = _sibling(task, ".meta.json")
    paths = {"task": task, "log": log, "out": out, "meta": meta_path}
    fingerprint = _fingerprint(paths)

    row = conn.execute("SELECT parser_version, state_json FROM files WHERE path=?", (str(task),)).fetchone()
    if not full and row is not None and row["parser_version"] == PARSER_VERSION:
        try:
            prior = json.loads(row["state_json"] or "{}")
        except (TypeError, ValueError):
            prior = {}
        if prior.get("fingerprint") == fingerprint:
            return

    # open_cursor owns replacement handling and forgets the old node when a
    # task file is replaced.  Force a restart when a sibling changed while the
    # prompt itself did not.
    cur = open_cursor(conn, task, SOURCE, PARSER_VERSION, full=True if full or row is not None else False)
    if cur is None:
        return
    stats.files_parsed += 1
    node_id = f"wr:{task}"
    conn.execute("DELETE FROM links WHERE from_id=?", (node_id,))

    meta = _read_meta(meta_path)
    log_fields = _log_fields(log)
    session_id = _first(meta.get("codex_session_id"), log_fields.get("session_id"))
    model = _first(meta.get("model"), log_fields.get("model"))
    effort = _first(meta.get("effort"), log_fields.get("effort"))
    workdir = _first(meta.get("cwd"), log_fields.get("workdir"))
    tier = _first(meta.get("tier"), "unknown")

    started = norm_ts(meta.get("started")) or _mtime(task)
    ended = norm_ts(meta.get("ended"))
    if ended is None:
        ended_values = [_mtime(out), _mtime(log)]
        ended = max((value for value in ended_values if value), default=None)

    description = _as_clean(task.stem, cfg)
    model_clean = _ident(model)
    effort_clean = _ident(effort)
    workdir_text = str(workdir) if workdir not in (None, "") else None
    extra: dict[str, Any] = {
        "tier": _ident(tier),
        "sandbox": _as_clean(meta.get("sandbox"), cfg),
        "cwd": _as_clean(workdir, cfg),
    }
    if "exit_code" in meta:
        extra["exit_code"] = meta.get("exit_code")
    # Only keep the session id as metadata.  Prompt, output, and arbitrary log
    # text are never included in the index.
    if session_id:
        extra["codex_session_id"] = _ident(session_id)
    extra = {key: value for key, value in extra.items() if value is not None}

    upsert_node(
        conn,
        node_id,
        source=SOURCE,
        kind="wrapper",
        agent_type=f"codex-wrapper-{_ident(tier) or 'unknown'}",
        description=description,
        model=model_clean,
        family=model_family(str(model) if model is not None else None),
        effort=effort_clean,
        status=_status(meta, out, log),
        started=started,
        ended=ended,
        project_path=workdir_text,
        raw_file=str(task),
        parser_version=PARSER_VERSION,
        parent_hint=f"wrapper-caller:{task}",
        extra_json=extra,
    )
    touch_project(conn, workdir_text, started or ended)
    stats.nodes += 1

    link_exists = False
    if session_id:
        put_link(conn, "wrapper-codex", node_id, f"cx:{session_id}", "exact", "codex exec header")
        link_exists = True
    else:
        inferred = _infer_codex(conn, started, workdir_text)
        if len(inferred) == 1:
            put_link(conn, "wrapper-codex", node_id, inferred[0], "inferred", "matching workdir and start time")
            link_exists = True

    # Wrapper metadata token counts are only a fallback when there is no
    # linked Codex rollout.  The normal Codex parser owns usage otherwise.
    tokens = meta.get("tokens")
    if isinstance(tokens, dict) and not link_exists:
        put_usage(
            conn,
            SOURCE,
            node_id,
            node_id,
            ts=ended or started,
            model=model_clean,
            input=_token_value(tokens, "input", "input_tokens"),
            cached_input=_token_value(tokens, "cached_input", "cached_input_tokens"),
            output=_token_value(tokens, "output", "output_tokens"),
            reasoning=_token_value(tokens, "reasoning", "reasoning_output_tokens"),
            raw_file=str(task),
        )

    cur.state = {"fingerprint": fingerprint}
    cur.offset = cur.size
    save_cursor(conn, cur, SOURCE, PARSER_VERSION)


def scan(conn: sqlite3.Connection, cfg: Any, full: bool = False) -> SourceStats:
    dirs = [Path(value) for value in getattr(cfg, "wrapper_dirs", [])]
    stats = SourceStats(source=SOURCE, root=":".join(str(path) for path in dirs), present=any(path.exists() for path in dirs))
    paths: list[Path] = []
    for root in dirs:
        paths.extend(safe_files(root, "**/*.md"))
    # A task is a run only when at least one sidecar exists.  Keep one path per
    # task even if wrapper_dirs overlap.
    tasks = sorted({path.absolute() for path in paths if any(_sibling(path, suffix).exists() for suffix in (".log", ".out", ".meta.json"))})
    stats.files_seen = len(tasks)
    current = {str(path) for path in tasks}
    old = conn.execute("SELECT path FROM files WHERE source=?", (SOURCE,)).fetchall()
    for root in dirs:
        if not root.exists():
            continue
        prefix = str(root.absolute()).rstrip("/") + "/"
        for row in old:
            old_path = str(row["path"])
            if old_path.startswith(prefix) and old_path not in current:
                forget_file(conn, old_path)
                conn.execute("DELETE FROM links WHERE from_id=?", (f"wr:{old_path}",))
    for task in tasks:
        conn.execute("SAVEPOINT wrapper_file")
        try:
            _scan_one(conn, cfg, task, full, stats)
            conn.execute("RELEASE SAVEPOINT wrapper_file")
        except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
            conn.execute("ROLLBACK TO SAVEPOINT wrapper_file")
            conn.execute("RELEASE SAVEPOINT wrapper_file")
            stats.errors.append(f"{task}: {type(exc).__name__}: {exc}")
    conn.commit()
    return stats
