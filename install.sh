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
#
# On macOS it also installs the menu bar app (Agent Tree.app) from the latest release,
# into /Applications if you can write there, else ~/Applications. Files fetched by curl
# are not quarantined, so macOS shows no "can't verify developer" prompt.
#
# Flags:   --no-app      CLI only (same as AGENT_TREE_NO_APP=1)
#          --app-only    only the macOS app
#          --uninstall   remove the CLI and the app (never touches ~/.agent-tree)
# Env:     AGENT_TREE_APP_DIR   folder to put the app in
#          AGENT_TREE_NO_OPEN=1 do not launch the app after installing
set -eu

REPO="Whyater/agent-tree"
REF="${AGENT_TREE_REF:-main}"
PREFIX="${AGENT_TREE_PREFIX:-$HOME/.local/share/agent-tree}"
BIN_DIR="${AGENT_TREE_BIN:-$HOME/.local/bin}"
PY_RELEASE="20261009"
PY_VERSION="3.12.15"
APP_NAME="Agent Tree.app"
APP_ID="io.github.whyater.agent-tree"
RELEASES_URL="https://github.com/$REPO/releases/latest"

say() { printf '%s\n' "$*"; }
die() { printf 'install.sh: %s\n' "$*" >&2; exit 1; }

DO_CLI=1
DO_APP=1
UNINSTALL=0
[ "${AGENT_TREE_NO_APP:-}" = "1" ] && DO_APP=0
for arg in "$@"; do
  case "$arg" in
    --uninstall) UNINSTALL=1 ;;
    --no-app) DO_APP=0 ;;
    --app-only) DO_CLI=0 ;;
    *) die "unknown option: $arg (use --no-app, --app-only or --uninstall)" ;;
  esac
done
[ "$DO_CLI" = 0 ] && [ "$DO_APP" = 0 ] && die "--app-only and --no-app cannot be combined"
IS_MAC=0
[ "$(uname -s)" = "Darwin" ] && IS_MAC=1
[ "$IS_MAC" = 0 ] && DO_APP=0
if [ "$DO_CLI" = 0 ] && [ "$DO_APP" = 0 ]; then
  die "the menu bar app is macOS only; run the installer without --app-only"
fi

# Temp paths cleaned up on exit; the app part adds to these.
TMP=""
STAGE=""
APP_TMP=""
MNT=""
cleanup() {
  if [ -n "$MNT" ] && [ -d "$MNT" ]; then
    hdiutil detach -quiet "$MNT" >/dev/null 2>&1 || hdiutil detach -quiet -force "$MNT" >/dev/null 2>&1 || true
  fi
  [ -n "$TMP" ] && rm -rf "$TMP"
  [ -n "$STAGE" ] && rm -rf "$STAGE"
  [ -n "$APP_TMP" ] && rm -rf "$APP_TMP"
  return 0
}
trap cleanup EXIT
trap 'exit 1' INT TERM HUP

# --- app helpers (macOS) ---

# Folders to look in for an installed app.
app_dirs() {
  if [ -n "${AGENT_TREE_APP_DIR:-}" ]; then
    printf '%s\n' "$AGENT_TREE_APP_DIR"
  else
    printf '%s\n' "/Applications" "$HOME/Applications"
  fi
}

# Quit the running app, wait up to ~10 seconds. Does nothing if it is not running.
quit_app() {
  command -v osascript >/dev/null 2>&1 || return 0
  [ "$(osascript -e "application id \"$APP_ID\" is running" 2>/dev/null || echo false)" = "true" ] || return 0
  say "Quitting Agent Tree..."
  osascript -e "quit app id \"$APP_ID\"" >/dev/null 2>&1 || true
  i=0
  while [ "$i" -lt 20 ]; do
    [ "$(osascript -e "application id \"$APP_ID\" is running" 2>/dev/null || echo false)" = "true" ] || return 0
    sleep 0.5
    i=$((i + 1))
  done
  say "Agent Tree is still running; quit it and run the installer again."
  return 1
}

uninstall_app() {
  app_dirs | while IFS= read -r d; do
    if [ -d "$d/$APP_NAME" ]; then
      quit_app || exit 1
      rm -rf "${d:?}/$APP_NAME"
      say "Removed $d/$APP_NAME."
    fi
  done
}

# Prints the first release asset URL matching a pattern (no jq needed).
release_url() {
  printf '%s\n' "$2" | grep -o '"browser_download_url": *"[^"]*"' | sed 's/^[^:]*: *"//; s/"$//' | grep -E -e "$1" | head -n 1
}

