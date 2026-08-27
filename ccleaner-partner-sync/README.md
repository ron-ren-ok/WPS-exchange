# CCleaner Partner Sync

从 Gmail 读取主题为 `CCleaner - WPS - B - Daily Report PBI` 的最新已验证邮件及其 PDF 附件，并写入 Google Sheet「合作方新增血量」长表。

- 合作方固定为 `CCleaner`，运营位固定为 `气泡`。
- 第 1 页 `Split by Date & Geo` 中，安装量 `Total` 写入 `新增`，Costs 的美元 `Total` 写入 `血量`；两张表分别按各自日期列对齐。
- 当报告未提供 Costs 时不会写入或覆盖 `血量`。
- 默认以报告日期列的最新日期为锚点，检查该日及前两日；只覆盖报告中实际存在的日期，例如仅有 8 月 24 日时只覆盖 8 月 24 日。手动运行可指定任意开始与结束日期，范围内缺失日期自动跳过。
- 以「日期 + 合作方 + 运营位」定位记录。已有记录用报告中的新增及已提供血量覆盖；不存在则追加。
- 只接受 `no-reply-powerbi@microsoft.com` 直接发送的邮件，或正文可验证该原始发件人的 `partner@wps.com` 转发。

GitHub Actions 每天北京时间 03:00（UTC `19:00`）自动运行，也可手动指定开始和结束日期。复用 Avast 同步的三个 Secrets：`GOOGLE_SHEET_SERVICE_ACCOUNT_JSON`、`GMAIL_IMAP_USERNAME`、`GMAIL_APP_PASSWORD`。
