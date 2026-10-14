import unittest
from io import BytesIO
from pathlib import Path

from agent_tree.config import Config
from agent_tree.server import ScanState, make_handler


class HostHeaderStressTests(unittest.TestCase):
    def test_rejects_non_numeric_ports(self):
        cfg = Config(Path("/tmp/agent-tree-test-home"), Path("/tmp/c"), Path("/tmp/x"), Path("/tmp/t"), [])
        handler = make_handler(cfg, ScanState())
        request = object.__new__(handler)
        for bad in ("localhost:abc", "127.0.0.1:abc", "[::1]:x1", "[::1", "localhost:", "localhost:99999",
                    "evil.com:8711", "localhost:\u0661\u0662"):
            request.headers = {"Host": bad}
            self.assertFalse(request._host_ok(), bad)
        for good in ("localhost", "127.0.0.1:8711", "[::1]", "[::1]:8711"):
            request.headers = {"Host": good}
            self.assertTrue(request._host_ok(), good)

    def test_rejects_malformed_content_length(self):
        cfg = Config(Path("/tmp/agent-tree-test-home"), Path("/tmp/c"), Path("/tmp/x"), Path("/tmp/t"), [])
        handler = make_handler(cfg, ScanState())
        request = object.__new__(handler)
        request.headers = {"Host": "localhost", "X-Agent-Tree": "1", "Content-Length": "nope"}
        request.path = "/api/scan"
        request.rfile = BytesIO(b"{}")
        errors = []
        request._error = lambda status, message: errors.append((status, message))
        request._host_ok = lambda: True
        request.do_POST()
        self.assertEqual(errors, [(400, "invalid Content-Length")])


if __name__ == "__main__":
    unittest.main()
