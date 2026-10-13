"""Local web server: static UI plus the JSON API from docs/API.md.

Binds to 127.0.0.1 only, rejects requests whose Host header isn't local (DNS
rebinding guard) and requires a custom header on POST (CSRF guard).
"""

from __future__ import annotations

import json
import mimetypes
import threading
import time
import traceback
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import db, export, indexer, queries
from .config import Config

WEB_ROOT = Path(str(resources.files("agent_tree").joinpath("web")))
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


class ScanState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        self.last_result: dict | None = None

    def run(self, cfg: Config, full: bool = False) -> dict:
        if not self.lock.acquire(blocking=False):
            return {"ok": False, "error": "a scan is already running", "scan_running": True}
        from .indexer import ScanBusy
        self.running = True
        started = time.perf_counter()
        try:
            conn = db.connect(cfg.db_path)
            try:
                stats = indexer.scan(conn, cfg, full=full, wait=False)
            except ScanBusy:
                return {"ok": False, "error": "another process is scanning; try again shortly", "scan_running": True}
            finally:
                conn.close()
            self.last_result = {"ok": True, "seconds": round(time.perf_counter() - started, 2),
                                "stats": [s.as_dict() for s in stats]}
            return self.last_result
        except Exception as exc:
            traceback.print_exc()
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        finally:
            self.running = False
            self.lock.release()


