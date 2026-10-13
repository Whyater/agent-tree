import json
import tempfile
import unittest
from pathlib import Path

from agent_tree import integrations

from .helpers import make_config

ORIGINAL = {
    "theme": "dark",
    "env": {"X": "1"},
    "hooks": {"SubagentStart": [{"hooks": [{"type": "command", "command": "echo other-tool"}]}],
              "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "guard.sh"}]}]},
}


class ClaudeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_config(Path(self.tmp.name))
        self.cfg.claude_dir.mkdir(parents=True)
        self.settings = self.cfg.claude_dir / "settings.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_connect_preserves_other_settings_and_disconnect_restores(self):
        self.settings.write_text(json.dumps(ORIGINAL, indent=2))
        res = integrations.connect_claude(self.cfg)
        self.assertTrue(res["ok"])
        self.assertTrue(Path(res["backup"]).exists())
        data = json.loads(self.settings.read_text())
        self.assertEqual(data["theme"], "dark")
        self.assertEqual(data["hooks"]["PreToolUse"], ORIGINAL["hooks"]["PreToolUse"])
        self.assertEqual(data["hooks"]["SubagentStart"][0], ORIGINAL["hooks"]["SubagentStart"][0])
        self.assertIn("-m agent_tree.hook", data["hooks"]["SubagentStart"][1]["hooks"][0]["command"])
        self.assertIn("-m agent_tree.statusline", data["statusLine"]["command"])
        self.assertTrue(integrations.status(self.cfg)["claude"]["connected"])
        # Connecting twice changes nothing.
        integrations.connect_claude(self.cfg)
        self.assertEqual(len(json.loads(self.settings.read_text())["hooks"]["SubagentStop"]), 1)
        integrations.disconnect_claude(self.cfg)
        self.assertEqual(json.loads(self.settings.read_text()), ORIGINAL)

    def test_existing_status_line_is_kept(self):
        self.settings.write_text(json.dumps({"statusLine": {"type": "command", "command": "my-line.sh"}}))
        res = integrations.connect_claude(self.cfg)
        self.assertTrue(res["ok"])
        self.assertIn("Kept your existing status line", res["note"])
        self.assertEqual(json.loads(self.settings.read_text())["statusLine"]["command"], "my-line.sh")
        integrations.disconnect_claude(self.cfg)
        self.assertEqual(json.loads(self.settings.read_text()), {"statusLine": {"type": "command", "command": "my-line.sh"}})

    def test_broken_settings_are_left_alone(self):
        self.settings.write_text("{not json")
        res = integrations.connect_claude(self.cfg)
        self.assertFalse(res["ok"])
        self.assertEqual(self.settings.read_text(), "{not json")

    def test_no_settings_file_yet(self):
        res = integrations.connect_claude(self.cfg)
        self.assertTrue(res["ok"])
        self.assertIsNone(res["backup"])
        integrations.disconnect_claude(self.cfg)
        self.assertEqual(json.loads(self.settings.read_text()), {})
