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
        # A large counter drop starts a new segment; the usage before it is kept:
        # uncached (20-2)+(25-3)=40, cached 2+3, cache write 1+2, output 5+6, reasoning 1+1.
        self.assertEqual(self._usage("cx:reset")[:5], (40, 5, 3, 11, 2))
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



class CodexContinuationTests(unittest.TestCase):
    """Long threads continue in rollout-...-<id>_<suffix>.jsonl with the counter carrying on."""

    TID = "01a09262-0000-7000-8000-000000000001"

    def setUp(self):
        import tempfile
        from agent_tree.config import Config
        from agent_tree.db import connect
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.codex = root / "codex"
        self.cfg = Config(root / "home", root / "claude", self.codex, root / "t3", [])
        self.conn = connect(root / "index.sqlite")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _file(self, day, name, ordinal0, model, totals):
        path = self.codex / "sessions" / "2026" / "09" / day / name
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            {"timestamp": f"2026-09-{day}T00:00:00Z", "ordinal": ordinal0, "type": "session_meta",
             "payload": {"id": self.TID, "timestamp": "2026-09-11T00:00:00Z", "cwd": "/w"}},
            {"timestamp": f"2026-09-{day}T00:00:01Z", "ordinal": ordinal0 + 1, "type": "turn_context",
             "payload": {"model": model, "effort": "high"}},
        ]
        for i, (inp, cached, out) in enumerate(totals):
            lines.append({"timestamp": f"2026-09-{day}T00:0{i + 2}:00Z", "ordinal": ordinal0 + 2 + i, "type": "event_msg",
                          "payload": {"type": "token_count", "info": {"total_token_usage": {
                              "input_tokens": inp, "cached_input_tokens": cached, "output_tokens": out,
                              "reasoning_output_tokens": 0, "total_tokens": inp + out}}}})
        with path.open("w", encoding="utf-8") as fh:
            for line in lines:
                fh.write(json.dumps(line) + "\n")
        return path

    def _rows(self):
        return self.conn.execute(
            "SELECT substr(ts,1,10) d, model, SUM(input+cached_input) i, SUM(cached_input) c, SUM(output) o "
            "FROM usage WHERE node_id=? GROUP BY d, model ORDER BY d", (f"cx:{self.TID}",)).fetchall()

    def test_increments_keep_their_day_and_model(self):
        from agent_tree.sources.codex import scan
        self._file("11", f"rollout-2026-09-11T00-00-00-{self.TID}.jsonl", 0, "gpt-a", [(100, 50, 10), (300, 200, 30)])
        cont = self._file("15", f"rollout-2026-09-15T00-00-00-{self.TID}_01a0a4e7-0000-7000-8000-000000000002.jsonl",
                          500, "gpt-b", [(350, 220, 35), (900, 600, 90)])
        scan(self.conn, self.cfg)
        rows = [tuple(r) for r in self._rows()]
        self.assertEqual(rows, [("2026-09-11", "gpt-a", 300, 200, 30), ("2026-09-15", "gpt-b", 600, 400, 60)])
        node = self.conn.execute("SELECT started, raw_file FROM nodes WHERE id=?", (f"cx:{self.TID}",)).fetchone()
        self.assertTrue(node["started"].startswith("2026-09-11"))
        self.assertNotIn("_01a0a4e7", node["raw_file"])

        # Appending to the continuation adds only the increase.
        with cont.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"timestamp": "2026-09-16T00:00:00Z", "ordinal": 600, "type": "event_msg",
                                 "payload": {"type": "token_count", "info": {"total_token_usage": {
                                     "input_tokens": 1000, "cached_input_tokens": 650, "output_tokens": 95,
                                     "reasoning_output_tokens": 0, "total_tokens": 1095}}}}) + "\n")
        scan(self.conn, self.cfg)
        total = self.conn.execute("SELECT SUM(input+cached_input), SUM(output) FROM usage WHERE node_id=?", (f"cx:{self.TID}",)).fetchone()
        self.assertEqual(tuple(total), (1000, 95))

        # A full rebuild gives the same split.
        scan(self.conn, self.cfg, full=True)
        total = self.conn.execute("SELECT SUM(input+cached_input), SUM(output) FROM usage WHERE node_id=?", (f"cx:{self.TID}",)).fetchone()
        self.assertEqual(tuple(total), (1000, 95))
        self.assertEqual(len(self._rows()), 3)

    def _scan_total(self, full=False):
        from agent_tree.sources.codex import scan
        scan(self.conn, self.cfg, full=full)
        row = self.conn.execute("SELECT SUM(input+cached_input), SUM(output), COUNT(*) FROM usage WHERE node_id=?",
                                (f"cx:{self.TID}",)).fetchone()
        return row[0], row[1], row[2]

    def test_two_continuations_with_a_counter_dip(self):
        self._file("11", f"rollout-2026-09-11T00-00-00-{self.TID}.jsonl", 0, "gpt-a", [(100, 50, 10), (400, 200, 40)])
        # The counter dips by a few tokens at the start of the first continuation.
        self._file("12", f"rollout-2026-09-12T00-00-00-{self.TID}_s1.jsonl", 500, "gpt-a", [(398, 200, 40), (700, 300, 70)])
        self._file("13", f"rollout-2026-09-13T00-00-00-{self.TID}_s2.jsonl", 900, "gpt-b", [(698, 300, 70), (1000, 400, 100)])
        self.assertEqual(self._scan_total()[:2], (1000, 100))
        self.assertEqual(self._scan_total(full=True)[:2], (1000, 100))

    def test_files_sharing_byte_offsets_do_not_collide(self):
        # Same shape, so token_count lines sit at the same byte offsets in both files.
        self._file("11", f"rollout-2026-09-11T00-00-00-{self.TID}.jsonl", 0, "gpt-a", [(100, 50, 10), (300, 100, 30)])
        self._file("11", f"rollout-2026-09-11T09-00-00-{self.TID}_s1.jsonl", 0, "gpt-a", [(600, 200, 60), (900, 300, 90)])
        total, out, rows = self._scan_total()
        self.assertEqual((total, out, rows), (900, 90, 4))

    def test_split_across_sessions_and_archive(self):
        orig = self._file("11", f"rollout-2026-09-11T00-00-00-{self.TID}.jsonl", 0, "gpt-a", [(300, 100, 30)])
        cont = self._file("12", f"rollout-2026-09-12T00-00-00-{self.TID}_s1.jsonl", 500, "gpt-a", [(900, 300, 90)])
        archived = self.codex / "archived_sessions" / cont.name
        archived.parent.mkdir(parents=True)
        cont.rename(archived)
        self.assertEqual(self._scan_total(full=True)[:2], (900, 90))
        # A copy of the continuation in both places is still counted once.
        archived_copy = self.codex / "sessions" / "2026" / "09" / "12" / archived.name
        archived_copy.write_bytes(archived.read_bytes())
        self.assertEqual(self._scan_total(full=True)[:2], (900, 90))
        # And the original archived with the continuation live.
        archived_copy.unlink()
        orig.rename(self.codex / "archived_sessions" / orig.name)
        archived.rename(archived_copy)
        self.assertEqual(self._scan_total(full=True)[:2], (900, 90))

    def test_reset_inside_a_continuation_starts_a_new_baseline(self):
        self._file("11", f"rollout-2026-09-11T00-00-00-{self.TID}.jsonl", 0, "gpt-a", [(1000, 500, 100)])
        self._file("12", f"rollout-2026-09-12T00-00-00-{self.TID}_s1.jsonl", 500, "gpt-a", [(30, 10, 3), (60, 20, 6)])
        self.assertEqual(self._scan_total()[:2], (1060, 106))

    def test_reset_inside_one_file_keeps_the_earlier_segment(self):
        # Seen on real data: the counter restarts mid-file (2,083,896 -> 108,921). Both segments are real usage.
        self._file("11", f"rollout-2026-09-11T00-00-00-{self.TID}.jsonl", 0, "gpt-a",
                   [(1000, 400, 100), (2000, 800, 200), (100, 40, 10), (300, 120, 30)])
        self.assertEqual(self._scan_total()[:2], (2000 + 300, 200 + 30))


if __name__ == "__main__":
    unittest.main()
