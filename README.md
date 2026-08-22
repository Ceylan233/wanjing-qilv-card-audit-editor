# 万境奇旅卡牌校对编辑器发布仓库

这个公开仓库只负责发布人工校对编辑器的 Windows EXE 和 Steam Deck/Linux AppImage。

卡牌 JSON、中文卡图、故事书和游戏素材不上传到 GitHub，仍保留在本地资源包中。编辑器更新只替换程序文件，不会修改 `data/rules/zh_cn/manual_card_audit.json` 或图片。

## 发布

推送形如 `editor-v0.3.1` 的 tag 后，GitHub Actions 会同时构建：

- `wanjing-card-audit-editor-windows.exe`
- `wanjing-card-audit-editor-linux.AppImage`
- `SHA256SUMS.txt`

编辑器启动后会后台检查 GitHub Releases。发现新版本时，Windows 会在退出后替换 EXE，Steam Deck 会替换 AppImage；下载内容先通过 SHA256 校验。

v0.3.26 修复 Windows 安装目录含中文时自动更新失败的问题：更新脚本现在通过 Unicode 命令行直接启动，不再生成 ASCII 编码的 CMD 启动器。从 v0.3.25 升级到该版本需要手动下载一次，之后可继续使用自动更新。

v0.3.27 将能力限定骰槽明确显示为“颜色/技能＋具体行动名”，并修复统一骰槽尺寸时保持中心点导致边框向上偏移的问题；已有框现在保留牌面原始左上角。

v0.3.28 补全倒置源图的默认显示方向：1092–1104、1138、1139、1146 会自动旋转 180° 后显示，源图片和骰槽原始坐标保持不变。

## 多设备远程校对

编辑器支持从线上配置读取校对 JSON，并通过 HTTPS `PUT`/`POST` 上传同步。阿里云 OSS 建议使用对象读取 URL和上传预签名 URL，示例见 `tools/remote_card_audit_config.example.json`。配置文件中只放 URL 和参数，不要放 AccessKey/Secret；令牌可放在每台设备的环境变量 `CARD_AUDIT_SYNC_TOKEN`，也可保存到配置指定的本地 `token_file`。

## 牌面总结与 AI 纠正

v0.3.24 起，编辑器为 801 张大卡、900 张普通小卡和 12 张《交锋》卡显示“牌面元素 + 功能”总结。大卡只显示卡牌编号，不显示牌名；小卡和交锋卡显示名称。v0.3.25 增加人工核验优先级、核验原因与分类核对清单，并可用“AI建议优先”筛选 OCR/视觉检测生成的能力。发现错误时，在“总结与AI纠正”页直接写事实纠正，例如：

> 时间结果是红色不是橙色。图示表示花费2点强化后可以获得一个蓝色标记。

保存后使用“导出待AI校对任务包”。任务包会携带卡牌总结、完整结构化数据、卡图路径和纠正提示词；处理时应同步更新人工校对总表、运行卡牌数据、能力数据、生成器/权威覆盖、相关游戏代码与回归测试。处理完成的卡牌保留提示词，并显示“AI已处理”及结果摘要，方便追溯。

“导出全部牌面总结”可生成独立的 1713 张卡牌总结 JSON。

v0.3.30 修复自动更新后重新启动 EXE 或 AppImage 时继承旧 PyInstaller 内部环境、触发安全校验失败的问题。从 v0.3.28 或更早版本升级时若已经看到该错误，请首次手动下载并替换程序；之后可继续使用内置自动更新。

启动时设置线上配置 URL：

```powershell
$env:CARD_AUDIT_REMOTE_CONFIG_URL = "https://your-domain.example/card-audit/config.json"
.\wanjing-card-audit-editor-windows.exe
```

Steam Deck 同理设置 `CARD_AUDIT_REMOTE_CONFIG_URL` 后启动 AppImage。编辑器会先下载 JSON 到本地缓存；点击“上传/同步远程”或保存时自动同步。上传使用 `ETag`/`If-Match`，发现其他设备已修改时会拒绝覆盖并提示先加载远程版本。

首次点击“配置远程”后，配置 URL 会保存在当前设备，下一次启动会自动读取。仅提供 `document_url` 而没有 `upload_url` 的配置是只读模式；要实现多设备校对，服务器必须允许经过认证的 `PUT` 或 `POST` 写入。阿里云 OSS 的上传 URL需要具备 `PutObject` 权限并允许覆盖目标对象。

## 本地构建

Windows：安装 Python、PyInstaller 和 Pillow 后执行 PyInstaller 命令；Linux/Steam Deck 使用 `tools/build_manual_card_audit_appimage.sh`。
