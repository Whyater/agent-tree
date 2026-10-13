# Proposal: live subagent events from Claude Code hooks (opt-in)

**Status: proposed, not applied.** Agent Tree never edits `~/.claude/settings.json` itself.

## Why

Transcripts tell us everything after the fact, but while a session is still
running, "is this background subagent still working?" is a guess based on how
recently its transcript changed. Claude Code's `SubagentStart` and
`SubagentStop` hooks make that exact.

Hooks carry no token counts, so tokens still come from transcripts. The hook
only records ids and a timestamp, never prompts or outputs.

## What it does

Each event runs `agent_tree/hook.py`, which appends one line like this to
`~/.agent-tree/events.jsonl` and always exits 0:

```json
{"hook_event_name":"SubagentStop","session_id":"6d4f…","agent_id":"a33f…","agent_type":"sol-worker","agent_transcript_path":"/Users/me/.claude/projects/…/agent-a33f….jsonl","ts":"2026-10-12T04:20:11.512Z"}
```

`att scan` reads it incrementally (`agent_tree/sources/events.py`) and marks
those subagents `running` or `completed`.

## The exact change

Add a `hooks` block to `~/.claude/settings.json`. If you installed with
`install.sh`, `att` lives in `~/.local/bin`; adjust the path if yours differs.

```diff
 {
   "enableWorkflows": true,
   "theme": "…",
   "attribution": { … },
   "includeCoAuthoredBy": false,
-  "env": { … }
+  "env": { … },
+  "hooks": {
+    "SubagentStart": [
+      { "hooks": [ { "type": "command", "command": "PYTHONPATH=\"$HOME/.local/share/agent-tree/app\" python3 -m agent_tree.hook", "timeout": 5 } ] }
+    ],
+    "SubagentStop": [
+      { "hooks": [ { "type": "command", "command": "PYTHONPATH=\"$HOME/.local/share/agent-tree/app\" python3 -m agent_tree.hook", "timeout": 5 } ] }
+    ]
+  }
 }
```

If you run Agent Tree from a clone instead, point `PYTHONPATH` at the clone.
`python3` must be 3.8+ for the hook script (it only uses `json`, `os`, `time`).

## Undo

Delete the `hooks` block (or just the two entries) and optionally
`~/.agent-tree/events.jsonl`. Nothing else depends on it.

## Codex

Codex's `notify` setting is often already used by other tools, so Agent Tree
doesn't propose touching it. Codex rollouts already record turn start and
completion, which covers the same need.
