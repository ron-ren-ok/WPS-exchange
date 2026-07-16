# Yandex Partner Sync

Yandex 换量弹窗与气泡数据同步项目。

后续范围：

- 从 Yandex Distribution API 获取每日 Installations 与 Partner reward。
- 按已验证的换量弹窗、气泡 Profile 进行数据校验。
- 将通过预检的数据写入 Google Sheet 的对应表头。
- 由 GitHub Actions 在工作日北京时间 10:00 执行。

凭证只通过 GitHub Actions Secrets 或云端身份联合提供，绝不提交到仓库。