def make_handler(cfg: Config, scan_state: ScanState):
    class Handler(BaseHTTPRequestHandler):
        server_version = "agent-tree"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # quiet by default
            pass

        # -- plumbing ---------------------------------------------------------------
        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").strip().lower()
            if host.startswith("["):  # [::1] or [::1]:8711
                end = host.find("]")
                rest = host[end + 1:]
                if end < 0 or (rest and not rest[1:].isdigit()) or (rest and rest[0] != ":"):
                    return False
                host = host[: end + 1]
            else:
                host = host.rsplit(":", 1)[0]
            return host in LOCAL_HOSTS

        def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'",
            )
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, payload, status: int = 200) -> None:
            self._send(status, json.dumps(payload, default=str).encode(), "application/json; charset=utf-8")

        def _error(self, status: int, message: str) -> None:
            self._json({"error": message}, status)

        # -- routing ----------------------------------------------------------------
        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            if not self._host_ok():
                return self._error(HTTPStatus.FORBIDDEN, "non-local Host header")
            url = urlparse(self.path)
            if url.path.startswith("/api/"):
                return self._api_get(url)
            return self._static(url.path)

        def do_POST(self):
            if not self._host_ok():
                return self._error(HTTPStatus.FORBIDDEN, "non-local Host header")
            if self.headers.get("X-Agent-Tree") != "1":
                return self._error(HTTPStatus.FORBIDDEN, "missing X-Agent-Tree header")
            url = urlparse(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(min(length, 65536)) if length else b""
            if url.path == "/api/scan":
                try:
                    full = bool(json.loads(body or b"{}").get("full"))
                except (ValueError, AttributeError):
                    full = False
                result = scan_state.run(cfg, full=full)
                return self._json(result, 200 if result.get("ok") else 409 if result.get("scan_running") else 500)
            return self._error(HTTPStatus.NOT_FOUND, "unknown endpoint")

        def _static(self, path: str) -> None:
            rel = unquote(path).lstrip("/") or "index.html"
            root = WEB_ROOT.resolve()
            try:
                target = (root / rel).resolve()
                ok = target.is_relative_to(root) and target.is_file()
            except (ValueError, OSError):  # e.g. a NUL byte in the path
                ok = False
            if not ok:
                target = WEB_ROOT / "index.html"  # hash routing: unknown paths get the app shell
                if not target.is_file():
                    return self._send(404, b"UI files are missing from this install.", "text/plain; charset=utf-8")
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript", "image/svg+xml"):
                ctype += "; charset=utf-8"
            self._send(200, target.read_bytes(), ctype)

        def _api_get(self, url) -> None:
            qs = {k: v[-1] for k, v in parse_qs(url.query).items()}
            path = url.path
            conn = db.connect(cfg.db_path)
            try:
                if path == "/api/meta":
                    return self._json(queries.meta(conn, cfg, scan_state.running or indexer.scan_in_progress(cfg)))
                if path == "/api/summary":
                    return self._json(queries.summary(conn, cfg))
                if path == "/api/threads":
                    return self._json(queries.list_threads(
                        conn, cfg, project=qs.get("project"), source=qs.get("source"), model=qs.get("model"),
                        since=qs.get("since"), until=qs.get("until"), flagged=qs.get("flagged") in ("1", "true"),
                        q=qs.get("q"), sort=qs.get("sort", "started"),
                        limit=max(1, min(int(qs.get("limit", 200)), 5000)), offset=max(0, int(qs.get("offset", 0)))))
                if path.startswith("/api/threads/"):
                    tid = unquote(path[len("/api/threads/"):])
                    detail = queries.thread_detail(conn, cfg, tid)
                    return self._json(detail) if detail else self._error(404, f"thread not found: {tid}")
                if path == "/api/projects":
                    return self._json(queries.projects(conn, cfg, qs.get("since", "90d")))
                if path == "/api/models":
                    return self._json(queries.models(conn, cfg, qs.get("since", "30d")))
                if path == "/api/flags":
                    return self._json(queries.flags(conn, cfg, qs.get("since"), qs.get("rule"), qs.get("severity")))
                if path.startswith("/api/export/"):
                    return self._export(conn, path[len("/api/export/"):], qs)
                return self._error(404, "unknown endpoint")
            except ValueError as exc:
                return self._error(400, str(exc))
            except Exception as exc:
                traceback.print_exc()
                return self._error(500, f"{type(exc).__name__}: {exc}")
            finally:
                conn.close()

        def _export(self, conn, rest: str, qs: dict) -> None:
            if rest.startswith("thread/"):
                name = unquote(rest[len("thread/"):])
                stem, _, ext = name.rpartition(".")
                detail = queries.thread_detail(conn, cfg, stem)
                if not detail:
                    return self._error(404, "thread not found")
                safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in detail["thread"]["id"])[:60]
                if ext == "json":
                    body, ctype = export.to_json(detail), "application/json"
                elif ext == "csv":
                    body, ctype = export.nodes_csv(detail["nodes"], detail["thread"]["id"]), "text/csv"
                elif ext == "md":
                    body, ctype = export.thread_markdown(detail), "text/markdown"
                else:
                    return self._error(400, "use .json, .csv or .md")
                return self._send(200, body.encode(), ctype + "; charset=utf-8",
                                  {"Content-Disposition": f'attachment; filename="agent-tree-{safe}.{ext}"'})
            if rest in ("range.json", "range.csv"):
                nodes = queries.range_nodes(conn, cfg, qs.get("since"), qs.get("until"))
                if rest.endswith(".json"):
                    body, ctype = export.to_json({"since": qs.get("since"), "until": qs.get("until"), "nodes": nodes}), "application/json"
                else:
                    body, ctype = export.nodes_csv(nodes), "text/csv"
                return self._send(200, body.encode(), ctype + "; charset=utf-8",
                                  {"Content-Disposition": f'attachment; filename="agent-tree-range.{rest.split(".")[-1]}"'})
            return self._error(404, "unknown export")

    return Handler


def serve(cfg: Config, port: int | None = None, open_browser: bool = False, scan_first: bool = True,
          rescan_every: int = 0) -> None:
    port = port or cfg.port
    scan_state = ScanState()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(cfg, scan_state))
    httpd.daemon_threads = True
    url = f"http://127.0.0.1:{port}/"

    def background():
        if scan_first:
            scan_state.run(cfg)
        while rescan_every > 0:
            time.sleep(rescan_every)
            scan_state.run(cfg)

    threading.Thread(target=background, daemon=True).start()
    print(f"Agent Tree is running at {url}  (Ctrl-C to stop)")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
