#!/usr/bin/env python3
"""Development server for the Agent Tree web UI.

Serves agent_tree/web/ and answers every endpoint in docs/API.md with
deterministic synthetic data, so the front end can be built and screenshotted
without a real index.

    python3 scripts/mock_server.py                 # http://127.0.0.1:8799
    python3 scripts/mock_server.py --threads 2500  # stress the thread list
    python3 scripts/mock_server.py --first-run     # last_scan = null, no data
    python3 scripts/mock_server.py --metadata-only # titles/descriptions null
    python3 scripts/mock_server.py --fail          # every /api call returns 500
    python3 scripts/mock_server.py --latency 400   # add delay (ms) to /api calls

Standard library only.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import mimetypes
import random
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

try:
    from zoneinfo import ZoneInfo

    TZ = ZoneInfo("America/Denver")
except Exception:  # pragma: no cover - tzdata missing
    TZ = timezone.utc

WEB_ROOT = Path(__file__).resolve().parent.parent / "agent_tree" / "web"
TZ_NAME = "America/Denver"

# --------------------------------------------------------------------------- #
# Prices (USD per 1M tokens) and families
# --------------------------------------------------------------------------- #

PRICES = {
    "claude-opus-5-5": (4.00, 0.20, 5.00, 20.00),
    "claude-sonnet-5-5": (2.00, 0.20, 2.50, 10.00),
    "claude-haiku-5-5": (0.10, 0.01, 0.125, 0.50),
    "claude-fable-5-1": (10.00, 0.25, 12.50, 50.00),
    "gpt-6.1-sol": (2.00, 0.10, 2.00, 10.00),
    "gpt-6-luna": (0.10, 0.01, 0.10, 0.50),
    "gpt-6-astra": (10.00, 1.00, 10.00, 50.00),
}
COVERED = {"anthropic": False, "openai": True}
BLOCKED = {"gpt-6-astra"}
EXPENSIVE = {"claude-opus-5-5", "claude-fable-5-1", "gpt-6-astra"}

RULES = [
    ("depth", "Depth above limit", "warn", False),
    ("concurrency", "Too many agents at once", "warn", False),
    ("blocked_model", "Blocked model used", "alert", False),
    ("openai_on_ui", "OpenAI model on UI work", "warn", True),
    ("same_family_critic", "Critic from the same model family", "warn", True),
    ("orphan", "Parent session not found", "warn", False),
    ("long_running", "Agent ran longer than 30 min", "warn", False),
    ("heavy_tokens", "Agent used over 2M tokens", "warn", False),
    ("expensive_model_trivial_task", "Expensive model on a trivial task", "warn", True),
    ("duplicate_tasks", "Near-duplicate tasks", "warn", True),
]
RULE_INFO = {r[0]: r for r in RULES}


def family_of(model: str | None) -> str:
    if not model:
        return "other"
    if model.startswith("claude"):
        return "anthropic"
    if model.startswith("gpt"):
        return "openai"
    return "other"


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def parse_iso(s: str) -> datetime:
    return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def zero_tokens() -> dict:
    return {"input": 0, "cached_input": 0, "cache_write": 0, "output": 0, "reasoning": 0, "total": 0}


def add_tokens(a: dict, b: dict) -> None:
    for k in ("input", "cached_input", "cache_write", "output", "reasoning", "total"):
        a[k] += b[k]


class MoneyAcc:
    def __init__(self) -> None:
        self.cost = 0.0
        self.covered = 0.0
        self.any_known = False
        self.all_known = True
        self.empty = True

    def add(self, m: dict) -> None:
        self.empty = False
        if m["cost"] is None:
            self.all_known = False
        else:
            self.any_known = True
            self.cost += m["cost"]
            if not m["cost_known"]:
                self.all_known = False
        self.covered += m["covered_cost"]

    def out(self) -> dict:
        if self.empty:
            return {"cost": 0.0, "cost_known": True, "covered_cost": 0.0}
        cost = round(self.cost, 6) if (self.any_known or self.covered) else None
        return {"cost": cost, "cost_known": self.all_known, "covered_cost": round(self.covered, 6)}


# --------------------------------------------------------------------------- #
# Synthetic data generation
# --------------------------------------------------------------------------- #

PROJECTS = [
    ("/Users/me/Projects/agent-tracker-tree", "agent-tracker-tree"),
    ("/Users/me/Projects/zephyr-mesh", "zephyr-mesh"),
    ("/Users/me/Projects/orbit-physics", "orbit-physics"),
    ("/Users/me/Projects/ledger-api", "ledger-api"),
    ("/Users/me/Projects/portfolio-site", "portfolio-site"),
    ("/Users/me/Projects/t3code-plugins", "t3code-plugins"),
    ("/Users/me/dotfiles", "dotfiles"),
]

TITLES = [
    "Add incremental JSONL reader with offsets",
    "Fix flaky physics step at high dt",
    "Refactor ledger reconciliation job",
    "Write README and screenshots",
    "Investigate memory growth in mesh sync",
    "Port settings screen to new layout",
    "Audit auth middleware for CSRF gaps",
    "Price table update for October",
    "Migrate CI to cached wheels",
    "Profile cold start of the indexer",
    "Design empty states for dashboard",
    "Split collision solver into S0/S2 passes",
    "Rename config keys and add migration",
    "Triage failing snapshot tests",
    "Build CSV export for thread trees",
    "Draft release notes for 0.3",
    "Review PR #212: retry policy",
    "Scan repo for leaked tokens",
    "Implement rate limiter for ledger webhooks",
    "Clean up dotfiles and zsh startup time",
    "Plugin manifest validation",
    "Benchmark sqlite WAL vs rollback",
    "Write property tests for the parser",
    "Investigate Codex session linking",
]

SUB_TASKS = [
    ("Explore", "research", "Map how sessions reference subagent files"),
    ("Explore", "scan", "Find all callers of read_jsonl"),
    ("general-purpose", "build", "Implement offset tracking in reader"),
    ("general-purpose", "build", "Physics core fixes (S0/S2)"),
    ("Plan", "design", "Plan the migration in three steps"),
    ("general-purpose", "test", "Write fixtures for truncated lines"),
    ("general-purpose", "review", "Critic review of the reader change"),
    ("Explore", "research", "Compare cache-write pricing between tiers"),
    ("general-purpose", "build", "Wire export endpoints"),
    ("general-purpose", "design", "Tighten spacing on the settings screen"),
    ("general-purpose", "review", "Audit error handling in scan loop"),
    ("Explore", "scan", "Count TODOs per module"),
]

LUNA_TASKS = [
    ("scan", "Grep for deprecated config keys"),
    ("scan", "List files touching the ledger schema"),
    ("test", "Run unit tests for storage module"),
    ("build", "Convert storage calls to async"),
    ("build", "Update call sites in handlers/"),
    ("review", "Lint check of changed files"),
    ("research", "Summarize open issues on retries"),
    ("test", "Add regression test for webhook replay"),
    ("build", "Port fixtures to new loader"),
    ("other", "Relay results to orchestrator"),
]


class Gen:
    def __init__(self, seed: int, now: datetime, metadata_only: bool) -> None:
        self.r = random.Random(seed)
        self.now = now
        self.meta_only = metadata_only
        self.threads: list[dict] = []  # each: {"summary":..., "nodes": [...], "flags": [...]}
        self._hex = 0

    # ids ------------------------------------------------------------------
    def hexid(self, n: int = 12) -> str:
        return "".join(self.r.choice("0123456789abcdef") for _ in range(n))

    def uuid(self) -> str:
        h = self.hexid(32)
        return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"

    # tokens ---------------------------------------------------------------
    def tokens_for(self, model: str | None, dur_s: float, scale: float = 1.0) -> dict:
        if model is None:
            return zero_tokens()
        r = self.r
        fam = family_of(model)
        k = max(dur_s, 5) * scale
        inp = int(k * r.uniform(30, 90))
        cached = int(k * r.uniform(500, 2200))
        cw = int(k * r.uniform(120, 500)) if fam == "anthropic" else 0
        out = int(k * r.uniform(14, 48))
        reasoning = int(out * r.uniform(0.25, 0.6)) if fam == "openai" else 0
        t = {"input": inp, "cached_input": cached, "cache_write": cw, "output": out, "reasoning": reasoning}
        t["total"] = inp + cached + cw + out
        return t

    def money_for(self, model: str | None, t: dict) -> dict:
        if model is None:
            return {"cost": 0.0, "cost_known": True, "covered_cost": 0.0}
        p = PRICES.get(model)
        if p is None:
            return {"cost": None, "cost_known": False, "covered_cost": 0.0}
        c = (t["input"] * p[0] + t["cached_input"] * p[1] + t["cache_write"] * p[2] + t["output"] * p[3]) / 1e6
        if COVERED.get(family_of(model)):
            return {"cost": 0.0, "cost_known": True, "covered_cost": round(c, 6)}
        return {"cost": round(c, 6), "cost_known": True, "covered_cost": 0.0}

    # nodes ----------------------------------------------------------------
    def mk(self, parent: dict | None, *, source: str, kind: str, model: str | None, start: datetime,
           dur_s: float, desc: str | None, agent_type: str | None, task_type: str = "other",
           role: str | None = None, effort: str | None = None, status: str = "completed",
           link: str = "exact", scale: float = 1.0, project: str = "x") -> dict:
        if source == "claude-code":
            nid = ("cc:" + self.uuid()) if kind == "root" else ("cc-agent:" + self.hexid(16))
        elif source == "codex":
            nid = ("codex:" + self.uuid()) if kind == "root" else ("codex-agent:" + self.hexid(16))
        elif source == "t3":
            nid = ("t3:" + self.hexid(10)) if kind == "t3-thread" else ("t3-task:" + self.hexid(10))
        else:
            nid = "wrap:" + self.hexid(12)
        ended = None if status == "running" else start + timedelta(seconds=dur_s)
        if source == "claude-code":
            raw = f"/Users/me/.claude/projects/-Users-me-Projects-{project}/" + (
                f"{nid.split(':')[1]}.jsonl" if kind == "root" else f"subagents/agent-{nid.split(':')[1]}.jsonl")
            pv = "cc-1"
        elif source == "codex":
            raw = f"/Users/me/.codex/sessions/{start:%Y/%m/%d}/rollout-{start:%Y-%m-%dT%H-%M-%S}-{nid.split(':')[1]}.jsonl"
            pv = "codex-1"
        elif source == "t3":
            raw = "/Users/me/.t3/userdata/state.sqlite"
            pv = "t3-1"
        else:
            raw = f"/tmp/codex-tasks/{nid.split(':')[1]}.meta.json"
            pv = "wrap-1"
        tok = self.tokens_for(model, dur_s, scale)
        node = {
            "id": nid,
            "parent_id": parent["id"] if parent else None,
            "depth": (parent["depth"] + 1) if parent else 0,
            "source": source,
            "kind": kind,
            "agent_type": agent_type,
            "role": role,
            "description": None if self.meta_only else desc,
            "model": model,
            "family": family_of(model),
            "effort": effort,
            "status": status,
            "started": start,
            "ended": ended,
            "self": {"tokens": tok, "money": self.money_for(model, tok)},
            "covered": bool(COVERED.get(family_of(model))) if model else False,
            "link_confidence": link,
            "raw_file": raw,
            "raw_offset": 0 if kind in ("root", "t3-thread") else self.r.randint(1, 900) * 1024,
            "parser_version": pv,
            "flags": [],
            "_task_type": task_type,
            "_children": [],
            "_desc_raw": desc,
        }
        if parent:
            parent["_children"].append(node)
        return node

    # thread shapes ----------------------------------------------------------
    def claude_thread(self, start: datetime, proj: tuple, title: str, size: int, running: bool = False) -> dict:
        r = self.r
        pname = proj[1]
        root = self.mk(None, source="claude-code", kind="root", model=r.choice(["claude-opus-5-5"] * 4 + ["claude-fable-5-1"]),
                       start=start, dur_s=60, desc=title, agent_type="main", effort="high", project=pname,
                       task_type="build", scale=2.2, status="running" if running else "completed")
        t = start + timedelta(seconds=r.randint(30, 200))
        for i in range(size):
            at, tt, desc = r.choice(SUB_TASKS)
            model = r.choice(["claude-sonnet-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-5-5"])
            if at == "Explore":
                model = "claude-haiku-5-5"
            dur = r.uniform(40, 900) if r.random() > 0.04 else r.uniform(1900, 2600)
            st = "completed"
            if running and i == size - 1:
                st = "running"
            elif r.random() < 0.05:
                st = "failed"
            n = self.mk(root, source="claude-code", kind="subagent", model=model, start=t, dur_s=dur, desc=desc,
                        agent_type=at, task_type=tt, effort=r.choice([None, "medium", "high"]), status=st, project=pname)
            if r.random() < 0.35:
                # nested depth-2 helpers
                t2 = t + timedelta(seconds=r.uniform(5, 60))
                for _ in range(r.randint(1, 3)):
                    at2, tt2, d2 = r.choice(SUB_TASKS)
                    self.mk(n, source="claude-code", kind="subagent", model="claude-haiku-5-5", start=t2,
                            dur_s=r.uniform(20, 240), desc=d2, agent_type=at2, task_type=tt2, project=pname)
                    t2 += timedelta(seconds=r.uniform(3, 40))
            if r.random() < 0.5:
                t += timedelta(seconds=r.uniform(4, 30))  # parallel-ish
            else:
                t += timedelta(seconds=dur + r.uniform(20, 300))
        return root

    def codex_thread(self, start: datetime, proj: tuple, title: str, workers: int) -> dict:
        r = self.r
        root = self.mk(None, source="codex", kind="root", model="gpt-6.1-sol", start=start, dur_s=60, desc=title,
                       agent_type="orchestrator", role="orchestrator", effort="high", task_type="build", scale=1.5,
                       project=proj[1])
        t = start + timedelta(seconds=r.randint(20, 90))
        for i in range(workers):
            tt, desc = r.choice(LUNA_TASKS)
            self.mk(root, source="codex", kind="subagent", model="gpt-6-luna", start=t + timedelta(seconds=r.uniform(0, 20)),
                    dur_s=r.uniform(30, 400), desc=desc, agent_type="worker", role="worker", effort="low",
                    task_type=tt, project=proj[1])
            if i % 4 == 3:
                t += timedelta(seconds=r.uniform(200, 500))
        return root

    def t3_thread(self, start: datetime, proj: tuple, title: str) -> dict:
        r = self.r
        p = proj[1]
        t3 = self.mk(None, source="t3", kind="t3-thread", model=None, start=start, dur_s=60, desc=title,
                     agent_type=None, project=p)
        cc = self.mk(t3, source="claude-code", kind="root", model="claude-opus-5-5", start=start + timedelta(seconds=4),
                     dur_s=60, desc=title, agent_type="main", effort="high", task_type="build", scale=1.8, project=p)
        t = start + timedelta(seconds=90)
        self.mk(cc, source="claude-code", kind="subagent", model="claude-haiku-5-5", start=t, dur_s=180,
                desc="Map the plugin manifest loader", agent_type="Explore", task_type="research", project=p)
        t += timedelta(seconds=200)
        wrap = self.mk(cc, source="claude-code", kind="wrapper", model="claude-haiku-5-5", start=t, dur_s=40,
                       desc="sol-worker: implement manifest schema validation", agent_type="sol-worker",
                       task_type="build", project=p, scale=0.3)
        cx = self.mk(wrap, source="codex", kind="root", model="gpt-6.1-sol", start=t + timedelta(seconds=6), dur_s=900,
                     desc="Implement manifest schema validation", agent_type="orchestrator", role="orchestrator",
                     effort="high", task_type="build", project=p)
        for j in range(4):
            tt, desc = LUNA_TASKS[j]
            self.mk(cx, source="codex", kind="subagent", model="gpt-6-luna", start=t + timedelta(seconds=60 + j * 15),
                    dur_s=r.uniform(120, 420), desc=desc, agent_type="worker", role="worker", task_type=tt, project=p)
        self.mk(cx, source="codex", kind="subagent", model="gpt-6-luna", start=t + timedelta(seconds=700),
                dur_s=150, desc="Tighten the layout of the plugin settings screen", agent_type="worker", role="worker",
                task_type="design", project=p)
        # inferred link: matched by time window
        self.mk(cc, source="claude-code", kind="subagent", model="claude-sonnet-5-5", start=t + timedelta(seconds=1100),
                dur_s=300, desc="Critic review of the reader change", agent_type="general-purpose", task_type="review",
                link="inferred", project=p)
        self.mk(cc, source="claude-code", kind="subagent", model="qwen3-coder-32b", start=t + timedelta(seconds=1500),
                dur_s=260, desc="Local model: summarize test output", agent_type="local-runner", task_type="test",
                project=p)
        return t3

    def big_thread(self, start: datetime, proj: tuple, title: str) -> dict:
        r = self.r
        p = proj[1]
        root = self.mk(None, source="claude-code", kind="root", model="claude-opus-5-5", start=start, dur_s=60,
                       desc=title, agent_type="main", effort="max", task_type="build", scale=4.0, project=p)
        t = start + timedelta(seconds=120)
        phases = ["Phase 1: storage interfaces", "Phase 2: handlers", "Phase 3: webhooks", "Phase 4: jobs",
                  "Phase 5: CLI", "Phase 6: fixtures", "Phase 7: docs", "Phase 8: cleanup"]
        for pi, ph in enumerate(phases):
            sn = self.mk(root, source="claude-code", kind="subagent", model="claude-sonnet-5-5", start=t, dur_s=120,
                         desc=ph, agent_type="general-purpose", task_type="build", project=p)
            wr = self.mk(sn, source="claude-code", kind="wrapper", model="claude-haiku-5-5",
                         start=t + timedelta(seconds=30), dur_s=30, desc=f"sol-worker: {ph.lower()}",
                         agent_type="sol-worker", task_type="build", project=p, scale=0.2)
            cx = self.mk(wr, source="codex", kind="root", model="gpt-6.1-sol", start=t + timedelta(seconds=40),
                         dur_s=300, desc=ph, agent_type="orchestrator", role="orchestrator", effort="high",
                         task_type="build", project=p)
            wt = t + timedelta(seconds=70)
            for w in range(30):
                tt, desc = r.choice(LUNA_TASKS)
                lw = self.mk(cx, source="codex", kind="subagent", model="gpt-6-luna",
                             start=wt + timedelta(seconds=r.uniform(0, 40)), dur_s=r.uniform(60, 420), desc=desc,
                             agent_type="worker", role="worker", task_type=tt, project=p)
                if w % 6 == 0:
                    tt2, d2 = r.choice(LUNA_TASKS)
                    self.mk(lw, source="codex", kind="subagent", model="gpt-6-luna",
                            start=lw["started"] + timedelta(seconds=10), dur_s=r.uniform(20, 90), desc=d2,
                            agent_type="worker", role="worker", task_type=tt2, project=p)
                if w % 24 == 23:
                    wt += timedelta(seconds=500)
            t += timedelta(seconds=r.uniform(900, 1500))
        # one opus scan on a trivial task
        self.mk(root, source="claude-code", kind="subagent", model="claude-opus-5-5", start=t, dur_s=90,
                desc="Scan repo for leftover sync calls", agent_type="Explore", task_type="scan", project=p)
        return root

    def orphan_thread(self, start: datetime, proj: tuple) -> dict:
        n = self.mk(None, source="claude-code", kind="subagent", model="claude-sonnet-5-5", start=start, dur_s=420,
                    desc="Write fixtures for truncated lines", agent_type="general-purpose", task_type="test",
                    link="orphan", project=proj[1])
        return n

    # finishing ----------------------------------------------------------------
    def finish(self, root: dict, project: tuple, title: str | None, t3_title: str | None = None) -> None:
        nodes: list[dict] = []

        def walk(n: dict) -> None:
            n["_children"].sort(key=lambda c: c["started"])
            nodes.append(n)
            for c in n["_children"]:
                walk(c)

        walk(root)

        # parents end after their children; build subtree aggregates
        def fix(n: dict) -> tuple[dict, MoneyAcc, int, int, datetime | None, bool]:
            tok = dict(n["self"]["tokens"])
            acc = MoneyAcc()
            acc.add(n["self"]["money"])
            cnt = 1
            md = 0
            running = n["ended"] is None
            end = n["ended"]
            for c in n["_children"]:
                ct, cacc, cc, cmd, cend, crun = fix(c)
                add_tokens(tok, ct)
                acc.add(cacc.out())
                cnt += cc
                md = max(md, cmd + 1)
                running = running or crun
                if cend and (end is None or cend > end) and not running:
                    end = cend + timedelta(seconds=self.r.uniform(5, 60))
            if n["_children"] and n["ended"] is not None:
                if running:
                    n["ended"] = None
                    n["status"] = "running"
                else:
                    n["ended"] = max(n["ended"], end)
            n["subtree"] = {"tokens": tok, "money": acc.out(), "nodes": cnt, "max_depth": md}
            return tok, acc, cnt, md, n["ended"], running

        fix(root)
        # parents with long children: retoken root proportional to its wall time (keeps root realistic)
        tid = root["id"]
        flags: list[dict] = []

        def flag(n: dict, rule: str, msg: str) -> None:
            n["flags"].append(rule)
            info = RULE_INFO[rule]
            flags.append({"rule": rule, "severity": info[2], "heuristic": info[3], "tree_id": tid, "node_id": n["id"],
                          "message": msg, "ts": iso(n["started"]), "thread_title": title})

        by_id = {n["id"]: n for n in nodes}
        seen_desc: dict[str, str] = {}
        for n in nodes:
            end = n["ended"] or self.now
            wall = (end - n["started"]).total_seconds()
            if n["depth"] > 4:
                flag(n, "depth", f"Depth {n['depth']} is above the limit of 4")
            if n["model"] in BLOCKED:
                flag(n, "blocked_model", f"{n['model']} is on the blocked list")
            d = (n["_desc_raw"] or "").lower()
            if n["family"] == "openai" and n["kind"] == "subagent" and re.search(r"\b(ui|layout|screen|design|style)\b", d):
                flag(n, "openai_on_ui", "OpenAI model assigned to UI work (matched \"" +
                     re.search(r"\b(ui|layout|screen|design|style)\b", d).group(1) + "\")")
            par = by_id.get(n["parent_id"])
            if (par and re.search(r"\b(critic|review|audit)\b", d) and par["family"] == n["family"]
                    and n["family"] != "other" and self.r.random() < 0.35):
                flag(n, "same_family_critic", f"Reviewer is {n['family']}, same family as the work it reviews")
            if n["link_confidence"] == "orphan":
                flag(n, "orphan", "Parent session file was not found")
            if wall > 1800 and n["kind"] not in ("root", "t3-thread"):
                flag(n, "long_running", f"Ran {int(wall // 60)} min, above the limit of 30 min")
            if n["self"]["tokens"]["total"] > 2_000_000 and n["kind"] != "t3-thread":
                flag(n, "heavy_tokens", f"Used {n['self']['tokens']['total'] / 1e6:.1f}M tokens, above 2.0M")
            if n["model"] in EXPENSIVE and n["kind"] == "subagent" and re.search(r"\b(scan|grep|count|list|find)\b", d):
                flag(n, "expensive_model_trivial_task", f"{n['model']} used for a trivial task")
            if d and n["kind"] == "subagent" and n["family"] == "anthropic":
                key = (n["parent_id"], d)
                if key in seen_desc:
                    flag(n, "duplicate_tasks", "Same task as an earlier sibling agent")
                seen_desc[key] = n["id"]
        # concurrency across the tree
        events = []
        for n in nodes:
            if n["depth"] == 0:
                continue
            events.append((n["started"], 1, n))
            events.append((n["ended"] or self.now, -1, n))
        events.sort(key=lambda e: (e[0], e[1]))
        cur = 0
        peak = 0
        peak_node = None
        for ts, delta, n in events:
            cur += delta
            if cur > peak:
                peak, peak_node = cur, n
        if peak > 20 and peak_node:
            flag(peak_node, "concurrency", f"{peak} agents ran at the same time, above the limit of 20")

        for n in nodes:
            n["wall_ms"] = int(((n["ended"] or self.now) - n["started"]).total_seconds() * 1000)

        models = []
        fams = []
        sources = []
        for n in nodes:
            if n["model"] and n["model"] not in models:
                models.append(n["model"])
            if n["model"] and n["family"] not in fams:
                fams.append(n["family"])
            if n["source"] not in sources:
                sources.append(n["source"])
        summary = {
            "id": tid,
            "source": root["source"],
            "title": None if self.meta_only else title,
            "project_path": project[0],
            "project_name": project[1],
            "started": root["started"],
            "ended": root["ended"],
            "wall_ms": root["wall_ms"],
            "node_count": len(nodes),
            "max_depth": max(n["depth"] for n in nodes),
            "tokens": root["subtree"]["tokens"],
            "money": root["subtree"]["money"],
            "models": models,
            "families": fams,
            "flag_count": len(flags),
            "sources": sources,
            "t3_title": None if (self.meta_only or not t3_title) else t3_title,
        }
        self.threads.append({"summary": summary, "nodes": nodes, "flags": flags})

    def build(self, n_threads: int, first_run: bool) -> None:
        if first_run:
            return
        r = self.r
        now = self.now
        # Hero threads (recent)
        p = PROJECTS
        self.finish(self.t3_thread(now - timedelta(hours=2, minutes=10), p[5], "Plugin manifest validation"),
                    p[5], "Plugin manifest validation", t3_title="Manifest validation + settings polish")
        self.finish(self.big_thread(now - timedelta(hours=5, minutes=30), p[3], "Migrate ledger-api to async storage layer"),
                    p[3], "Migrate ledger-api to async storage layer")
        self.finish(self.claude_thread(now - timedelta(minutes=34), p[0], "Build the thread timeline view", 7, running=True),
                    p[0], "Build the thread timeline view")
        self.finish(self.codex_thread(now - timedelta(hours=7), p[3], "Implement rate limiter for ledger webhooks", 9),
                    p[3], "Implement rate limiter for ledger webhooks")
        self.finish(self.orphan_thread(now - timedelta(hours=9), p[0]), p[0], None)
        # an astra thread (blocked model)
        root = self.mk(None, source="codex", kind="root", model="gpt-6-astra", start=now - timedelta(days=1, hours=3),
                       dur_s=780, desc="Draft release notes for 0.3", agent_type="orchestrator", task_type="other",
                       project=p[1])
        self.finish(root, p[1], "Draft release notes for 0.3")
        # Filler across 90 days, denser recently
        for i in range(max(0, n_threads - 6)):
            age_h = r.expovariate(1 / (24 * 9)) if i < 60 else r.uniform(0, 24 * 85)
            age_h = min(age_h, 24 * 88)
            st = now - timedelta(hours=age_h + 1)
            proj = r.choice(p)
            title = r.choice(TITLES)
            kind = r.random()
            if kind < 0.55:
                root = self.claude_thread(st, proj, title, r.randint(0, 8))
                self.finish(root, proj, title, t3_title=title + " (T3)" if r.random() < 0.15 else None)
            elif kind < 0.85:
                root = self.codex_thread(st, proj, title, r.randint(1, 8))
                self.finish(root, proj, title)
            else:
                root = self.t3_thread(st, proj, title)
                self.finish(root, proj, title, t3_title=title)
        self.threads.sort(key=lambda t: t["summary"]["started"], reverse=True)
        self.by_id = {t["summary"]["id"]: t for t in self.threads}


# --------------------------------------------------------------------------- #
# Serialization helpers
# --------------------------------------------------------------------------- #

def ser(obj):
    if isinstance(obj, datetime):
        return iso(obj)
    if isinstance(obj, dict):
        return {k: ser(v) for k, v in obj.items() if not k.startswith("_")}
    if isinstance(obj, list):
        return [ser(v) for v in obj]
    return obj


def node_out(n: dict) -> dict:
    d = ser(n)
    d["child_count"] = len(n["_children"])
    return d


def local_day(dt: datetime) -> str:
    return dt.astimezone(TZ).strftime("%Y-%m-%d")


def parse_since(val: str | None, now: datetime, default: str | None) -> datetime | None:
    val = val or default
    if not val or val == "all":
        return None
    m = re.fullmatch(r"(\d+)([dhw])", val)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit == "d":
            # "1d" = today (local), "7d" = last 7 days including today
            start_local = now.astimezone(TZ).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=n - 1)
            return start_local.astimezone(timezone.utc)
        if unit == "h":
            return now - timedelta(hours=n)
        return now - timedelta(weeks=n)
    try:
        d = datetime.strptime(val[:10], "%Y-%m-%d").replace(tzinfo=TZ)
        return d.astimezone(timezone.utc)
    except ValueError:
        return None


def parse_until(val: str | None) -> datetime | None:
    if not val:
        return None
    try:
        d = datetime.strptime(val[:10], "%Y-%m-%d").replace(tzinfo=TZ) + timedelta(days=1)
        return d.astimezone(timezone.utc)
    except ValueError:
        return None


class State:
    def __init__(self, args) -> None:
        self.args = args
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.gen = Gen(7, self.now, args.metadata_only)
        self.gen.build(args.threads, args.first_run)
        self.gen.by_id = {t["summary"]["id"]: t for t in self.gen.threads}
        self.last_scan = None if args.first_run else self.now - timedelta(minutes=4)
        self.scan_running = False
        self.lock = threading.Lock()

    def all_nodes(self):
        for t in self.gen.threads:
            for n in t["nodes"]:
                yield t, n

    # summary ------------------------------------------------------------------
    def period(self, since: datetime) -> dict:
        tok = zero_tokens()
        acc = MoneyAcc()
        fam: dict[str, list] = {}
        mod: dict[str, list] = {}
        nodes = 0
        threads = set()
        for t, n in self.all_nodes():
            if n["started"] < since:
                continue
            nodes += 1
            threads.add(t["summary"]["id"])
            st = n["self"]
            add_tokens(tok, st["tokens"])
            acc.add(st["money"])
            for key, store in ((n["family"], fam), (n["model"], mod)):
                if key is None:
                    continue
                if key not in store:
                    store[key] = [zero_tokens(), MoneyAcc(), n["family"]]
                add_tokens(store[key][0], st["tokens"])
                store[key][1].add(st["money"])
        by_family = sorted(({"family": k, "tokens": v[0], "money": v[1].out()} for k, v in fam.items()),
                           key=lambda x: -x["tokens"]["total"])
        by_model = sorted(({"model": k, "family": v[2], "tokens": v[0], "money": v[1].out()} for k, v in mod.items()),
                          key=lambda x: -((x["money"]["cost"] or 0) + x["money"]["covered_cost"]))
        return {"tokens": tok, "money": acc.out(), "nodes": nodes, "threads": len(threads),
                "by_family": by_family, "by_model": by_model}

    def daily(self, days: int, since: datetime) -> list[dict]:
        out = {}
        today = self.now.astimezone(TZ).date()
        for i in range(days - 1, -1, -1):
            d = (today - timedelta(days=i)).isoformat()
            out[d] = [zero_tokens(), MoneyAcc(), {}]
        for t, n in self.all_nodes():
            d = local_day(n["started"])
            if d not in out:
                continue
            row = out[d]
            add_tokens(row[0], n["self"]["tokens"])
            row[1].add(n["self"]["money"])
            if n["model"]:
                row[2][n["family"]] = row[2].get(n["family"], 0) + n["self"]["tokens"]["total"]
        return [{"day": d, "tokens": v[0], "money": v[1].out(), "by_family": v[2]} for d, v in out.items()]

    def summary(self) -> dict:
        now = self.now
        res = {"periods": {
            "today": self.period(parse_since("1d", now, None)),
            "week": self.period(parse_since("7d", now, None)),
            "month": self.period(parse_since("30d", now, None)),
        }, "daily": self.daily(30, parse_since("30d", now, None))}
        since = parse_since("30d", now, None)
        recent = [t for t in self.gen.threads if t["summary"]["started"] >= since]
        recent.sort(key=lambda t: -((t["summary"]["money"]["cost"] or 0)))
        res["top_threads"] = [ser(t["summary"]) for t in recent[:10]]
        fl = [f for t in self.gen.threads for f in t["flags"]]
        res["flag_count"] = len(fl)
        res["flag_counts"] = {"alert": sum(1 for f in fl if f["severity"] == "alert"),
                              "warn": sum(1 for f in fl if f["severity"] == "warn")}
        return res

    def threads(self, q: dict) -> dict:
        g = lambda k: (q.get(k) or [None])[0]
        since = parse_since(g("since"), self.now, None)
        until = parse_until(g("until"))
        rows = []
        text = (g("q") or "").lower()
        for t in self.gen.threads:
            s = t["summary"]
            if g("project") and s["project_name"] != g("project") and s["project_path"] != g("project"):
                continue
            if g("source") and g("source") not in s["sources"]:
                continue
            if g("model") and g("model") not in s["models"]:
                continue
            if since and s["started"] < since:
                continue
            if until and s["started"] >= until:
                continue
            if g("flagged") == "1" and not s["flag_count"]:
                continue
            if text and text not in ((s["title"] or "") + " " + (s["t3_title"] or "")).lower():
                continue
            rows.append(s)
        sort = g("sort") or "started"
        if sort == "cost":
            rows.sort(key=lambda s: -((s["money"]["cost"] or 0) + s["money"]["covered_cost"]))
        elif sort == "tokens":
            rows.sort(key=lambda s: -s["tokens"]["total"])
        else:
            rows.sort(key=lambda s: s["started"], reverse=True)
        limit = int(g("limit") or 200)
        offset = int(g("offset") or 0)
        return {"total": len(rows), "threads": [ser(s) for s in rows[offset:offset + limit]]}

    def thread(self, tid: str) -> dict | None:
        t = self.gen.by_id.get(tid)
        if not t:
            return None
        return {"thread": ser(t["summary"]), "nodes": [node_out(n) for n in t["nodes"]], "flags": t["flags"]}

    def projects(self, q: dict) -> dict:
        since = parse_since((q.get("since") or ["90d"])[0], self.now, "90d")
        days = 30
        day_keys = [(self.now.astimezone(TZ).date() - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
        out: dict[str, dict] = {}
        for t in self.gen.threads:
            s = t["summary"]
            if since and s["started"] < since:
                continue
            p = out.setdefault(s["project_path"], {"path": s["project_path"], "name": s["project_name"],
                                                   "first_ts": s["started"], "last_ts": s["started"], "threads": 0,
                                                   "nodes": 0, "tokens": zero_tokens(), "_m": MoneyAcc(),
                                                   "_d": {k: [0, 0.0] for k in day_keys}})
            p["first_ts"] = min(p["first_ts"], s["started"])
            p["last_ts"] = max(p["last_ts"], s["started"])
            p["threads"] += 1
            p["nodes"] += s["node_count"]
            add_tokens(p["tokens"], s["tokens"])
            p["_m"].add(s["money"])
            for n in t["nodes"]:
                d = local_day(n["started"])
                if d in p["_d"]:
                    p["_d"][d][0] += n["self"]["tokens"]["total"]
                    p["_d"][d][1] += (n["self"]["money"]["cost"] or 0)
        res = []
        for p in out.values():
            p["money"] = p["_m"].out()
            p["daily"] = [{"day": k, "total": v[0], "cost": round(v[1], 6)} for k, v in p["_d"].items()]
            res.append(ser(p))
        res.sort(key=lambda p: -((p["money"]["cost"] or 0) + p["money"]["covered_cost"]))
        return {"projects": res}

    def models(self, q: dict) -> dict:
        since = parse_since((q.get("since") or ["30d"])[0], self.now, "30d")
        mods: dict[str, dict] = {}
        fams: dict[str, dict] = {}
        for t, n in self.all_nodes():
            if not n["model"] or (since and n["started"] < since):
                continue
            m = mods.setdefault(n["model"], {"model": n["model"], "family": n["family"], "nodes": 0, "_t": set(),
                                             "tokens": zero_tokens(), "_m": MoneyAcc(),
                                             "priced": n["model"] in PRICES, "_tt": {}})
            m["nodes"] += 1
            m["_t"].add(t["summary"]["id"])
            add_tokens(m["tokens"], n["self"]["tokens"])
            m["_m"].add(n["self"]["money"])
            tt = m["_tt"].setdefault(n["_task_type"], [0, 0])
            tt[0] += 1
            tt[1] += n["self"]["tokens"]["total"]
            f = fams.setdefault(n["family"], {"family": n["family"], "tokens": zero_tokens(), "_m": MoneyAcc(), "nodes": 0})
            f["nodes"] += 1
            add_tokens(f["tokens"], n["self"]["tokens"])
            f["_m"].add(n["self"]["money"])
        models = []
        for m in mods.values():
            m["threads"] = len(m["_t"])
            m["money"] = m["_m"].out()
            m["by_task_type"] = sorted(({"task_type": k, "nodes": v[0], "total": v[1]} for k, v in m["_tt"].items()),
                                       key=lambda x: -x["total"])
            models.append(ser(m))
        models.sort(key=lambda m: -m["tokens"]["total"])
        families = []
        for f in fams.values():
            f["money"] = f["_m"].out()
            families.append(ser(f))
        families.sort(key=lambda f: -f["tokens"]["total"])
        daily = [{"day": d["day"], "by_family": d["by_family"]} for d in self.daily(30, since)]
        return {"models": models, "families": families, "daily": daily}

    def flags(self, q: dict) -> dict:
        g = lambda k: (q.get(k) or [None])[0]
        since = parse_since(g("since"), self.now, None)
        allf = [f for t in self.gen.threads for f in t["flags"]]
        counts: dict[str, int] = {}
        for f in allf:
            counts[f["rule"]] = counts.get(f["rule"], 0) + 1
        out = []
        for f in allf:
            if g("rule") and f["rule"] != g("rule"):
                continue
            if g("severity") and f["severity"] != g("severity"):
                continue
            if since and parse_iso(f["ts"]) < since:
                continue
            out.append(f)
        out.sort(key=lambda f: f["ts"], reverse=True)
        rules = [{"id": r[0], "label": r[1], "severity": r[2], "heuristic": r[3],
                  "enabled": True, "count": counts.get(r[0], 0)} for r in RULES]
        return {"flags": out, "rules": rules}

    def meta(self) -> dict:
        n_cc = sum(1 for _, n in self.all_nodes() if n["source"] == "claude-code")
        n_cx = sum(1 for _, n in self.all_nodes() if n["source"] == "codex")
        n_t3 = sum(1 for _, n in self.all_nodes() if n["source"] == "t3")
        return {"version": "0.1.0", "last_scan": iso(self.last_scan), "scan_running": self.scan_running,
                "metadata_only": self.args.metadata_only, "covered_by_plan": COVERED, "timezone": TZ_NAME,
                "sources": [
                    {"source": "claude-code", "present": True, "root": "~/.claude", "files": n_cc,
                     "lines_skipped": 3, "parser_version": "cc-1"},
                    {"source": "codex", "present": True, "root": "~/.codex", "files": n_cx,
                     "lines_skipped": 0, "parser_version": "codex-1"},
                    {"source": "t3", "present": True, "root": "~/.t3", "files": n_t3,
                     "lines_skipped": 0, "parser_version": "t3-1"},
                ]}


# --------------------------------------------------------------------------- #
# Exports
# --------------------------------------------------------------------------- #

CSV_COLS = ["id", "parent_id", "depth", "source", "kind", "agent_type", "description", "model", "family", "status",
            "started", "ended", "wall_ms", "input", "cached_input", "cache_write", "output", "reasoning", "total",
            "cost", "covered_cost", "link_confidence", "flags"]


def nodes_csv(nodes: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLS)
    for n in nodes:
        t = n["self"]["tokens"]
        m = n["self"]["money"]
        w.writerow([n["id"], n["parent_id"], n["depth"], n["source"], n["kind"], n["agent_type"], n["description"],
                    n["model"], n["family"], n["status"], n["started"], n["ended"], n["wall_ms"], t["input"],
                    t["cached_input"], t["cache_write"], t["output"], t["reasoning"], t["total"], m["cost"],
                    m["covered_cost"], n["link_confidence"], ";".join(n["flags"])])
    return buf.getvalue()


def nodes_md(data: dict) -> str:
    th = data["thread"]
    lines = [f"# {th['title'] or th['id']}", "", f"- Project: {th['project_name']}",
             f"- Tokens: {th['tokens']['total']:,}", f"- Est. cost: ${th['money']['cost'] or 0:.2f}", ""]
    for n in data["nodes"]:
        label = n["description"] or n["agent_type"] or n["kind"]
        lines.append("  " * n["depth"] + f"- {label} ({n['model'] or '-'}, {n['subtree']['tokens']['total']:,} tok)")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

STATE: State


CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
       "connect-src 'self'; frame-ancestors 'none'")


class Handler(BaseHTTPRequestHandler):
    """Sends the same Content-Security-Policy as the real server (agent_tree/server.py)."""

    server_version = "AgentTreeMock/0.1"

    def end_headers(self):
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def log_message(self, fmt, *args):  # quieter
        if self.path.startswith("/api"):
            super().log_message(fmt, *args)

    def send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, text: str, ctype: str, filename: str | None = None):
        body = text.encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    def api_guard(self) -> bool:
        if STATE.args.latency:
            time.sleep(STATE.args.latency / 1000)
        if STATE.args.fail:
            self.send_json({"error": "mock failure (--fail)"}, 500)
            return False
        return True

    def do_POST(self):
        u = urlparse(self.path)
        if u.path != "/api/scan":
            return self.send_json({"error": "not found"}, 404)
        if self.headers.get("X-Agent-Tree") != "1":
            return self.send_json({"error": "missing X-Agent-Tree header"}, 403)
        if not self.api_guard():
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        with STATE.lock:
            STATE.scan_running = True
        time.sleep(1.6)
        with STATE.lock:
            STATE.scan_running = False
            STATE.last_scan = datetime.now(timezone.utc)
        self.send_json({"ok": True, "seconds": 1.6, "stats": [
            {"source": "claude-code", "files": 640, "new_lines": 312, "lines_skipped": 0},
            {"source": "codex", "files": 211, "new_lines": 88, "lines_skipped": 0}]})

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path
        q = parse_qs(u.query)
        if path.startswith("/api/"):
            if not self.api_guard():
                return
            if path == "/api/meta":
                return self.send_json(STATE.meta())
            if path == "/api/summary":
                return self.send_json(STATE.summary())
            if path == "/api/threads":
                return self.send_json(STATE.threads(q))
            if path.startswith("/api/threads/"):
                tid = unquote(path[len("/api/threads/"):])
                d = STATE.thread(tid)
                return self.send_json(d) if d else self.send_json({"error": f"unknown thread {tid}"}, 404)
            if path == "/api/projects":
                return self.send_json(STATE.projects(q))
            if path == "/api/models":
                return self.send_json(STATE.models(q))
            if path == "/api/flags":
                return self.send_json(STATE.flags(q))
            m = re.fullmatch(r"/api/export/thread/(.+)\.(json|csv|md)", path)
            if m:
                tid = unquote(m.group(1))
                d = STATE.thread(tid)
                if not d:
                    return self.send_json({"error": "unknown thread"}, 404)
                safe = re.sub(r"[^A-Za-z0-9_-]", "_", tid)[:40]
                if m.group(2) == "json":
                    return self.send_text(json.dumps(d, indent=2), "application/json", f"{safe}.json")
                if m.group(2) == "csv":
                    return self.send_text(nodes_csv(d["nodes"]), "text/csv", f"{safe}.csv")
                return self.send_text(nodes_md(d), "text/markdown", f"{safe}.md")
            m = re.fullmatch(r"/api/export/range\.(json|csv)", path)
            if m:
                since = parse_since((q.get("since") or ["7d"])[0], STATE.now, "7d")
                nodes = [node_out(n) for _, n in STATE.all_nodes() if not since or n["started"] >= since]
                if m.group(1) == "json":
                    return self.send_text(json.dumps({"nodes": nodes}), "application/json", "range.json")
                return self.send_text(nodes_csv(nodes), "text/csv", "range.csv")
            return self.send_json({"error": "not found"}, 404)
        # static
        rel = unquote(path).lstrip("/") or "index.html"
        target = (WEB_ROOT / rel).resolve()
        if WEB_ROOT.resolve() not in target.parents and target != WEB_ROOT.resolve():
            return self.send_json({"error": "forbidden"}, 403)
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            return self.send_json({"error": "not found"}, 404)
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if target.suffix == ".js":
            ctype = "text/javascript"
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text") else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    global STATE
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--threads", type=int, default=56, help="number of threads to generate (default 56)")
    ap.add_argument("--first-run", action="store_true", help="no scan yet, no data")
    ap.add_argument("--metadata-only", action="store_true", help="titles and descriptions are null")
    ap.add_argument("--fail", action="store_true", help="every API call returns HTTP 500")
    ap.add_argument("--latency", type=int, default=0, help="extra delay per API call in ms")
    args = ap.parse_args()
    STATE = State(args)
    n_nodes = sum(len(t["nodes"]) for t in STATE.gen.threads)
    print(f"Agent Tree mock: {len(STATE.gen.threads)} threads, {n_nodes} nodes")
    print(f"Serving {WEB_ROOT} on http://127.0.0.1:{args.port}/")
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
