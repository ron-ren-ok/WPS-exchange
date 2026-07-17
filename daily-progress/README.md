# WPS 日进度播报

读取 Google 表格中现有的 `日进度追踪` 公式结果，并通过 WPS 群机器人发送日进度卡片。此任务只读表格，不使用 AI，也不修改表格数据。

## GitHub Secrets

在仓库 **Settings → Secrets and variables → Actions** 中配置：

- `GOOGLE_SERVICE_ACCOUNT_JSON`：可读取目标 Google 表格的服务账号 JSON；服务账号邮箱须以查看者身份共享到该表格。
- `WPS_WEBHOOK_URL`：WPS 群机器人 webhook 地址。
- `WPS_WEBHOOK_KEY` 和 `WPS_WEBHOOK_SECRET`：仅当机器人启用签名校验时成对配置。

工作流每天北京时间 07:00 自动运行，也可以在 Actions 页面手动触发。