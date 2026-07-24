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
- GMAIL_IMAP_USERNAME：读取 Avast 邮件的 Gmail 地址（目前为 `54lingbai@gmail.com`）。
- GMAIL_APP_PASSWORD：该 Gmail 账号为本任务创建的 16 位 Gmail 应用专用密码。

凭证仅存于 GitHub Secrets，绝不提交到仓库。工作流每天北京时间 03:00 运行，也可在 Actions 页面手动补数。

## 首次 Gmail IMAP 配置

1. 为 `54lingbai@gmail.com` 开启两步验证。
2. 打开 Google 账号的“应用专用密码”，创建一个名称为 `WPS Avast Partner Sync` 的密码。
3. 将 Gmail 地址保存到 GitHub Secret `GMAIL_IMAP_USERNAME`，将生成的 16 位密码保存到 `GMAIL_APP_PASSWORD`（可包含或不包含显示用空格）。

同步器只通过 `imap.gmail.com:993` 的只读邮箱连接获取符合主题和发件人校验的 PDF；不再使用 Google OAuth refresh token。旧的 `GMAIL_OAUTH_CLIENT_JSON` 与 `GMAIL_REFRESH_TOKEN` 可以在新任务验证成功后删除。
