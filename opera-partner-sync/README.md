# Opera Partner Sync

读取 Gmail 中 Looker 发送的 Opera PDF 附件，并按 `Summary table` 的每日 Campaign 行同步到 Google Sheet「合作方返回数据」。

- `wpstest`：气泡，写入 `Opera气泡新增`、`Opera气泡血量`。
- `wpstest2/opera.exe`：换量弹窗，写入 `Opera换量弹窗新增`、`Opera换量弹窗血量`。
- 仅接受 `noreply@lookermail.com` 发件、主题为 `Opera for Computers distribution partner dashboard` 的带 PDF 邮件。
- 同一日期采用邮箱中最新报表的值，默认补齐至北京时间昨天。

## GitHub Actions Secrets

复用 Avast 已配置的凭证，不需要新增 Secret：

- `GOOGLE_SHEET_SERVICE_ACCOUNT_JSON`
- `GMAIL_OAUTH_CLIENT_JSON`
- `GMAIL_REFRESH_TOKEN`

服务账号必须拥有目标表格编辑权限。工作流会在每个工作日北京时间 10:00 执行，也可在 Actions 页面手动选择日期范围补数。