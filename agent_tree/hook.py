"""Claude Code hook sink (opt-in, see docs/proposals/claude-hooks.md).

Claude Code pipes a JSON event to this command on SubagentStart/SubagentStop.
We append one compact line to ~/.agent-tree/events.jsonl and exit 0 no matter
what, so a problem here can never block or slow down a Claude session.
Only ids, the event name and a timestamp are kept: never prompts or outputs.
"""

import json
import os
import sys
import time

KEEP = ("hook_event_name", "session_id", "agent_id", "agent_type", "agent_transcript_path", "transcript_path", "cwd")


def main() -> int:
    try:
        event = json.loads(sys.stdin.read() or "{}")
        if not isinstance(event, dict):
            return 0
        row = {k: event[k] for k in KEEP if isinstance(event.get(k), str)}
        row["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + f".{int(time.time() * 1000) % 1000:03d}Z"
        home = os.environ.get("AGENT_TREE_HOME") or os.path.expanduser("~/.agent-tree")
        os.makedirs(home, exist_ok=True)
        with open(os.path.join(home, "events.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
