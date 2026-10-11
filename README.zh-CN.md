# Codex 一键迁移

把 Codex 的项目、可读取的工作记录、关联资源与工作流配置一起搬到新电脑，
在用户选择的总文件夹中校验恢复，并提供有来源的续接说明。
这是社区插件，不是 OpenAI 官方产品，也不是整机镜像或聊天界面克隆工具。

版本：1.1.0-beta.3（Windows 优先的公开测试版）。迁移验收完成前请保留旧电脑和独立备份。

[安装教程](docs/INSTALL.md) · [常见问题](docs/FAQ.md) · [测试与速度边界](docs/VALIDATION.md) · [版本记录](CHANGELOG.md)

## 三步开始

1. **两台电脑都先安装插件。** 按[安装教程](docs/INSTALL.md)操作，完全退出并重启 Codex，
   新建聊天，从 `@` 菜单选择真实的“Codex一键迁移”插件。
2. **旧电脑打包。** 发送 `@Codex一键迁移 迁移`，等待清点、采集、打包及校验完成。
   将迁移包与同目录生成的 `.cmd`、`.ps1` 部署入口一起搬到新电脑。
3. **新电脑部署。** 发送 `@Codex一键迁移 部署`，或点击“开始部署.cmd”后发送预填请求。
   选择总文件夹的位置和名称，等校验恢复完成，再检查报告并运行关键项目。

没有找到包时，告诉 Codex 包的实际本地路径或所在目录即可；不需要上传大型包。

## 插件安装包 ≠ 私人迁移包

