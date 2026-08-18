# 万境奇旅卡牌校对编辑器发布仓库

这个公开仓库只负责发布人工校对编辑器的 Windows EXE 和 Steam Deck/Linux AppImage。

卡牌 JSON、中文卡图、故事书和游戏素材不上传到 GitHub，仍保留在本地资源包中。编辑器更新只替换程序文件，不会修改 `data/rules/zh_cn/manual_card_audit.json` 或图片。

## 发布

推送形如 `editor-v0.3.1` 的 tag 后，GitHub Actions 会同时构建：

- `wanjing-card-audit-editor-windows.exe`
- `wanjing-card-audit-editor-linux.AppImage`
- `SHA256SUMS.txt`

编辑器启动后会后台检查 GitHub Releases。发现新版本时，Windows 会在退出后替换 EXE，Steam Deck 会替换 AppImage；下载内容先通过 SHA256 校验。

## 多设备远程校对

编辑器支持从线上配置读取校对 JSON，并通过 HTTPS `PUT`/`POST` 上传同步。阿里云 OSS 建议使用对象读取 URL和上传预签名 URL，示例见 `tools/remote_card_audit_config.example.json`。配置文件中只放 URL 和参数，不要放 AccessKey/Secret；令牌可放在每台设备的环境变量 `CARD_AUDIT_SYNC_TOKEN`，也可保存到配置指定的本地 `token_file`。

启动时设置线上配置 URL：

```powershell
$env:CARD_AUDIT_REMOTE_CONFIG_URL = "https://your-domain.example/card-audit/config.json"
.\wanjing-card-audit-editor-windows.exe
```

Steam Deck 同理设置 `CARD_AUDIT_REMOTE_CONFIG_URL` 后启动 AppImage。编辑器会先下载 JSON 到本地缓存；点击“上传/同步远程”或保存时自动同步。上传使用 `ETag`/`If-Match`，发现其他设备已修改时会拒绝覆盖并提示先加载远程版本。

首次点击“配置远程”后，配置 URL 会保存在当前设备，下一次启动会自动读取。仅提供 `document_url` 而没有 `upload_url` 的配置是只读模式；要实现多设备校对，服务器必须允许经过认证的 `PUT` 或 `POST` 写入。阿里云 OSS 的上传 URL需要具备 `PutObject` 权限并允许覆盖目标对象。

## 本地构建

Windows：安装 Python、PyInstaller 和 Pillow 后执行 PyInstaller 命令；Linux/Steam Deck 使用 `tools/build_manual_card_audit_appimage.sh`。
