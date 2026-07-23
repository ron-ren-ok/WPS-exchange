# Avast Partner Sync

从 Gmail 读取 Avast Daily PBI Report 的 PDF 附件，并写入 Google Sheet「合作方新增血量」长表。

| 邮件主题 | 合作方 | 运营位 |
| --- | --- | --- |
| Avast AV - WPS - Daily PBI report | Avast | 换量弹窗 |
| Avast AV - WPS - Toast - Daily PBI report | Avast | 气泡 |
| Avast One - WPS - C - Daily Report PBI | Avast | 卸载后引导H5 |

长表使用「日期、合作方、运营位、新增、血量」五个字段。同步器以日期 + 合作方 + 运营位定位记录：已有记录更新新增和血量；不存在则追加一行。第三类卸载后引导H5邮件不存在时会跳过，不会导致任务失败。

每份 PBI 报告中，第一个不带美元符号的 Total 是新增；紧邻的带美元符号的 Total 是血量。仅接受 no-reply-powerbi@microsoft.com 发件的邮件，或正文中明确标注该原始发件人的 partner@wps.com 转发。

## GitHub Actions Secrets

- GOOGLE_SHEET_SERVICE_ACCOUNT_JSON：可复用既有服务账号 JSON。
- GMAIL_OAUTH_CLIENT_JSON：Google Cloud OAuth 客户端 JSON。
- GMAIL_REFRESH_TOKEN：以含 Avast 邮件的 Gmail 账号授权 gmail.readonly 后取得的 refresh token。

凭证仅存于 GitHub Secrets，绝不提交到仓库。工作流每天北京时间 03:00 运行，也可在 Actions 页面手动补数。

## 首次 Gmail 授权

先将 OAuth JSON 全文保存到 GitHub Secret GMAIL_OAUTH_CLIENT_JSON。然后在本机运行：

    python scripts/create_gmail_refresh_token.py --client-json "下载的 OAuth JSON 文件路径" --output "D:\User\下载\gmail_refresh_token.txt"

浏览器中必须登录 54lingbai@gmail.com 并确认只读 Gmail 授权。将终端生成的 refresh token 保存到 GitHub Secret GMAIL_REFRESH_TOKEN，不要发送或提交该值。