# Local HTTP API

`att serve` binds to `127.0.0.1` only (default port 8711) and serves the web UI
plus a small JSON API. Everything is read from `~/.agent-tree/index.sqlite`.

All timestamps are ISO-8601 UTC strings (`2026-10-12T04:01:11.955Z`) or `null`.
All token counts are integers. Costs are floats in US dollars or `null` when the
model has no price (show `?`). Costs are always estimates.

## Shared shapes

```jsonc
// Tokens
{ "input": 0, "cached_input": 0, "cache_write": 0, "output": 0, "reasoning": 0, "total": 0 }
// total = input + cached_input + cache_write + output  (reasoning is a subset of output)

// Money
{ "cost": 1.23,          // estimate in USD; excludes plan-covered usage
  "cost_known": true,    // false when some usage has no price (UI shows "≥ $x" or "?")
  "covered_cost": 0.40 } // estimate of usage marked "covered by plan" (UI shows greyed)
```

## Endpoints

### `GET /api/meta`
```jsonc
{ "version": "0.1.0", "last_scan": "…", "scan_running": false,
  "metadata_only": false, "covered_by_plan": {"anthropic": false, "openai": true},
  "timezone": "America/Denver",
  "sources": [{ "source": "claude-code", "present": true, "root": "~/.claude",
                "files": 640, "lines_skipped": 3, "parser_version": "cc-1" }] }
```

### `POST /api/scan`
Runs an incremental scan. Requires header `X-Agent-Tree: 1` (CSRF guard).
Body optional: `{"full": true}`. Returns `{ "ok": true, "seconds": 1.2, "stats": [...] }`.

### `GET /api/summary`
```jsonc
{ "periods": {
    "today": { "tokens": Tokens, "money": Money, "nodes": 12, "threads": 3,
               "by_family": [{ "family": "anthropic", "tokens": Tokens, "money": Money }],
               "by_model":  [{ "model": "claude-opus-5-5", "family": "anthropic", "tokens": Tokens, "money": Money }] },
    "week":  { … same … },     // last 7 days including today
    "month": { … same … } },   // last 30 days including today
  "daily": [ { "day": "2026-10-12", "tokens": Tokens, "money": Money,
               "by_family": {"anthropic": 123, "openai": 456} } ],   // last 30 days, oldest first, total tokens per family
  "top_threads": [ ThreadSummary ],  // top 10 by cost over the last 30 days
  "flag_count": 7, "flag_counts": {"alert": 1, "warn": 6} }
```

### `GET /api/threads`
Query: `project`, `source` (claude-code|codex|t3), `model`, `since`, `until`
(ISO date or relative like `7d`), `flagged=1`, `q` (title search), `limit`
(default 200), `offset`, `sort` (`started`|`cost`|`tokens`, default `started` desc).
```jsonc
{ "total": 123, "threads": [ ThreadSummary ] }

// ThreadSummary
{ "id": "cc:6d4f…",                 // tree id = root node id
  "source": "claude-code",          // source of the root node
  "title": "Build the thing",       // may be null in metadata-only mode
  "project_path": "/Users/me/Projects/x", "project_name": "x",
  "started": "…", "ended": "…", "wall_ms": 123456,
  "node_count": 14, "max_depth": 3,
  "tokens": Tokens, "money": Money,
  "models": ["claude-opus-5-5", "gpt-6.1-sol"], "families": ["anthropic", "openai"],
  "flag_count": 2, "sources": ["t3", "claude-code", "codex"],
  "t3_title": "Thread title in T3 Code" }
```

### `GET /api/threads/<id>`
`<id>` is URL-encoded. 404 with `{"error": "..."}` when unknown.
```jsonc
{ "thread": ThreadSummary,
  "nodes": [ Node ],              // depth-first order, parents before children, siblings by start time
  "flags": [ Flag ] }

// Node
{ "id": "cc-agent:a630…", "parent_id": "cc:6d4f…", "depth": 1,
  "source": "claude-code", "kind": "subagent",     // root | subagent | wrapper | t3-thread | t3-task
  "agent_type": "general-purpose", "role": null,
  "description": "Physics core fixes (S0/S2)",     // ≤160 chars or null
  "model": "claude-opus-5-5", "family": "anthropic", "effort": "high",
  "status": "completed",                            // completed | running | failed | unknown …
  "started": "…", "ended": "…", "wall_ms": 60000,
  "self":    { "tokens": Tokens, "money": Money },
  "subtree": { "tokens": Tokens, "money": Money, "nodes": 5, "max_depth": 2 },
  "covered": false,                                 // family is covered by plan
  "link_confidence": "exact",                       // exact | inferred | orphan
  "raw_file": "/Users/me/.claude/projects/…/agent-a630….jsonl", "raw_offset": 0,
  "parser_version": "cc-1",
  "child_count": 2,
  "flags": ["depth"] }                              // rule ids on this node

// Flag
{ "rule": "depth", "severity": "warn", "heuristic": false,
  "tree_id": "cc:…", "node_id": "cc-agent:…", "message": "Depth 5 is above the limit of 4",
  "ts": "…", "thread_title": "…" }
```

