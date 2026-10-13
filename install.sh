#!/bin/sh
# Agent Tree installer. No sudo, no pip, no dependencies.
#
#   curl -fsSL https://raw.githubusercontent.com/Whyater/agent-tree/main/install.sh | sh
#
# or, from a clone:   ./install.sh
# uninstall:          ./install.sh --uninstall   (keeps your index in ~/.agent-tree)
#
# Installs into ~/.local/share/agent-tree and puts an `att` command in ~/.local/bin.
# If no Python 3.11+ is on your PATH it downloads a private standalone Python
# into the same folder (about 25 MB).
set -eu

REPO="Whyater/agent-tree"
REF="${AGENT_TREE_REF:-main}"
PREFIX="${AGENT_TREE_PREFIX:-$HOME/.local/share/agent-tree}"
BIN_DIR="${AGENT_TREE_BIN:-$HOME/.local/bin}"
PY_RELEASE="20261009"
PY_VERSION="3.12.15"

say() { printf '%s\n' "$*"; }
die() { printf 'install.sh: %s\n' "$*" >&2; exit 1; }

if [ "${1:-}" = "--uninstall" ]; then
  rm -rf "$PREFIX" "$BIN_DIR/att"
  say "Removed $PREFIX and $BIN_DIR/att. Your index and settings in ~/.agent-tree were left alone."
  exit 0
fi

[ "$(id -u)" = "0" ] && die "please run this as your normal user, not root (no sudo needed)"
command -v curl >/dev/null 2>&1 || die "curl is required"
command -v tar >/dev/null 2>&1 || die "tar is required"

mkdir -p "$PREFIX" "$BIN_DIR"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# 1. Source: use the local checkout when run from one, otherwise download.
SCRIPT_DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd || echo "")"
if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/agent_tree/__init__.py" ]; then
  say "Installing from $SCRIPT_DIR"
  SRC="$SCRIPT_DIR"
else
  say "Downloading Agent Tree ($REF)..."
  curl -fsSL "https://github.com/$REPO/archive/$REF.tar.gz" -o "$TMP/src.tar.gz" || die "download failed"
  tar -xzf "$TMP/src.tar.gz" -C "$TMP"
  SRC="$(find "$TMP" -maxdepth 1 -type d -name 'agent-tree-*' | head -n 1)"
  [ -n "$SRC" ] || die "unexpected archive layout"
fi
# Stage the new copy next to the old one; it only replaces the live one after it runs.
STAGE="$PREFIX/app.new"
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -R "$SRC/agent_tree" "$STAGE/"
find "$STAGE" -name '__pycache__' -type d -prune -exec rm -rf {} +

# 2. Python: prefer a system Python 3.11+, else a private standalone build.
PY=""
for cand in python3.14 python3.13 python3.12 python3.11 python3; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    PY="$(command -v "$cand")"
    break
  fi
done
if [ -z "$PY" ]; then
  if [ -x "$PREFIX/python/bin/python3" ]; then
    PY="$PREFIX/python/bin/python3"
  else
    case "$(uname -s)-$(uname -m)" in
      Darwin-arm64) TRIPLE="aarch64-apple-darwin" ;;
      Darwin-x86_64) TRIPLE="x86_64-apple-darwin" ;;
      Linux-x86_64) TRIPLE="x86_64-unknown-linux-gnu" ;;
      Linux-aarch64) TRIPLE="aarch64-unknown-linux-gnu" ;;
      *) die "no Python 3.11+ found and no standalone build for $(uname -sm); install Python 3.11+ and rerun" ;;
    esac
    say "No Python 3.11+ found, downloading a private copy (about 25 MB)..."
    NAME="cpython-${PY_VERSION}%2B${PY_RELEASE}-${TRIPLE}-install_only_stripped.tar.gz"
    curl -fL --progress-bar "https://github.com/astral-sh/python-build-standalone/releases/download/${PY_RELEASE}/${NAME}" \
      -o "$TMP/python.tar.gz" || die "Python download failed"
    tar -xzf "$TMP/python.tar.gz" -C "$PREFIX"
    PY="$PREFIX/python/bin/python3"
  fi
fi

# 3. Check the staged copy with the chosen Python, then swap it in.
PYTHONPATH="$STAGE" "$PY" -m agent_tree --version >/dev/null 2>&1 || { rm -rf "$STAGE"; die "the new version did not start with $PY; your existing install was left as it was"; }
rm -rf "$PREFIX/app.old"
[ -d "$PREFIX/app" ] && mv "$PREFIX/app" "$PREFIX/app.old"
mv "$STAGE" "$PREFIX/app"
rm -rf "$PREFIX/app.old"

# 4. The `att` command.
cat > "$BIN_DIR/att" <<EOF
#!/bin/sh
PYTHONPATH="$PREFIX/app\${PYTHONPATH:+:\$PYTHONPATH}" exec "$PY" -m agent_tree "\$@"
EOF
chmod +x "$BIN_DIR/att"
"$BIN_DIR/att" --version >/dev/null || die "the installed command did not run"

say ""
say "Agent Tree is installed: $BIN_DIR/att  (Python: $PY)"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) say ""
     say "Add $BIN_DIR to your PATH, for example:"
     say "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc && source ~/.zshrc" ;;
esac
say ""
say "Next:  att open      # index your sessions and open the dashboard"
say "       att doctor    # check what was found"
