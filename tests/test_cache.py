import threading
import time
import unittest

from agent_tree import db
from agent_tree.cache import ReadCache


class ReadCacheTests(unittest.TestCase):
    def test_generation_change_invalidates_cached_value(self):
        cache = ReadCache()
        calls = []

        def compute():
            calls.append(1)
            return len(calls)

        self.assertEqual(cache.get_or_compute(("summary", "gen-1", "2026-10-14"), compute), 1)
        self.assertEqual(cache.get_or_compute(("summary", "gen-1", "2026-10-14"), compute), 1)
        self.assertEqual(cache.get_or_compute(("summary", "gen-2", "2026-10-14"), compute), 2)
        self.assertEqual(len(calls), 2)

    def test_concurrent_identical_requests_compute_once(self):
        cache = ReadCache()
        started = threading.Event()
        release = threading.Event()
        calls = 0
        lock = threading.Lock()

        def compute():
            nonlocal calls
            with lock:
                calls += 1
            started.set()
            release.wait(2)
            return {"ok": True}

        results = []
        errors = []

        def request():
            try:
                results.append(cache.get_or_compute(("projects", "gen-1", "2026-10-14"), compute))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=request) for _ in range(16)]
        for thread in threads:
            thread.start()
        self.assertTrue(started.wait(1))
        time.sleep(0.02)
        release.set()
        for thread in threads:
            thread.join(2)
        self.assertEqual(errors, [])
        self.assertEqual(calls, 1)
        self.assertEqual(len(results), 16)

    def test_date_rollover_changes_key(self):
        cache = ReadCache()
        calls = []

        def compute():
            calls.append(len(calls) + 1)
            return calls[-1]

        key = lambda day: ("summary", "gen-1", day)
        self.assertEqual(cache.get_or_compute(key("2026-10-14"), compute), 1)
        self.assertEqual(cache.get_or_compute(key("2026-10-15"), compute), 2)
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()


class NoChangeScanTests(unittest.TestCase):
    def test_running_status_expires_without_new_files(self):
        # "Running" comes from the clock, so a scan with no changed files must
        # still retire an agent that went quiet.
        from agent_tree import indexer
        from .helpers import TempIndex

        with TempIndex() as t:
            db.init(t.conn)
            t.conn.execute(
                "INSERT INTO nodes (id, source, kind, status, started) VALUES (?, ?, ?, ?, ?)",
                ("cx:quiet", "codex", "root", "running", "2026-01-01T00:00:00Z"))
            t.conn.commit()
            indexer.scan(t.conn, t.cfg)
            status = t.conn.execute("SELECT status FROM nodes WHERE id = 'cx:quiet'").fetchone()[0]
            self.assertEqual(status, "stopped")
