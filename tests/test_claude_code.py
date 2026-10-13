import json
import tempfile
import unittest
from pathlib import Path

from agent_tree.config import Config
from agent_tree.db import connect
from agent_tree.sources.claude_code import scan


class ClaudeCodeParserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.claude = root / "claude"
        self.project = self.claude / "projects" / "-tmp-demo"
        self.project.mkdir(parents=True)
        self.cfg = Config(root / "home", self.claude, root / "codex", root / "t3", [])
        self.conn = connect(root / "index.sqlite")
        self.sid = "session-main"
        self.main = self.project / f"{self.sid}.jsonl"
        self._write_fixtures()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _write_jsonl(self, path, records):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for record in records:
                if record == "MALFORMED":
                    fh.write("{not json\n")
                else:
                    fh.write(json.dumps(record) + "\n")

    def _assistant(self, uid, ts, request_id, output, content=None, model="claude-opus-5-5", usage=True):
        message = {"id": f"msg-{uid}", "model": model, "content": content or []}
        if usage:
            message["usage"] = {
                "input_tokens": 10,
                "cache_read_input_tokens": 2,
                "cache_creation_input_tokens": 3,
                "output_tokens": output,
                "output_tokens_details": {"thinking_tokens": 1},
            }
        return {
            "type": "assistant", "uuid": uid, "requestId": request_id,
            "sessionId": self.sid, "timestamp": ts, "cwd": "/tmp/demo",
            "effort": "high", "message": message,
        }

    def _write_fixtures(self):
        spawn_sync = {
            "type": "tool_use", "id": "toolu-sync", "name": "Agent",
            "input": {"description": "sync child description", "subagent_type": "general-purpose",
                       "model": "opus", "prompt": "DO NOT STORE THIS PROMPT", "run_in_background": False},
        }
        spawn_async = {
            "type": "tool_use", "id": "toolu-async", "name": "Task",
            "input": {"description": "background child description", "model": "sonnet",
                       "prompt": "DO NOT STORE THIS PROMPT EITHER", "run_in_background": True},
        }
        bash = {
            "type": "tool_use", "id": "toolu-bash", "name": "Bash",
            "input": {"command": "FOO=bar /usr/local/bin/codex-agent.sh sol /tmp /tmp/work /tmp/prompt-file high"},
        }
        records = [
            self._assistant("a-1", "2026-10-01T00:00:00Z", "req-1", 2, [spawn_sync, bash]),
            self._assistant("a-1b", "2026-10-01T00:00:01Z", "req-1", 5),
            self._assistant("a-2", "2026-10-01T00:00:02Z", "req-2", 4, [spawn_async]),
            {"type": "user", "uuid": "u-sync", "sessionId": self.sid, "timestamp": "2026-10-01T00:00:03Z",
             "toolUseResult": {"agentId": "agent-sync", "status": "completed", "resolvedModel": "claude-opus-5-5"},
             "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu-sync", "content": "ignored"}]}},
            {"type": "user", "uuid": "u-async", "sessionId": self.sid, "timestamp": "2026-10-01T00:00:04Z",
             "toolUseResult": {"agentId": "agent-async", "status": "async_launched", "resolvedModel": "claude-sonnet"},
             "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu-async"}]}},
            {"type": "ai-title", "sessionId": self.sid, "timestamp": "2026-10-01T00:00:05Z", "aiTitle": "Demo title"},
            {"type": "cost-state", "sessionId": self.sid, "timestamp": "2026-10-01T00:00:06Z", "totalCostUSD": 1.25},
            {"type": "drifted", "sessionId": self.sid, "timestamp": "2026-10-01T00:00:07Z", "unknown": {"x": 1}},
            self._assistant("synthetic", "2026-10-01T00:00:08Z", "req-synthetic", 99, model="<synthetic>"),
            "MALFORMED",
        ]
        self._write_jsonl(self.main, records)

        sub = self.project / self.sid / "subagents"
        nested = {
            "type": "tool_use", "id": "toolu-nested", "name": "Agent",
            "input": {"description": "nested child", "model": "haiku", "prompt": "nested secret"},
        }
        self._write_jsonl(sub / "agent-agent-sync.jsonl", [
            self._assistant("s-1", "2026-10-01T00:00:10Z", "req-sub", 6, [nested], model="claude-sonnet"),
        ])
        self._write_jsonl(sub / "agent-agent-async.jsonl", [
            self._assistant("s-2", "2026-10-01T00:00:11Z", "req-sub-2", 3, model="claude-haiku"),
        ])
        self._write_jsonl(sub / "agent-agent-nested.jsonl", [
            self._assistant("s-3", "2026-10-01T00:00:12Z", "req-nested", 2, model="claude-haiku"),
        ])
        for aid, depth, shape, model, desc, tool in [
            ("agent-sync", 1, "sync", "opus", "sync child description", "toolu-sync"),
            ("agent-async", 1, "background", "sonnet", "background child description", "toolu-async"),
            ("agent-nested", 2, "sync", "haiku", "nested child", "toolu-nested"),
        ]:
            (sub / f"agent-{aid}.meta.json").write_text(json.dumps({
                "agentType": "general-purpose", "description": desc, "toolUseId": tool,
                "spawnDepth": depth, "requestShape": shape, "model": model,
            }), encoding="utf-8")

    def test_tree_usage_spawns_privacy_and_incremental(self):
        stats = scan(self.conn, self.cfg)
        self.assertTrue(stats.present)
        self.assertEqual(stats.nodes, 4)
        self.assertEqual(stats.lines_skipped, 1)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM nodes").fetchone()[0], 4)
        self.assertEqual(self.conn.execute("SELECT sum(output) FROM usage").fetchone()[0], 20)
        self.assertEqual(self.conn.execute("SELECT sum(input) FROM usage").fetchone()[0], 50)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM spawns").fetchone()[0], 3)
        root_spawn = self.conn.execute("SELECT * FROM spawns WHERE key='toolu-sync'").fetchone()
        self.assertEqual(root_spawn["parent_node_id"], "cc:session-main")
        nested_spawn = self.conn.execute("SELECT * FROM spawns WHERE key='toolu-nested'").fetchone()
        self.assertEqual(nested_spawn["parent_node_id"], "cc-agent:agent-sync")
        self.assertEqual(self.conn.execute("SELECT parent_hint FROM nodes WHERE id='cc-agent:agent-nested'").fetchone()[0], "toolu-nested")
        self.assertEqual(self.conn.execute("SELECT title FROM sessions WHERE id='cc:session-main'").fetchone()[0], "Demo title")
        self.assertEqual(self.conn.execute("SELECT json_extract(extra_json, '$.reported_cost_usd') FROM sessions WHERE id='cc:session-main'").fetchone()[0], 1.25)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM links WHERE kind='wrapper-caller'").fetchone()[0], 1)
        all_text = " ".join(str(row[0]) for row in self.conn.execute("SELECT description FROM nodes WHERE description IS NOT NULL"))
        self.assertNotIn("DO NOT STORE", all_text)
        self.assertLessEqual(max(len(str(value)) for row in self.conn.execute("SELECT * FROM nodes") for value in row if isinstance(value, str)), 160)

        with self.main.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(self._assistant("a-new", "2026-10-01T00:00:20Z", "req-new", 7)) + "\n")
        second = scan(self.conn, self.cfg)
        self.assertEqual(second.lines_parsed, 1)
        self.assertEqual(self.conn.execute("SELECT sum(output) FROM usage").fetchone()[0], 27)
        self.assertEqual(scan(self.conn, self.cfg).lines_parsed, 0)

    def test_replace_and_metadata_only(self):
        scan(self.conn, self.cfg)
        self.main.write_text(json.dumps(self._assistant("replacement", "2026-10-02T00:00:00Z", "req-replaced", 1)) + "\n", encoding="utf-8")
        scan(self.conn, self.cfg)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM usage WHERE node_id='cc:session-main'").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT sum(output) FROM usage WHERE node_id='cc:session-main'").fetchone()[0], 1)

        root = Path(self.tmp.name) / "metadata.sqlite"
        conn = connect(root)
        cfg = Config(Path(self.tmp.name) / "metadata-home", self.claude, Path(), Path(), [], metadata_only=True)
        scan(conn, cfg)
        self.assertIsNone(conn.execute("SELECT title FROM sessions WHERE id='cc:session-main'").fetchone()[0])
        self.assertIsNone(conn.execute("SELECT description FROM nodes WHERE id='cc-agent:agent-sync'").fetchone()[0])
        conn.close()


if __name__ == "__main__":
    unittest.main()
