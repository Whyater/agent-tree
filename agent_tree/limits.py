"""Read local Codex and Claude plan usage limits."""
from __future__ import annotations

import json
import re
import os
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .sources.base import norm_ts, safe_files


def _now_epoch(now: Any = None) -> float:
    if now is None:
        return time.time()
    if isinstance(now, datetime):
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return now.timestamp()
    if isinstance(now, (int, float)):
        return float(now)
    if isinstance(now, str):
        ts = norm_ts(now)
        if ts:
            try:
                return datetime.fromisoformat(ts[:-1] + "+00:00").timestamp()
            except ValueError:
                pass
    return time.time()


def _iso_epoch(value: Any) -> str | None:
    return norm_ts(value)


def _record_epoch(value: Any) -> float | None:
    iso = norm_ts(value)
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso[:-1] + "+00:00").timestamp()
    except ValueError:
        return None


def _window(item: Any, now_epoch: float, default_id: str | None = None) -> dict | None:
    if not isinstance(item, dict):
        return None
    used = item.get("used_percent", item.get("used_percentage"))
    reset = item.get("resets_at")
    if isinstance(used, bool) or isinstance(reset, bool):
        return None
    try:
        used = float(used)
        reset_epoch = float(reset)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(used) or not math.isfinite(reset_epoch):
        return None
    if reset_epoch <= now_epoch:
        return None
    minutes = item.get("window_minutes")
    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        minutes = 300 if default_id == "five_hour" else 10080 if default_id == "seven_day" else 0
    if minutes == 300:
        ident, label = "five_hour", "Session"
    elif minutes == 10080:
        ident, label = "seven_day", "Week"
    else:
        ident = default_id if default_id in {"five_hour", "seven_day"} else "custom"
        if minutes and minutes % (24 * 60) == 0:
            label = f"{minutes // (24 * 60)}d"
        elif minutes:
            label = f"{minutes / 60:g}h"
        else:
            label = "custom"
    return {
        "id": ident,
        "label": label,
        "used_percent": used,
        "window_minutes": minutes,
        "resets_at": _iso_epoch(reset_epoch),
    }


def _provider(name: str, available: bool, plan: Any, as_of: str | None, stale: bool,
              source: str, reason: str | None, windows: list[dict]) -> dict:
    return {"provider": name, "available": available, "plan": plan,
            "as_of": as_of, "stale": stale, "source": source,
            "reason": reason, "windows": windows}


def _read_codex(cfg, now_epoch: float) -> dict:
    source = "Codex session logs"
    files: list[Path] = []
    try:
        for root in (Path(cfg.codex_dir) / "sessions", Path(cfg.codex_dir) / "archived_sessions"):
            files.extend(safe_files(root, "**/rollout-*.jsonl"))
        files = sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[:8]
    except Exception:
        files = []
    newest: tuple[float, dict, str] | None = None
    for path in files:
        try:
            size = path.stat().st_size
            with path.open("rb") as fh:
                fh.seek(max(0, size - 256 * 1024))
                if fh.tell():
                    fh.readline()  # discard partial first line
                raw = fh.read(256 * 1024)
            for line in raw.splitlines():
                try:
                    obj = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue
                if not isinstance(obj, dict):
                    continue
                payload = obj.get("payload")
                if not isinstance(payload, dict) or payload.get("type") != "token_count":
                    continue
                limits = payload.get("rate_limits")
                if not isinstance(limits, dict):
                    continue
                ts = obj.get("timestamp")
                epoch = _record_epoch(ts)
                iso = norm_ts(ts)
                if epoch is None or iso is None:
                    continue
                if newest is None or epoch > newest[0]:
                    newest = (epoch, limits, iso)
        except (OSError, ValueError):
            continue
        # Older rollout files cannot contain a record newer than their mtime.
        if newest is not None:
            try:
                next_mtime = files[files.index(path) + 1].stat().st_mtime
            except (IndexError, OSError):
                next_mtime = None
            if next_mtime is not None and newest[0] > next_mtime:
                break
    if newest is None:
        return _provider("codex", False, None, None, False, source,
                         "No Codex limit data found yet.", [])
    epoch, limits, as_of = newest
    windows = []
    for key in ("primary", "secondary"):
        win = _window(limits.get(key), now_epoch)
        if win:
            windows.append(win)
    reason = None
    if not windows:
        reason = "Limits reset since Codex last reported; they'll update on your next Codex turn."
    return _provider("codex", True, limits.get("plan_type"), as_of,
                     now_epoch - epoch > 6 * 3600, source, reason, windows)


def _read_claude(cfg, now_epoch: float) -> dict:
    source = "Claude Code status line"
    path = Path(cfg.home) / "claude-limits.json"
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError
    except (OSError, ValueError, UnicodeDecodeError):
        return _provider("claude", False, None, None, False, source,
                         "Claude Code only shares limits with a status-line script. Turn it on in Settings.", [])
    as_of = norm_ts(data.get("captured_at"))
    if not as_of:
        return _provider("claude", False, None, None, False, source, "Malformed Claude limit data.", [])
    windows = []
    for key in ("five_hour", "seven_day"):
        win = _window(data.get(key), now_epoch, key)
        if win:
            windows.append(win)
    epoch = _record_epoch(as_of) or now_epoch
    return _provider("claude", True, data.get("plan"), as_of,
                     now_epoch - epoch > 6 * 3600, source, None, windows)


