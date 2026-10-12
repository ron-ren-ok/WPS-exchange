# CCleaner Partner Sync-Country

从 `54lingbai@gmail.com` 中发件人为 `online.acquisition@avast.com`、标题以 `Daily Performance Snapshot from Avast | WPS |` 开头的邮件附件 CSV 读取数据，与 Avast partner sync-Country 使用同一邮件来源。

- 只同步 `cookie=mmm_ccl_ppi_008_617_b` 的分国家数据，合作方为 `CCleaner`，运营位为 `气泡`。优先读取 `cookie` 列；没有该列时，仅在能够通过指定 cookie 唯一定位数据列的情况下解析。
- 写入同一 Google Sheet 的「合作方新增血量分国家」，唯一键为「日期 + 合作方 + 国家代码 + 运营位」。`installs` 写入新增，同一附件内相同键先汇总；多个邮件报告重叠时使用较新邮件中的数据，不累加、不让旧报告覆盖新报告。
- 血量沿用 Avast 国家同步的计价方式：读取「合作方价格」中 `CCleaner + 国家Code + 气泡` 的价格，计算 `新增 × 价格`。缺少价格时仍写新增，新记录血量留空，已有血量不改；缺价明细会记录在 Actions 日志中。CSV 自带金额列不参与计价。
- 默认以指定 cookie 的最新数据日期为截止，同步当天及前两天。手动填写开始和结束日期（`YYYY-MM-DD`）可同步该闭区间；实际结束日期为不晚于指定结束日期的最新有数据日期。只填开始日期则同步至最新数据日期，只填结束日期则同步实际结束日期及前两天。
- 已有记录的新增和可计算血量会覆盖，未变化的值不重复写，不存在的键追加。国家代码标准化为大写两位代码，空国家跳过，其他非法代码报错。

GitHub Actions 中手动运行 **CCleaner partner sync-Country**，复用三个 Secrets：`GOOGLE_SHEET_SERVICE_ACCOUNT_JSON`、`GMAIL_IMAP_USERNAME`、`GMAIL_APP_PASSWORD`。与现有 Avast 国家工作流一致，不设置自动运行时间；原有 CCleaner PDF 总量同步保持独立。并发运行此工作流会排队。读取较新累计报告覆盖全部请求日期后停止下载历史邮件；写入后重新读取目标记录，核对新增及可计算血量，核验失败则任务失败。

首次运行前，在「合作方价格」配置 CCleaner 各国家的气泡价格，并确认国家明细表包含 `日期、合作方、国家代码、运营位、新增、血量` 六列。没有目标 cookie 数据时，脚本失败且不写表。

本地验证：`python -m unittest discover -s ccleaner-partner-sync-country/tests -v`。

已用提供的 `wps (5).csv` 完整验证解析，并核对 Gmail 中 2026-10-11 的报告正文和附件元数据：附件字段为 `date,country,cookie,brand,partner,installs`，目标 cookie 的 `brand` 为 `PIRIFORM`、`partner` 为 `wps`。上传文件共 47,321 行，目标 cookie 共 4,163 行，其中两行国家为空，解析得到 4,161 条国家记录（2026-08-13 至 2026-10-10）。默认窗口 2026-10-08 至 2026-10-10 共 263 条记录，安装量分别为 9,088、12,984、7,917，合计 29,989。测试样本保留实际六列结构、Avast 混合行及空国家行；此验证未向 Google Sheet 写入数据。
