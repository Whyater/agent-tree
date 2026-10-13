"""The normalized SQLite index (~/.agent-tree/index.sqlite).

Parsers never compute totals themselves. They write:
- ``nodes``   one row per agent run, merged field by field across rescans;
- ``usage``   one row per billable unit (deduplicated by a stable key);
- ``spawns``  "node X asked for an agent here" records used to link children;
- ``links``   extra cross-source joins (T3 thread -> session, wrapper -> Codex);
- ``files``   incremental scan state per source file.

The indexer (``indexer.py``) then resolves parents, trees, depth, rollups,
cost and flags from those rows.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS files (
    path            TEXT PRIMARY KEY,
    source          TEXT NOT NULL,
    size            INTEGER NOT NULL DEFAULT 0,
    mtime_ns        INTEGER NOT NULL DEFAULT 0,
    inode           INTEGER,
    offset          INTEGER NOT NULL DEFAULT 0,
    parser_version  TEXT,
    state_json      TEXT,
    parsed_lines    INTEGER NOT NULL DEFAULT 0,
    skipped_lines   INTEGER NOT NULL DEFAULT 0,
    last_scan       TEXT
);

CREATE TABLE IF NOT EXISTS projects (
    path      TEXT PRIMARY KEY,
    name      TEXT,
    first_ts  TEXT,
    last_ts   TEXT
);

-- One row per source-level session (Claude session, Codex thread, T3 thread).
-- Display "threads" are the roots of the resolved trees, see the indexer.
CREATE TABLE IF NOT EXISTS sessions (
    id              TEXT PRIMARY KEY,       -- e.g. cc:<sessionId>, cx:<threadId>, t3:<threadId>
    source          TEXT NOT NULL,          -- claude-code | codex | t3
    project_path    TEXT,
    title           TEXT,
    started         TEXT,
    ended           TEXT,
    root_node_id    TEXT,
    raw_file        TEXT,
    parser_version  TEXT,
    extra_json      TEXT
);

CREATE TABLE IF NOT EXISTS nodes (
    id               TEXT PRIMARY KEY,
    session_id       TEXT,                  -- sessions.id this node was parsed from
    parent_id        TEXT,                  -- resolved parent node id (indexer may fill)
    parent_hint      TEXT,                  -- raw parent key, e.g. tool_use id or parent thread id
    source           TEXT NOT NULL,
    kind             TEXT NOT NULL,         -- root | subagent | wrapper | t3-thread | t3-task
    agent_type       TEXT,
    role             TEXT,
    description      TEXT,                  -- <=160 chars, redacted, NULL in metadata-only mode
    model            TEXT,
    family           TEXT,                  -- anthropic | openai | other | NULL
    effort           TEXT,
    status           TEXT,
    started          TEXT,                  -- ISO-8601 UTC
    ended            TEXT,
    project_path     TEXT,
    link_confidence  TEXT,                  -- exact | inferred | orphan (indexer finalizes)
    raw_file         TEXT,
    raw_offset       INTEGER,
    parser_version   TEXT,
    extra_json       TEXT,
    -- filled by the indexer
    tree_id          TEXT,                  -- root node id of the resolved tree; NULL = merged into another node
    depth            INTEGER,
    counts_usage     INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS nodes_parent ON nodes(parent_id);
CREATE INDEX IF NOT EXISTS nodes_tree ON nodes(tree_id);
CREATE INDEX IF NOT EXISTS nodes_session ON nodes(session_id);

CREATE TABLE IF NOT EXISTS usage (
    source        TEXT NOT NULL,
    key           TEXT NOT NULL,            -- dedupe key, e.g. cc:<requestId>
    node_id       TEXT NOT NULL,
    ts            TEXT,
    model         TEXT,
    input         INTEGER NOT NULL DEFAULT 0, -- uncached input tokens
    cached_input  INTEGER NOT NULL DEFAULT 0, -- cache reads
    cache_write   INTEGER NOT NULL DEFAULT 0, -- cache creation
    output        INTEGER NOT NULL DEFAULT 0, -- includes reasoning for OpenAI
    reasoning     INTEGER NOT NULL DEFAULT 0, -- informational subset of output
    raw_file      TEXT,
    cost          REAL,                     -- estimate, filled by the indexer (NULL = no price)
    family        TEXT,                     -- model family, filled by the indexer
    PRIMARY KEY (source, key)
);
CREATE INDEX IF NOT EXISTS usage_node ON usage(node_id);
CREATE INDEX IF NOT EXISTS usage_ts ON usage(ts);

CREATE TABLE IF NOT EXISTS spawns (
    key             TEXT PRIMARY KEY,       -- e.g. tool_use id "toolu_..."
    parent_node_id  TEXT NOT NULL,
    child_hint      TEXT,                   -- e.g. agentId once the result is seen
    description     TEXT,
    requested_model TEXT,
    resolved_model  TEXT,
    ts              TEXT,
    status          TEXT
);

CREATE TABLE IF NOT EXISTS links (
    kind        TEXT NOT NULL,              -- t3-session | wrapper-codex | wrapper-caller | t3-task
    from_id     TEXT NOT NULL,
    to_id       TEXT NOT NULL,
    confidence  TEXT NOT NULL,              -- exact | inferred
    detail      TEXT,
    PRIMARY KEY (kind, from_id, to_id)
);

CREATE TABLE IF NOT EXISTS flags (
    rule        TEXT NOT NULL,
    severity    TEXT NOT NULL,              -- warn | alert
    tree_id     TEXT,
    node_id     TEXT,
    message     TEXT,
    heuristic   INTEGER NOT NULL DEFAULT 0,
    ts          TEXT
);
CREATE INDEX IF NOT EXISTS flags_tree ON flags(tree_id);

-- Rollups written by the indexer after every scan.
CREATE TABLE IF NOT EXISTS node_totals (
    node_id          TEXT PRIMARY KEY,
    self_input       INTEGER, self_cached INTEGER, self_cache_write INTEGER, self_output INTEGER, self_reasoning INTEGER,
    self_cost        REAL,      -- estimate for usage not covered by a plan
    self_covered     REAL,      -- estimate for plan-covered usage
    self_unpriced    INTEGER,   -- usage rows with tokens but no price
    sub_input        INTEGER, sub_cached INTEGER, sub_cache_write INTEGER, sub_output INTEGER, sub_reasoning INTEGER,
    sub_cost         REAL, sub_covered REAL, sub_unpriced INTEGER,
    sub_nodes        INTEGER, sub_max_depth INTEGER
);
"""

