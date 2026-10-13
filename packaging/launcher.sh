#!/bin/bash
# Starts/stops the Agent Tree server for the macOS app bundle.
# Usage: launcher.sh start|stop|open
set -u
RES="$(cd "$(dirname "$0")" && pwd)"
PY="$RES/python/bin/python3"
STATE="${AGENT_TREE_HOME:-$HOME/.agent-tree}"
PIDFILE="$STATE/app-server.pid"
LOG="$STATE/app.log"
export PYTHONPATH="$RES/app"
export PYTHONDONTWRITEBYTECODE=1
mkdir -p "$STATE"

port() { "$PY" -c 'from agent_tree.config import load_config; print(load_config().port)' 2>/dev/null || echo 8711; }
up() { curl -fsS -m 1 "http://127.0.0.1:$1/api/meta" >/dev/null 2>&1; }

case "${1:-start}" in
  start)
    P="$(port)"
    if ! up "$P"; then
      nohup "$PY" -m agent_tree serve --port "$P" >>"$LOG" 2>&1 &
      echo $! >"$PIDFILE"
      for _ in $(seq 1 50); do up "$P" && break; sleep 0.1; done
    fi
    open "http://127.0.0.1:$P/"
    ;;
  open)
    open "http://127.0.0.1:$(port)/"
    ;;
  stop)
    if [ -f "$PIDFILE" ]; then
      kill "$(cat "$PIDFILE")" 2>/dev/null || true
      rm -f "$PIDFILE"
    fi
    ;;
esac
