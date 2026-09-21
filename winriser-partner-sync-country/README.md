# Winriser Partner Sync-Country

独立下载 Tracker Export Excel 国家明细，只写入「合作方新增血量分国家」；不读取、不修改原有 Winriser partner sync。

唯一键：日期 + 合作方 + 国家代码 + 运营位。Install Count 写入新增，PPI 写入血量；同国家的 campaign / publisher 明细先汇总。

手动运行时可填写“开始日期”和“结束日期”（`YYYY-MM-DD`），会选择 Tracker 的全历史导出并同步该闭区间内的国家明细；未填写开始日期时，保留原有有限日期窗口的日常同步。若 Tracker 不再提供全历史导出选项，会明确报错而不是写入不完整区间。`UNKNOWN` 或空国家代码为非国家占位值，会跳过；其他异常国家代码会报错。
