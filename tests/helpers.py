"""Shared test helpers: temporary homes and configs that never touch real data."""

import tempfile
from pathlib import Path

from agent_tree import db
from agent_tree.config import Config, ensure_home


def make_config(tmp: Path, **overrides) -> Config:
    home = tmp / "home"
    ensure_home(home)
    cfg = Config(
        home=home,
        claude_dir=tmp / "claude",
        codex_dir=tmp / "codex",
        t3_dir=tmp / "t3",
        wrapper_dirs=[tmp / "wrapper"],
    )
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


class TempIndex:
    """Context manager giving (cfg, conn) backed by a throwaway directory."""

    def __init__(self, **overrides):
        self.overrides = overrides

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.cfg = make_config(self.tmp, **self.overrides)
        self.conn = db.connect(self.cfg.db_path)
        return self

    def __exit__(self, *exc):
        self.conn.close()
        self._tmp.cleanup()
