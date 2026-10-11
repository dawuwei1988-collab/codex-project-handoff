# 部署流程

新电脑的真实包路径优先于旧电脑原路径。包可留在本地或移动硬盘，不需要上传。

```powershell
python scripts/codex_migrate.py discover --search-dir <当前工作目录>
python scripts/codex_migrate.py deploy <包.pyz>
```

第二个命令只读返回包元数据、驱动器空间、系统盘标识和名称建议，不创建目录。
可以追加用户允许访问的 Downloads、Desktop、移动盘 Codex迁移输出 目录；
搜索默认两层、最多 5000 条目，不递归扫描全部硬盘。不可访问或触及上限时标注
searchIncomplete，不能据此自动断言唯一候选。相同 capsuleId 的副本归为同一候选。

一次询问让用户选择父目录（列出实际非系统盘及可用空间）和总目录名称，允许自填。
优先 NTFS 本地非系统分区，系统盘保留为用户自主选择。名称使用单个合法目录名。
原电脑、创建时间、项目数和大小用于多个包的选择，不默认选择最新包。

加密包先读取公开元数据；读取完整清单和恢复时需要 --key-file，必要时请用户提供
单独保管的本地密钥文件。不要输出或写入聊天密钥内容。

用户选择后内部连续执行：

```powershell
python scripts/codex_migrate.py deploy <包.pyz> --destination <父目录> --folder-name <名称> --dry-run
python scripts/codex_migrate.py deploy <包.pyz> --destination <父目录> --folder-name <名称>
```

预览只检查清单与映射，标注 payloadVerified=false；实际恢复在读取块时验证包中数据，
每个文件写入后再作一次独立读回。所有根目录校验通过后一起提交，提交失败回滚。
同盘重命名后检查目录，避免再次读取全部文件。中断会保留检查点及完成文件；
续作使用同一包、目标名称及 `--resume`，先检查已完成文件是否变化。

总目录中的 项目 保持原目录结构，关联资源 记录旧位置，环境配置/原始 保持原配置，
环境配置/适配 仅保存需要改路径的副本，迁移说明 包含上下文、覆盖率、路径适配和环境检查。
配置存档不会覆盖新机全局配置。通过受支持安装/设置流程启用需要的技能、插件和 MCP，
自动化适配副本保持暂停；用户的新账号连接仍需重新授权。

阅读 RESTORE-CONTEXT.md、RESTORE-REPORT.json、ENVIRONMENT-CHECK.json 和任务原始记录；
查看真实 Git 状态、链接和运行依赖。Git worktree 的 .git/commondir 可能需要 repair，
普通文件校验成功不能替代“项目能运行”的验证。接口不能注册项目时说明需打开目录一次。