def _claude_app_dir() -> Path:
    override = os.environ.get("AGENT_TREE_CLAUDE_APP_DIR")
    return Path(override).expanduser() if override else Path.home() / "Library" / "Application Support" / "Claude"


def _claude_plan(cfg) -> str | None:
    """Plan name ("Max 20x", "Pro"...) from two fields of ~/.claude.json. Nothing else is read or kept."""
    try:
        with (Path(cfg.claude_dir).parent / ".claude.json").open("r", encoding="utf-8") as fh:
            account = json.load(fh).get("oauthAccount") or {}
    except (OSError, ValueError, AttributeError):
        return None
    tier = str(account.get("organizationRateLimitTier") or account.get("userRateLimitTier") or "").lower()
    kind = str(account.get("organizationType") or "").lower()
    m = re.search(r"max_(\d+)x", tier)
    if m:
        return f"Max {m.group(1)}x"
    if "max" in kind or "max" in tier:
        return "Max"
    if "pro" in kind or "pro" in tier:
        return "Pro"
    if "team" in kind:
        return "Team"
    if "enterprise" in kind:
        return "Enterprise"
    return None


def _hour_floor(epoch: float) -> float:
    return epoch - (epoch % 3600)


def _read_claude_app(cfg, now_epoch: float) -> dict | None:
    """Claude plan limits from the Claude desktop app's own usage history.

    ~/Library/Application Support/Claude/plan-usage-history.json holds samples
    {"t": ms, "u": {"fh": session %, "sd": weekly %, ...}} (version 2; version 1
    keeps the percentages at the top level). It has no reset times, so they are
    estimated: the session window ends 5 h after the current run of non-zero
    session readings began; the weekly window ends 7 days after the last time
    the weekly figure dropped. Both are labelled estimated.
    """
    path = _claude_app_dir() / "plan-usage-history.json"
    try:
        if path.stat().st_size > 4 * 1024 * 1024:
            return None
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict) or data.get("version") not in (1, 2) or not isinstance(data.get("samples"), list):
        return None
    samples = []
    for s in data["samples"]:
        if not isinstance(s, dict):
            continue
        u = s.get("u") if data.get("version") == 2 else s
        t = s.get("t")
        if not isinstance(u, dict) or not isinstance(t, (int, float)):
            continue
        samples.append((t / 1000.0, u.get("fh"), u.get("sd")))
    samples.sort(key=lambda x: x[0])
    if not samples:
        return None
    last_t, last_fh, last_sd = samples[-1]
    windows = []
    if isinstance(last_fh, (int, float)):
        start = None
        for t, fh_, _ in reversed(samples):  # start of the current non-zero session run
            if not isinstance(fh_, (int, float)) or fh_ <= 0:
                break
            start = t
        reset = start + 5 * 3600 if start else None
        if reset is not None and reset <= now_epoch:
            reset = None  # that session has ended; it will read 0% next time Claude reports
            used = 0.0
        else:
            used = float(last_fh)
        windows.append({"id": "five_hour", "label": "Session", "used_percent": used, "window_minutes": 300,
                        "resets_at": _iso_epoch(reset) if reset else None, "resets_estimated": True})
    if isinstance(last_sd, (int, float)):
        drop = None
        for (t0, _, a), (t1, _, b) in zip(samples, samples[1:]):
            if isinstance(a, (int, float)) and isinstance(b, (int, float)) and b < a:
                drop = t1
        reset = None
        if drop is not None:
            reset = _hour_floor(drop) + 7 * 86400
            while reset <= now_epoch:
                reset += 7 * 86400
        windows.append({"id": "seven_day", "label": "Week", "used_percent": float(last_sd), "window_minutes": 10080,
                        "resets_at": _iso_epoch(reset) if reset else None, "resets_estimated": True})
    return _provider("claude", True, _claude_plan(cfg), _iso_epoch(last_t), now_epoch - last_t > 6 * 3600,
                     "Claude app usage history", None, windows)


def read_limits(cfg, now=None) -> dict:
    """Return plan usage limits in the documented API shape.

    Claude: the status-line snapshot (exact reset times) wins when it is newer;
    otherwise the Claude desktop app's usage history is used. Both are local
    files, so nothing here touches the network.
    """
    now_epoch = _now_epoch(now)
    claude = _read_claude(cfg, now_epoch)
    app = _read_claude_app(cfg, now_epoch)
    if app and (not claude["available"] or (_record_epoch(app["as_of"]) or 0) > (_record_epoch(claude["as_of"]) or 0)):
        claude = app
    elif claude["available"] and not claude.get("plan"):
        claude["plan"] = _claude_plan(cfg)
    if not claude["available"]:
        claude["reason"] = ("No Claude limit data on this Mac. Open the Claude desktop app once, "
                            "or add the Agent Tree status line to Claude Code.")
    return {"providers": [claude, _read_codex(cfg, now_epoch)]}
