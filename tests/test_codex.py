import json
import shutil
import tempfile
import unittest
from pathlib import Path

from agent_tree.config import Config
from agent_tree.db import connect
from agent_tree.sources.codex import scan


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "codex"


class CodexParserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name)
        self.codex = self.work / "codex"
        shutil.copytree(FIXTURE_ROOT, self.codex)
        self.cfg = Config(
            home=self.work / "home",
            claude_dir=self.work / "claude",
            codex_dir=self.codex,
            t3_dir=self.work / "t3",
            wrapper_dirs=[],
        )
        self.conn = connect(self.work / "index.sqlite")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _usage(self, node_id):
        row = self.conn.execute(
            """SELECT COALESCE(sum(input), 0), COALESCE(sum(cached_input), 0),
                      COALESCE(sum(cache_write), 0), COALESCE(sum(output), 0),
                      COALESCE(sum(reasoning), 0), count(*)
                 FROM usage WHERE node_id=?""",
            (node_id,),
        ).fetchone()
        return tuple(row)

    def _all_text(self):
        values = []
        for table in ("nodes", "sessions", "usage", "projects", "files"):
            columns = [row[1] for row in self.conn.execute(f"PRAGMA table_info({table})")]
            for row in self.conn.execute(f"SELECT * FROM {table}"):
                values.extend(str(value) for value in row if isinstance(value, str))
        return values

    def test_tree_cutoffs_parent_links_titles_and_usage(self):
        stats = scan(self.conn, self.cfg)

        self.assertTrue(stats.present)
        self.assertEqual(stats.files_seen, 7)
        self.assertEqual(stats.nodes, 6)
        self.assertGreaterEqual(stats.lines_skipped, 1)

        # Child and grandchild links come only from subagent spawn metadata.
        child = self.conn.execute("SELECT * FROM nodes WHERE id='cx:child'").fetchone()
        grandchild = self.conn.execute("SELECT * FROM nodes WHERE id='cx:grandchild'").fetchone()
        root = self.conn.execute("SELECT * FROM nodes WHERE id='cx:root'").fetchone()
        self.assertEqual(child["kind"], "subagent")
        self.assertEqual(child["agent_type"], "codex-subagent")
        self.assertEqual(child["parent_hint"], "cx:root")
        self.assertEqual(grandchild["parent_hint"], "cx:child")
        self.assertEqual(root["kind"], "root")
        self.assertIsNone(root["parent_hint"])
        self.assertEqual(child["model"], "gpt-6.1-sol")
        self.assertEqual(child["effort"], "medium")
        self.assertEqual(grandchild["family"], "openai")
        self.assertEqual(json.loads(grandchild["extra_json"])["depth"], 2)

        old = self.conn.execute("SELECT * FROM nodes WHERE id='cx:old'").fetchone()
        self.assertIsNone(old["parent_hint"])
        self.assertEqual(json.loads(old["extra_json"])["forked_from"], "root")

        # Titles come from the latest session_index entry, even when rollout data is unchanged.
        root_title = self.conn.execute("SELECT title FROM sessions WHERE id='cx:root'").fetchone()[0]
        self.assertTrue(root_title.startswith("Renamed root title "))
        self.assertEqual(len(root_title), 160)
        self.assertTrue(root_title.endswith("…"))
        self.assertEqual(
            self.conn.execute("SELECT title FROM sessions WHERE id='cx:child'").fetchone()[0],
            "Child title",
        )

        # Every token_count total is cumulative. The final total for each thread
        # must equal the sum of stored deltas, with cached input split out.
        self.assertEqual(self._usage("cx:root")[:5], (100, 30, 7, 50, 10))
        self.assertEqual(self._usage("cx:child")[:5], (11, 3, 2, 8, 2))
        self.assertEqual(self._usage("cx:grandchild")[:5], (5, 1, 0, 3, 1))
        # The ordinal-free prehistory is ignored until its first current task_started.
        self.assertEqual(self._usage("cx:old")[:5], (8, 1, 0, 4, 1))
        # A counter decrease starts a new baseline, so earlier totals are removed.
        self.assertEqual(self._usage("cx:reset")[:5], (22, 3, 2, 6, 1))
        self.assertEqual(self._usage("cx:record")[:5], (12, 3, 1, 7, 1))

        # The copied parent token_count in the child file is before the cutoff.
        child_rows = self.conn.execute(
            "SELECT count(*) FROM usage WHERE node_id='cx:child'"
        ).fetchone()[0]
        self.assertEqual(child_rows, 2)
        all_text = " ".join(self._all_text())
        self.assertNotIn("DO NOT STORE THIS MESSAGE", all_text)
        self.assertNotIn("CHILD MESSAGE MUST NOT BE STORED", all_text)
        display_values = []
        for table, columns in (("nodes", ("description", "role", "model", "effort")),
                               ("sessions", ("title",)),
                               ("projects", ("name",))):
            for column in columns:
                display_values.extend(row[0] for row in self.conn.execute(
                    f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL"))
        self.assertLessEqual(max(map(len, display_values)), 160)

        # The archived copy has a lower cumulative total and must not double count.
        self.assertEqual(
            self.conn.execute("SELECT sum(input + cached_input) FROM usage WHERE node_id='cx:root'").fetchone()[0],
            130,
        )
        self.assertTrue(any("duplicate usage" in note for note in stats.notes))

    def test_incremental_append_and_unchanged_rescan(self):
        first = scan(self.conn, self.cfg)
        self.assertGreater(first.lines_parsed, 0)
        root_path = self.codex / "sessions/2026/10/01/rollout-root.jsonl"
        extra = {
            "timestamp": "2026-10-01T00:00:06Z",
            "ordinal": 6,
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": 150,
                        "cached_input_tokens": 35,
                        "cache_write_input_tokens": 8,
                        "output_tokens": 60,
                        "reasoning_output_tokens": 12,
                        "total_tokens": 210,
                    }
                },
            },
        }
        with root_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(extra) + "\n")

        second = scan(self.conn, self.cfg)
        self.assertEqual(second.lines_parsed, 1)
        self.assertEqual(self._usage("cx:root")[:5], (115, 35, 8, 60, 12))
        before = self.conn.execute("SELECT count(*) FROM usage").fetchone()[0]
        third = scan(self.conn, self.cfg)
        self.assertEqual(third.lines_parsed, 0)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM usage").fetchone()[0], before)

    def test_token_usage_record_is_fallback_only(self):
        first = scan(self.conn, self.cfg)
        self.assertTrue(any("token_usage_record" in note for note in first.notes))
        rows = self.conn.execute(
            "SELECT key, input, cached_input, output FROM usage WHERE node_id='cx:record' ORDER BY key"
        ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual([(row[1], row[2], row[3]) for row in rows], [(8, 2, 4), (4, 1, 3)])
        self.assertTrue(sum("token_usage_record" in note for note in scan(self.conn, self.cfg).notes) == 0)


if __name__ == "__main__":
    unittest.main()
