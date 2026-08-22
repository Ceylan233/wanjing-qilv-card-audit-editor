万境奇旅卡牌校对编辑器 - Steam Deck 完整包

本包包含：
1. Linux x86-64 AppImage 编辑器。
2. 1713 张卡牌的完整校对 JSON。
3. 801 张地点牌与 912 张标准卡的中文版图片。
4. Steam Deck 启动脚本。

使用方法：
1. 必须先完整解压本包，不能直接在压缩包内运行。
2. 进入桌面模式，打开 Konsole。
3. 进入解压后的文件夹，执行：
   chmod +x "启动编辑器_SteamDeck.sh" "万境奇旅卡牌校对编辑器.AppImage"
   ./启动编辑器_SteamDeck.sh

启动脚本会绕过部分 SteamOS 缺少 FUSE 时 AppImage 无法启动的问题。
校对结果会直接保存到 data/rules/zh_cn/manual_card_audit.json，请定期备份整个 data 文件夹。

骰槽核验规则：只有牌面上的完整留白方框才算骰槽。主动/被动技能文字块、圆形效果、彩色行动列表和插画元素都不算骰槽。

添加到 Steam：
在桌面版 Steam 中选择“添加非 Steam 游戏”，浏览并选择“启动编辑器_SteamDeck.sh”。
如果文件选择器未显示脚本，把文件类型改为“所有文件”。
