# TeraBox Partner Sync

每天从源表 [WPS PC 导流 TeraBox](https://docs.google.com/spreadsheets/d/1YN0VF4zPyYeWe0LeaFmVPsEZr_YT83mDyPwX9pFeJ1c/edit?usp=sharing) 的「WPS PC 导流 TeraBox」读取每日数据，并写入目标表「合作方新增血量」长表。

- 源表 A 列：日期；B 列：气泡运营位新增。
- 目标记录：`合作方=Terabox`、`运营位=气泡`。
- TeraBox 未提供血量，脚本只写入 `新增`，不会写入或覆盖 `血量`。
- 默认仅补充新增缺失的数据，并复查最近 14 天的空缺；已有不同的非空新增会记录为冲突，不覆盖。
- GitHub Actions 每天北京时间 03:00（UTC `19:00`）运行；可通过 `workflow_dispatch` 指定历史补数日期。

## GitHub Secret

配置 `GOOGLE_SHEET_SERVICE_ACCOUNT_JSON`。对应服务账号须同时拥有源表的查看权限与目标表的编辑权限。
