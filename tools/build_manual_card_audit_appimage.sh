#!/usr/bin/env bash
set -euo pipefail

# 在 Ubuntu/WSL 中运行。首次运行会安装构建依赖，并生成 AppImage。
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="$PROJECT_DIR/.codex-temp/appimage_manual_card_audit"
APPDIR="$BUILD_DIR/AppDir"
DIST_DIR="$PROJECT_DIR/校对工具"
PYTHON_BIN="${PYTHON_BIN:-python3}"

mkdir -p "$BUILD_DIR" "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor/256x256/apps" "$DIST_DIR"

if ! dpkg-query -W -f='${Status}' python3-tk 2>/dev/null | grep -q 'install ok installed'; then
  sudo apt-get update
  sudo apt-get install -y python3 python3-venv python3-tk wget file
fi

VENV_DIR="$BUILD_DIR/venv"
if [ ! -x "$VENV_DIR/bin/python" ]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/python" -m pip install --upgrade pip pyinstaller pillow certifi
"$VENV_DIR/bin/python" -m PyInstaller --noconfirm --clean --onefile --name manual-card-audit-editor-linux \
  --distpath "$BUILD_DIR/dist" \
  --workpath "$BUILD_DIR/work" \
  --specpath "$BUILD_DIR" \
  --collect-data certifi \
  --hidden-import PIL._tkinter_finder \
  "$PROJECT_DIR/tools/manual_card_audit_visual_editor.py"

cp "$BUILD_DIR/dist/manual-card-audit-editor-linux" "$APPDIR/usr/bin/manual-card-audit-editor"
chmod +x "$APPDIR/usr/bin/manual-card-audit-editor"

cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/manual-card-audit-editor" "$@"
EOF
chmod +x "$APPDIR/AppRun"

cat > "$APPDIR/usr/share/applications/manual-card-audit-editor.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=万境奇旅卡牌校对编辑器
Exec=manual-card-audit-editor
Icon=manual-card-audit-editor
Categories=Utility;Game;
Terminal=false
EOF
cp "$APPDIR/usr/share/applications/manual-card-audit-editor.desktop" \
  "$APPDIR/manual-card-audit-editor.desktop"

if [ -f "$PROJECT_DIR/icon.svg" ]; then
  cp "$PROJECT_DIR/icon.svg" "$APPDIR/usr/share/icons/hicolor/256x256/apps/manual-card-audit-editor.svg"
else
  cat > "$APPDIR/usr/share/icons/hicolor/256x256/apps/manual-card-audit-editor.svg" <<'EOF'
<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256"><rect width="256" height="256" rx="32" fill="#283044"/><rect x="42" y="28" width="172" height="200" rx="12" fill="#f1d9ad"/><path d="M66 70h124M66 94h124M66 118h92" stroke="#76583e" stroke-width="9" stroke-linecap="round"/></svg>
EOF
fi
cp "$APPDIR/usr/share/icons/hicolor/256x256/apps/manual-card-audit-editor.svg" \
  "$APPDIR/manual-card-audit-editor.svg"

APPIMAGETOOL="$BUILD_DIR/appimagetool-x86_64.AppImage"
if [ ! -x "$APPIMAGETOOL" ]; then
  wget -O "$APPIMAGETOOL" https://github.com/AppImage/appimagetool/releases/latest/download/appimagetool-x86_64.AppImage
  chmod +x "$APPIMAGETOOL"
fi

ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$APPIMAGETOOL" "$APPDIR" "$DIST_DIR/万境奇旅卡牌校对编辑器.AppImage"
echo "已生成: $DIST_DIR/万境奇旅卡牌校对编辑器.AppImage"
