# Opera Partner Sync

读取 Gmail 中 Looker 发送的 Opera PDF 附件，并按 `Summary table` 的每日 Campaign 行同步到 Google Sheet「合作方新增血量」长表。

| Campaign | 合作方 | 运营位 |
| --- | --- | --- |
| `wpstest2/opera.exe` | Opera | 换量弹窗 |
| `wpstest` | Opera | 气泡 |

长表字段为「日期、合作方、运营位、新增、血量」。同步器以「日期 + 合作方 + 运营位」定位记录：已有记录更新新增和血量；不存在则追加一行。

仅接受 `noreply@lookermail.com` 发件、主题为 `Opera for Computers distribution partner dashboard` 的带 PDF 邮件。同一日期采用邮箱中最新报表的值，默认补齐至北京时间昨天。

## GitHub Actions Secrets

复用 Avast 已配置的凭证，不需要新增 Secret：

- `GOOGLE_SHEET_SERVICE_ACCOUNT_JSON`
- `GMAIL_IMAP_USERNAME`
- `GMAIL_APP_PASSWORD`

服务账号必须拥有目标表格编辑权限。工作流每天北京时间 03:00 执行，也可在 Actions 页面手动选择日期范围补数。Opera 与 Avast 共用同一个 Gmail IMAP 应用专用密码，不再使用 OAuth refresh token。