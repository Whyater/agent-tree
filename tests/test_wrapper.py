import json
import tempfile
import unittest
from pathlib import Path

from agent_tree import db
from agent_tree.config import Config
from agent_tree.sources import wrapper


class WrapperParserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.run_dir = root / "runs"
        self.run_dir.mkdir()
        self.cfg = Config(root / "home", root / "claude", root / "codex", root / "t3", [self.run_dir])
        self.cfg.home.mkdir(parents=True)
        self.conn = db.connect(self.cfg.home / "index.sqlite")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_log_meta_failure_and_incremental(self):
        exact = self.run_dir / "exact.md"
        exact.write_text("secret prompt that must not be indexed", encoding="utf-8")
        (self.run_dir / "exact.md.log").write_text("model: gpt-5-codex\nreasoning effort: high\nworkdir: /tmp/project\nsession id: 550e8400-e29b-41d4-a716-446655440000\n", encoding="utf-8")
        (self.run_dir / "exact.md.out").write_text("private final response", encoding="utf-8")

        meta = self.run_dir / "meta.md"
        meta.write_text("private prompt", encoding="utf-8")
        (self.run_dir / "meta.md.meta.json").write_text(json.dumps({"tier": "luna", "model": "gpt-5-mini", "effort": "low", "cwd": "/tmp/meta", "exit_code": 0, "tokens": {"input": 4, "cached_input": 2, "output": 3}}), encoding="utf-8")

        failed = self.run_dir / "failed.md"
        failed.write_text("prompt", encoding="utf-8")
        (self.run_dir / "failed.md.log").write_text("failed: exit 1\n", encoding="utf-8")
        ignored = self.run_dir / "ignored.md"
        ignored.write_text("no sidecar", encoding="utf-8")

        stats = wrapper.scan(self.conn, self.cfg)
        self.assertEqual(stats.files_seen, 3)
        rows = {r["id"]: dict(r) for r in self.conn.execute("SELECT * FROM nodes")}
        self.assertEqual(rows["wr:" + str(exact.absolute())]["status"], "completed")
        self.assertEqual(rows["wr:" + str(meta.absolute())]["agent_type"], "codex-wrapper-luna")
        self.assertEqual(rows["wr:" + str(failed.absolute())]["status"], "failed")
        self.assertNotIn("secret prompt", json.dumps(rows))
        self.assertNotIn("private final response", json.dumps(rows))
        link = self.conn.execute("SELECT to_id,confidence FROM links WHERE kind='wrapper-codex'").fetchone()
        self.assertEqual(tuple(link), ("cx:550e8400-e29b-41d4-a716-446655440000", "exact"))
        usage = self.conn.execute("SELECT input,cached_input,output FROM usage WHERE source='wrapper'").fetchone()
        self.assertEqual(tuple(usage), (4, 2, 3))
        before = self.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        self.assertEqual(wrapper.scan(self.conn, self.cfg).files_parsed, 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0], before)


if __name__ == "__main__":
    unittest.main()
