#!/bin/bash
# Builds the native "Agent Tree" app shell (Swift/SwiftUI) without the Python runtime.
# packaging/build_app.sh copies python + agent_tree into Contents/Resources and signs.
#
#   packaging/build_menubar.sh <out.app> [arm64|x86_64]
#
# Dev loop, on sample data:
#   packaging/build_menubar.sh /tmp/AgentTreeDev.app
#   AGENT_TREE_MOCK=1 /tmp/AgentTreeDev.app/Contents/MacOS/AgentTree
# Against the server running from this checkout:
#   AGENT_TREE_PYTHON=python3 AGENT_TREE_APP_PATH="$PWD" /tmp/AgentTreeDev.app/Contents/MacOS/AgentTree
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"

if [ $# -lt 1 ]; then
  echo "usage: $0 <out.app> [arm64|x86_64]" >&2
  exit 2
fi
OUT_APP="$1"
ARCH="${2:-arm64}"
case "$ARCH" in
  arm64|aarch64) ARCH="arm64" ;;
  x86_64) ;;
  *) echo "unknown arch: $ARCH" >&2; exit 2 ;;
esac

if ! xcrun --find swiftc >/dev/null 2>&1; then
  echo "swiftc not found. Install Xcode or the Command Line Tools." >&2
  exit 1
fi
SDK="$(xcrun --sdk macosx --show-sdk-path)"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' agent_tree/__init__.py)"
VERSION="${VERSION:-0.0.0}"

rm -rf "$OUT_APP"
mkdir -p "$OUT_APP/Contents/MacOS" "$OUT_APP/Contents/Resources"

echo "Compiling Swift sources ($ARCH)"
xcrun swiftc -O -swift-version 5 \
  -sdk "$SDK" -target "$ARCH-apple-macos13.0" \
  -module-name AgentTree \
  -o "$OUT_APP/Contents/MacOS/AgentTree" \
  macos/Sources/*.swift

# Desktop widget (WidgetKit extension, macOS 14 and later). It shares the tree mark
# from Theme.swift and reads ~/.agent-tree/live.json, nothing else.
WIDGET="$OUT_APP/Contents/PlugIns/AgentTreeWidget.appex"
mkdir -p "$WIDGET/Contents/MacOS"
echo "Compiling widget ($ARCH)"
# Like Xcode, start the extension in _NSExtensionMain; with the default entry point the
# widget exits as soon as macOS asks it for its sizes and never reaches the gallery.
xcrun swiftc -O -swift-version 5 -parse-as-library -application-extension \
  -Xlinker -e -Xlinker _NSExtensionMain \
  -sdk "$SDK" -target "$ARCH-apple-macos14.0" \
  -module-name AgentTreeWidget \
  -o "$WIDGET/Contents/MacOS/AgentTreeWidget" \
  macos/Widget/*.swift macos/Sources/Theme.swift

cat > "$WIDGET/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key><string>en</string>
  <key>CFBundleExecutable</key><string>AgentTreeWidget</string>
  <key>CFBundleIdentifier</key><string>io.github.whyater.agent-tree.widget</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleName</key><string>Agent Tree Widget</string>
  <key>CFBundleDisplayName</key><string>Agent Tree</string>
  <key>CFBundlePackageType</key><string>XPC!</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>NSHumanReadableCopyright</key><string>MIT License</string>
  <key>NSExtension</key>
  <dict>
    <key>NSExtensionPointIdentifier</key><string>com.apple.widgetkit-extension</string>
  </dict>
</dict>
</plist>
PLIST

cp packaging/AppIcon.icns "$OUT_APP/Contents/Resources/AppIcon.icns"
mkdir -p "$OUT_APP/Contents/Resources/Fixtures"
cp macos/Fixtures/*.json "$OUT_APP/Contents/Resources/Fixtures/"

cat > "$OUT_APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key><string>en</string>
  <key>CFBundleExecutable</key><string>AgentTree</string>
  <key>CFBundleIdentifier</key><string>io.github.whyater.agent-tree</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleName</key><string>Agent Tree</string>
  <key>CFBundleDisplayName</key><string>Agent Tree</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSUIElement</key><false/>
  <key>LSApplicationCategoryType</key><string>public.app-category.developer-tools</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSSupportsAutomaticGraphicsSwitching</key><true/>
  <key>NSHumanReadableCopyright</key><string>MIT License</string>
  <key>NSAppTransportSecurity</key>
  <dict>
    <key>NSAllowsLocalNetworking</key><true/>
  </dict>
</dict>
</plist>
PLIST
printf 'APPL????' > "$OUT_APP/Contents/PkgInfo"

# Ad-hoc signature so the dev build launches cleanly; build_app.sh re-signs the full bundle.
# A signing failure stops the build unless ALLOW_UNSIGNED=1 is set.
# The widget is signed first, with its sandbox entitlements, then sealed into the app.
# Set SIGN_IDENTITY to a code signing identity (see `security find-identity -v -p
# codesigning`) to sign with it instead of ad-hoc.
SIGN_IDENTITY=${SIGN_IDENTITY:--}
if ! { codesign --force --sign "$SIGN_IDENTITY" --entitlements macos/Widget/AgentTreeWidget.entitlements "$WIDGET" \
       && codesign --force --sign "$SIGN_IDENTITY" "$OUT_APP"; }; then
  if [ "${ALLOW_UNSIGNED:-0}" = "1" ]; then
    echo "warning: codesign failed; continuing unsigned because ALLOW_UNSIGNED=1" >&2
  else
    echo "error: codesign failed (set ALLOW_UNSIGNED=1 to continue without a signature)" >&2
    exit 1
  fi
fi

echo "Built $OUT_APP"
