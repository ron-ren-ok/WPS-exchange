# 三方换量用户健康度预警

`.github/workflows/partner-health-alert.yml` 每天北京时间 05:00 运行。

仅在满足任一条件时调用 WPS Webhook：

- `数据总览` 的任意合作方为“红色预警”；
- 数据最新日期无法识别或比北京时间当天落后超过 1 天；
- 大盘指标区出现公式错误（例如 `#REF!`）。

黄色关注、正常、样本不足不会触发推送。命中多个红色合作方时，按表格顺序逐个输出相同的预警区块。

## GitHub Secrets

- `GOOGLE_SHEET_SERVICE_ACCOUNT_JSON`：可读取目标 Google Sheet 的服务账号 JSON。服务账号邮箱须以“查看者”身份共享到该表格。
- `WPS_WEBHOOK_URL`：正式 WPS 群机器人地址。
- `WPS_WEBHOOK_URL_TEST`：手动测试时可选用的机器人地址。
- `WPS_WEBHOOK_KEY`、`WPS_WEBHOOK_SECRET`：仅在机器人启用签名校验时成对配置。

所有凭据仅从 GitHub Secrets 在运行时读取；脚本和日志均不会输出 Secret 内容。
