# Winriser Partner Sync

读取 Tracker / EntireTrack 的 `Daily Install Report`，仅保留主 source `WPS` 下已映射的子 source，并写入 Google Sheet「合作方新增血量」长表。

## 子 source 映射

- `wnrwpsofc` → 运营位：`气泡`
- `wnrwpsofc_exchange` → 运营位：`换量弹窗`
- `wnrwps_radar` → 运营位：`文档雷达`
- 合作方：`Winriser`；新增：`Install Count`；血量：`Spend-PPI($)`
- 主 source `WPS` 是汇总行，不写入；其他未映射子 source 也不写入。

长表字段为「日期、合作方、运营位、新增、血量」。同步器以「日期 + 合作方 + 运营位」定位记录：已有记录补空或按显式覆盖参数更新，不存在则追加记录。

## GitHub Actions secrets

- `GOOGLE_SHEET_SERVICE_ACCOUNT_JSON`
- `WINRISER_LOGIN_SECRET`（本地凭证文件中 `WINRISER_LOGIN_SECRET=` 后的值；不含变量名或引号）

任务每天北京时间 03:00 运行，使用 Tracker 的最近一周报告，更新至前一个北京时间自然日。可在 Actions 手动指定结束日期。

## 区间回补与校验

- 手动输入 `start_date` 和 `end_date` 时，日期区间包含首尾两天。开始日期晚于结束日期会拒绝执行。
- 未输入开始日期时，保留 Tracker 默认日期选项 `3`。输入开始日期后，遍历 Tracker 实际提供的日期选项，合并重复父记录，再按天获取子 source；可回补范围仍受 Tracker 可选报告限制。
- 完整读取目标表 `A:E`，仅解析 Winriser 的日期与指标，以「日期 + 合作方 + 运营位」去重；Winriser 重复键会拒绝写入，其他合作方保持原样。
- Tracker、Sheets 读取和指标更新遇到超时、连接中断、HTTP 408/429/500/502/503/504 时，首次失败后最多重试 3 次，每次间隔 5 秒。非临时错误直接报错。
- 追加请求发生临时错误时，先等待 5 秒并回读目标表，只重试仍缺失的记录。若回读失败、发现重复键或指标冲突，停止追加，避免盲目重试造成重复记录。
- 写入结束后回读完整去重范围，逐条检查本次 source 的存在性与新增、血量；缺失、重复或指标不一致会使任务失败。
- JSON 摘要包含 `requested_range`、`actual_range`、各运营位最新日期、缺失日期/运营位记录、真实零值记录、未映射 source 计数、跳过原因和写后校验结果。`status=partial` 表示覆盖不完整或有跳过报告/异常行，已获取的有效记录仍会同步；Actions 成功仅说明执行与写后校验通过，不等于区间完整。
- 未指定开始日期时，从实际父报告的最早日期至结束日期检查缺失，不推测 Tracker 默认窗口的额外日期。未映射 source 和 WPS 汇总行不写入；缺失记录不补零，也不删除已有数据。
- GitHub Actions 使用 pip 缓存，缓存键随本同步器的 `requirements.txt` 更新。

本地测试：`python -m unittest discover -s winriser-partner-sync/tests -v`。
