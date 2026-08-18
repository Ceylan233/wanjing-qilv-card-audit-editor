#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APPIMAGE="$ROOT_DIR/万境奇旅卡牌校对编辑器.AppImage"
AUDIT_JSON="$ROOT_DIR/data/rules/zh_cn/manual_card_audit.json"

cd "$ROOT_DIR"

if [[ ! -f "$APPIMAGE" ]]; then
  echo "找不到编辑器：$APPIMAGE" >&2
  exit 2
fi

if [[ ! -f "$AUDIT_JSON" ]]; then
  echo "找不到校对数据：$AUDIT_JSON" >&2
  exit 3
fi

chmod +x "$APPIMAGE"
export APPIMAGE_EXTRACT_AND_RUN=1
export CARD_AUDIT_APPIMAGE_PATH="$APPIMAGE"
exec "$APPIMAGE" "$AUDIT_JSON" "$@"
