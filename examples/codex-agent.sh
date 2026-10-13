#!/bin/zsh
# Example wrapper: run one OpenAI Codex subagent for a Claude orchestrator.
# Usage: codex-agent.sh <sol|luna> <read-only|workspace-write> <workdir> <prompt-file> [effort]
# Writes the agent's final message to <prompt-file>.out and the full log to <prompt-file>.log.
# Also writes <prompt-file>.meta.json (parent id, tier, model, timing, codex session id, tokens)
# and <prompt-file>.events.jsonl (codex exec --json event stream) for Agent Tree.
# Set AGENT_PARENT_ID="<claude session id>:<agent id>" to record who called this run.
set -eu
tier="$1"; sandbox="$2"; workdir="$3"; prompt="$4"; effort="${5:-medium}"
case "$tier" in
  sol)  model="gpt-6.1-sol" ;;
  luna) model="gpt-6-luna" ;;
  *) echo "blocked tier: $tier (allowed: sol, luna)" >&2; exit 2 ;;
esac
case "$sandbox" in read-only|workspace-write) ;; *) echo "bad sandbox: $sandbox" >&2; exit 2 ;; esac
case "$effort" in low|medium|high) ;; *) echo "bad effort: $effort" >&2; exit 2 ;; esac
rules="$(dirname "$0")/codex-agent-rules.md"
started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
exit_code=0
for attempt in 1 2 3; do
  if { [ -f "$rules" ] && cat "$rules"; echo; echo "--- TASK ---"; cat "$prompt"; } | \
    codex exec --json -m "$model" -s "$sandbox" -C "$workdir" --skip-git-repo-check \
      -c model_reasoning_effort="$effort" -c approval_policy=never \
      -o "$prompt.out" - > "$prompt.events.jsonl" 2> "$prompt.log"; then
    exit_code=0
    break
  else
    exit_code=$?
  fi
  if grep -q "at capacity" "$prompt.log" "$prompt.events.jsonl" 2>/dev/null && [ "$attempt" -lt 3 ]; then
    echo "capacity on $model, retry $attempt in $((attempt * 45))s" >&2; sleep $((attempt * 45)); continue
  fi
  break
done
ended="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Sidecar metadata for Agent Tree. Best effort: never fails the run.
{
  /usr/bin/env python3 - "$prompt" "$tier" "$model" "$effort" "$sandbox" "$workdir" "$started" "$ended" "$exit_code" "${AGENT_PARENT_ID:-}" <<'PY'
import json, sys
prompt, tier, model, effort, sandbox, cwd, started, ended, code, parent = sys.argv[1:11]
session, tokens = None, {"input": 0, "cached_input": 0, "output": 0, "reasoning": 0}
try:
    with open(prompt + ".events.jsonl", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("type") == "thread.started":
                session = ev.get("thread_id") or session
            usage = ev.get("usage") if ev.get("type") == "turn.completed" else None
            if isinstance(usage, dict):
                tokens["input"] += int(usage.get("input_tokens") or 0)
                tokens["cached_input"] += int(usage.get("cached_input_tokens") or 0)
                tokens["output"] += int(usage.get("output_tokens") or 0)
                tokens["reasoning"] += int(usage.get("reasoning_output_tokens") or 0)
except OSError:
    pass
meta = {"parent_id": parent or None, "tier": tier, "model": model, "effort": effort, "sandbox": sandbox,
        "cwd": cwd, "started": started, "ended": ended, "exit_code": int(code),
        "codex_session_id": session, "tokens": tokens}
with open(prompt + ".meta.json", "w", encoding="utf-8") as fh:
    json.dump(meta, fh, indent=2)
PY
} 2>/dev/null || true

if [ "$exit_code" -ne 0 ]; then
  echo "failed: $model, see $prompt.log" >&2; exit 1
fi
echo "done: $model ($sandbox, $effort) -> $prompt.out"
