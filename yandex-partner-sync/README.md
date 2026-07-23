# Yandex Partner Sync

Yandex 换量弹窗与气泡数据同步项目，不依赖 AI 或本机浏览器。

## 工作内容

- 调用 Yandex Distribution API，读取 `Installations` 与 `Partner reward`。
- 以版本化 Profile 校验国家、Pack ID、指标字段与 API 模板指纹。
- 写入 Google Sheet「合作方新增血量」长表：
  - Profile `换量弹窗` 写入合作方 `Yandex`、运营位 `换量弹窗`；
  - Profile `气泡` 写入合作方 `Yandex`、运营位 `气泡`。
- 长表字段是「日期、合作方、运营位、新增、血量」，以这三项共同定位一条记录；已有记录更新指标，不存在则追加。
- 默认补齐至北京时间昨天。Yandex API 省略的零活跃日期会显式写入 `0 / 0`；没有历史数据的连续前置区间会跳过。

## GitHub Actions

工作流可手动执行，并在每天北京时间 03:00 定时运行。

必须配置以下 GitHub Actions Secrets：

- `YANDEX_DISTRIBUTION_TOKEN`（可保存纯 Token，或带 `OAuth ` 前缀）
- `GOOGLE_SHEET_SERVICE_ACCOUNT_JSON`

服务账号必须已被授予目标 Google Sheet 的编辑权限。凭证绝不提交到仓库。手动补数时可在 Actions 页面填写 `start_date`、`end_date`。已验证的 Yandex API 是这两个运营位数据的权威来源，任务会自动覆盖旧值并在日志中记录。