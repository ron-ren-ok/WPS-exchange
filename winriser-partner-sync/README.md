# Winriser Partner Sync

读取 Tracker / EntireTrack 的 `Daily Install Report`，仅保留主 source `WPS` 下已映射的子 source，并写入 Google Sheet「合作方新增血量」长表。

## 子 source 映射

- `wnrwpsofc` → 运营位：`气泡`
- `wnrwpsofc_exchange` → 运营位：`换量弹窗`
- 合作方：`Winriser`；新增：`Install Count`；血量：`Spend-PPI($)`
- 主 source `WPS` 是汇总行，不写入；其他未映射子 source 也不写入。

长表字段为「日期、合作方、运营位、新增、血量」。同步器以「日期 + 合作方 + 运营位」定位记录：已有记录补空或按显式覆盖参数更新，不存在则追加记录。

## GitHub Actions secrets

- `GOOGLE_SHEET_SERVICE_ACCOUNT_JSON`
- `WINRISER_LOGIN_SECRET`（本地凭证文件中 `WINRISER_LOGIN_SECRET=` 后的值；不含变量名或引号）

任务每天北京时间 03:00 运行，使用 Tracker 的最近一周报告，更新至前一个北京时间自然日。可在 Actions 手动指定结束日期。