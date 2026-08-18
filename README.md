# 万境奇旅卡牌校对编辑器发布仓库

这个公开仓库只负责发布人工校对编辑器的 Windows EXE 和 Steam Deck/Linux AppImage。

卡牌 JSON、中文卡图、故事书和游戏素材不上传到 GitHub，仍保留在本地资源包中。编辑器更新只替换程序文件，不会修改 `data/rules/zh_cn/manual_card_audit.json` 或图片。

## 发布

推送形如 `editor-v0.3.1` 的 tag 后，GitHub Actions 会同时构建：

- `wanjing-card-audit-editor-windows.exe`
- `wanjing-card-audit-editor-linux.AppImage`
- `SHA256SUMS.txt`

编辑器启动后会后台检查 GitHub Releases。发现新版本时，Windows 会在退出后替换 EXE，Steam Deck 会替换 AppImage；下载内容先通过 SHA256 校验。

## 本地构建

Windows：安装 Python、PyInstaller 和 Pillow 后执行 PyInstaller 命令；Linux/Steam Deck 使用 `tools/build_manual_card_audit_appimage.sh`。
