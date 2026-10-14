"""Tests for the parts that don't depend on a specific source: privacy, pricing,
tree resolution, rollups, flags, queries, exports and the HTTP guards."""

import json
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer

from agent_tree import db, export, indexer, queries
from agent_tree.pricing import PriceBook, load_prices
from agent_tree.privacy import TEXT_LIMIT, clean_text, is_secret_path, looks_secret

from .helpers import TempIndex


class PrivacyTests(unittest.TestCase):
    def test_future_timestamp_is_not_recent(self):
        now = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
        self.assertFalse(indexer._recent("2026-10-14T12:30:00Z", now))
        # A minute of skew between tools is normal and still counts as recent.
        self.assertTrue(indexer._recent("2026-10-14T12:01:00Z", now))

    def test_truncates_to_limit(self):
        out = clean_text("word " * 100)
        self.assertLessEqual(len(out), TEXT_LIMIT)
        self.assertTrue(out.endswith("…"))

    def test_limit_can_only_shrink(self):
        self.assertLessEqual(len(clean_text("x" * 500, limit=10_000)), TEXT_LIMIT)

    def test_redacts_keys_before_truncating(self):
        for secret in ("sk-ant-api03-" + "a" * 40, "ghp_" + "b" * 36, "AKIA" + "C" * 16,
                       "Bearer " + "d" * 30, "api_key=" + "e" * 20, "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghij"):
            out = clean_text(f"use {secret} please")
            self.assertNotIn(secret, out)
            self.assertIn("[redacted]", out)
            self.assertFalse(looks_secret(out))

    def test_metadata_only(self):
        self.assertIsNone(clean_text("anything", metadata_only=True))

    def test_whitespace_and_empty(self):
        self.assertEqual(clean_text("  a \n\t b  "), "a b")
        self.assertIsNone(clean_text("   "))
        self.assertIsNone(clean_text(None))

    def test_secret_paths(self):
        for p in ("/x/.t3/userdata/secrets/k", "/x/clerk-tokens.json", "/x/auth.json", "/p/.env", "/p/.env.local",
                  "/p/credentials.json", "/p/server.pem"):
            self.assertTrue(is_secret_path(p), p)
        for p in ("/x/.claude/projects/a/b.jsonl", "/x/state.sqlite", "/x/secretary/notes.jsonl"):
            self.assertFalse(is_secret_path(p), p)


class PricingTests(unittest.TestCase):
    book = PriceBook({"models": {
        "claude-opus-5-5": {"input": 4, "cached_input": 0.2, "cache_write": 5, "output": 20, "aliases": ["opus"]},
        "gpt-6.1-sol": {"input": 2, "cached_input": 0.1, "output": 10, "long_context_threshold": 272000,
                        "long_input": 4, "long_cached_input": 0.2, "long_output": 15},
        "broken": {"input": "x"},
    }})

    def test_basic_cost(self):
        self.assertAlmostEqual(self.book.cost("claude-opus-5-5", 1_000_000, 0, 0, 1_000_000), 24.0)
        self.assertAlmostEqual(self.book.cost("claude-opus-5-5", 0, 1_000_000, 1_000_000, 0), 5.2)

    def test_aliases_prefixes_and_dates(self):
        self.assertIsNotNone(self.book.lookup("opus"))
        self.assertIsNotNone(self.book.lookup("anthropic/claude-opus-5-5"))
        self.assertIsNotNone(self.book.lookup("claude-opus-5-5-20261001"))

    def test_unknown_and_broken_rows(self):
        self.assertIsNone(self.book.cost("mystery-model", 10, 0, 0, 10))
        self.assertIsNone(self.book.cost("broken", 10, 0, 0, 10))
        self.assertIsNone(self.book.cost(None, 10, 0, 0, 10))

    def test_long_context_tier(self):
        short = self.book.cost("gpt-6.1-sol", 200_000, 0, 0, 1000)
        long_ = self.book.cost("gpt-6.1-sol", 300_000, 0, 0, 1000)
        self.assertAlmostEqual(short, (200_000 * 2 + 1000 * 10) / 1e6)
        self.assertAlmostEqual(long_, (300_000 * 4 + 1000 * 15) / 1e6)

    def test_bundled_prices_parse(self):
        with TempIndex() as t:
            book = load_prices(t.cfg)
            for m in ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1",
                      "gpt-6.1-sol", "gpt-6-luna", "gpt-6-astra"):
                self.assertIsNotNone(book.lookup(m), m)