| 类型 | 用途 | 是否可以公开 |
| --- | --- | --- |
| `codex-project-handoff-版本-installer.pyz` | 仅安装插件代码和说明 | 从 [Releases](https://github.com/dawuwei1988-collab/codex-project-handoff/releases) 下载 |
| 自己生成的项目迁移 `.pyz` | 包含项目、历史、资源和配置，可能含密钥 | **不可以放到公开仓库或 Issues** |

普通迁移包不加密。仅执行可信来源的安装包和迁移包；SHA-256 校验能发现内容改变，
不能证明作者身份。需要加密时，在打包请求中明确要求加密，恢复密钥与包分开保管。

## 短指令与新机入口

旧电脑输入 `@Codex一键迁移 迁移`，完整打包项目、工作记录、工作流配置和关联资产。
新电脑输入 `@Codex一键迁移 部署`，或者点击随包生成的“开始部署.cmd”，发送预填提示，
选择总文件夹的位置和名称后自动恢复。英文操作词是 pack、restore。

@ 必须从输入框菜单中选择真实插件。新机需要先安装插件；安装包在 Releases 单独提供。
部署入口只打开 Codex 并填入实际本地包路径，不自动发送提示或绕过文件访问权限。
把入口的 cmd、ps1 和迁移包放在同一目录一起搬运；路径按入口当前所在位置计算。

## 自动关联与总目录

自动找包先查当前目录或用户指定的目录。搜索有限深度和条目数，不扫描整块硬盘。
通过包内标识区分项目包、普通 ZIP 和插件安装包。唯一有效候选自动关联；多个包由用户选，
搜索不完整时明确报告。大型包保存在本地，不需要上传。

新机使用 Windows 原生接口检查实际系统盘、分区和可用空间，优先推荐容量足够的非系统盘。
用户选择父目录和名称，可用 Codex工作空间、带日期、原电脑名称，也可自主填写。
所有解包内容位于一个总目录：

```text
用户选择的总目录/
├─ 项目/               原项目名称和文件树
├─ 关联资源/           项目外生成资产、链接依赖
├─ 环境配置/原始/      原始规则、技能、个人插件、配置和自动化
├─ 环境配置/适配/      改路径后的副本；自动化暂停
├─ 迁移说明/           工作记录、路径映射、覆盖率和校验报告
└─ .迁移状态/          中断恢复检查点
```

已有总目录默认不覆盖。同一恢复中断后可继续，已完成的文件先验证再复用。
原始配置保持不变；新机账号授权和全局配置激活使用 Codex 支持的设置/安装流程，
不会覆盖已有账号或自动启动旧自动化。

## 完整与速度

项目根目录零排除：包含 .git、未提交修改、隐藏文件、.env、依赖、构建结果、二进制和空目录。
每个文件保存 SHA-256、大小、修改时间、权限模式和 Windows 属性；链接记录原目标并按映射适配。
不可读、改变或不支持的条目会失败，不静默跳过。

schema 3 使用独立数据块、小文件合并、内容去重、低等级并行压缩和媒体直接存储。
恢复时验证块及文件，另做一次独立落盘读回；所有项目通过后提交，提交失败回滚。
预览只检查清单和路径，不重复扫描全部数据。支持 schema 2 旧迁移包。

支持导出/恢复中断继续，以及从 schema 3 旧包复用相同数据块生成新的独立完整包。
增量包不依赖携带旧包。源文件仍按内容读取和哈希，避免仅信任时间戳。
速度取决于源文件构成、磁盘和机器；[基准说明](docs/VALIDATION.md)只代表对应合成测试环境，
不包含实际跨电脑传输，也不承诺任何数据规模下都更快。

## 工作记录与关联资源

优先通过官方 app-server 分页历史接口把原始记录直接保存为文件，避免聊天输出大小限制。
与 app 工具清点的任务 ID 对照，报告详细记录覆盖率和缺失项；采集失败不声称完整接续。
恢复后阅读原始记录和有来源的续接说明，优先检查文件，避免让用户重述已有内容。

环境清点使用白名单，不读取 Codex 认证存储、系统凭据库或内部侧边栏数据库。
自动关联资产覆盖可识别的显式文件引用、链接目标和 Git worktree 的共同 Git 目录；
藏在无法解析输出中的路径需要缺失清单，不能承诺无条件发现所有外部资源。

## 加密与环境

加密可用 `--encrypt`，Windows 通过系统 CNG 使用 AES-256-GCM，不需要安装加密依赖。
未指定密钥文件时生成恢复密钥。只报告文件位置；请将密钥和迁移包分开保管。
新机解密使用 `--key-file`。密钥遗失无法恢复。非 Windows 解密需要带 cryptography 的 Python。

普通包不加密；完整包可能含项目密钥和私有历史，始终按私密资产处理。
本插件不上传、发送或发布迁移包。

需 Python 3.10+，可使用 Codex 自带 Python。建议 3.11+，以完整使用 TOML 配置依赖清点。
当前本地自动化验收运行于 Windows/Python 3.12；其他平台以实际 CI 和手动验收为准。
不同机器的软件、驱动、二进制依赖和 Git worktree 可能需要修复；文件复制不能替代软件安装。
官方接口未证明跨电脑原样导入桌面任务和侧边栏，因此不承诺聊天界面完全克隆。

## 开发与高级接口

普通用户使用 @ 入口。以下命令供开发、验证或独立恢复：
命令中的 `scripts/` 指插件技能目录内的脚本目录，不是仓库根目录。
从源码运行时先进入 `plugins/codex-project-handoff/skills/codex-project-handoff`。

```powershell
python scripts/codex_migrate.py inventory --projects-file projects.json --codex-context context.json --codex-home <Codex目录> --agents-home <agents目录>
python scripts/codex_migrate.py pack --projects-file projects.json --codex-context context.json --output migration.pyz
python scripts/codex_migrate.py discover --search-dir <迁移目录>
python scripts/codex_migrate.py deploy migration.pyz
python scripts/codex_migrate.py deploy migration.pyz --destination E:\ --folder-name Codex工作空间 --dry-run
python scripts/codex_migrate.py deploy migration.pyz --destination E:\ --folder-name Codex工作空间
python migration.pyz verify
```

pack 本身完成最终验包。旧 restore --map-root OLD=NEW 接口继续支持。
正常部署不使用原电脑路径作为默认目标。restore/deploy 预览返回 payloadVerified=false，
只有实际恢复后的校验才验证全部文件内容。

## 反馈与维护

普通问题通过 GitHub Issues 提交版本、系统、Python/Codex 版本、复现步骤与脱敏错误信息。
不要附真实迁移包、恢复密钥、完整聊天记录或公司资产；安全漏洞请按 [SECURITY.md](SECURITY.md)
使用私密渠道报告。修改须通过测试与发布检查，新版本经维护者确认后发布。
维护流程见 [MAINTENANCE.md](docs/MAINTENANCE.md)，手动验收见 [MANUAL-TEST.zh-CN.md](MANUAL-TEST.zh-CN.md)。

MIT License.
