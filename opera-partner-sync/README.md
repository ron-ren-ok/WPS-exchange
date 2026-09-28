# Opera Partner Sync

读取 Gmail 中 Looker 发送的 Opera 与 OperaGX PDF 附件，并按 `Summary table` 的每日行同步到 Google Sheet「合作方新增血量」长表。

| Campaign | 合作方 | 运营位 |
| --- | --- | --- |
| `wpstest2/opera.exe` | Opera | 换量弹窗 |
| `wpstest` | Opera | 气泡 |
| `toast`（GX 报表的 `Utm Content`） | Opera GX | 气泡 |
| `bundle`（GX 报表的 `Utm Content`） | Opera GX | 换量弹窗 |
| `recall`（GX 报表的 `Utm Content`） | Opera GX | 卸载引导 |

长表字段为「日期、合作方、运营位、新增、血量」。同步器以「日期 + 合作方 + 运营位」定位记录：已有记录更新新增和血量；不存在则追加一行。

Opera GX 日常同步仅解析发件人 `noreply@lookermail.com`、标题为 `OperaGX for Computers distribution partner dashboard` 的最新一封匹配邮件；该滚动看板中的 `bundle` 对应换量弹窗，`toast` 对应气泡，`recall` 对应卸载引导。某个运营位未出现在报表中时跳过该项，不生成零值记录，也不阻断其他运营位同步。

仅接受 `noreply@lookermail.com` 发件、主题为 `Opera for Computers distribution partner dashboard` 或 `OperaGX for Computers distribution partner dashboard` 的带 PDF 邮件。同一日期采用邮箱中最新报表的值。

## 同步窗口与性能

- 不传日期：Opera 和 Opera GX 都同步北京时间昨天及之前六天，共 7 个完整自然日；邮件搜索限定最近 7 个日历日。Opera 从新到旧读取，全部日期和运营位齐全后停止；Opera GX 只读取最新一封匹配邮件。
- 传入 `--start-date` 或 `--end-date`：按包含起止日期的指定数据区间补数，Opera 和 Opera GX 都可从新到旧检索历史邮件。未传结束日期时截止到北京时间昨天，未传开始日期时取结束日期及之前六天。
- 历史邮件搜索从数据起始日期开始，不以数据结束日期截断邮件接收日期：稍后发送的滚动报表可能包含所需数据。实际写入严格限制在指定数据区间；全部日期和运营位齐全后停止。GX 缺失运营位仍跳过，不生成零值。
- 每封邮件只下载一次，每份 PDF 只提取一次文本，同时解析该合作方的所有运营位。当前真实 Looker 报表为单页；若报表变为多页，仍读取所有页面以保留跨页表格。
- 日志输出抓取邮件数、解析 PDF 数、PDF 文本提取耗时、总抓取解析耗时，以及各运营位已找到和未找到的天数。Actions 使用 pip 缓存。

## GitHub Actions Secrets

复用 Avast 已配置的凭证，不需要新增 Secret：

- `GOOGLE_SHEET_SERVICE_ACCOUNT_JSON`
- `GMAIL_IMAP_USERNAME`
- `GMAIL_APP_PASSWORD`

服务账号必须拥有目标表格编辑权限。工作流每天北京时间 03:00 执行，也可在 Actions 页面手动选择日期范围补数。Opera 与 Avast 共用同一个 Gmail IMAP 应用专用密码，不再使用 OAuth refresh token。
