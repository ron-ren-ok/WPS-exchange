# WPS 日进度播报

此任务只读 Google Sheet，通过 WPS 群机器人发送日进度与月度预测卡片；不使用 AI，也不修改表格数据。

## 数据逻辑

- 只读取「合作方新增血量」长表的表头：`日期`、`合作方`、`运营位`、`新增`、`血量`。
- 不读取「日进度追踪」，不依赖其中的公式、固定单元格或固定列。
- 血量自动汇总当月全部合作方与运营位；新增自动汇总合作方为 `360` 的所有运营位。
- 月度预测对每个已发现的「合作方 + 运营位」序列独立按最近 14 个观测日均值补齐，再汇总。因此新增合作方或运营位时无需修改代码。
- 仅从「目标完成度」读取当月的目标值（`当月目标`、`我方新增目标`），不读取任何实际完成公式。

## GitHub Secrets

在仓库 **Settings → Secrets and variables → Actions** 中配置：

- `GOOGLE_SHEET_SERVICE_ACCOUNT_JSON`：可读取目标表格的服务账号 JSON；服务账号邮箱须以查看者身份共享到该表格。
- `WPS_WEBHOOK_URL_TEST`：测试阶段使用的 WPS 群机器人 webhook 地址。
- `WPS_WEBHOOK_URL`：正式 WPS 群机器人 webhook 地址。
- `WPS_WEBHOOK_KEY` 和 `WPS_WEBHOOK_SECRET`：仅当机器人启用签名校验时成对配置。

工作流每天北京时间 04:00 自动运行。手动触发时可选择 `test` 或 `production`。