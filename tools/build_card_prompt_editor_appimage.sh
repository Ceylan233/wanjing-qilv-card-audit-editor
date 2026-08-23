#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="$PROJECT_DIR/.codex-temp/appimage_card_prompt_editor"
APPDIR="$BUILD_DIR/AppDir"
DIST_DIR="$PROJECT_DIR/校对工具"
PYTHON_BIN="${PYTHON_BIN:-python3}"

mkdir -p "$BUILD_DIR" "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor/256x256/apps" "$DIST_DIR"
if ! dpkg-query -W -f='${Status}' python3-tk 2>/dev/null | grep -q 'install ok installed'; then
  sudo apt-get update
  sudo apt-get install -y python3 python3-venv python3-tk wget file
fi
VENV_DIR="$BUILD_DIR/venv"
if [ ! -x "$VENV_DIR/bin/python" ]; then "$PYTHON_BIN" -m venv "$VENV_DIR"; fi
"$VENV_DIR/bin/python" -m pip install --upgrade pip 'pyinstaller==6.22.2' pillow certifi
"$VENV_DIR/bin/python" -m PyInstaller --noconfirm --clean --onefile --name wanjing-card-prompt-editor-linux \
  --distpath "$BUILD_DIR/dist" --workpath "$BUILD_DIR/work" --specpath "$BUILD_DIR" \
  --collect-data certifi --hidden-import PIL._tkinter_finder "$PROJECT_DIR/tools/card_prompt_editor.py"
cp "$BUILD_DIR/dist/wanjing-card-prompt-editor-linux" "$APPDIR/usr/bin/card-prompt-editor"
chmod +x "$APPDIR/usr/bin/card-prompt-editor"
cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/card-prompt-editor" "$@"
EOF
chmod +x "$APPDIR/AppRun"
cat > "$APPDIR/usr/share/applications/card-prompt-editor.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=万境奇旅卡牌提示词编辑器
Exec=card-prompt-editor
Icon=card-prompt-editor
Categories=Utility;Game;
Terminal=false
EOF
cp "$APPDIR/usr/share/applications/card-prompt-editor.desktop" "$APPDIR/card-prompt-editor.desktop"
if [ -f "$PROJECT_DIR/icon.svg" ]; then
  cp "$PROJECT_DIR/icon.svg" "$APPDIR/usr/share/icons/hicolor/256x256/apps/card-prompt-editor.svg"
else
  printf '<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256"><rect width="256" height="256" fill="#283044"/></svg>\n' > "$APPDIR/usr/share/icons/hicolor/256x256/apps/card-prompt-editor.svg"
fi
cp "$APPDIR/usr/share/icons/hicolor/256x256/apps/card-prompt-editor.svg" "$APPDIR/card-prompt-editor.svg"
APPIMAGETOOL="$BUILD_DIR/appimagetool-x86_64.AppImage"
if [ ! -x "$APPIMAGETOOL" ]; then
  wget -O "$APPIMAGETOOL" https://github.com/AppImage/appimagetool/releases/latest/download/appimagetool-x86_64.AppImage
  chmod +x "$APPIMAGETOOL"
fi
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$APPIMAGETOOL" "$APPDIR" "$DIST_DIR/万境奇旅卡牌提示词编辑器.AppImage"
