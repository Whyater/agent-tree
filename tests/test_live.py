import datetime
import threading
import unittest
from pathlib import Path
from unittest import mock

from agent_tree import db, indexer, live
from agent_tree.config import Config


class LiveWriteTests(unittest.TestCase):
    def test_concurrent_writers_use_distinct_staging_files(self):
        home = Path(self._tmpdir())
        index = home / "index.sqlite"
        conn = db.connect(index)
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        db.upsert_session(conn, "cx:T", source="codex", title="running")
        db.upsert_node(conn, "cx:T", session_id="cx:T", source="codex", kind="root",
                       started=now, status="running", model="gpt-6.1-sol", family="openai")
        db.upsert_node(conn, "cx:A", session_id="cx:T", source="codex", kind="subagent",
                       parent_hint="cx:T", started=now, status="running", model="gpt-6-luna", family="openai")
        conn.commit()
        cfg = Config(home, home / "claude", home / "codex", home / "t3", [])
        indexer.resolve(conn, cfg)
        conn.commit()

        paths = []
        original = live.os.replace
        barrier = threading.Barrier(2)

        def replace(src, dst):
            paths.append(src)
            barrier.wait(timeout=5)
            return original(src, dst)

        def run():
            c = db.connect(index)
            try:
                live.write(c, cfg)
            finally:
                c.close()

        with mock.patch.object(live.os, "replace", side_effect=replace):
            threads = [threading.Thread(target=run) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(len(paths), 2)
        self.assertEqual(len(set(paths)), 2)
        check = db.connect(index)
        try:
            self.assertEqual(live.build(check, cfg)["tree"]["thread_id"], "cx:T")
        finally:
            check.close()
        conn.close()

    def _tmpdir(self):
        import tempfile
        return tempfile.mkdtemp()
