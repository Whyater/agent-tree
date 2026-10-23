#!/bin/bash
# Builds "Agent Tree.app" with a bundled Python and wraps it in a .dmg.
# No sudo. Needs the Swift compiler (Xcode or its Command Line Tools); uses codesign (ad-hoc) and hdiutil.
#
#   packaging/build_app.sh [arm64|x86_64]     (default: this Mac's architecture)
#
# Output: packaging/out/AgentTree-<version>-<arch>.dmg
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
ARCH="${1:-$(uname -m)}"
PY_RELEASE="20261009"
PY_VERSION="3.12.15"
case "$ARCH" in
  arm64|aarch64) PBS_ARCH="aarch64"; ARCH="arm64" ;;
  x86_64) PBS_ARCH="x86_64" ;;
  *) echo "unknown arch: $ARCH" >&2; exit 2 ;;
esac
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' agent_tree/__init__.py)"
OUT="$ROOT/packaging/out"
CACHE="$ROOT/packaging/.cache"
APP="$OUT/$ARCH/Agent Tree.app"
mkdir -p "$OUT" "$CACHE"

TARBALL="cpython-${PY_VERSION}+${PY_RELEASE}-${PBS_ARCH}-apple-darwin-install_only_stripped.tar.gz"
if [ ! -f "$CACHE/$TARBALL" ]; then
  echo "Downloading standalone Python ($PBS_ARCH)..."
  curl -fL --progress-bar -o "$CACHE/$TARBALL.part" \
    "https://github.com/astral-sh/python-build-standalone/releases/download/${PY_RELEASE}/${TARBALL/+/%2B}"
  mv "$CACHE/$TARBALL.part" "$CACHE/$TARBALL"
fi

echo "Building $APP"
rm -rf "$OUT/$ARCH"
mkdir -p "$OUT/$ARCH"
# The native app (menu bar panel, desktop window, settings) is compiled from macos/Sources.
packaging/build_menubar.sh "$APP" "$ARCH"
RES="$APP/Contents/Resources"
tar -xzf "$CACHE/$TARBALL" -C "$RES"           # creates $RES/python
rm -rf "$RES/python/lib/python3.12/test" "$RES/python/lib/python3.12/idlelib" "$RES/python/lib/python3.12/tkinter" \
       "$RES/python/lib/python3.12/turtledemo" "$RES/python/lib/python3.12/ensurepip" "$RES/python/lib/python3.12/site-packages/pip"* \
       "$RES/python/lib/tcl"* "$RES/python/lib/tk"* "$RES/python/lib/itcl"* "$RES/python/lib/thread"* 2>/dev/null || true
mkdir -p "$RES/app"
rsync -a --exclude '__pycache__' --exclude '*.pyc' agent_tree "$RES/app/"
rm -rf "$RES/Fixtures"  # sample data is only for development builds

PLIST="$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$PLIST" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string $VERSION" "$PLIST"

# Smoke test the bundled interpreter before signing.
PYTHONPATH="$RES/app" "$RES/python/bin/python3" -c "import agent_tree.cli, agent_tree.server; print('bundle ok')"

# Ad-hoc signature so Gatekeeper sees a consistent bundle (not notarized).
# --deep signs the bundled Python too, but drops the widget's sandbox entitlements, so
# the widget is signed again with them and the app re-sealed around it.
codesign --force --deep --sign - "$APP"
codesign --force --sign - --entitlements macos/Widget/AgentTreeWidget.entitlements "$APP/Contents/PlugIns/AgentTreeWidget.appex"
codesign --force --sign - "$APP"

DMG="$OUT/AgentTree-${VERSION}-${ARCH}.dmg"
STAGE="$OUT/$ARCH/dmg"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
rm -f "$DMG"
hdiutil create -quiet -volname "Agent Tree" -srcfolder "$STAGE" -fs HFS+ -format UDZO "$DMG"
rm -rf "$STAGE"
( cd "$OUT" && shasum -a 256 "$(basename "$DMG")" > "$(basename "$DMG").sha256" )
echo "Built $DMG ($(du -h "$DMG" | cut -f1))"
