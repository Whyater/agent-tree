"""Rule-based flags, configured in rules.toml.

Rules marked heuristic guess intent from short task descriptions. They are
stored with heuristic=1 and labelled as such everywhere they are shown.
"""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from datetime import datetime
from difflib import SequenceMatcher

from .config import Config, load_toml
from .privacy import clean_text

RULE_LABELS = {
    "depth": "Tree deeper than the limit",
    "concurrency": "Too many agents at once",
    "blocked_model": "Blocked model used",
    "openai_on_ui": "OpenAI model on UI/design work",
    "same_family_critic": "Critic from the builder's model family",
    "orphan": "Parent not found",
    "long_running": "Long-running agent",
    "heavy_tokens": "Very high token use",
    "expensive_model_trivial_task": "Expensive model on a trivial task",
    "duplicate_tasks": "Duplicate tasks",
}
HEURISTIC_RULES = {"openai_on_ui", "same_family_critic", "expensive_model_trivial_task", "duplicate_tasks"}
ROOT_KINDS = {"root", "t3-thread"}


def load_rules(cfg: Config) -> dict:
    return load_toml(cfg.home, "rules.toml")


def _ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _keyword_re(words) -> re.Pattern | None:
    words = [w for w in (words or []) if w]
    if not words:
        return None
    return re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(w.lower()) for w in words) + r")(?![a-z0-9])")


def _text(n: dict) -> str:
    return " ".join(str(n.get(k) or "") for k in ("description", "role", "agent_type")).lower()


def _norm_desc(text: str | None) -> str:
    return re.sub(r"[^a-z ]+", " ", (text or "").lower()).strip()


def evaluate(conn: sqlite3.Connection, cfg: Config) -> int:
    rules = load_rules(cfg)
    nodes = [
        dict(r)
        for r in conn.execute(
            """SELECT n.*, t.self_input + t.self_cached + t.self_cache_write + t.self_output AS self_total
               FROM nodes n LEFT JOIN node_totals t ON t.node_id = n.id
               WHERE n.tree_id IS NOT NULL"""
        )
    ]
    by_id = {n["id"]: n for n in nodes}
    out: list[tuple] = []

    def rule(name: str) -> dict | None:
        r = rules.get(name)
        if not isinstance(r, dict) or not r.get("enabled", True):
            return None
        return r

    def add(name: str, r: dict, node: dict, message: str) -> None:
        out.append((name, r.get("severity", "warn"), node["tree_id"], node["id"], message,
                    1 if name in HEURISTIC_RULES else 0, node.get("started")))

    if r := rule("depth"):
        limit = int(r.get("max_depth", 4))
        for n in nodes:
            if (n["depth"] or 0) > limit:
                add("depth", r, n, f"Depth {n['depth']} is above the limit of {limit}")

    if r := rule("concurrency"):
        limit = int(r.get("max_concurrent", 20))
        trees = defaultdict(list)
        for n in nodes:
            if n["kind"] in ROOT_KINDS:
                continue
            s, e = _ts(n["started"]), _ts(n["ended"]) or _ts(n["started"])
            if s is not None:
                trees[n["tree_id"]].append((s, 1, n))
                trees[n["tree_id"]].append((max(e, s), -1, n))
        for tree_id, events in trees.items():
            events.sort(key=lambda x: (x[0], x[1]))  # ends before starts at the same instant
            live = peak = 0
            peak_node = None
            for _, delta, n in events:
                live += delta
                if live > peak:
                    peak, peak_node = live, n
            if peak > limit and peak_node:
                add("concurrency", r, peak_node, f"{peak} agents ran at once (limit {limit})")

    if r := rule("blocked_model"):
        blocked = {m.lower() for m in r.get("models", [])}
        for n in nodes:
            if n["model"] and n["model"].lower() in blocked:
                add("blocked_model", r, n, f"{n['model']} is on the blocked list")

    if r := rule("openai_on_ui"):
        families = set(r.get("families", ["openai"]))
        kw = _keyword_re(r.get("keywords"))
        for n in nodes:
            if kw and n["family"] in families and n["kind"] not in ROOT_KINDS:
                m = kw.search(_text(n))
                if m:
                    add("openai_on_ui", r, n, f"{n['model'] or n['family']} on a task mentioning '{m.group(1)}' (heuristic)")

    if r := rule("same_family_critic"):
        kw = _keyword_re(r.get("critic_keywords"))
        siblings = defaultdict(list)
        for n in nodes:
            siblings[n["parent_id"]].append(n)
        for group in siblings.values():
            group.sort(key=lambda x: x["started"] or "")
            for i, n in enumerate(group):
                if not kw or not n["family"] or not kw.search(_text(n)):
                    continue
                builder = next((b for b in reversed(group[:i]) if b["family"] and not kw.search(_text(b))), None)
                if builder and builder["family"] == n["family"]:
                    add("same_family_critic", r, n,
                        f"Reviewer ({n['model']}) is from the same family as the builder it likely reviewed ({builder['model']}) (heuristic)")

    if r := rule("orphan"):
        for n in nodes:
            if n["link_confidence"] == "orphan":
                add("orphan", r, n, "Parent agent not found in the index")

    if r := rule("long_running"):
        limit = float(r.get("max_minutes", 30))
        include_roots = bool(r.get("include_roots", False))
        for n in nodes:
            if n["kind"] in ROOT_KINDS and not include_roots:
                continue
            s, e = _ts(n["started"]), _ts(n["ended"])
            if s is not None and e is not None and (e - s) / 60 > limit:
                add("long_running", r, n, f"Ran for {(e - s) / 60:.0f} min (limit {limit:.0f})")

    if r := rule("heavy_tokens"):
        limit = int(r.get("max_tokens", 2_000_000))
        include_roots = bool(r.get("include_roots", False))
        for n in nodes:
            if n["kind"] in ROOT_KINDS and not include_roots:
                continue
            if (n["self_total"] or 0) > limit:
                add("heavy_tokens", r, n, f"Used {n['self_total']:,} tokens (limit {limit:,})")

    if r := rule("expensive_model_trivial_task"):
        expensive = {m.lower() for m in r.get("models", [])}
        kw = _keyword_re(r.get("keywords"))
        for n in nodes:
            if kw and n["model"] and n["model"].lower() in expensive and n["kind"] not in ROOT_KINDS:
                m = kw.search(_text(n))
                if m:
                    add("expensive_model_trivial_task", r, n,
                        f"{n['model']} used for a task that looks like '{m.group(1)}' (heuristic)")

    if r := rule("duplicate_tasks"):
        threshold = float(r.get("similarity", 0.9))
        trees = defaultdict(list)
        for n in nodes:
            d = _norm_desc(n["description"])
            if n["kind"] not in ROOT_KINDS and len(d) >= 8:
                trees[n["tree_id"]].append((n["started"] or "", d, n))
        for items in trees.values():
            items.sort(key=lambda x: x[0])
            items = items[:400]
            seen: list[tuple[str, dict]] = []
            for _, d, n in items:
                for prev_d, prev in seen:
                    sm = SequenceMatcher(None, prev_d, d)
                    if sm.real_quick_ratio() >= threshold and sm.quick_ratio() >= threshold and sm.ratio() >= threshold:
                        add("duplicate_tasks", r, n, f"Near-identical to an earlier task: \"{clean_text(prev['description'], limit=80)}\" (heuristic)")
                        break
                seen.append((d, n))

    conn.execute("DELETE FROM flags")
    conn.executemany("INSERT INTO flags(rule, severity, tree_id, node_id, message, heuristic, ts) VALUES(?,?,?,?,?,?,?)", out)
    return len(out)
