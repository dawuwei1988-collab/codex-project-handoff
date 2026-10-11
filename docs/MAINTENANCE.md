# 维护与发布 / Maintenance and release

## 范围

维护包括 CI 故障、可复现缺陷、安全风险、Codex 安装/历史接口兼容性与文档。
不承诺响应时限，不把 Beta 当作关键数据唯一备份，不自动打开或运行用户提交的迁移包。
每周巡检由维护者的独立任务安排，不在插件用户电脑中安装定时任务。

定期检查默认只读：查看新 Issues、CI 和兼容性变化；无变化保持安静。
可验证的故障或风险才报告。未经明确授权不回复第三方、关闭 Issues、合并 PR、
变更权限或发布新版本。任何自动化安排以实际创建结果为准，本文不等于已启动服务。

## 缺陷处理

1. 用脱敏的最小合成项目复现；迁移包、密钥和私人报告不进入仓库。
2. 先判断影响：数据损失/错误覆盖/泄露 > 无法迁移恢复 > 性能或易用性。
3. 修复须有失败复现测试；修复后运行全量测试、公开文件检查及对应手动验收。
4. 变更格式要说明向后兼容性；文档记录实际支持范围和已知限制。
5. 影响用户数据的修复需要维护者审阅，不自动批量部署。

## 发布前检查

```text
python tests/check_publication.py
python -m unittest discover -s tests -v
```

审查首次提交/本次 diff。源代码仓库不得含迁移包、恢复密钥、全量聊天和个人环境资料。
便携安装包只能从插件目录构建，必须 `inspect` 与 `verify`，核对其版本及文件清单。
SHA256SUMS 仅校验完整性，不宣传为代码签名。

`plugin.json`、`.codex-plugin/plugin.json` 与脚本 VERSION 必须一致。
按风险更新 MANUAL-TEST、CHANGELOG、安装包名称与下载说明。CI 矩阵全部成功后再发布。

## 手动发布流程

仓库提供 `Release beta` 工作流，仅维护者手动触发，不因普通 push 或 PR 自动发布。
它重新跑 Windows/Ubuntu × Python 3.10/3.12，检查公开文件，再从插件目录构建安装包、
验包并生成 SHA256SUMS。发布步骤使用只对该 job 授予的仓库 contents write 权限。
当前只允许 Beta 预发布，不覆盖已有 Release；若版本已存在需新版本或明确人工处理。

首次或缺少 Actions 写权限时，也可在矩阵通过后手动创建同版本预发布，
上传经验证的插件安装包、SHA256SUMS 和本次测试结果。
私人项目迁移包永远不上传。发布后检查下载文件哈希、安装路径、菜单发现和独立恢复。

MIT、社区插件和已知限制保持醒目。稳定版须另行完成真实两机验收与安全审查。

English: triage privacy-safe reproductions, prioritize data integrity, add regression tests,
run the CI matrix, and explicitly approve each release. The manual beta workflow builds only
the plugin payload, validates it, generates checksums and never uploads migration capsules.
