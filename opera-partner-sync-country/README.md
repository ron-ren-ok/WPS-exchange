# Opera Partner Sync-Country

从指定 Gmail 邮箱接收的 Looker ZIP 国家明细，写入「合作方新增血量分国家」。唯一键：日期 + 合作方 + 国家代码 + 运营位。

Campaign 映射：`wpstest2/opera.exe` → 换量弹窗，`wpstest` → 气泡，`recall2/opera.exe` → 卸载引导，合作方均为 Opera。卸载引导按日期、国家汇总新增和血量；报表未提供该 campaign 时跳过，不补零。

默认运行仅解析主题与发件人匹配的最新一封邮件中的 ZIP 附件。手动填写“开始日期”和“结束日期”（`YYYY-MM-DD`）时，会遍历匹配的历史附件，抓取该闭区间内的所有数据；同一日期、国家和运营位重复出现时，以最新邮件的数据为准。
