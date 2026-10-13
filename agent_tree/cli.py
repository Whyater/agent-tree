"""`att`: the Agent Tree command line."""

from __future__ import annotations

import argparse
import json
import os
import socket
import sqlite3
import sys
import time
from pathlib import Path

from . import __version__, db, export, indexer, queries
from .config import EDITABLE_FILES, Config, load_config, load_toml, reset_user_file

USE_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else text


def dim(t): return c(t, "2")
def bold(t): return c(t, "1")
def amber(t): return c(t, "33")
def red(t): return c(t, "31")
def green(t): return c(t, "32")


FAMILY_CODE = {"anthropic": "38;5;173", "openai": "38;5;74"}


def fam(text: str, family: str | None) -> str:
    return c(text, FAMILY_CODE.get(family or "", "37"))


def table(rows: list[list[str]], headers: list[str], align: str = "") -> str:
    """Plain fixed-width table. `align` is a string of l/r per column."""
    import re
    strip = re.compile(r"\033\[[0-9;]*m")
    width = [len(h) for h in headers]
    for r in rows:
        for i, cell in enumerate(r):
            width[i] = max(width[i], len(strip.sub("", cell)))
    def fmt(r, is_header=False):
        out = []
        for i, cell in enumerate(r):
            pad = width[i] - len(strip.sub("", cell))
            right = (align[i:i + 1] or "l") == "r"
            out.append((" " * pad + cell) if right else (cell + " " * pad))
        line = "  ".join(out).rstrip()
        return dim(line) if is_header else line
    return "\n".join([fmt(headers, True)] + [fmt(r) for r in rows])


def open_db(cfg: Config) -> sqlite3.Connection:
    conn = db.connect(cfg.db_path)
    if db.get_meta(conn, "last_scan") is None:
        print(dim("No index yet, running a first scan..."), file=sys.stderr)
        _do_scan(conn, cfg, full=False, quiet=True)
    return conn


def _do_scan(conn, cfg, full: bool, quiet: bool = False) -> None:
    first = db.get_meta(conn, "last_scan") is None
    t0 = time.perf_counter()
    stats = indexer.scan(conn, cfg, full=full)
    secs = time.perf_counter() - t0
    if quiet:
        return
    rows = []
    for s in stats:
        state = green("ok") if s.present and not s.errors else (red("error") if s.errors else dim("not found"))
        rows.append([s.source, state, str(s.files_seen), str(s.files_parsed), f"{s.lines_parsed:,}", str(s.lines_skipped),
                     (s.notes[-1] if s.notes else "")])
    print(table(rows, ["source", "status", "files", "changed", "lines read", "skipped", "time"], "llrrrrr"))
    for s in stats:
        for e in s.errors:
            print(red(f"  {s.source}: {e}"))
    n = conn.execute("SELECT COUNT(*) FROM nodes WHERE tree_id IS NOT NULL").fetchone()[0]
    t = conn.execute("SELECT COUNT(*) FROM nodes WHERE tree_id = id").fetchone()[0]
    f = conn.execute("SELECT COUNT(*) FROM flags").fetchone()[0]
    mode = "Full" if full or first else "Incremental"
    print(f"\n{bold(f'{t:,}')} threads, {n:,} agents, {f} flags. {mode} scan took {secs:.1f}s.")


# --- commands -----------------------------------------------------------------------

def cmd_scan(args, cfg):
    conn = db.connect(cfg.db_path)
    _do_scan(conn, cfg, full=args.full)


def cmd_serve(args, cfg):
    from .server import serve
    port = cfg.port if args.port is None else args.port
    if port and not _port_free(port):
        url = f"http://127.0.0.1:{port}/"
        print(f"Port {port} is busy. If Agent Tree is already running, open {url}")
        if args.open:
            import webbrowser
            webbrowser.open(url)
        return 1
    serve(cfg, port=port, open_browser=args.open, scan_first=not args.no_scan, rescan_every=args.rescan,
          ready_file=getattr(args, "ready_file", None), parent_pid=getattr(args, "parent_pid", None))


