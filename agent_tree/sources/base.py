"""Shared helpers for source parsers: incremental JSONL reading, scan stats,
timestamp normalization and model-family detection."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from ..privacy import is_secret_path


@dataclass
class SourceStats:
    source: str
    root: str = ""
    present: bool = False
    files_seen: int = 0
    files_parsed: int = 0       # files that had new bytes this run
    lines_parsed: int = 0
    lines_skipped: int = 0      # malformed or unrecognized-but-broken lines
    nodes: int = 0
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class FileCursor:
    """Incremental read state for one file, persisted in the ``files`` table."""
    path: str
    offset: int
    state: dict
    restarted: bool             # True when the file was new, shrank, or was replaced
    size: int
    mtime_ns: int
    inode: int
    parsed_lines: int = 0
    skipped_lines: int = 0


def open_cursor(conn: sqlite3.Connection, path: Path, source: str, parser_version: str, full: bool) -> FileCursor | None:
    """Return a cursor positioned after the last fully parsed line, or None
    when the file is unchanged since the last scan (nothing to do)."""
    st = path.stat()
    row = conn.execute("SELECT * FROM files WHERE path=?", (str(path),)).fetchone()
    restart = (
        full
        or row is None
        or row["parser_version"] != parser_version
        or (row["inode"] is not None and row["inode"] != st.st_ino)
        or st.st_size < row["offset"]
    )
    if not restart and row["size"] == st.st_size and row["mtime_ns"] == st.st_mtime_ns:
        return None
    if restart:
        from ..db import forget_file  # local import to avoid a cycle at module load
        if row is not None:
            forget_file(conn, str(path))
        return FileCursor(str(path), 0, {}, True, st.st_size, st.st_mtime_ns, st.st_ino)
    state = json.loads(row["state_json"]) if row["state_json"] else {}
    return FileCursor(
        str(path), int(row["offset"]), state, False, st.st_size, st.st_mtime_ns, st.st_ino,
        int(row["parsed_lines"]), int(row["skipped_lines"]),
    )


def save_cursor(conn: sqlite3.Connection, cur: FileCursor, source: str, parser_version: str) -> None:
    conn.execute(
        """INSERT INTO files(path, source, size, mtime_ns, inode, offset, parser_version, state_json, parsed_lines, skipped_lines, last_scan)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime_ns=excluded.mtime_ns, inode=excluded.inode,
             offset=excluded.offset, parser_version=excluded.parser_version, state_json=excluded.state_json,
             parsed_lines=excluded.parsed_lines, skipped_lines=excluded.skipped_lines, last_scan=excluded.last_scan""",
        (cur.path, source, cur.size, cur.mtime_ns, cur.inode, cur.offset, parser_version,
         json.dumps(cur.state, separators=(",", ":")) if cur.state else None,
         cur.parsed_lines, cur.skipped_lines, now_iso()),
    )


def iter_jsonl(cur: FileCursor) -> Iterator[tuple[int, Any]]:
    """Stream complete lines from ``cur.offset`` and yield ``(line_offset, obj)``.

    - Never loads the whole file; reads line by line in binary mode.
    - A trailing line without a newline is treated as still being written and
      is left for the next scan (the cursor stops before it).
    - Malformed JSON lines and non-object values are counted in
      ``cur.skipped_lines`` and skipped.
    - ``cur.offset`` advances as lines are consumed, so callers persist it with
      ``save_cursor`` when they are done.
    """
    if is_secret_path(cur.path):
        return
    with open(cur.path, "rb") as fh:
        fh.seek(cur.offset)
        pos = cur.offset
        for raw in fh:
            if not raw.endswith(b"\n"):
                break
            line_offset = pos
            pos += len(raw)
            cur.offset = pos
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except (ValueError, UnicodeDecodeError):
                cur.skipped_lines += 1
                continue
            if not isinstance(obj, dict):
                cur.skipped_lines += 1
                continue
            cur.parsed_lines += 1
            yield line_offset, obj


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def norm_ts(value: Any) -> str | None:
    """Normalize ISO strings, epoch seconds and epoch milliseconds to
    ``YYYY-MM-DDTHH:MM:SS.mmmZ`` (UTC). Returns None for anything unparseable."""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            seconds = value / 1000.0 if value > 1e11 else float(value)
            dt = datetime.fromtimestamp(seconds, tz=timezone.utc)
        elif isinstance(value, str):
            text = value.strip()
            if text.isdigit():
                return norm_ts(int(text))
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt = dt.astimezone(timezone.utc)
        else:
            return None
    except (ValueError, OverflowError, OSError):
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def model_family(model: str | None) -> str | None:
    if not model:
        return None
    m = model.lower()
    if m.startswith(("claude", "anthropic")) or m in {"opus", "sonnet", "haiku", "fable"} or "claude" in m:
        return "anthropic"
    if m.startswith(("gpt", "o1", "o3", "o4", "codex", "openai", "chatgpt")):
        return "openai"
    return "other"


def as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


@contextmanager
def readonly_sqlite(path: Path) -> Iterator[sqlite3.Connection]:
    """Open a live app database without taking locks.

    Uses ``mode=ro&immutable=1`` so SQLite never touches the WAL or SHM files.
    Immutable mode ignores un-checkpointed WAL pages, so for a database with a
    large WAL we copy db+wal to a private temp dir and read the copy instead.
    The original files are only ever read.
    """
    wal = Path(str(path) + "-wal")
    tmpdir = None
    try:
        if wal.exists() and wal.stat().st_size > 0:
            tmpdir = tempfile.mkdtemp(prefix="agent-tree-")
            target = Path(tmpdir) / path.name
            shutil.copyfile(path, target)
            shutil.copyfile(wal, Path(str(target) + "-wal"))
            # The copy is private, so letting SQLite replay the WAL into it is fine.
            conn = sqlite3.connect(str(target), timeout=5)
        else:
            conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)


def safe_files(root: Path, pattern: str) -> Iterator[Path]:
    """rglob that skips secret-looking paths and unreadable entries."""
    if not root.exists():
        return
    for p in sorted(root.glob(pattern)):
        try:
            if p.is_file() and not is_secret_path(p.relative_to(root)) and os.access(p, os.R_OK):
                yield p
        except OSError:
            continue