def seed_tree(conn):
    """A Claude root with two subagents (one nested), a wrapper run under one of
    them leading to a Codex session with a child, a T3 thread wrapping the root,
    and an orphan subagent."""
    up = db.upsert_node
    up(conn, "t3:T", session_id="t3:T", source="t3", kind="t3-thread", started="2026-10-10T00:00:00.000Z")
    db.upsert_session(conn, "t3:T", source="t3", title="T3 title")
    up(conn, "cc:S", session_id="cc:S", source="claude-code", kind="root", model="claude-opus-5-5", family="anthropic",
       started="2026-10-10T00:00:01.000Z", ended="2026-10-10T01:00:00.000Z", project_path="/p/demo")
    db.upsert_session(conn, "cc:S", source="claude-code", title="Session title", project_path="/p/demo")
    db.put_link(conn, "t3-session", "t3:T", "cc:S", "exact")
    up(conn, "cc-agent:A", session_id="cc:S", source="claude-code", kind="subagent", parent_hint="toolu_A",
       description="Build the parser", model="claude-sonnet-5-5", family="anthropic",
       started="2026-10-10T00:01:00.000Z", ended="2026-10-10T00:02:00.000Z")
    db.put_spawn(conn, "toolu_A", "cc:S", child_hint="A", status="completed")
    up(conn, "cc-agent:B", session_id="cc:S", source="claude-code", kind="subagent", parent_hint="toolu_B",
       description="Critic review of the parser", model="claude-haiku-5-5", family="anthropic",
       started="2026-10-10T00:03:00.000Z", ended="2026-10-10T00:04:00.000Z", extra_json={"spawn_depth": 2})
    db.put_spawn(conn, "toolu_B", "cc-agent:A")
    up(conn, "cc-agent:O", session_id="cc:S", source="claude-code", kind="subagent", parent_hint="toolu_missing",
       description="Lost one", model="claude-haiku-5-5", family="anthropic", extra_json={"spawn_depth": 3},
       started="2026-10-10T00:05:00.000Z", ended="2026-10-10T00:06:00.000Z")
    up(conn, "wr:/tmp/codex-tasks/job.md", source="wrapper", kind="wrapper", parent_hint="wrapper-caller:/tmp/codex-tasks/job.md",
       description="job", started="2026-10-10T00:10:00.000Z", ended="2026-10-10T00:20:00.000Z")
    db.put_link(conn, "wrapper-caller", "/tmp/codex-tasks/job.md", "cc-agent:A", "exact")
    db.put_link(conn, "wrapper-codex", "wr:/tmp/codex-tasks/job.md", "cx:C", "exact")
    up(conn, "cx:C", session_id="cx:C", source="codex", kind="root", model="gpt-6.1-sol", family="openai",
       description="design the dashboard layout", started="2026-10-10T00:10:05.000Z", ended="2026-10-10T00:19:00.000Z")
    up(conn, "cx:D", session_id="cx:D", source="codex", kind="subagent", parent_hint="cx:C", model="gpt-6-luna", family="openai",
       description="/root/scan", started="2026-10-10T00:11:00.000Z", ended="2026-10-10T00:12:00.000Z")
    up(conn, "t3-task:T:x", source="t3", kind="t3-task", session_id="t3:T", parent_hint="t3:T", description="dup of A")
    db.put_link(conn, "t3-task", "t3-task:T:x", "toolu:toolu_A", "exact")
    u = db.put_usage
    u(conn, "claude-code", "cc:r1", "cc:S", ts="2026-10-10T00:00:02.000Z", model="claude-opus-5-5", input=1000, cached_input=50_000, cache_write=2000, output=500)
    u(conn, "claude-code", "cc:r2", "cc-agent:A", ts="2026-10-10T00:01:30.000Z", model="claude-sonnet-5-5", input=100, output=100)
    u(conn, "claude-code", "cc:r3", "cc-agent:B", ts="2026-10-10T00:03:30.000Z", model="claude-haiku-5-5", input=10, output=10)
    u(conn, "codex", "cx:1", "cx:C", ts="2026-10-10T00:15:00.000Z", model="gpt-6.1-sol", input=3000, cached_input=7000, output=400, reasoning=100)
    u(conn, "codex", "cx:2", "cx:D", ts="2026-10-10T00:11:30.000Z", model="mystery-model", input=10, output=10)
    conn.commit()