NODE_FIELDS = (
    "session_id", "parent_id", "parent_hint", "source", "kind", "agent_type", "role",
    "description", "model", "family", "effort", "status", "started", "ended",
    "project_path", "link_confidence", "raw_file", "raw_offset", "parser_version", "extra_json",
)


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=OFF")
    init(conn)
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    if row is None:
        conn.execute("INSERT INTO meta(key, value) VALUES('schema_version', ?)", (str(SCHEMA_VERSION),))
    elif int(row[0]) != SCHEMA_VERSION:
        # Schema changes are rare; the index is a cache, so rebuild it.
        wipe(conn)
        conn.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))
    conn.commit()


def wipe(conn: sqlite3.Connection) -> None:
    for table in ("files", "projects", "sessions", "nodes", "usage", "spawns", "links", "flags", "node_totals"):
        conn.execute(f"DELETE FROM {table}")


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


# --- writers used by parsers -------------------------------------------------

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


def upsert_node(conn: sqlite3.Connection, node_id: str, **fields: Any) -> None:
    """Insert or merge a node.

    Merge rules: ``started`` keeps the earliest value, ``ended`` the latest;
    every other field is overwritten only when the new value is not None, so a
    later partial record never erases what an earlier one established.
    ``extra_json`` may be given as a dict and is merged key by key.
    """
    unknown = set(fields) - set(NODE_FIELDS)
    if unknown:
        raise ValueError(f"unknown node fields: {sorted(unknown)}")
    if isinstance(fields.get("extra_json"), dict):
        fields["extra_json"] = fields["extra_json"] or None
    row = conn.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
    if row is None:
        if "source" not in fields or "kind" not in fields:
            raise ValueError("new nodes need source and kind")
        data = {k: fields.get(k) for k in NODE_FIELDS}
        if isinstance(data["extra_json"], dict):
            data["extra_json"] = json.dumps(data["extra_json"], sort_keys=True)
        cols = ", ".join(("id",) + NODE_FIELDS)
        marks = ", ".join("?" for _ in range(len(NODE_FIELDS) + 1))
        conn.execute(f"INSERT INTO nodes({cols}) VALUES({marks})", (node_id, *[data[k] for k in NODE_FIELDS]))
        return
    updates: dict[str, Any] = {}
    for key, value in fields.items():
        if value is None:
            continue
        if key == "started":
            value = _min_ts(row["started"], value)
        elif key == "ended":
            value = _max_ts(row["ended"], value)
        elif key == "extra_json":
            merged = json.loads(row["extra_json"]) if row["extra_json"] else {}
            merged.update(value if isinstance(value, dict) else json.loads(value))
            value = json.dumps(merged, sort_keys=True)
        if row[key] != value:
            updates[key] = value
    if updates:
        sets = ", ".join(f"{k}=?" for k in updates)
        conn.execute(f"UPDATE nodes SET {sets} WHERE id=?", (*updates.values(), node_id))