### `GET /api/projects`
Query: `since` (default 90d).
```jsonc
{ "projects": [ { "path": "…", "name": "x", "first_ts": "…", "last_ts": "…",
                  "threads": 4, "nodes": 30, "tokens": Tokens, "money": Money,
                  "daily": [ { "day": "2026-10-12", "total": 12345, "cost": 0.5 } ] } ] }
```

### `GET /api/models`
Query: `since` (default 30d).
```jsonc
{ "models": [ { "model": "gpt-6.1-sol", "family": "openai", "nodes": 9, "threads": 3,
                "tokens": Tokens, "money": Money, "priced": true,
                "by_task_type": [ { "task_type": "build", "nodes": 4, "total": 120000 } ] } ],
  "families": [ { "family": "openai", "tokens": Tokens, "money": Money, "nodes": 9 } ],
  "daily": [ { "day": "…", "by_family": {"anthropic": 1, "openai": 2} } ] }
```
`task_type` is a heuristic from descriptions and agent types: build, review, research, scan, design, test, other.

### `GET /api/flags`
Query: `since`, `rule`, `severity`. Returns `{ "flags": [ Flag ], "rules": [ { "id": "depth", "label": "Depth above limit", "severity": "warn", "heuristic": false, "enabled": true, "count": 2 } ] }`.

### Exports
- `GET /api/export/thread/<id>.json` full thread payload
- `GET /api/export/thread/<id>.csv` one row per node
- `GET /api/export/thread/<id>.md` Markdown tree for session notes
- `GET /api/export/range.json?since=7d&until=` and `/api/export/range.csv?...` one row per node in the range

Exports set `Content-Disposition: attachment`.

## Menu bar endpoints

### Starting the server for an app

`att serve --port 0 --ready-file PATH --no-open` binds a free port on 127.0.0.1 and,
once it is listening, writes `{"port": 51234, "pid": 4242, "version": "0.3.0"}` to
PATH (atomically). Apps read the port from there. The server exits when its
parent process goes away if `--parent-pid PID` is given.

### `GET /api/limits`
Plan usage limits, read from local files only (no network).
```jsonc
{ "providers": [
  { "provider": "codex", "available": true, "plan": "prolite",
    "as_of": "2026-10-13T08:11:52.861Z",   // when the tool last recorded it
    "stale": false,                         // as_of older than 6 hours
    "source": "Codex session logs",
    "reason": null,                         // why unavailable, when available=false
    "windows": [ { "id": "seven_day", "label": "Week", "used_percent": 2.0,
                   "window_minutes": 10080, "resets_at": "2026-10-19T21:38:24.000Z" } ] },
  { "provider": "claude", "available": false, "plan": null, "as_of": null, "stale": false,
    "source": "Claude Code status line",
    "reason": "Claude Code only shares limits with a status-line script. Turn it on in Settings.",
    "windows": [] } ] }
```
Window ids: `five_hour` (label "Session"), `seven_day` (label "Week"), otherwise `custom`
with a label like "8h". `used_percent` is 0 to 100 (can exceed 100 once a limit is hit).

### `GET /api/now`
Trees with activity in the last 10 minutes.
```jsonc
{ "running": [ { "thread_id": "t3:…", "title": "…", "project_name": "agent-tree",
                 "model": "claude-opus-5-5", "family": "anthropic",
                 "started": "…", "last_activity": "…", "agents_running": 3,
                 "active_since": "…",   // start of the current burst of work (use for the elapsed timer)
                 "tokens": Tokens, "money": Money } ] }
```

### `GET /api/verify`
Cross-checks against other tools on this Mac, when installed.
```jsonc
{ "checks": [ { "id": "t3-usage", "label": "Token totals vs T3 Code",
                "status": "match",   // match | mismatch | unavailable
                "detail": "641 of 641 transcripts agree", "compared": 641, "matched": 641 } ] }
```
