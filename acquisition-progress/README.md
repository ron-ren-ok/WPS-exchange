# 导量、三方、Affiliate 日活与 MAU 达成播报

每日从 Google Sheet「三方&导量运营数据」只读获取 `新增&月活提取` 和 `目标`：

- 渠道：导量（安卓导PC）、三方（三方换量）、Affiliate。
- 指标：昨日新增、本月日均实际/目标、本月实际总新增/目标、最近 30 天 MAD 实际/目标。
- 趋势：最近 12 个滚动七天窗口的新增日均迷你趋势图，并显示首尾窗口的上涨或下跌比例。

## GitHub Secrets

- `GOOGLE_SHEET_SERVICE_ACCOUNT_JSON`：可读取该表格的服务账号 JSON；须将服务账号邮箱以查看者权限共享给表格。
- `WPS_WEBHOOK_ACQUISITION`：正式群机器人地址。
- `WPS_WEBHOOK_URL_TEST`：测试机器人地址。

每日定时任务默认推送正式地址；手动触发默认推送测试地址。Webhooks 只在运行时读取，绝不输出到日志或仓库。
