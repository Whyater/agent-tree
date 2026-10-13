import json
import os
import tempfile
import unittest
from pathlib import Path

from agent_tree import db
from agent_tree.verify import run_checks
from tests.helpers import TempIndex


class VerifyTests(unittest.TestCase):
    def test_match_and_in_progress_skip(self):
        with TempIndex() as t:
            transcript = t.cfg.claude_dir / "session.jsonl"
            transcript.parent.mkdir(parents=True)
            transcript.write_text("x" * 10)
            db.put_usage(t.conn, "claude-code", "u1", "cc:s", ts=None, model="claude-opus-5-5",
                         input=10, cached_input=2, cache_write=3, output=4, raw_file=str(transcript))
            t.conn.commit()
            ud = t.cfg.t3_dir / "userdata"
            ud.mkdir(parents=True)
            (ud / "usage-scan-cache.json").write_text(json.dumps({
                "models": ["claude-opus-5-5"], "files": {
                    str(transcript): {"s": 10, "o": 10, "p": "claude", "r": [[0, 0, 0, 10, 2, 3, 4, 0, None, None, 0]]},
                    str(t.cfg.claude_dir / "partial.jsonl"): {"s": 20, "o": 4, "p": "claude", "r": [[0, 0, 0, 999, 0, 0, 0, 0, None, None, 0]]},
                }}))
            (ud / "usage-model-rates.json").write_text(json.dumps({"document": {
                "claude-opus-5-5": {"input_cost_per_token": 0.000004, "output_cost_per_token": 0.00002,
                                    "cache_read_input_token_cost": 0.0000002, "cache_creation_input_token_cost": 0.000005}}}))
            vp = t.tmp / "vorssaint"
            vp.mkdir()
            (vp / "agent-prices.json").write_text(json.dumps({"schema": 1, "claude": {"models": [{
                "id": "claude-opus-5-5", "input": 4, "output": 20, "cacheRead": 0.2, "cacheWrite": 5}]}}))
            old = os.environ.get("AGENT_TREE_VORSSAINT_DIR")
            os.environ["AGENT_TREE_VORSSAINT_DIR"] = str(vp)
            try:
                checks = {c["id"]: c for c in run_checks(t.conn, t.cfg)["checks"]}
            finally:
                if old is None:
                    os.environ.pop("AGENT_TREE_VORSSAINT_DIR", None)
                else:
                    os.environ["AGENT_TREE_VORSSAINT_DIR"] = old
            self.assertEqual(checks["t3-usage"]["status"], "match")
            self.assertEqual(checks["t3-usage"]["compared"], 1)
            self.assertEqual(checks["t3-prices"]["status"], "match")
            self.assertEqual(checks["vorssaint-prices"]["status"], "match")

    def test_mismatch_and_missing_unavailable(self):
        with TempIndex() as t:
            old = os.environ.get("AGENT_TREE_VORSSAINT_DIR")
            os.environ["AGENT_TREE_VORSSAINT_DIR"] = str(t.tmp / "missing-vorssaint")
            p = t.cfg.claude_dir / "x.jsonl"
            p.parent.mkdir(parents=True)
            p.write_text("x")
            db.put_usage(t.conn, "claude-code", "u", "cc:s", ts=None, model="claude-opus-5-5", input=1, output=1, raw_file=str(p))
            t.conn.commit()
            ud = t.cfg.t3_dir / "userdata"
            ud.mkdir(parents=True)
            (ud / "usage-scan-cache.json").write_text(json.dumps({"models": ["claude-opus-5-5"], "files": {
                str(p): {"s": 1, "o": 1, "p": "claude", "r": [[0, 0, 0, 99, 0, 0, 1, 0, None, None, 0]]}}}))
            try:
                checks = {c["id"]: c for c in run_checks(t.conn, t.cfg)["checks"]}
            finally:
                if old is None:
                    os.environ.pop("AGENT_TREE_VORSSAINT_DIR", None)
                else:
                    os.environ["AGENT_TREE_VORSSAINT_DIR"] = old
            self.assertEqual(checks["t3-usage"]["status"], "mismatch")
            self.assertEqual(checks["t3-prices"]["status"], "unavailable")
            self.assertEqual(checks["vorssaint-prices"]["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