class ResolveTests(unittest.TestCase):
    def test_tree_shape_and_confidence(self):
        with TempIndex() as t:
            seed_tree(t.conn)
            indexer.resolve(t.conn, t.cfg)
            n = {r["id"]: dict(r) for r in t.conn.execute("SELECT * FROM nodes")}
            self.assertEqual(n["cc:S"]["parent_id"], "t3:T")
            self.assertEqual(n["cc-agent:A"]["parent_id"], "cc:S")
            self.assertEqual(n["cc-agent:B"]["parent_id"], "cc-agent:A")
            self.assertEqual(n["cc-agent:B"]["depth"], 3)
            self.assertEqual(n["wr:/tmp/codex-tasks/job.md"]["parent_id"], "cc-agent:A")
            self.assertEqual(n["cx:C"]["parent_id"], "wr:/tmp/codex-tasks/job.md")
            self.assertEqual(n["cx:D"]["depth"], 5)
            self.assertEqual(n["cc-agent:O"]["link_confidence"], "orphan")
            self.assertEqual(n["cc-agent:O"]["parent_id"], "cc:S")
            self.assertIsNone(n["t3-task:T:x"]["tree_id"], "T3 task duplicating a Claude subagent is merged away")
            self.assertEqual({r["tree_id"] for r in n.values() if r["tree_id"]}, {"t3:T"})

    def test_rollups_match_usage(self):
        with TempIndex() as t:
            seed_tree(t.conn)
            indexer.resolve(t.conn, t.cfg)
            root = dict(t.conn.execute("SELECT * FROM node_totals WHERE node_id='t3:T'").fetchone())
            u = t.conn.execute("SELECT SUM(input), SUM(cached_input), SUM(cache_write), SUM(output) FROM usage").fetchone()
            self.assertEqual((root["sub_input"], root["sub_cached"], root["sub_cache_write"], root["sub_output"]), tuple(u))
            self.assertEqual(root["sub_unpriced"], 1)
            self.assertEqual(root["sub_nodes"], 8)
            a = dict(t.conn.execute("SELECT * FROM node_totals WHERE node_id='cc-agent:A'").fetchone())
            self.assertEqual(a["self_input"], 100)
            self.assertEqual(a["sub_input"], 100 + 10 + 3000 + 10)

    def test_covered_by_plan_moves_cost(self):
        with TempIndex(covered_by_plan={"openai": True}) as t:
            seed_tree(t.conn)
            indexer.resolve(t.conn, t.cfg)
            c = dict(t.conn.execute("SELECT * FROM node_totals WHERE node_id='cx:C'").fetchone())
            self.assertEqual(c["self_cost"], 0)
            self.assertGreater(c["self_covered"], 0)

    def test_cycle_is_broken(self):
        with TempIndex() as t:
            db.upsert_node(t.conn, "cx:X", source="codex", kind="subagent", parent_hint="cx:Y")
            db.upsert_node(t.conn, "cx:Y", source="codex", kind="subagent", parent_hint="cx:X")
            indexer.resolve(t.conn, t.cfg)
            rows = t.conn.execute("SELECT id, tree_id, link_confidence FROM nodes").fetchall()
            self.assertTrue(all(r["tree_id"] for r in rows))
            self.assertIn("orphan", {r["link_confidence"] for r in rows})


