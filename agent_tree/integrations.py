"""Optional live integrations, applied only when the user asks for them.

Claude Code can push two things Agent Tree otherwise has to infer:
- its status line receives the plan limits (session and week, with exact reset
  times), which ``agent_tree.statusline`` records;
- SubagentStart / SubagentStop hooks say exactly when each subagent starts and
  stops, which ``agent_tree.hook`` records.

``connect_claude`` adds both to ~/.claude/settings.json and ``disconnect_claude``
removes them again. Both keep a timestamped backup, write atomically, and only
ever touch entries that run Agent Tree's own modules. Codex and T3 Code need no
setup: their logs and database are watched directly.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
import time
from pathlib import Path

from .config import Config

STATUSLINE_MODULE = "agent_tree.statusline"
HOOK_MODULE = "agent_tree.hook"
HOOK_EVENTS = ("SubagentStart", "SubagentStop")


def _command(module: str) -> str:
    """Shell command that runs one of our modules with this installation's Python."""
    root = Path(__file__).resolve().parent.parent
    return f"PYTHONPATH={shlex.quote(str(root))} {shlex.quote(sys.executable)} -m {module}"


def _settings_path(cfg: Config) -> Path:
    return Path(cfg.claude_dir) / "settings.json"


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("settings.json is not a JSON object")
    return data


def _is_ours(command: object, module: str) -> bool:
    return isinstance(command, str) and f"-m {module}" in command


def _statusline_state(settings: dict) -> str:
    line = settings.get("statusLine")
    if not line:
        return "none"
    return "agent-tree" if isinstance(line, dict) and _is_ours(line.get("command"), STATUSLINE_MODULE) else "other"


def _hooks_state(settings: dict) -> str:
    hooks = settings.get("hooks") if isinstance(settings.get("hooks"), dict) else {}
    found = 0
    for event in HOOK_EVENTS:
        for group in hooks.get(event) or []:
            if isinstance(group, dict) and any(isinstance(h, dict) and _is_ours(h.get("command"), HOOK_MODULE)
                                               for h in group.get("hooks") or []):
                found += 1
                break
    return "agent-tree" if found == len(HOOK_EVENTS) else "partial" if found else "none"


def _additions(statusline: bool) -> dict:
    add: dict = {"hooks": {event: [{"hooks": [{"type": "command", "command": _command(HOOK_MODULE), "timeout": 5}]}]
                           for event in HOOK_EVENTS}}
    if statusline:
        add["statusLine"] = {"type": "command", "command": _command(STATUSLINE_MODULE)}
    return add


def status(cfg: Config) -> dict:
    path = _settings_path(cfg)
    claude_installed = Path(cfg.claude_dir).exists()
    try:
        settings = _load(path)
        error = None
    except (OSError, ValueError) as exc:
        settings, error = {}, f"Can't read {path}: {exc}"
    statusline, hooks = _statusline_state(settings), _hooks_state(settings)
    keep_other = statusline == "other"
    preview = (
        "Adds two Claude Code hooks (SubagentStart, SubagentStop) so live subagents show exactly"
        + ("" if keep_other else ", and a status line that records your session and week limits")
        + f". A backup of {path.name} is saved first, and Disconnect removes only these entries.\n\n"
        + json.dumps(_additions(statusline=not keep_other), indent=2)
    )
    return {
        "claude": {
            "installed": claude_installed,
            "connected": hooks == "agent-tree" and statusline in ("agent-tree", "other"),
            "statusline": statusline,
            "hooks": hooks,
            "settings_path": str(path).replace(str(Path.home()), "~", 1),
            "preview": preview,
            "error": error,
        },
        "codex": {"installed": Path(cfg.codex_dir).exists()},
        "t3": {"installed": (Path(cfg.t3_dir) / "userdata" / "state.sqlite").exists()},
        "vorssaint": {"installed": Path("/Applications/Vorssaint.app").exists()},
    }


def _write(path: Path, settings: dict) -> Path | None:
    """Back up the current file, then replace it atomically."""
    backup = None
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_name(f"{path.name}.agent-tree-backup-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(path, backup)
    tmp = path.with_name(path.name + ".agent-tree-tmp")
    tmp.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    if path.exists():
        shutil.copymode(path, tmp)
    os.replace(tmp, path)
    return backup


def connect_claude(cfg: Config) -> dict:
    path = _settings_path(cfg)
    if not Path(cfg.claude_dir).exists():
        return {"ok": False, "error": "Claude Code isn't installed (no ~/.claude folder)."}
    try:
        settings = _load(path)
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": f"Can't read {path}: {exc}. Nothing was changed."}
    note = None
    if _statusline_state(settings) == "other":
        note = "Kept your existing status line; Claude limits will come from the Claude app's history instead."
    elif _statusline_state(settings) == "none":
        settings["statusLine"] = _additions(statusline=True)["statusLine"]
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        return {"ok": False, "error": "The hooks entry in settings.json isn't an object. Nothing was changed."}
    for event in HOOK_EVENTS:
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            return {"ok": False, "error": f"hooks.{event} in settings.json isn't a list. Nothing was changed."}
        if not any(isinstance(g, dict) and any(isinstance(h, dict) and _is_ours(h.get("command"), HOOK_MODULE)
                                               for h in g.get("hooks") or []) for g in groups):
            groups.append(_additions(statusline=False)["hooks"][event][0])
    backup = _write(path, settings)
    return {"ok": True, "note": note, "backup": str(backup) if backup else None, "integrations": status(cfg)}


def disconnect_claude(cfg: Config) -> dict:
    path = _settings_path(cfg)
    try:
        settings = _load(path)
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": f"Can't read {path}: {exc}. Nothing was changed."}
    changed = False
    if _statusline_state(settings) == "agent-tree":
        del settings["statusLine"]
        changed = True
    hooks = settings.get("hooks")
    if isinstance(hooks, dict):
        for event in HOOK_EVENTS:
            groups = hooks.get(event)
            if not isinstance(groups, list):
                continue
            kept = []
            for g in groups:
                if isinstance(g, dict) and isinstance(g.get("hooks"), list):
                    inner = [h for h in g["hooks"] if not (isinstance(h, dict) and _is_ours(h.get("command"), HOOK_MODULE))]
                    if len(inner) != len(g["hooks"]):
                        changed = True
                    if inner:
                        kept.append({**g, "hooks": inner})
                else:
                    kept.append(g)
            if kept:
                hooks[event] = kept
            else:
                hooks.pop(event, None)
        if not hooks:
            settings.pop("hooks", None)
    backup = _write(path, settings) if changed else None
    return {"ok": True, "backup": str(backup) if backup else None, "integrations": status(cfg)}
