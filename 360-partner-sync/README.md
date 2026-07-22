# 360 Partner Sync

每天从源表 [360&WPS换量合作](https://docs.google.com/spreadsheets/d/1fHVgG5EnrSR-BXOsQxmNbIM_fk88qwTHe8u-gkxsFvw/edit?usp=sharing) 的「每日」读取 360 新增数据，并写入目标表「合作方新增血量」长表。

| 源表头 | 运营位 | 写入字段 |
| --- | --- | --- |
| `360-1` | 换量弹窗 | 新增 |
| `360-2` | 气泡 | 新增 |
| `360-3` | 卸载后引导H5 | 新增 |

源表未提供血量，因此同步器只写入或更新「新增」，绝不覆盖目标长表中已有的「血量」。记录以「日期 + 合作方(360) + 运营位」定位，不存在则追加。日常运行仅从各运营位最后有效日期向后同步，并检查最近 14 天的空白新增；不会自动回补早于该窗口的历史空洞。仅手动传入 `start_date` 时才会执行历史补数。已有的非空且不一致数据会保留，并在日志中列为 `skipped_conflicts`。

## GitHub Actions Secret

复用 `GOOGLE_SHEET_SERVICE_ACCOUNT_JSON`。对应服务账号需要源表的查看者权限，以及目标表的编辑者权限；请将服务账号邮箱共享到源表。

工作流每天北京时间 03:00 运行，也可在 Actions 页面手动填写日期范围补数。