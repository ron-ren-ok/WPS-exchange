# Avast Partner Sync

从 Gmail 读取 Avast Daily PBI Report 的 PDF 附件，并写入 Google Sheet「合作方新增血量」长表。

| 邮件主题 | 合作方 | 运营位 |
| --- | --- | --- |
| Avast AV - WPS - Daily PBI report | Avast | 换量弹窗 |
| Avast AV - WPS - E - Daily PBI report | Avast | &#25991;&#26723;&#38647;&#36798; |
| Avast AV - WPS - Toast - Daily PBI report | Avast | 气泡 |
| Avast One - WPS - C - Daily Report PBI | Avast | 卸载后引导H5 |

默认只检查、覆盖和补充北京时间昨天及之前 6 天，共 7 个自然日。例如 9 月 28 日运行只写 9 月 21–27 日。普通运行仍只读取当天每个运营位最新的一封匹配邮件。

填写 `--start-date` 或 `--end-date` 后启用历史补数，按数据日期闭区间过滤；只填截止日期时默认回溯 7 天，只填开始日期时截止到昨天。反向范围会在联网前被拒绝。历史模式检索从开始日期到今天的邮件，包含次日送达和后续修订的报告，按 UID 从新到旧读取；同一运营位、同一天优先使用较新报告的完整指标，旧报告仅补齐未覆盖的日期。

历史补数示例：`python src/avast_partner_sync.py --start-date 2026-08-01 --end-date 2026-08-31 --allow-overwrite`。

长表使用「日期、合作方、运营位、新增、血量」五个字段。同步器以日期 + 合作方 + 运营位定位记录：空值补充，差异值仅在 `--allow-overwrite` 时覆盖，不存在则追加一行。读取完整 `A:E` 范围，超过第 10000 行的记录也参与查重。只修改本次日期范围内的 Avast 新增和血量。

每份 PBI 报告中，第一个不带美元符号的 Total 是新增；紧邻的带美元符号的 Total 是血量。仅接受 no-reply-powerbi@microsoft.com 发件的邮件，或正文中明确标注该原始发件人的 partner@wps.com 转发。

同一天只有新增或只有血量时，两个字段都不写，其他指标齐全的日期继续同步，并告警列出跳过日期。PDF 存在空白列时，优先根据日期表头和数值的实际列位置对应数据；文本回退只接受日期列与数值列数量一致且日期唯一的表。无法确定日期对应关系时不猜测、不平移指标，跳过并告警。共享表头仅用于确实没有独立血量表头的导出。

## 失败、告警与回读

- 缺报、没有可用数据、抓取失败、解析失败、正常无变化分别记录；缺报运营位不阻止其他运营位同步。换量弹窗和气泡缺报会告警；可选的 H5/文档雷达没有邮件时不单独告警。
- 全部运营位没有完整日指标时任务失败，触发原有生产 webhook；抓取/解析失败时先完成其他运营位的写入及回读，再以失败状态退出。
- 缺单项指标、无法确认列对应关系或请求日期未覆盖时，任务可完成，但生产 webhook 会发送跳过日期告警。告警包含范围、运营位、原因和运行链接。正常且无告警时不发送消息。
- Sheets 读取/覆盖写入及可重试的网络错误最多重试 3 次，间隔 1、2、4 秒，网络请求超时 30 秒。追加响应不确定时先回查完整主键和值，只补写仍缺失的记录，避免盲目重复追加。
- 同步完成后回读并核对本次完整指标。`--status-file sync-status.json` 保存成功/失败、告警、写入数量和验证数量；日志记录来源邮件 UID、附件名称和实际数据日期范围。
- Actions 使用 pip 缓存、30 分钟任务超时及工作流级互斥，定时和手动运行不会同时执行；每次连接只选择一次只读邮箱。

## GitHub Actions Secrets

- GOOGLE_SHEET_SERVICE_ACCOUNT_JSON：可复用既有服务账号 JSON。
- GMAIL_IMAP_USERNAME：读取 Avast 邮件的 Gmail 地址（目前为 `54lingbai@gmail.com`）。
- GMAIL_APP_PASSWORD：该 Gmail 账号为本任务创建的 16 位 Gmail 应用专用密码。

凭证仅存于 GitHub Secrets，绝不提交到仓库。工作流每天北京时间 03:00 运行，也可在 Actions 页面手动补数。

## 首次 Gmail IMAP 配置

1. 为 `54lingbai@gmail.com` 开启两步验证。
2. 打开 Google 账号的“应用专用密码”，创建一个名称为 `WPS Partner Sync` 的密码。
3. 将 Gmail 地址保存到 GitHub Secret `GMAIL_IMAP_USERNAME`，将生成的 16 位密码保存到 `GMAIL_APP_PASSWORD`（可包含或不包含显示用空格）。

同步器只通过 `imap.gmail.com:993` 的只读邮箱连接获取符合主题和发件人校验的 PDF；不再使用 Google OAuth refresh token。旧的 `GMAIL_OAUTH_CLIENT_JSON` 与 `GMAIL_REFRESH_TOKEN` 可以在新任务验证成功后删除。