# Install the app. Every failure returns 1 so the caller can carry on.
install_app() {
  case "$(uname -m)" in
    arm64|aarch64) ARCH="arm64" ;;
    x86_64) ARCH="x86_64" ;;
    *) say "No app build for $(uname -m)."; return 1 ;;
  esac
  for tool in hdiutil ditto codesign shasum; do
    command -v "$tool" >/dev/null 2>&1 || { say "$tool is missing."; return 1; }
  done

  say "Finding the latest Agent Tree app ($ARCH)..."
  JSON="$(curl -fsSL -H 'Accept: application/vnd.github+json' "https://api.github.com/repos/$REPO/releases/latest")" \
    || { say "Could not read the latest release."; return 1; }
  DMG_URL="$(release_url "-${ARCH}\\.dmg\$" "$JSON")"
  [ -n "$DMG_URL" ] || { say "The latest release has no $ARCH disk image."; return 1; }
  SUM_URL="$(release_url "-${ARCH}\\.dmg\\.sha256\$" "$JSON")"

  APP_TMP="$(mktemp -d)" || return 1
  DMG="$APP_TMP/$(basename "$DMG_URL")"
  say "Downloading $(basename "$DMG_URL")..."
  curl -fL --retry 3 --retry-all-errors --progress-bar "$DMG_URL" -o "$DMG" || { say "App download failed."; return 1; }

  # Checksum when the release publishes one.
  VERIFIED_SUM=0
  if [ -n "$SUM_URL" ]; then
    WANT="$(curl -fsSL "$SUM_URL" | awk '{print $1; exit}')" || WANT=""
    GOT="$(shasum -a 256 "$DMG" | awk '{print $1}')"
    if [ -z "$WANT" ] || [ "$WANT" != "$GOT" ]; then
      say "Checksum of the app download did not match; not installing it."
      return 1
    fi
    VERIFIED_SUM=1
  fi

  MNT="$APP_TMP/mnt"
  mkdir -p "$MNT"
  hdiutil attach -nobrowse -readonly -quiet -mountpoint "$MNT" "$DMG" >/dev/null \
    || { say "Could not open the disk image."; MNT=""; return 1; }
  [ -d "$MNT/$APP_NAME" ] || { say "The disk image does not contain $APP_NAME."; return 1; }

  # Pick the folder: given, else /Applications if writable, else ~/Applications.
  if [ -n "${AGENT_TREE_APP_DIR:-}" ]; then
    DEST="$AGENT_TREE_APP_DIR"
  elif [ -w /Applications ]; then
    DEST="/Applications"
  else
    DEST="$HOME/Applications"
  fi
  mkdir -p "$DEST" || { say "Could not create $DEST."; return 1; }

  # Copy next to the final spot, check it, then swap it in.
  NEW="$DEST/.Agent Tree.new.app"
  rm -rf "$NEW"
  ditto "$MNT/$APP_NAME" "$NEW" || { rm -rf "$NEW"; say "Could not copy the app into $DEST."; return 1; }
  xattr -dr com.apple.quarantine "$NEW" >/dev/null 2>&1 || true
  if ! codesign --verify --deep --strict "$NEW" >/dev/null 2>&1; then
    rm -rf "$NEW"
    say "The app's signature check failed; not installing it."
    return 1
  fi
  hdiutil detach -quiet "$MNT" >/dev/null 2>&1 || true

  if [ -d "$DEST/$APP_NAME" ]; then
    quit_app || { rm -rf "$NEW"; return 1; }
    rm -rf "$DEST/$APP_NAME"
  fi
  mv "$NEW" "$DEST/$APP_NAME" || { rm -rf "$NEW"; say "Could not move the app into place."; return 1; }
  APP_PATH="$DEST/$APP_NAME"
  say "Agent Tree app is installed: $APP_PATH"
  if [ "${AGENT_TREE_NO_OPEN:-}" != "1" ]; then
    open "$APP_PATH" >/dev/null 2>&1 || say "Installed, but could not open it; open it from $DEST."
  fi
  return 0
}

# Run the app install; the CLI install stands even if this fails.
try_app() {
  APP_OK=0
  if install_app; then
    APP_OK=1
  else
    say "App not installed. Get it manually from $RELEASES_URL"
  fi
  [ "$APP_OK" = 1 ] || [ "$DO_CLI" = 1 ] || exit 1
  return 0
}

# --- uninstall ---

if [ "$UNINSTALL" = 1 ]; then
  if [ "$DO_CLI" = 1 ]; then
    rm -rf "$PREFIX" "$BIN_DIR/att"
    say "Removed $PREFIX and $BIN_DIR/att. Your index and settings in ~/.agent-tree were left alone."
  fi
  if [ "$IS_MAC" = 1 ] && [ "$DO_APP" = 1 ]; then
    uninstall_app
  fi
  exit 0
fi

[ "$(id -u)" = "0" ] && die "please run this as your normal user, not root (no sudo needed)"
command -v curl >/dev/null 2>&1 || die "curl is required"

if [ "$DO_CLI" = 0 ]; then
  try_app
  exit 0
fi

command -v tar >/dev/null 2>&1 || die "tar is required"

mkdir -p "$PREFIX" "$BIN_DIR"
TMP="$(mktemp -d)"
STAGE="$PREFIX/app.new"

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
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -R "$SRC/agent_tree" "$STAGE/"
find "$STAGE" -name '__pycache__' -type d -prune -exec rm -rf {} +

# 2. Python: prefer a system Python 3.11+, else a private standalone build.
PY=""
for cand in python3.14 python3.13 python3.12 python3.11 python3; do
  # On a Mac without the developer tools, /usr/bin/python3 is a stub that pops an install dialog.
  if [ "$(command -v "$cand" 2>/dev/null)" = "/usr/bin/$cand" ] && [ "$IS_MAC" = 1 ] \
     && ! xcode-select -p >/dev/null 2>&1; then
    continue
  fi
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

# 5. The menu bar app (macOS only).
if [ "$DO_APP" = 1 ]; then
  say ""
  try_app
fi

say ""
if [ "${APP_OK:-0}" = 1 ] && [ "${AGENT_TREE_NO_OPEN:-}" != "1" ]; then
  say "The app is starting; look for its icon in the menu bar."
fi
say "Next:  att open      # index your sessions and open the dashboard"
say "       att doctor    # check what was found"
