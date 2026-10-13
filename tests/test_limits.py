import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from agent_tree.config import Config
from agent_tree.limits import read_limits


class LimitsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cfg = Config(root / "home", root / "claude", root / "codex", root / "t3", [])
        self.cfg.home.mkdir(parents=True)
        self.now = datetime(2026, 10, 13, 12, tzinfo=timezone.utc).timestamp()
        # Never read the real Claude app folder from tests.
        self.app_dir = root / "claude-app"
        self._env = os.environ.get("AGENT_TREE_CLAUDE_APP_DIR")
        os.environ["AGENT_TREE_CLAUDE_APP_DIR"] = str(self.app_dir)

    def tearDown(self):
        if self._env is None:
            os.environ.pop("AGENT_TREE_CLAUDE_APP_DIR", None)
        else:
            os.environ["AGENT_TREE_CLAUDE_APP_DIR"] = self._env
        self.tmp.cleanup()

    def _write(self, path, records):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for record in records:
                fh.write(record if isinstance(record, str) else json.dumps(record))
                fh.write("\n")

    def _rate(self, timestamp, primary, secondary=None, plan="prolite"):
        return {
            "timestamp": timestamp,
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {},
                "rate_limits": {
                    "limit_id": "codex", "primary": primary, "secondary": secondary,
                    "plan_type": plan,
                },
            },
        }

    def test_codex_reads_recent_tail_maps_windows_and_ignores_expired(self):
        sessions = self.cfg.codex_dir / "sessions" / "2026" / "10" / "13"
        old = sessions / "rollout-old.jsonl"
        newest = sessions / "rollout-new.jsonl"
        self._write(old, [self._rate("2026-10-13T08:00:00Z", {
            "used_percent": 4, "window_minutes": 10080, "resets_at": 1792472304,
        })])
        # Include enough preceding data to exercise bounded tail reads.
        filler = json.dumps({"type": "noise", "payload": "x" * 4096})
        self._write(newest, [filler] * 80 + ["{malformed", self._rate(
            "2026-10-13T10:11:12.861Z",
            {"used_percent": 12.5, "window_minutes": 300, "resets_at": 1700000000},
            {"used_percent": 48, "window_minutes": 10080, "resets_at": 1792472304},
        )])
        # A newer mtime should win even when file names sort differently.
        old.touch()
        newest.touch()
        result = read_limits(self.cfg, now=self.now)
        codex = result["providers"][1]
        self.assertTrue(codex["available"])
        self.assertEqual(codex["provider"], "codex")
        self.assertEqual(codex["plan"], "prolite")
        self.assertEqual(codex["as_of"], "2026-10-13T10:11:12.861Z")
        self.assertFalse(codex["stale"])
        self.assertEqual(codex["source"], "Codex session logs")
        self.assertEqual(codex["reason"], None)
        self.assertEqual(codex["windows"], [
            {"id": "seven_day", "label": "Week", "used_percent": 48,
             "window_minutes": 10080, "resets_at": "2026-10-20T04:58:24.000Z"},
        ])

    def test_codex_archived_files_and_expired_snapshot(self):
        path = self.cfg.codex_dir / "archived_sessions" / "rollout-archived.jsonl"
        self._write(path, [self._rate("2026-10-13T11:00:00Z", {
            "used_percent": 99, "window_minutes": 300, "resets_at": 1700000000,
        })])
        result = read_limits(self.cfg, now=self.now)["providers"][1]
        self.assertTrue(result["available"])
        self.assertEqual(result["windows"], [])
        self.assertEqual(result["reason"], "Limits reset since Codex last reported; they'll update on your next Codex turn.")

    def test_missing_sources_and_claude_snapshot(self):
        claude = self.cfg.home / "claude-limits.json"
        claude.write_text(json.dumps({
            "captured_at": "2026-10-13T11:00:00.000Z",
            "five_hour": {"used_percentage": 12.0, "resets_at": 1700000000},
            "seven_day": {"used_percentage": 48.0, "resets_at": 1792472304},
            "plan": None,
        }), encoding="utf-8")
        result = read_limits(self.cfg, now=self.now)
        claude_result, codex_result = result["providers"]
        self.assertEqual(claude_result["provider"], "claude")
        self.assertTrue(claude_result["available"])
        self.assertEqual([w["id"] for w in claude_result["windows"]], ["seven_day"])
        self.assertEqual(claude_result["windows"][0]["label"], "Week")
        self.assertFalse(codex_result["available"])
        self.assertEqual(codex_result["reason"], "No Codex limit data found yet.")

    def test_malformed_claude_file_is_unavailable(self):
        (self.cfg.home / "claude-limits.json").write_text("not json", encoding="utf-8")
        claude = read_limits(self.cfg, now=self.now)["providers"][0]
        self.assertFalse(claude["available"])
        self.assertEqual(claude["windows"], [])


class ClaudeAppLimitsTests(unittest.TestCase):
    def test_claude_app_history_with_estimated_resets(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cfg = Config(root / "home", root / ".claude", root / "codex", root / "t3", [])
            (root / ".claude.json").write_text(json.dumps({"oauthAccount": {
                "organizationType": "claude_max", "organizationRateLimitTier": "default_claude_max_20x",
                "emailAddress": "someone@example.com"}}))
            app = root / "app"
            app.mkdir()
            hour = 3600 * 1000
            t0 = datetime(2026, 10, 6, 14, 5, tzinfo=timezone.utc).timestamp() * 1000
            samples = [
                {"t": t0 - hour, "u": {"fh": 0, "sd": 90}},
                {"t": t0, "u": {"fh": 0, "sd": 0}},              # weekly reset seen at 14:05 -> 14:00 cadence
                {"t": t0 + 6 * 24 * hour, "u": {"fh": 10, "sd": 40}},  # session run starts here
                {"t": t0 + 6 * 24 * hour + 2 * hour, "u": {"fh": 35, "sd": 52}},
            ]
            (app / "plan-usage-history.json").write_text(json.dumps({"version": 2, "samples": samples}))
            os.environ["AGENT_TREE_CLAUDE_APP_DIR"] = str(app)
            try:
                now = (t0 + 6 * 24 * hour + 3 * hour) / 1000
                claude = read_limits(cfg, now=now)["providers"][0]
            finally:
                os.environ.pop("AGENT_TREE_CLAUDE_APP_DIR", None)
            self.assertTrue(claude["available"])
            self.assertEqual(claude["plan"], "Max 20x")
            self.assertNotIn("someone@example.com", json.dumps(claude))
            w = {x["id"]: x for x in claude["windows"]}
            self.assertEqual(w["five_hour"]["used_percent"], 35.0)
            self.assertEqual(w["five_hour"]["resets_at"], "2026-10-12T19:05:00.000Z")  # run start + 5 h
            self.assertEqual(w["seven_day"]["used_percent"], 52.0)
            self.assertEqual(w["seven_day"]["resets_at"], "2026-10-13T14:00:00.000Z")  # last drop hour + 7 d
            self.assertTrue(w["seven_day"]["resets_estimated"])


if __name__ == "__main__":
    unittest.main()
