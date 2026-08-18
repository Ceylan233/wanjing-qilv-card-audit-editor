#!/usr/bin/env bash
set -u

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APPIMAGE="$PROJECT_DIR/校对工具/万境奇旅卡牌校对编辑器.AppImage"
JSON="$PROJECT_DIR/data/rules/zh_cn/manual_card_audit.json"
STDOUT_LOG="$(mktemp)"
STDERR_LOG="$(mktemp)"
trap 'rm -f -- "$STDOUT_LOG" "$STDERR_LOG"' EXIT

timeout 12s xvfb-run -a env APPIMAGE_EXTRACT_AND_RUN=1 \
  "$APPIMAGE" "$JSON" >"$STDOUT_LOG" 2>"$STDERR_LOG"
code=$?
printf 'exit=%s\n' "$code"
printf '%s\n' 'stderr:'
sed -n '1,80p' "$STDERR_LOG"
if [[ "$code" == "124" ]]; then
  echo 'gui_stayed_alive=1'
fi
exit 0