def add_builder(conn):
    """A Claude builder that did its own work, just before the critics in these tests."""
    db.upsert_node(conn, "cc-agent:BLD", session_id="cc:S", source="claude-code", kind="subagent", parent_hint="toolu_BLD",
                   description="Build the exporter", model="claude-sonnet-5-5", family="anthropic",
                   started="2026-10-10T00:02:10.000Z", ended="2026-10-10T00:02:20.000Z")
    db.put_spawn(conn, "toolu_BLD", "cc:S")
    db.put_usage(conn, "claude-code", "cc:bld", "cc-agent:BLD", ts="2026-10-10T00:02:15.000Z",
                 model="claude-sonnet-5-5", input=500, output=500)


class FlagTests(unittest.TestCase):
    def test_rules_fire(self):
        with TempIndex() as t:
            seed_tree(t.conn)
            db.upsert_node(t.conn, "cx:E", source="codex", kind="subagent", parent_hint="cx:C", model="gpt-6-astra",
                           family="openai", description="/root/scan again", started="2026-10-10T00:12:00.000Z",
                           ended="2026-10-10T01:12:00.000Z")
            db.upsert_node(t.conn, "cx:F", source="codex", kind="subagent", parent_hint="cx:C", model="gpt-6-luna",
                           family="openai", description="/root/scan again", started="2026-10-10T00:13:00.000Z",
                           ended="2026-10-10T00:14:00.000Z")
            db.upsert_node(t.conn, "cc-agent:R", session_id="cc:S", source="claude-code", kind="subagent",
                           parent_hint="toolu_R", description="Critic: review the parser", model="claude-opus-5-5",
                           family="anthropic", started="2026-10-10T00:02:30.000Z", ended="2026-10-10T00:02:50.000Z")
            db.put_spawn(t.conn, "toolu_R", "cc:S")
            add_builder(t.conn)
            indexer.resolve(t.conn, t.cfg)
            got = {(r["rule"], r["node_id"]) for r in t.conn.execute("SELECT rule, node_id FROM flags")}
            self.assertIn(("depth", "cx:D"), got)
            self.assertIn(("orphan", "cc-agent:O"), got)
            self.assertIn(("blocked_model", "cx:E"), got)
            self.assertIn(("long_running", "cx:E"), got)
            self.assertIn(("duplicate_tasks", "cx:F"), got)
            self.assertIn(("same_family_critic", "cc-agent:R"), got)
            heur = {r["rule"]: r["heuristic"] for r in t.conn.execute("SELECT rule, heuristic FROM flags")}
            self.assertEqual(heur["duplicate_tasks"], 1)
            self.assertEqual(heur["depth"], 0)
            for (msg,) in t.conn.execute("SELECT message FROM flags"):
                self.assertLessEqual(len(msg), 200)

    def test_critic_judged_by_the_model_that_ran_under_it(self):
        # A small Claude agent that hands the review to Codex is an OpenAI critic.
        with TempIndex() as t:
            seed_tree(t.conn)
            add_builder(t.conn)
            up = db.upsert_node
            up(t.conn, "cc-agent:R", session_id="cc:S", source="claude-code", kind="subagent", parent_hint="toolu_R",
               description="Critic: review the parser", model="claude-haiku-5-5", family="anthropic",
               started="2026-10-10T00:02:30.000Z", ended="2026-10-10T00:09:00.000Z")
            db.put_spawn(t.conn, "toolu_R", "cc:S")
            up(t.conn, "wr:/tmp/codex-tasks/review.md", source="wrapper", kind="wrapper",
               parent_hint="wrapper-caller:/tmp/codex-tasks/review.md", description="review",
               started="2026-10-10T00:03:00.000Z", ended="2026-10-10T00:08:00.000Z")
            db.put_link(t.conn, "wrapper-caller", "/tmp/codex-tasks/review.md", "cc-agent:R", "exact")
            db.put_link(t.conn, "wrapper-codex", "wr:/tmp/codex-tasks/review.md", "cx:RV", "exact")
            up(t.conn, "cx:RV", session_id="cx:RV", source="codex", kind="root", model="gpt-6.1-sol", family="openai",
               started="2026-10-10T00:03:05.000Z", ended="2026-10-10T00:07:00.000Z")
            db.put_usage(t.conn, "claude-code", "cc:rr", "cc-agent:R", ts="2026-10-10T00:02:40.000Z",
                         model="claude-haiku-5-5", input=50, output=20)
            db.put_usage(t.conn, "codex", "cx:rv", "cx:RV", ts="2026-10-10T00:05:00.000Z",
                         model="gpt-6.1-sol", input=40_000, output=2000)
            t.conn.commit()
            indexer.resolve(t.conn, t.cfg)
            got = {(r["rule"], r["node_id"]) for r in t.conn.execute("SELECT rule, node_id FROM flags")}
            self.assertNotIn(("same_family_critic", "cc-agent:R"), got)

    def test_disabled_rule(self):
        with TempIndex() as t:
            (t.cfg.home / "rules.toml").write_text("[orphan]\nenabled = false\n")
            seed_tree(t.conn)
            indexer.resolve(t.conn, t.cfg)
            self.assertEqual(t.conn.execute("SELECT COUNT(*) FROM flags WHERE rule='orphan'").fetchone()[0], 0)


