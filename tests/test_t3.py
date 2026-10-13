import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent_tree import db
from agent_tree.config import Config
from agent_tree.sources import t3


class T3ParserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cfg = Config(root / "home", root / "claude", root / "codex", root / "t3", [root / "wrapper"])
        self.cfg.home.mkdir(parents=True)
        self.cfg.t3_dir.joinpath("userdata").mkdir(parents=True)
        self.conn = db.connect(self.cfg.home / "index.sqlite")
        self._make_state()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _make_state(self):
        path = self.cfg.t3_dir / "userdata" / "state.sqlite"
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        c.executescript(
            """
            CREATE TABLE projection_projects(project_id TEXT, title TEXT, workspace_root TEXT, created_at TEXT, updated_at TEXT, deleted_at TEXT);
            CREATE TABLE projection_threads(thread_id TEXT, project_id TEXT, title TEXT, branch TEXT, worktree_path TEXT, latest_turn_id TEXT, created_at TEXT, updated_at TEXT, deleted_at TEXT, model_selection_json TEXT, archived_at TEXT, settled_at TEXT);
            CREATE TABLE projection_turns(thread_id TEXT, turn_id TEXT, state TEXT, requested_at TEXT, started_at TEXT, completed_at TEXT);
            CREATE TABLE projection_thread_sessions(thread_id TEXT, status TEXT, provider_name TEXT, provider_session_id TEXT, provider_thread_id TEXT);
            CREATE TABLE provider_session_runtime(thread_id TEXT, provider_name TEXT, adapter_key TEXT, status TEXT, last_seen_at TEXT, resume_cursor_json TEXT, runtime_payload_json TEXT);
            CREATE TABLE projection_thread_activities(activity_id TEXT, thread_id TEXT, turn_id TEXT, kind TEXT, summary TEXT, payload_json TEXT, created_at TEXT, sequence INTEGER);
            """
        )
        c.execute("INSERT INTO projection_projects VALUES(?,?,?,?,?,?)", ("p1", "Demo project", "/tmp/demo", "2026-01-01T00:00:00Z", "2026-01-01T00:10:00Z", None))
        rows = [
            ("import:claudeAgent:claude-1", "p1", "Claude title", "", "", "turn-1", "2026-01-01T00:00:00Z", "2026-01-01T00:02:00Z", None, json.dumps({"instanceId": "claudeAgent", "model": "claude-opus-5-5", "options": [{"id": "effort", "value": "high"}]}), None, None),
            ("import:codex:codex-1", "p1", "Codex title", "", "", None, "2026-01-01T00:03:00Z", "2026-01-01T00:04:00Z", None, json.dumps({"instanceId": "codex", "model": "gpt-5-codex"}), None, None),
            ("native-1", "p1", "Native title", "", "", None, "2026-01-01T00:05:00Z", "2026-01-01T00:06:00Z", None, json.dumps({"instanceId": "claudeAgent", "model": "claude-sonnet-4"}), None, None),
            ("archived-1", "p1", "Archived title", "", "", None, "2026-01-01T00:07:00Z", "2026-01-01T00:08:00Z", None, json.dumps({"instanceId": "codex", "model": "gpt-5"}), "2026-01-01T00:09:00Z", None),
            ("deleted-1", "p1", "Deleted title", "", "", None, "2026-01-01T00:07:00Z", "2026-01-01T00:08:00Z", "2026-01-01T00:09:00Z", json.dumps({"instanceId": "codex"}), None, None),
        ]
        c.executemany("INSERT INTO projection_threads VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        c.execute("INSERT INTO projection_turns VALUES(?,?,?,?,?,?)", ("import:claudeAgent:claude-1", "turn-1", "completed", "2026-01-01T00:00:01Z", "2026-01-01T00:00:02Z", "2026-01-01T00:02:00Z"))
        c.executemany("INSERT INTO projection_thread_sessions VALUES(?,?,?,?,?)", [("import:codex:codex-1", "done", "codex", None, None)])
        c.executemany("INSERT INTO provider_session_runtime VALUES(?,?,?,?,?,?,?)", [
            ("import:codex:codex-1", "codex", "", "done", "", '{"threadId":"codex-1"}', '{"importedTranscripts":[{"provider":"codex","providerSessionId":"cx-a"},{"provider":"codex","providerSessionId":"cx-b"}]}'),
            ("native-1", "claudeAgent", "", "done", "", '{"threadId":"claude-native"}', "{}"),
        ])
        activities = [
            ("a1", "import:claudeAgent:claude-1", "turn-1", "context-window.updated", "", '{"usedTokens":123,"maxTokens":1000}', "2026-01-01T00:01:00Z", 1),
            ("a2", "import:claudeAgent:claude-1", "turn-1", "task.started", "", '{"taskId":"550e8400-e29b-41d4-a716-446655440000","agentKind":"agent","taskType":"local_agent","title":"Sub agent","toolUseId":"toolu_123"}', "2026-01-01T00:01:01Z", 2),
            ("a3", "import:claudeAgent:claude-1", "turn-1", "task.completed", "", '{"taskId":"550e8400-e29b-41d4-a716-446655440000","agentKind":"agent","status":"completed"}', "2026-01-01T00:01:02Z", 3),
            ("a4", "import:claudeAgent:claude-1", "turn-1", "task.started", "", '{"taskId":"bash-1","agentKind":"background","taskType":"local_bash"}', "2026-01-01T00:01:03Z", 4),
        ]
        c.executemany("INSERT INTO projection_thread_activities VALUES(?,?,?,?,?,?,?,?)", activities)
        c.commit()
        c.close()

    def test_threads_links_tasks_and_rescan(self):
        first = t3.scan(self.conn, self.cfg)
        self.assertEqual(first.files_parsed, 1)
        nodes = {r["id"]: dict(r) for r in self.conn.execute("SELECT * FROM nodes")}
        self.assertEqual(len([n for n in nodes.values() if n["source"] == "t3"]), 5)
        self.assertNotIn("t3:deleted-1", nodes)
        self.assertEqual(json.loads(nodes["t3:import:claudeAgent:claude-1"]["extra_json"])["t3_context"], {"usedTokens": 123, "maxTokens": 1000})
        self.assertEqual(len(self.conn.execute("SELECT 1 FROM usage").fetchall()), 0)
        links = {tuple(r) for r in self.conn.execute("SELECT kind,from_id,to_id,confidence FROM links")}
        self.assertIn(("t3-session", "t3:import:claudeAgent:claude-1", "cc:claude-1", "exact"), links)
        self.assertIn(("t3-session", "t3:import:codex:codex-1", "cx:cx-a", "exact"), links)
        self.assertIn(("t3-session", "t3:import:codex:codex-1", "cx:cx-b", "exact"), links)
        self.assertTrue(any(x[0] == "t3-task" for x in links))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM nodes WHERE id LIKE 't3-task:%'").fetchone()[0], 1)
        before = self.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        self.assertEqual(t3.scan(self.conn, self.cfg).files_parsed, 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0], before)


if __name__ == "__main__":
    unittest.main()
