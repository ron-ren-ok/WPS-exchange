# Yandex Partner Sync

Yandex 换量弹窗与气泡数据同步项目，不依赖 AI 或本机浏览器。

## 工作内容

- 调用 Yandex Distribution API，读取 `Installations` 与 `Partner reward`。
- 以版本化 Profile 校验国家、Pack ID、指标字段与 API 模板指纹。
- 按 Google Sheet 的实时表头写入四个 Yandex 指标。
- 默认从最早缺失数据日期补齐至北京时间昨天；非空差异默认中止，只有手动触发并显式允许时才可纠正。

## GitHub Actions

工作流支持手动执行，并在工作日北京时间 10:00 定时运行。

必须配置以下 GitHub Actions Secrets：

- `YANDEX_DISTRIBUTION_TOKEN`
- `GOOGLE_SHEET_SERVICE_ACCOUNT_JSON`

服务账号必须已被授予目标 Google Sheet 的编辑权限。凭证绝不提交到仓库。

手动补数时，可在 Actions 页面填写 `start_date`、`end_date`。若确认要以 Yandex API 值纠正已存在的不同数据，才开启 `allow_overwrite`。