class QueryAndExportTests(unittest.TestCase):
    def test_thread_detail_and_exports(self):
        with TempIndex() as t:
            seed_tree(t.conn)
            indexer.resolve(t.conn, t.cfg)
            lst = queries.list_threads(t.conn, t.cfg)
            self.assertEqual(lst["total"], 1)
            th = lst["threads"][0]
            self.assertEqual(th["title"], "T3 title")
            self.assertEqual(th["sources"][0], "t3")
            d = queries.thread_detail(t.conn, t.cfg, "cc:S")  # any node id resolves to its tree
            self.assertEqual(d["thread"]["id"], "t3:T")
            ids = [n["id"] for n in d["nodes"]]
            self.assertEqual(ids[0], "t3:T")
            self.assertLess(ids.index("cc-agent:A"), ids.index("cc-agent:B"))
            md = export.thread_markdown(d)
            self.assertIn("Build the parser", md)
            csv_text = export.nodes_csv(d["nodes"], d["thread"]["id"])
            self.assertEqual(len(csv_text.strip().splitlines()), len(d["nodes"]) + 1)
            self.assertIsNone(queries.thread_detail(t.conn, t.cfg, "nope"))

    def test_relative_dates(self):
        with TempIndex() as t:
            self.assertIsNone(queries.parse_since("all", t.cfg))
            self.assertTrue(queries.parse_since("7d", t.cfg).endswith("Z"))
            self.assertTrue(queries.parse_since("2026-10-01", t.cfg).endswith("Z"))
            with self.assertRaises(ValueError):
                queries.parse_since("last tuesday", t.cfg)


class ServerTests(unittest.TestCase):
    def test_guards_and_endpoints(self):
        from agent_tree.server import ScanState, make_handler
        with TempIndex() as t:
            seed_tree(t.conn)
            indexer.resolve(t.conn, t.cfg)
            t.conn.commit()
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(t.cfg, ScanState()))
            port = httpd.server_address[1]
            th = threading.Thread(target=httpd.serve_forever, daemon=True)
            th.start()
            try:
                base = f"http://127.0.0.1:{port}"
                for path in ("/api/meta", "/api/summary", "/api/threads", "/api/projects", "/api/models", "/api/flags",
                             "/api/threads/t3%3AT", "/api/export/thread/t3%3AT.md"):
                    with urllib.request.urlopen(base + path) as r:
                        self.assertEqual(r.status, 200, path)
                with urllib.request.urlopen(base + "/api/threads/t3%3AT") as r:
                    self.assertEqual(json.load(r)["thread"]["id"], "t3:T")
                req = urllib.request.Request(base + "/api/meta", headers={"Host": "evil.example"})
                with self.assertRaises(urllib.error.HTTPError) as e:
                    urllib.request.urlopen(req)
                self.assertEqual(e.exception.code, 403)
                req = urllib.request.Request(base + "/api/scan", data=b"{}", method="POST")
                with self.assertRaises(urllib.error.HTTPError) as e:
                    urllib.request.urlopen(req)
                self.assertEqual(e.exception.code, 403)
                with self.assertRaises(urllib.error.HTTPError) as e:
                    urllib.request.urlopen(base + "/api/threads/missing")
                self.assertEqual(e.exception.code, 404)
                with urllib.request.urlopen(base + "/../../etc/passwd") as r:
                    self.assertNotIn(b"root:", r.read())
            finally:
                httpd.shutdown()
                httpd.server_close()



