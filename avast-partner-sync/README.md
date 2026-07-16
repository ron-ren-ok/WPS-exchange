# Avast Partner Sync

从 Gmail 中读取 Avast Daily PBI Report 的 PDF 附件，解析首页 `Split by Date & Geo` 的两个 `Total`，并同步到 Google Sheet。

- 换量弹窗：`Avast AV - WPS - Daily PBI report` → `Avast换量弹窗新增`、`Avast换量弹窗血量`
- 气泡：`Avast AV - WPS - Toast - Daily PBI report` → `Avast气泡新增`、`Avast气泡血量`
- 第一条不带 `$` 的 `Total` 是新增；紧邻的带 `$` 的 `Total` 是血量。
- 仅接受 `no-reply-powerbi@microsoft.com` 发件的邮件，或正文中明确标注该原始发件人的 `partner@wps.com` 转发。
- 以邮箱中最新报告作为同一日期的权威值；只写入上述四个 Avast 专属表头。

## GitHub Actions Secrets

- `GOOGLE_SHEET_SERVICE_ACCOUNT_JSON`：已用于 Yandex 的服务账号 JSON，可直接复用。
- `GMAIL_OAUTH_CLIENT_JSON`：Google Cloud OAuth 客户端下载的 JSON（Desktop 或 Web 类型）。
- `GMAIL_REFRESH_TOKEN`：以有 Avast 邮件的 Gmail 账号授权 `gmail.readonly` 后取得的 refresh token。

凭证仅存于 GitHub Secrets，绝不提交到仓库。工作流在每个工作日北京时间 10:00 运行，也可在 Actions 页面手动补数。
## 首次 Gmail 授权

先将下载的 OAuth JSON 全文保存到 GitHub Secret `GMAIL_OAUTH_CLIENT_JSON`。然后在本机运行一次：

```powershell
python scripts/create_gmail_refresh_token.py --client-json "下载的 OAuth JSON 文件路径"
```

浏览器中必须登录 `54lingbai@gmail.com` 并确认只读 Gmail 授权。终端只会输出一个 refresh token；将其保存到 GitHub Secret `GMAIL_REFRESH_TOKEN`，不要发送或提交该值。