def upsert_session(conn: sqlite3.Connection, session_id: str, **fields: Any) -> None:
    allowed = {"source", "project_path", "title", "started", "ended", "root_node_id", "raw_file", "parser_version", "extra_json"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"unknown session fields: {sorted(unknown)}")
    if isinstance(fields.get("extra_json"), dict):
        fields["extra_json"] = json.dumps(fields["extra_json"], sort_keys=True)
    row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    if row is None:
        cols = ["id", *fields]
        conn.execute(
            f"INSERT INTO sessions({', '.join(cols)}) VALUES({', '.join('?' for _ in cols)})",
            (session_id, *fields.values()),
        )
        return
    updates = {}
    for key, value in fields.items():
        if value is None:
            continue
        if key == "started":
            value = _min_ts(row["started"], value)
        elif key == "ended":
            value = _max_ts(row["ended"], value)
        updates[key] = value
    if updates:
        conn.execute(
            f"UPDATE sessions SET {', '.join(f'{k}=?' for k in updates)} WHERE id=?",
            (*updates.values(), session_id),
        )


def put_usage(
    conn: sqlite3.Connection,
    source: str,
    key: str,
    node_id: str,
    *,
    ts: str | None,
    model: str | None,
    input: int = 0,
    cached_input: int = 0,
    cache_write: int = 0,
    output: int = 0,
    reasoning: int = 0,
    raw_file: str | None = None,
) -> None:
    """Insert or replace one billable unit. Replacing by key is what dedupes
    streamed Claude blocks that repeat the same requestId."""
    conn.execute(
        """INSERT INTO usage(source, key, node_id, ts, model, input, cached_input, cache_write, output, reasoning, raw_file)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(source, key) DO UPDATE SET
             node_id=excluded.node_id, ts=COALESCE(usage.ts, excluded.ts), model=COALESCE(excluded.model, usage.model),
             input=excluded.input, cached_input=excluded.cached_input, cache_write=excluded.cache_write,
             output=excluded.output, reasoning=excluded.reasoning, raw_file=excluded.raw_file, cost=NULL""",
        (source, key, node_id, ts, model, int(input or 0), int(cached_input or 0), int(cache_write or 0),
         int(output or 0), int(reasoning or 0), raw_file),
    )


def put_spawn(conn: sqlite3.Connection, key: str, parent_node_id: str, **fields: Any) -> None:
    allowed = {"child_hint", "description", "requested_model", "resolved_model", "ts", "status"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"unknown spawn fields: {sorted(unknown)}")
    row = conn.execute("SELECT * FROM spawns WHERE key=?", (key,)).fetchone()
    if row is None:
        cols = ["key", "parent_node_id", *fields]
        conn.execute(
            f"INSERT INTO spawns({', '.join(cols)}) VALUES({', '.join('?' for _ in cols)})",
            (key, parent_node_id, *fields.values()),
        )
        return
    updates = {k: v for k, v in fields.items() if v is not None}
    if updates:
        conn.execute(
            f"UPDATE spawns SET {', '.join(f'{k}=?' for k in updates)} WHERE key=?", (*updates.values(), key)
        )


def put_link(conn: sqlite3.Connection, kind: str, from_id: str, to_id: str, confidence: str, detail: str | None = None) -> None:
    if confidence not in ("exact", "inferred"):
        raise ValueError("confidence must be exact or inferred")
    conn.execute(
        """INSERT INTO links(kind, from_id, to_id, confidence, detail) VALUES(?,?,?,?,?)
           ON CONFLICT(kind, from_id, to_id) DO UPDATE SET confidence=excluded.confidence, detail=excluded.detail""",
        (kind, from_id, to_id, confidence, detail),
    )


def touch_project(conn: sqlite3.Connection, path: str | None, ts: str | None) -> None:
    if not path:
        return
    name = Path(path).name or path
    conn.execute(
        """INSERT INTO projects(path, name, first_ts, last_ts) VALUES(?,?,?,?)
           ON CONFLICT(path) DO UPDATE SET
             first_ts=CASE WHEN excluded.first_ts IS NOT NULL AND (projects.first_ts IS NULL OR excluded.first_ts < projects.first_ts) THEN excluded.first_ts ELSE projects.first_ts END,
             last_ts=CASE WHEN excluded.last_ts IS NOT NULL AND (projects.last_ts IS NULL OR excluded.last_ts > projects.last_ts) THEN excluded.last_ts ELSE projects.last_ts END""",
        (path, name, ts, ts),
    )


def forget_file(conn: sqlite3.Connection, path: str) -> None:
    """Drop everything derived from one source file (used when a file shrinks or is replaced)."""
    conn.execute("DELETE FROM usage WHERE raw_file=?", (path,))
    conn.execute("DELETE FROM nodes WHERE raw_file=?", (path,))
    conn.execute("DELETE FROM files WHERE path=?", (path,))


def rows(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, tuple(params))]