class ReviewRegressionTests(unittest.TestCase):
    def test_cycle_members_still_get_rollups(self):
        with TempIndex() as t:
            db.upsert_node(t.conn, "cx:X", source="codex", kind="subagent", parent_hint="cx:Y")
            db.upsert_node(t.conn, "cx:Y", source="codex", kind="subagent", parent_hint="cx:X")
            db.put_usage(t.conn, "codex", "k1", "cx:X", ts="2026-10-10T00:00:00.000Z", model="gpt-6-luna", input=10)
            db.put_usage(t.conn, "codex", "k2", "cx:Y", ts="2026-10-10T00:00:00.000Z", model="gpt-6-luna", input=5)
            indexer.resolve(t.conn, t.cfg)
            totals = {r["node_id"]: r["sub_input"] for r in t.conn.execute("SELECT node_id, sub_input FROM node_totals")}
            self.assertEqual(set(totals), {"cx:X", "cx:Y"})
            self.assertEqual(max(totals.values()), 15)

    def test_wholly_unpriced_cost_is_null(self):
        self.assertIsNone(queries.money(0, 0, 2)["cost"])
        self.assertEqual(queries.money(1.0, 0, 2)["cost"], 1.0)
        self.assertEqual(export.fmt_cost(queries.money(0, 0, 2)), "?")

    def test_scan_lock_is_exclusive(self):
        with TempIndex() as t:
            with indexer.scan_lock(t.cfg):
                with self.assertRaises(indexer.ScanBusy):
                    with indexer.scan_lock(t.cfg, wait=False):
                        pass

    def test_static_files_cannot_escape_web_root(self):
        from agent_tree import server
        sibling = server.WEB_ROOT.parent / (server.WEB_ROOT.name + "_evil")
        self.assertFalse((sibling / "x").resolve().is_relative_to(server.WEB_ROOT.resolve()))


class T3StatusTests(unittest.TestCase):
    def test_thread_status(self):
        from agent_tree.sources.t3 import _thread_status
        running = {"state": "running", "requested_at": "2026-10-10T00:00:00Z", "completed_at": None}
        done = {"state": "completed", "requested_at": "2026-10-09T00:00:00Z", "completed_at": "2026-10-09T00:01:00Z"}
        self.assertEqual(_thread_status({}, [done, running]), "active")
        self.assertEqual(_thread_status({}, [done]), "settled")
        self.assertEqual(_thread_status({}, []), "settled")
        self.assertEqual(_thread_status({"archived_at": "2026-10-10"}, [done]), "archived")


class WrapperStatusTests(unittest.TestCase):
    def test_running_unknown_failed(self):
        import os, tempfile, time
        from pathlib import Path
        from agent_tree.sources.wrapper import _status
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            out, log = d / "t.md.out", d / "t.md.log"
            self.assertEqual(_status({}, out, log), "unknown")
            log.write_text("working\n")
            self.assertEqual(_status({}, out, log), "running")
            os.utime(log, (time.time() - 3600,) * 2)
            self.assertEqual(_status({}, out, log), "failed")
            out.write_text("done")
            self.assertEqual(_status({}, out, log), "completed")
            self.assertEqual(_status({"exit_code": 2}, out, log), "failed")


