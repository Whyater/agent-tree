import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StatuslineTests(unittest.TestCase):
    def run_statusline(self, home, payload):
        env = os.environ.copy()
        env["AGENT_TREE_HOME"] = str(home)
        return subprocess.run(
            [sys.executable, "-m", "agent_tree.statusline"],
            input=payload,
            text=True,
            capture_output=True,
            cwd=ROOT,
            env=env,
            timeout=5,
        )

    def test_captures_windows_atomically_and_prints_short_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            payload = json.dumps({
                "rate_limits": {
                    "five_hour": {"used_percentage": 12.0, "resets_at": 1792000000},
                    "seven_day": {"used_percentage": 48, "resets_at": 1792472304},
                },
                "model": {"display_name": "Opus 5.5"},
                "workspace": {"current_dir": "/private/should-not-be-stored"},
            })
            proc = self.run_statusline(home, payload)
            self.assertEqual(proc.returncode, 0)
            self.assertEqual(proc.stdout.strip(), "Opus 5.5 · session 12% · week 48%")
            self.assertEqual(proc.stderr, "")
            saved = json.loads((home / "claude-limits.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["five_hour"], {"used_percentage": 12.0, "resets_at": 1792000000})
            self.assertEqual(saved["seven_day"], {"used_percentage": 48, "resets_at": 1792472304})
            self.assertIn("captured_at", saved)
            self.assertNotIn("workspace", json.dumps(saved))
            self.assertNotIn("Opus", json.dumps(saved))

    def test_missing_rate_limits_keeps_previous_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            first = self.run_statusline(home, json.dumps({
                "rate_limits": {"five_hour": {"used_percentage": 2, "resets_at": 1792}},
                "model": {"display_name": "Sonnet"},
            }))
            self.assertEqual(first.returncode, 0)
            before = (home / "claude-limits.json").read_text(encoding="utf-8")
            second = self.run_statusline(home, json.dumps({"model": {"display_name": "Sonnet"}}))
            self.assertEqual(second.returncode, 0)
            self.assertEqual((home / "claude-limits.json").read_text(encoding="utf-8"), before)

    def test_garbage_input_never_crashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = self.run_statusline(Path(tmp), "not json\n")
            self.assertEqual(proc.returncode, 0)
            self.assertLessEqual(len(proc.stdout.strip()), 80)


if __name__ == "__main__":
    unittest.main()