def _port_free(port: int) -> bool:
    """True unless something is actually accepting connections on the port.

    (A bind test gives false "busy" results while old connections sit in
    TIME_WAIT, e.g. right after a browser tab was talking to a stopped server.)
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) != 0


def _cost_cell(m: dict) -> str:
    s = export.fmt_cost(m)
    if m["covered_cost"] and not m["cost"]:
        return dim(export.fmt_cost({**m, "cost": m["covered_cost"]}) + " plan")
    return s


def cmd_threads(args, cfg):
    conn = open_db(cfg)
    res = queries.list_threads(conn, cfg, project=args.project, source=args.source, model=args.model,
                               since=args.since, flagged=args.flagged, q=args.search, sort=args.sort, limit=args.limit)
    if args.json:
        print(json.dumps(res, indent=2))
        return
    rows = []
    for t in res["threads"]:
        title = (t["title"] or dim("(untitled)"))
        title = title if len(title) <= 48 else title[:47] + "…"
        rows.append([
            dim((t["started"] or "")[:16].replace("T", " ")),
            title,
            (t["project_name"] or "")[:22],
            "/".join({"claude-code": "cc", "codex": "cx", "t3": "t3", "wrapper": "wr"}.get(s, s) for s in t["sources"]),
            str(t["node_count"]), str(t["max_depth"]),
            export.fmt_tokens(t["tokens"]["total"]), _cost_cell(t["money"]),
            amber(str(t["flag_count"])) if t["flag_count"] else "",
            dim(t["id"]),
        ])
    print(table(rows, ["started (UTC)", "title", "project", "src", "agents", "depth", "tokens", "est. cost", "flags", "id"], "llllrrrrrl"))
    print(dim(f"\n{len(res['threads'])} of {res['total']} threads. Costs are estimates."))


def cmd_tree(args, cfg):
    conn = open_db(cfg)
    detail = queries.thread_detail(conn, cfg, args.thread_id)
    if not detail:
        print(red(f"No thread matches '{args.thread_id}'. Try `att threads` for ids."), file=sys.stderr)
        return 1
    if args.markdown:
        print(export.thread_markdown(detail))
        return
    if args.json:
        print(export.to_json(detail))
        return
    t = detail["thread"]
    print(bold(t["title"] or t["id"]))
    print(dim(f"{t['id']}  ·  {t['project_path'] or '-'}  ·  {' → '.join(t['sources'])}"))
    print(f"{t['node_count']} agents, depth {t['max_depth']}, {export.fmt_ms(t['wall_ms'])}, "
          f"{export.fmt_tokens(t['tokens']['total'])} tokens, est. {_cost_cell(t['money'])}\n")
    nodes = detail["nodes"]
    # Draw tree connectors from depth-first order.
    last_at_depth: dict[int, bool] = {}
    for i, n in enumerate(nodes):
        d = n["depth"]
        is_last = not any(m["depth"] == d and m["parent_id"] == n["parent_id"] for m in nodes[i + 1:])
        last_at_depth[d] = is_last
        prefix = "".join("   " if last_at_depth.get(k) else "│  " for k in range(1, d))
        branch = ("└─ " if is_last else "├─ ") if d else ""
        which = n["subtree"] if args.subtree else n["self"]
        label = export.node_label(n)
        label = label if len(label) <= 56 else label[:55] + "…"
        bits = [fam(n["model"] or "?", n["family"]), export.fmt_ms(n["wall_ms"]),
                export.fmt_tokens(which["tokens"]["total"]) + " tok", _cost_cell(which["money"])]
        if n["status"] not in ("completed", "done"):
            bits.append(n["status"])
        if n["link_confidence"] != "exact":
            bits.append(amber(n["link_confidence"]))
        if n["flags"]:
            bits.append(amber("⚑ " + ",".join(n["flags"])))
        print(f"{dim(prefix + branch)}{label}  {dim('·')} " + f" {dim('·')} ".join(bits))
    if detail["flags"]:
        print()
        for f in detail["flags"]:
            sev = red if f["severity"] == "alert" else amber
            print(sev(f"⚑ {f['rule']}") + dim(" (heuristic)" if f["heuristic"] else "") + f"  {f['message']}")
    print(dim(f"\nTotals are {'subtree' if args.subtree else 'self'} values. Costs are estimates."))


def cmd_top(args, cfg):
    conn = open_db(cfg)
    sort = {"cost": "cost", "tokens": "tokens", "time": "started"}[args.by]
    res = queries.list_threads(conn, cfg, since=args.since, sort=sort, limit=5000)["threads"]
    if args.by == "time":
        res.sort(key=lambda t: t["wall_ms"] or 0, reverse=True)
    rows = []
    for i, t in enumerate(res[: args.limit], 1):
        title = t["title"] or "(untitled)"
        rows.append([str(i), title[:50], (t["project_name"] or "")[:20], export.fmt_ms(t["wall_ms"]),
                     export.fmt_tokens(t["tokens"]["total"]), _cost_cell(t["money"]), dim(t["id"])])
    print(table(rows, ["#", "title", "project", "wall", "tokens", "est. cost", "id"], "rllrrrl"))


def cmd_flags(args, cfg):
    conn = open_db(cfg)
    res = queries.flags(conn, cfg, since=args.since, rule=args.rule)
    if args.json:
        print(json.dumps(res, indent=2))
        return
    if not res["flags"]:
        print(green("No flags.") + dim(" Rules live in ~/.agent-tree/rules.toml."))
        return
    rows = []
    for f in res["flags"][: args.limit]:
        sev = red if f["severity"] == "alert" else amber
        rows.append([sev(f["rule"]), dim("heuristic") if f["heuristic"] else "", (f["thread_title"] or "")[:36], f["message"][:80], dim(f["node_id"] or "")])
    print(table(rows, ["rule", "", "thread", "message", "node"]))
    print(dim(f"\n{len(res['flags'])} flags."))


def cmd_export(args, cfg):
    conn = open_db(cfg)
    if args.thread_id:
        detail = queries.thread_detail(conn, cfg, args.thread_id)
        if not detail:
            print(red("thread not found"), file=sys.stderr)
            return 1
        out = {"json": lambda: export.to_json(detail), "csv": lambda: export.nodes_csv(detail["nodes"], detail["thread"]["id"]),
               "md": lambda: export.thread_markdown(detail)}[args.format]()
    else:
        nodes = queries.range_nodes(conn, cfg, args.since, args.until)
        if args.format == "md":
            print(red("Markdown export is per thread; use --format json or csv for ranges."), file=sys.stderr)
            return 1
        out = export.to_json({"since": args.since, "until": args.until, "nodes": nodes}) if args.format == "json" else export.nodes_csv(nodes)
    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
        print(f"Wrote {args.output}")
    else:
        sys.stdout.write(out)


def cmd_doctor(args, cfg):
    from .sources.base import readonly_sqlite
    ok = True
    def line(state, name, detail=""):
        mark = {"ok": green("ok  "), "warn": amber("warn"), "fail": red("fail"), "info": dim("info")}[state]
        print(f"  {mark}  {name:<26} {dim(detail)}")

    print(bold(f"Agent Tree {__version__}") + dim(f"  (python {sys.version.split()[0]})"))
    if sys.version_info < (3, 11):
        line("fail", "python", "3.11 or newer is required")
        ok = False
    print("\nIndex")
    line("ok", "home", str(cfg.home))
    for name in EDITABLE_FILES:
        try:
            load_toml(cfg.home, name)
            line("ok", name, str(cfg.home / name))
        except SystemExit as exc:
            line("fail", name, str(exc))
            ok = False
    conn = db.connect(cfg.db_path)
    last = db.get_meta(conn, "last_scan")
    line("ok" if last else "warn", "last scan", last or "never, run `att scan`")
    line("info", "privacy", "metadata-only mode" if cfg.metadata_only else f"descriptions kept, max {cfg.text_limit} chars")

    print("\nSources (all opened read-only)")
    claude = cfg.claude_dir / "projects"
    if claude.is_dir():
        n_main = sum(1 for _ in claude.glob("*/*.jsonl"))
        n_sub = sum(1 for _ in claude.glob("*/*/subagents/*.jsonl"))
        version = _latest_field(claude.glob("*/*.jsonl"), "version")
        line("ok" if os.access(claude, os.R_OK) else "fail", "claude code", f"{claude}  {n_main} sessions, {n_sub} subagent transcripts, client {version or '?'}")
    else:
        line("warn", "claude code", f"{claude} not found")
    codex = cfg.codex_dir / "sessions"
    if codex.is_dir():
        n = sum(1 for _ in codex.glob("*/*/*/*.jsonl"))
        version = _codex_version(codex)
        line("ok" if os.access(codex, os.R_OK) else "fail", "codex", f"{codex}  {n} rollouts, cli {version or '?'}")
    else:
        line("warn", "codex", f"{codex} not found")
    t3db = cfg.t3_dir / "userdata" / "state.sqlite"
    if t3db.is_file():
        try:
            with readonly_sqlite(t3db) as t3:
                migrations = t3.execute("SELECT COUNT(*) FROM effect_sql_migrations").fetchone()[0]
                threads = t3.execute("SELECT COUNT(*) FROM projection_threads").fetchone()[0]
            line("ok", "t3 code", f"{t3db}  {threads} threads, schema migration {migrations}")
        except sqlite3.Error as exc:
            line("warn", "t3 code", f"{t3db}: {exc}")
    else:
        line("warn", "t3 code", f"{t3db} not found")
    for d in cfg.wrapper_dirs:
        n = len(list(d.glob("*.md"))) if d.is_dir() else 0
        line("ok" if d.is_dir() else "info", "wrapper runs", f"{d}  {n} task files" if d.is_dir() else f"{d} not present (fine if you don't use the wrapper)")

    print("\nParsers (from the last scan)")
    rows = conn.execute(
        "SELECT source, COUNT(*) files, SUM(parsed_lines) parsed, SUM(skipped_lines) skipped, GROUP_CONCAT(DISTINCT parser_version) pv FROM files GROUP BY source").fetchall()
    if not rows:
        line("info", "parsers", "nothing indexed yet")
    for r in rows:
        rate = (r["skipped"] or 0) / max(1, (r["parsed"] or 0) + (r["skipped"] or 0))
        state = "ok" if rate < 0.001 else "warn"
        line(state, r["source"], f"{r['pv']}: {r['files']} files, {r['parsed'] or 0:,} lines read, {r['skipped'] or 0} skipped ({rate:.3%})")
    stats = json.loads(db.get_meta(conn, "last_scan_stats") or "[]")
    for s in stats:
        for e in s.get("errors", []):
            line("fail", s["source"], e)
            ok = False

    print("\nServer")
    line("ok" if _port_free(cfg.port) else "info", "port", f"127.0.0.1:{cfg.port} " + ("free" if _port_free(cfg.port) else "in use (maybe Agent Tree is already running)"))
    from .server import WEB_ROOT
    line("ok" if (WEB_ROOT / "index.html").is_file() else "fail", "web ui", str(WEB_ROOT))
    print()
    print(green("All good.") if ok else red("Some checks failed."))
    return 0 if ok else 1


def _latest_field(paths, field: str) -> str | None:
    newest = max(paths, key=lambda p: p.stat().st_mtime, default=None)
    if not newest:
        return None
    try:
        with open(newest, "rb") as fh:
            for i, raw in enumerate(fh):
                if i > 50:
                    break
                try:
                    obj = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(obj, dict) and obj.get(field):
                    return str(obj[field])
    except OSError:
        return None
    return None


def _codex_version(root: Path) -> str | None:
    days = sorted(root.glob("*/*/*"))
    for d in reversed(days[-3:]):
        files = sorted(d.glob("*.jsonl"))
        if files:
            try:
                with open(files[-1], "rb") as fh:
                    first = json.loads(fh.readline())
                return (first.get("payload") or {}).get("cli_version")
            except (OSError, ValueError):
                continue
    return None


def cmd_config(args, cfg):
    if args.reset:
        path = reset_user_file(cfg, args.reset)
        print(f"Reset {path} (old copy saved as .bak)")
        return
    print(f"Settings live in {cfg.home}:")
    for name in EDITABLE_FILES:
        print(f"  {cfg.home / name}")
    print(f"Index: {cfg.db_path}")


def cmd_integrations(args, cfg):
    from . import integrations
    if args.action == "show":
        st = integrations.status(cfg)
        c = st["claude"]
        print(f"Claude Code  {'connected' if c['connected'] else 'not connected'}  (status line: {c['statusline']}, hooks: {c['hooks']})")
        for name in ("codex", "t3", "vorssaint"):
            print(f"{name:<12} {'installed' if st[name]['installed'] else 'not installed'}")
        if not c["connected"]:
            print(dim("\nRun `att connect claude` to add live subagent hooks and the limits status line."))
        return
    if args.tool != "claude":
        print("Only Claude Code needs connecting; Codex and T3 Code are read directly.")
        return 1
    if args.action == "connect":
        st = integrations.status(cfg)["claude"]
        print(st["preview"])
        if not args.yes and input("\nApply? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Nothing changed.")
            return 1
        res = integrations.connect_claude(cfg)
    else:
        res = integrations.disconnect_claude(cfg)
    if not res["ok"]:
        print(red(res["error"]), file=sys.stderr)
        return 1
    print(green("Done.") + (f" Backup: {res['backup']}" if res.get("backup") else ""))
    if res.get("note"):
        print(res["note"])


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="att", description="Agent Tree: see where your AI agent compute goes.")
    p.add_argument("--version", action="version", version=f"agent-tree {__version__}")
    sub = p.add_subparsers(dest="cmd", metavar="command")

    s = sub.add_parser("scan", help="index new transcript data (incremental)")
    s.add_argument("--full", action="store_true", help="rebuild the whole index")
    s.set_defaults(fn=cmd_scan)

    s = sub.add_parser("serve", help="start the local web UI")
    s.add_argument("--port", type=int)
    s.add_argument("--open", action="store_true", help="open a browser tab")
    s.add_argument("--no-scan", action="store_true", help="don't rescan on startup")
    s.add_argument("--rescan", type=int, default=60, metavar="SECONDS", help="rescan interval while running (0 = off, default 60)")
    s.add_argument("--no-open", action="store_true", help=argparse.SUPPRESS)  # the default; accepted for apps
    s.add_argument("--ready-file", metavar="PATH", help="write {port, pid, version} here once listening (use with --port 0)")
    s.add_argument("--parent-pid", type=int, metavar="PID", help="exit when this process exits")
    s.set_defaults(fn=cmd_serve)

    s = sub.add_parser("open", help="start the UI and open it in the browser")
    s.add_argument("--port", type=int)
    s.set_defaults(fn=lambda a, c: cmd_serve(argparse.Namespace(port=a.port, open=True, no_scan=False, rescan=60, ready_file=None, parent_pid=None), c))

    s = sub.add_parser("threads", help="list threads, newest first")
    s.add_argument("--project")
    s.add_argument("--source", choices=["claude-code", "codex", "t3", "wrapper"])
    s.add_argument("--model")
    s.add_argument("--since", help="7d, 24h, today, 2026-10-01 …")
    s.add_argument("--flagged", action="store_true")
    s.add_argument("--search", "-q")
    s.add_argument("--sort", choices=["started", "cost", "tokens"], default="started")
    s.add_argument("--limit", type=int, default=40)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_threads)

    s = sub.add_parser("tree", help="print one thread's agent tree")
    s.add_argument("thread_id", help="thread id, node id, session id, or a unique prefix")
    s.add_argument("--markdown", action="store_true", help="Markdown for session notes")
    s.add_argument("--json", action="store_true")
    s.add_argument("--subtree", action="store_true", help="show subtree totals instead of self")
    s.set_defaults(fn=cmd_tree)

    s = sub.add_parser("top", help="most expensive threads")
    s.add_argument("--by", choices=["cost", "tokens", "time"], default="cost")
    s.add_argument("--since", default="7d")
    s.add_argument("--limit", type=int, default=15)
    s.set_defaults(fn=cmd_top)

    s = sub.add_parser("flags", help="list rule violations")
    s.add_argument("--since")
    s.add_argument("--rule")
    s.add_argument("--limit", type=int, default=100)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_flags)

    s = sub.add_parser("export", help="export a thread or a date range")
    s.add_argument("thread_id", nargs="?")
    s.add_argument("--format", choices=["json", "csv", "md"], default="json")
    s.add_argument("--since")
    s.add_argument("--until")
    s.add_argument("-o", "--output")
    s.set_defaults(fn=cmd_export)

    s = sub.add_parser("integrations", help="show live integrations")
    s.set_defaults(fn=cmd_integrations, action="show", tool=None)
    for verb in ("connect", "disconnect"):
        s = sub.add_parser(verb, help=f"{verb} live tracking for a tool (claude)")
        s.add_argument("tool", choices=["claude"])
        if verb == "connect":
            s.add_argument("--yes", "-y", action="store_true", help="don't ask for confirmation")
        s.set_defaults(fn=cmd_integrations, action=verb, yes=False)

    s = sub.add_parser("doctor", help="check sources, parsers and settings")
    s.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("config", help="show or reset settings files")
    s.add_argument("--reset", choices=list(EDITABLE_FILES))
    s.set_defaults(fn=cmd_config)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "fn", None):
        parser.print_help()
        return 0
    cfg = load_config()
    try:
        return args.fn(args, cfg) or 0
    except ValueError as exc:
        print(red(f"att: {exc}"), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