class ReReviewRegressionTests(unittest.TestCase):
    def test_project_filter_is_exact(self):
        with TempIndex() as t:
            for i, path in enumerate(("/w/c", "/w/ca", "/w/abc")):
                db.upsert_node(t.conn, f"cc:s{i}", session_id=f"cc:s{i}", source="claude-code", kind="root",
                               project_path=path, started=f"2026-10-10T00:00:0{i}.000Z")
            indexer.resolve(t.conn, t.cfg)
            self.assertEqual([x["id"] for x in queries.list_threads(t.conn, t.cfg, project="/w/c")["threads"]], ["cc:s0"])
            self.assertEqual(queries.list_threads(t.conn, t.cfg, project="c")["total"], 1)

    def test_flag_rule_counts_follow_the_date_filter(self):
        with TempIndex() as t:
            t.conn.executemany("INSERT INTO flags(rule, severity, tree_id, node_id, message, heuristic, ts) VALUES(?,?,?,?,?,?,?)",
                               [("depth", "warn", "x", "x", "m", 0, "2020-01-01T00:00:00.000Z"),
                                ("depth", "warn", "x", "y", "m", 0, "2099-01-01T00:00:00.000Z")])
            res = queries.flags(t.conn, t.cfg, since="7d")
            self.assertEqual(len(res["flags"]), 1)
            self.assertEqual(next(r["count"] for r in res["rules"] if r["id"] == "depth"), 1)

    def test_host_header_rules(self):
        from agent_tree.server import ScanState, make_handler
        with TempIndex() as t:
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(t.cfg, ScanState()))
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            try:
                base = f"http://127.0.0.1:{httpd.server_address[1]}"
                for host, code in (("[::1]", 200), ("[::1]:8711", 200), ("[::1]evil", 403), ("localhost.evil.com", 403)):
                    req = urllib.request.Request(base + "/api/meta", headers={"Host": host})
                    try:
                        with urllib.request.urlopen(req) as r:
                            got = r.status
                    except urllib.error.HTTPError as e:
                        got = e.code
                    self.assertEqual(got, code, host)
                with urllib.request.urlopen(base + "/%00") as r:  # NUL byte: app shell, not a dropped connection
                    self.assertEqual(r.status, 200)
            finally:
                httpd.shutdown()
                httpd.server_close()


class UnresolvedWrapperCallTests(unittest.TestCase):
    def test_variable_task_path_is_matched_by_time(self):
        with TempIndex() as t:
            db.upsert_node(t.conn, "cc:S", session_id="cc:S", source="claude-code", kind="root")
            db.upsert_node(t.conn, "cc-agent:R", session_id="cc:S", source="claude-code", kind="subagent", parent_hint="toolu_R")
            db.put_spawn(t.conn, "toolu_R", "cc:S")
            db.upsert_node(t.conn, "wr:/tmp/codex-tasks/a.md", source="wrapper", kind="wrapper",
                           parent_hint="wrapper-caller:/tmp/codex-tasks/a.md", started="2026-10-10T00:00:00.000Z")
            db.upsert_node(t.conn, "wr:/tmp/codex-tasks/late.md", source="wrapper", kind="wrapper",
                           parent_hint="wrapper-caller:/tmp/codex-tasks/late.md", started="2026-10-10T02:00:00.000Z")
            db.put_link(t.conn, "wrapper-call-unresolved", "cc-agent:R@2026-10-10T00:00:30.000Z", "cc-agent:R", "inferred",
                        json.dumps({"ts": "2026-10-10T00:00:30.000Z"}))
            indexer.resolve(t.conn, t.cfg)
            n = {r["id"]: dict(r) for r in t.conn.execute("SELECT id, parent_id, link_confidence FROM nodes")}
            self.assertEqual((n["wr:/tmp/codex-tasks/a.md"]["parent_id"], n["wr:/tmp/codex-tasks/a.md"]["link_confidence"]),
                             ("cc-agent:R", "inferred"))
            self.assertIsNone(n["wr:/tmp/codex-tasks/late.md"]["parent_id"])  # no call within 10 minutes


if __name__ == "__main__":
    unittest.main()
