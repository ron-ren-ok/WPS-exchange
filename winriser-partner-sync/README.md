# Winriser Partner Sync

读取 Tracker / EntireTrack 的 `Daily Install Report`，仅保留 `Source = WPS` 的数据，并写入 Google Sheet「合作方新增血量」长表。

当前看板只提供 Winriser 气泡数据，因此每条记录写为：

- 合作方：`Winriser`
- 运营位：`气泡`
- 指标：`新增`、`血量`

长表字段为「日期、合作方、运营位、新增、血量」。同步器以「日期 + 合作方 + 运营位」定位记录：已有记录更新指标，不存在则追加记录；不会创建换量弹窗数据。

## GitHub Actions secrets

- `GOOGLE_SHEET_SERVICE_ACCOUNT_JSON`
- `WINRISER_LOGIN_SECRET`（本地凭证文件中 `WINRISER_LOGIN_SECRET=` 后的值；不含变量名或引号）

任务每天北京时间 03:00 运行，使用 Tracker 的最近一周报告，更新至前一个北京时间自然日。可在 Actions 手动指定结束日期。