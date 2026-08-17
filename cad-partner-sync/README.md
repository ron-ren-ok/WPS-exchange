# CAD Partner Sync

每天从源表 [DWG数据回收](https://docs.google.com/spreadsheets/d/1zhFz3d996b1oiD4p4Oc786dPWlriu7gg6g2S6TN83b8/edit) 的 `DWG新增` 读取 CAD 与 WPS 合作的每日新增，并写入目标表 [26年-三方运营](https://docs.google.com/spreadsheets/d/1vSBU84SFoVlXdaczYYAev8mC0PEfjRQyVSv8s2OAGW4/edit) 的 `合作方新增血量`。

- 源表：A=`日期`、B=`运营位`、C=`DWG安装量`。
- 目标表：`合作方=CAD`，运营位沿用源表 B 列，`DWG安装量` 写入 `新增`。
- 不写入或覆盖 `血量`；源表新增运营位时无需改代码。
- 源日期为 `7月15日` 这类无年份文本，脚本根据北京时间的同步截止日推断年份，并兼容跨年。
- 首次运行自动补齐已有历史记录；日常运行复查最近 14 天。已有不同的非空值只记录冲突，不自动覆盖。
- GitHub Actions 每天北京时间 03:00 运行，也支持手动指定日期补数。

## GitHub Secret

仓库 Secret 使用 `GOOGLE_SHEET_SERVICE_ACCOUNT_JSON`，内容为服务账号 JSON。服务账号 `github-wps-daily-progress@wps-office-502510.iam.gserviceaccount.com` 需要拥有源表读取和目标表编辑权限。
