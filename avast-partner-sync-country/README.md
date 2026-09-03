# Avast Partner Sync-Country

从 `54lingbai@gmail.com` 中发件人为 `online.acquisition@avast.com`、标题以 `Daily Performance Snapshot from Avast | WPS |` 开头的邮件附件 CSV 读取数据。

以附件 CSV 中四个指定 campaign 最新的有数据日期为锚点，同步该日及其前两天（例如最新为 8 月 23 日，则同步 8 月 21–23 日）。CSV 的 `installs` 写入新增；血量按「合作方价格」中 Avast 的国家代码、运营位价格计算（`新增 × 价格`）。任一实际数据行缺少价格时终止且不写表。已有键的新增和血量会用计算结果覆盖。

映射：`mmm_wps_ppi_008_595_b` → `avast气泡`；`_a` → `avast换量弹窗`；`_e` → `avast文档雷达`；`_c` → `avast卸载后弹出H5`。
