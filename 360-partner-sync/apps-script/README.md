# 360 同步：GitHub 管理，Apps Script 运行

目标是「26年-三方运营」的「合作方新增血量」；源表及业务规则与 Python 版相同。
此目录是 Apps Script 源码，GitHub Actions 不负责运行此版本。

## 安装到现有表格的脚本

1. 在目标表打开「扩展程序 → Apps Script」。保留已有文件，新建 `Sync360.gs`，复制本目录 `Code.gs` 全文；文件名可不同。
2. 在左侧「服务」添加 **Google Sheets API v4**（标识符 `Sheets`）。使用自定义标准 Google Cloud 项目的脚本，还需要在该项目启用 Google Sheets API。
3. 在项目设置中设置时区 `Asia/Shanghai`。本目录的 `appsscript.json` 是独立项目的参考；已有项目请合并服务与权限，不要覆盖其原有配置。
4. 用同时有源表查看和目标表编辑权限的账号运行 `preview360Daily`，授权后检查执行日志。预览不写入。
5. 运行 `sync360Daily`，检查写入结果，再次运行应不重复追加。已有非空新增保留，血量不被覆盖。
6. 在 GitHub 的 `360 partner sync` 工作流页面选择 **Disable workflow**，停止旧定时任务。再运行 `install360DailyTrigger`，每天北京时间 03:00–04:00 执行。该函数只替换当前账号创建的 `sync360Daily` 触发器；请仅由一个运营账号安装。

不需要 Web 应用部署，也不需要服务账号密钥。首次授权和触发器属于当前 Google 账号。
`LockService` 防止同一脚本项目的并发执行，不能锁住仍在运行的 GitHub 作业或另一个脚本项目；切换时应确认旧作业已结束。

## 历史补数

临时新增包装函数，先预览再将最后一个参数改成 `false` 执行：

```javascript
function backfill360() {
  return sync360Range('2026-09-01', '2026-09-30', true);
}
```

默认只追赶各运营位最后有效日期后的缺数并补查最近 14 天，截止北京时间昨天。源表空白与 `360-4` 的零均跳过，其他零有效。历史补数也只填空，不覆盖非空新增。
日期按源数据解析，不依赖源表每天一行的连续布局。源表无数据、表头异常、相关重复记录或无效数值均会中止，不带着未验证的计划写入。

## 后续通过 clasp 同步

如果已有脚本代码，先在单独目录执行 `clasp clone SCRIPT_ID` 备份并纳入版本管理，再加入本目录代码，合并 manifest。**不要直接把此目录 push 到有其他脚本的项目**，否则可能删除远端未纳入本地的文件。
完成合并后使用 `clasp push` 更新脚本；`clasp` 的登录令牌不得提交 GitHub。Apps Script 编辑器修改不会自动回传 GitHub，应同步回仓库后再更新。

本地验证：`node --test 360-partner-sync/tests/test_apps_script.cjs`。
