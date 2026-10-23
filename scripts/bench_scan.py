#!/usr/bin/env python3
"""Measure Agent Tree scans without touching the user's index or logs.

Set ``AGENT_TREE_HOME`` to a temporary directory before running.  Use
``--seed-index`` to copy an existing index into that directory with SQLite's
backup API.  Source logs remain read-only and are selected by the normal
configuration environment variables.
"""

from __future__ import annotations

import argparse
import cProfile
import importlib
import os
import pstats
import resource
import sqlite3
import sys
import time
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _rss_mb() -> float:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes; Linux reports KiB.
    if sys.platform == "darwin":
        return value / (1024 * 1024)
    return value / 1024


def _usage() -> tuple[float, float]:
    u = resource.getrusage(resource.RUSAGE_SELF)
    return u.ru_utime + u.ru_stime, _rss_mb()


def _measure(name: str, fn: Callable, records: dict[str, list[dict]], *args, **kwargs):
    before_cpu, _ = _usage()
    started = time.perf_counter()
    result = fn(*args, **kwargs)
    cpu, rss = _usage()
    records.setdefault(name, []).append({
        "wall": time.perf_counter() - started,
        "cpu": cpu - before_cpu,
        "rss": rss,
    })
    return result


def _seed_index(source: Path, destination: Path) -> None:
    source_uri = f"file:{source}?mode=ro"
    with sqlite3.connect(source_uri, uri=True) as src:
        with sqlite3.connect(destination) as dst:
            src.backup(dst)


def _install_timing(records: dict[str, list[dict]]) -> None:
    """Wrap scan phases while leaving their behavior unchanged."""
    indexer = importlib.import_module("agent_tree.indexer")
    live = importlib.import_module("agent_tree.live")

    original_resolve = indexer.resolve
    original_rollup = indexer.rollup
    original_live_write = live.write

    def resolve(*args, **kwargs):
        return _measure("resolve", original_resolve, records, *args, **kwargs)

    def rollup(*args, **kwargs):
        return _measure("rollup", original_rollup, records, *args, **kwargs)

    def live_write(*args, **kwargs):
        return _measure("live.write", original_live_write, records, *args, **kwargs)

    indexer.resolve = resolve
    indexer.rollup = rollup
    live.write = live_write

    for name in indexer.SOURCES:
        try:
            module = importlib.import_module(f"agent_tree.sources.{name}")
        except ImportError:
            continue
        original = module.scan

        def source_scan(*args, _name=name, _original=original, **kwargs):
            return _measure(f"_scan/{_name}", _original, records, *args, **kwargs)

        module.scan = source_scan


def _print_records(records: dict[str, list[dict]]) -> None:
    print("phase                         runs  wall_s  cpu_s  peak_rss_mb")
    print("----------------------------  ----  ------  -----  -----------")
    phases = ("_scan/claude_code", "_scan/codex", "_scan/t3", "_scan/wrapper",
              "_scan/events", "resolve", "rollup", "live.write", "cache invalidation",
              "queries.summary", "queries.now", "limits", "meta")
    for phase in phases:
        values = records.get(phase, [])
        print(f"{phase:28}  {len(values):4d}  "
              f"{sum(v['wall'] for v in values):6.3f}  "
              f"{sum(v['cpu'] for v in values):5.3f}  "
              f"{max((v['rss'] for v in values), default=0):11.1f}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-n", "--iterations", type=int, default=3,
                        help="number of incremental scans (default: 3)")
    parser.add_argument("--seed-index", type=Path, metavar="PATH",
                        help="copy PATH into AGENT_TREE_HOME/index.sqlite before scanning")
    parser.add_argument("--profile", type=Path, metavar="PATH",
                        help="write a cProfile report to PATH")
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("--iterations must be positive")
    home_value = os.environ.get("AGENT_TREE_HOME")
    if not home_value:
        parser.error("AGENT_TREE_HOME must point to a temporary directory")
    home = Path(home_value).expanduser().resolve()
    home.mkdir(parents=True, exist_ok=True)

    # Import after AGENT_TREE_HOME is validated so all writes stay in the temp home.
    from agent_tree import config, db, indexer, limits, queries
    cfg = config.load_config(create=True)
    if args.seed_index:
        source = args.seed_index.expanduser().resolve()
        if not source.is_file():
            parser.error(f"seed index does not exist: {source}")
        _seed_index(source, cfg.db_path)
    conn = db.connect(cfg.db_path)
    records: dict[str, list[dict]] = {}
    _install_timing(records)

    profiler = cProfile.Profile() if args.profile else None
    started = time.perf_counter()
    before_cpu, _ = _usage()
    try:
        if profiler:
            profiler.enable()
        for _ in range(args.iterations):
            # The server clears this process-local cache after a successful scan.
            from agent_tree.cache import read_cache
            cache_started = time.perf_counter()
            cache_cpu_before, _ = _usage()
            read_cache.clear()
            cache_cpu, cache_rss = _usage()
            records.setdefault("cache invalidation", []).append({
                "wall": time.perf_counter() - cache_started,
                "cpu": cache_cpu - cache_cpu_before,
                "rss": cache_rss,
            })
            indexer.scan(conn, cfg, full=False)
            _measure("queries.summary", queries.summary, records, conn, cfg)
            _measure("queries.now", queries.now, records, conn, cfg)
            _measure("limits", limits.read_limits, records, cfg)
            _measure("meta", queries.meta, records, conn, cfg, False)
    finally:
        if profiler:
            profiler.disable()
            profile_path = args.profile.expanduser().resolve()
            profile_path.parent.mkdir(parents=True, exist_ok=True)
            profiler.dump_stats(str(profile_path))
            stats = pstats.Stats(profiler).sort_stats("cumulative")
            print(f"profile: {profile_path}")
            stats.print_stats(20)
        conn.close()

    cpu = _usage()[0] - before_cpu
    print(f"iterations: {args.iterations}")
    print(f"total wall_s: {time.perf_counter() - started:.3f}")
    print(f"total cpu_s:  {cpu:.3f}")
    print(f"peak rss_mb:  {_rss_mb():.1f}")
    _print_records(records)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
