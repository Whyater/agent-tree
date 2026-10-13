"""Paths and user settings.

Everything the tool writes lives under ``~/.agent-tree`` (override with the
``AGENT_TREE_HOME`` environment variable). Source locations default to the
standard install paths and can be overridden in ``config.toml`` or with
environment variables, which is also how the tests point parsers at fixtures.
"""

from __future__ import annotations

import os
import shutil
import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

DEFAULTS_PACKAGE = "agent_tree.defaults"
EDITABLE_FILES = ("config.toml", "prices.toml", "rules.toml")


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else default


@dataclass
class Config:
    home: Path
    claude_dir: Path
    codex_dir: Path
    t3_dir: Path
    wrapper_dirs: list[Path]
    metadata_only: bool = False
    text_limit: int = 160
    port: int = 8711
    covered_by_plan: dict[str, bool] = field(default_factory=dict)
    timezone: str | None = None

    @property
    def db_path(self) -> Path:
        return self.home / "index.sqlite"

    @property
    def events_path(self) -> Path:
        return self.home / "events.jsonl"

    def user_file(self, name: str) -> Path:
        return self.home / name

    def is_covered(self, family: str | None) -> bool:
        return bool(family and self.covered_by_plan.get(family, False))


def default_text(name: str) -> str:
    return resources.files(DEFAULTS_PACKAGE).joinpath(name).read_text(encoding="utf-8")


def ensure_home(home: Path) -> None:
    """Create ~/.agent-tree and seed the editable TOML files on first run."""
    home.mkdir(parents=True, exist_ok=True)
    for name in EDITABLE_FILES:
        target = home / name
        if not target.exists():
            target.write_text(default_text(name), encoding="utf-8")


def load_toml(cfg_home: Path, name: str) -> dict:
    """Read a user TOML file, falling back to the bundled default."""
    path = cfg_home / name
    try:
        if path.exists():
            with path.open("rb") as fh:
                return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise SystemExit(f"att: could not read {path}: {exc}") from exc
    return tomllib.loads(default_text(name))


def load_config(create: bool = True) -> Config:
    home = _env_path("AGENT_TREE_HOME", Path.home() / ".agent-tree")
    if create:
        ensure_home(home)
    raw = load_toml(home, "config.toml")
    src = raw.get("sources", {})
    privacy = raw.get("privacy", {})
    server = raw.get("server", {})
    plan = raw.get("covered_by_plan", {})

    def pick(env: str, key: str, default: str) -> Path:
        return _env_path(env, Path(src.get(key) or default).expanduser())

    wrapper_env = os.environ.get("AGENT_TREE_WRAPPER_DIRS")
    if wrapper_env:
        wrapper_dirs = [Path(p).expanduser() for p in wrapper_env.split(os.pathsep) if p]
    else:
        wrapper_dirs = [Path(p).expanduser() for p in src.get("wrapper_dirs", ["/tmp/codex-tasks"])]

    return Config(
        home=home,
        claude_dir=pick("AGENT_TREE_CLAUDE_DIR", "claude_dir", "~/.claude"),
        codex_dir=pick("AGENT_TREE_CODEX_DIR", "codex_dir", "~/.codex"),
        t3_dir=pick("AGENT_TREE_T3_DIR", "t3_dir", "~/.t3"),
        wrapper_dirs=wrapper_dirs,
        metadata_only=bool(privacy.get("metadata_only", False)),
        text_limit=max(0, min(int(privacy.get("text_limit", 160)), 160)),
        port=int(server.get("port", 8711)),
        covered_by_plan={str(k): bool(v) for k, v in plan.items()},
        timezone=raw.get("display", {}).get("timezone") or None,
    )


def reset_user_file(cfg: Config, name: str) -> Path:
    """Overwrite a user TOML with the bundled default (keeps a .bak copy)."""
    target = cfg.home / name
    if target.exists():
        shutil.copy2(target, target.with_suffix(target.suffix + ".bak"))
    target.write_text(default_text(name), encoding="utf-8")
    return target
