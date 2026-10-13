# How agents are linked into trees

Every parent link records how it was made: **exact** (both sides carry the same
id), **inferred** (matched on weaker evidence such as time and directory) or
**orphan** (no parent found). This page lists every rule, in the order the
indexer applies them (`agent_tree/indexer.py`).

## Claude Code

| Link | Evidence | Confidence |
|---|---|---|
| subagent → spawning agent | `agent-<id>.meta.json` `toolUseId` equals the `id` of an `Agent`/`Task` `tool_use` block in the parent's transcript | exact |
| subagent → spawning agent | the parent's `toolUseResult.agentId` equals the subagent id (used when the meta file is missing) | exact |
| subagent → session root | no spawning call found and `spawnDepth` is 1 or unknown | inferred |
| subagent → session root | no spawning call found and `spawnDepth` ≥ 2 (the real parent is a missing subagent) | orphan |

Nested subagents work the same way: the spawning `tool_use` lives in the
parent subagent's own transcript, so the link points at that subagent.

## Codex

| Link | Evidence | Confidence |
|---|---|---|
| child thread → parent thread | `session_meta.source.subagent.thread_spawn.parent_thread_id`, or `parent_thread_id` on a subagent thread | exact |
| child thread whose parent rollout isn't on disk | none | orphan |

`forked_from_id` alone (a user fork) is recorded but not treated as a parent.
A thread that continues into `rollout-…-<id>_<suffix>.jsonl` files is one node;
its cumulative token counter carries across the files and is counted once.

## T3 Code

T3 is the app a thread was started from, so its thread becomes the top of the
tree and the Claude or Codex session it drove hangs underneath.

| Link | Evidence | Confidence |
|---|---|---|
| T3 thread → Claude session | thread id `import:claudeAgent:<sessionId>` | exact |
| T3 thread → Codex thread | thread id `import:codex:<threadId>` | exact |
| T3 thread → Claude session (threads started in T3) | `provider_session_runtime.resume_cursor_json.resume` holds the Claude Code session id | exact |
| T3 thread → Codex thread (threads started in T3) | `resume_cursor_json.threadId`, `provider_session_id`, or `runtime_payload_json.importedTranscripts[].providerSessionId` | exact |
| T3 thread → Claude session, nothing above available | exactly one Claude session root in the same workspace whose time window overlaps the thread | inferred |
| T3 task row → Codex child thread | the task id is a Codex thread id | exact |
| T3 task row → Claude subagent | the task's `toolUseId` equals the subagent's spawning `tool_use` id | exact |

T3 task rows that match a transcript agent are hidden, since the transcript
has the tokens and exact model. Unmatched task rows are shown only when the T3
thread has no transcript behind it. A task id that looks like a Codex id but
has no rollout on disk (for example, the session was deleted) stays unlinked.

T3's own context-window numbers describe the same tokens as the transcript, so
they are shown as information but never added to totals.

## Wrapper runs (Claude → Codex hand-offs)

A wrapper such as `codex-agent.sh <tier> <sandbox> <workdir> <task-file>` runs
`codex exec` on behalf of a Claude subagent.

| Link | Evidence | Confidence |
|---|---|---|
| wrapper run → Claude subagent | a `Bash` call in that subagent's transcript runs the wrapper with this task file (command position only; relative paths resolved against the transcript's working directory) | exact |
| Codex session → wrapper run | `<task>.meta.json` `codex_session_id`, or the `session id:` header `codex exec` prints into `<task>.log` | exact |
| Codex session → wrapper run | a Codex session that started within 120 s of the task file, in the same working directory | inferred |
| wrapper run → Claude subagent | the `Bash` call passed the task file through a shell variable (`"$F"`), so only its time is known: the nearest such call within 10 minutes after the task file was written | inferred |
| wrapper run, caller not found | none | orphan |

## What is still uncertain

- **Inferred T3 → Claude links** only happen for threads with no resume cursor
  and no imported id, which is rare on current T3 versions.
- **Wrapper runs without a log header** fall back to the 120-second window. The
  [wrapper sidecar proposal](proposals/codex-agent-wrapper.md) removes the guess.
- **Live status** is a guess (activity in the last 10 minutes) unless the
  [hooks proposal](proposals/claude-hooks.md) is enabled.
- These are private, undocumented formats. Each parser records its version on
  every row, and `att doctor` reports skipped lines so format drift is visible.
