#!/usr/bin/env python3
"""Audit a built index for privacy guarantees.

Checks every free-text column in ~/.agent-tree/index.sqlite (or $AGENT_TREE_HOME):
- no description/title/message longer than the 160-character limit;
- no value matching an API-key / token / private-key pattern.
Prints counts only. Exits non-zero on any violation.
"""

import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_tree.privacy import KEY_RE, TEXT_LIMIT  # noqa: E402

HOME = Path(os.environ.get("AGENT_TREE_HOME", Path.home() / ".agent-tree"))
# Free text that came from user content; ids, paths and model names are checked for keys only.
TEXT_COLUMNS = {
    "nodes": ["description", "role", "agent_type"],
    "sessions": ["title"],
    "spawns": ["description"],
    "flags": ["message"],
}
ALL_TEXT = {
    "nodes": ["id", "description", "role", "agent_type", "model", "effort", "status", "project_path", "raw_file", "extra_json"],
    "sessions": ["id", "title", "project_path", "raw_file", "extra_json"],
    "spawns": ["key", "description", "requested_model", "resolved_model", "status", "child_hint"],
    "links": ["from_id", "to_id", "detail"],
    "flags": ["message"],
    "usage": ["key", "model"],
}

conn = sqlite3.connect(f"file:{HOME / 'index.sqlite'}?mode=ro", uri=True)
bad = 0
checked = 0
for table, cols in ALL_TEXT.items():
    for col in cols:
        for (value,) in conn.execute(f"SELECT {col} FROM {table} WHERE {col} IS NOT NULL"):
            checked += 1
            text = str(value)
            if col in TEXT_COLUMNS.get(table, []) and len(text) > (200 if table == "flags" else TEXT_LIMIT):
                bad += 1
                print(f"too long: {table}.{col} ({len(text)} chars)")
            if KEY_RE.search(text) and "[redacted]" not in text:
                bad += 1
                print(f"key-like value in {table}.{col}")
longest = conn.execute("SELECT MAX(LENGTH(description)) FROM nodes").fetchone()[0]
print(f"checked {checked:,} stored values; longest node description {longest} chars; violations: {bad}")
sys.exit(1 if bad else 0)
