"""Claude Code status-line helper for capturing plan rate limits.

This module intentionally has no imports from :mod:`agent_tree`; it is safe to
invoke directly as ``python3 -m agent_tree.statusline`` from a status-line
configuration.
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _number(value: Any) -> int | float | None:
    """Return finite numeric values, excluding booleans."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _window(raw: Any) -> dict[str, int | float] | None:
    if not isinstance(raw, dict):
        return None
    used = _number(raw.get("used_percentage"))
    resets = _number(raw.get("resets_at"))
    if used is None or resets is None:
        return None
    return {"used_percentage": used, "resets_at": resets}


def _read_previous(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _write(path: Path, five: Any, seven: Any) -> None:
    payload = {
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "five_hour": five,
        "seven_day": seven,
        "plan": None,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=str(path.parent), prefix=".claude-limits.", delete=False
        ) as handle:
            temporary = handle.name
            json.dump(payload, handle, separators=(",", ":"))
            handle.write("\n")
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _format_percentage(value: Any) -> str | None:
    number = _number(value)
    if number is None:
        return None
    if isinstance(number, float) and number.is_integer():
        return str(int(number))
    return format(number, "g")


def _run() -> str:
    try:
        raw_input = sys.stdin.read()
        parsed = json.loads(raw_input)
        data = parsed if isinstance(parsed, dict) else {}
    except Exception:
        data = {}

    home = Path(os.environ.get("AGENT_TREE_HOME") or (Path.home() / ".agent-tree"))
    path = home / "claude-limits.json"
    previous = _read_previous(path)

    # Claude Code may omit rate_limits on some invocations. Preserve the last
    # captured values and avoid rewriting the file in that case.
    if "rate_limits" in data:
        limits = data.get("rate_limits")
        limits = limits if isinstance(limits, dict) else {}
        five = _window(limits.get("five_hour"))
        seven = _window(limits.get("seven_day"))
        try:
            _write(path, five, seven)
        except Exception:
            pass
    else:
        five = previous.get("five_hour")
        seven = previous.get("seven_day")

    model = data.get("model")
    model_name = model.get("display_name") if isinstance(model, dict) else None
    if not isinstance(model_name, str):
        model_name = None
    elif model_name:
        # Keep arbitrary input from putting extra lines into a status bar.
        model_name = " ".join(model_name.split())

    parts: list[str] = []
    if model_name:
        parts.append(model_name)
    five_text = _format_percentage(five.get("used_percentage")) if isinstance(five, dict) else None
    seven_text = _format_percentage(seven.get("used_percentage")) if isinstance(seven, dict) else None
    if five_text is not None:
        parts.append(f"session {five_text}%")
    if seven_text is not None:
        parts.append(f"week {seven_text}%")
    return " · ".join(parts)


def main() -> int:
    try:
        print(_run())
    except Exception:
        # A status-line command must never disrupt the caller.
        try:
            print("")
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
