# Proposal: make `codex-agent.sh` wrapper runs self-describing

**Status: proposed, not applied.** The exact diff is in
[`codex-agent-wrapper.patch`](codex-agent-wrapper.patch).

Some setups route work from a Claude orchestrator to OpenAI Codex through a
small wrapper script (`codex-agent.sh <sol|luna> <sandbox> <workdir> <task-file> [effort]`)
that runs `codex exec` and writes `<task>.out` and `<task>.log`.

## What Agent Tree can already link without any change

- **Caller → wrapper run (exact).** The relay subagent that ran the wrapper has
  the `Bash` call in its own transcript, including the task-file path. The Claude
  Code parser records that path, so the wrapper run is attached under the exact
  subagent that launched it.
- **Wrapper run → Codex session (exact when found).** `codex exec` prints a
  `session id:` header into `<task>.log`. When it is there, the run is joined to
  the Codex rollout, which supplies the tokens.
- **Fallback (inferred).** Without the header, a Codex session that started
  within 120 seconds of the task file in the same working directory is linked
  and labelled "inferred".

## What the patch adds

1. `codex exec --json`: the event stream goes to `<task>.events.jsonl`, which
   gives the thread id (`thread.started`) and per-turn usage (`turn.completed`)
   without parsing human-oriented log text.
2. A sidecar `<task>.meta.json` written after every run:
   ```json
   {"parent_id": "6d4f…:a33f…", "tier": "sol", "model": "gpt-6.1-sol", "effort": "high",
    "sandbox": "workspace-write", "cwd": "/path/to/project", "started": "2026-10-12T04:10:00Z",
    "ended": "2026-10-12T04:16:40Z", "exit_code": 0, "codex_session_id": "01a1…",
    "tokens": {"input": 0, "cached_input": 0, "output": 0, "reasoning": 0}}
   ```
3. Optional `AGENT_PARENT_ID` environment variable, recorded as `parent_id`.
   The transcript link above already identifies the caller exactly, so this is a
   convenience for scripts that call the wrapper from outside Claude Code.

Behaviour is otherwise unchanged: same arguments, same retries on "at capacity",
same `.out` file, same exit codes. Writing the sidecar is best effort and can't
fail a run. Note that with `--json`, stdout goes to `.events.jsonl` and `.log`
keeps only stderr.

## Apply

```sh
cd ~ && patch -p1 --dry-run < /path/to/agent-tree/docs/proposals/codex-agent-wrapper.patch
cd ~ && patch -p1 < /path/to/agent-tree/docs/proposals/codex-agent-wrapper.patch
```
