# 常见问题 / FAQ

## 只输入一个词就能开始吗？

可以。在 `@` 菜单选择真实插件后发送“迁移/打包/pack”或“部署/恢复/restore”。
只选择插件不写动作时，会询问这两个选项。导出内部连续完成清点、采集、打包和验包；
缺少文件访问权限、密钥或影响结果的选择时仍需要用户处理。

## 新电脑如何自动关联包？

最可靠的方式是把迁移包、`.cmd` 和 `.ps1` 一起搬过去，点击部署入口。
它按当前目录找到同伴迁移包并预填真实路径，不自动发送消息。
也可在存放包的目录中调用插件“部署”，或告诉它搜索哪一个目录。
搜索有深度和数量上限，不扫描整块硬盘。唯一有效候选且搜索完整时自动关联；
多个不同包由用户选择，找不到就指定本地路径。普通 ZIP 和插件安装包不会当作迁移包。

## 总目录在哪里？名称能自己取吗？

插件先读取实际磁盘、系统盘标识和可用空间，推荐合适的非系统盘，
随后一次询问父目录和总名称。可选 Codex工作空间、日期/原电脑名称，也可自填。
在用户选择前不创建恢复目录。所有项目、资源、配置和报告放在同一个总目录里。

## 会覆盖新电脑现有东西吗？

已有恢复目标默认阻断；同一中断恢复可使用 `--resume` 续作。
原始配置存档与适配副本分开，不直接覆盖新电脑的全局配置或登录状态。
自动化适配副本暂停。启用旧技能、插件、MCP、账号服务及安装运行环境仍需支持的设置流程。

## 是否包含聊天？会恢复到旧侧边栏吗？

保存官方读取接口能够获取的原始工作记录，并报告覆盖率和缺失项，
新电脑依据文件与记录继续工作。不承诺把原聊天导入成完全一样的桌面任务或侧边栏。
接口不可用、记录不可读或部分上下文只有摘要时必须明确披露。

## “完整”是否包含所有软件和所有外部文件？

选定项目根目录中的普通文件零排除，包括 `.env`、私有 Git 历史和构建产物。
外部资源按可识别引用与依赖清点；藏在无法解析输出里的引用可能需要手动纳入。
不复制安装的软件、系统凭据库、账号认证存储、私有产品数据库、NTFS ACL/ADS。
因此“文件已恢复”不等于“软件已安装”或“项目一定立即运行”。

## 包会上传到 GitHub 或其他服务器吗？

不会自动上传。公开仓库只用于插件代码和安装包。
你的迁移包可能包含密钥、公司资料和历史，必须使用可信的私密存储或传输途径。
不要把它作为 Issue 附件。普通包不加密；加密是明确启用的选项。

## 加密与恢复密钥怎么用？

让插件加密打包，或导出时加 `--encrypt`；生成的恢复密钥文件与包分开保存。
恢复时提供本地密钥路径（CLI 使用 `--key-file`），不要把密钥文本粘进聊天。
密钥遗失无法恢复。Windows 用系统 CNG；非 Windows 需要 Python 的 `cryptography` 包。

## 中断或者旧电脑文件变化了怎么办？

保留包和检查点，在相同输出或相同恢复目标使用 `--resume`。
已恢复文件会验证后复用；源文件或上下文改变时拒绝盲目续传，需要新的输出。
对已经完成的恢复目录手动修改文件后，不能把它假装成未改变的检查点继续覆盖。

## 文件很多，会不会更慢？

小文件合并、分块去重、适度并行和媒体直接存储减少部分 I/O、处理或传输量。
哈希与独立落盘读回仍需时间。唯一的不可压缩大文件、慢 USB、软件版本检查、
空间不足和跨电脑传输均可能成为瓶颈。合成基准见 [VALIDATION](VALIDATION.md)，没有统一加速保证。

## 不用插件还能恢复吗？

可信的迁移 `.pyz` 是自包含恢复程序，Python 3.10+ 可运行其 `inspect`、`verify` 和 `deploy`。
高级命令以 `--help` 为准。要继续使用 `@` 工作流仍需安装插件；加密包还需要恢复密钥。

## 如何报告错误？

提供版本、系统、Python/Codex 版本、复现步骤、预期/实际结果和脱敏错误。
用最小的虚构测试项目复现，不上传真实迁移包、全量报告、登录信息或公司路径。
安全问题按 [SECURITY](../SECURITY.md) 私密报告。

## English summary

`@` + `pack`/`restore` selects the workflow; restore requires your folder choice.
Discovery is bounded and ambiguity is never resolved by guessing the newest capsule.
The deployment entry follows the capsule's moved location and only prefills a request.
Configuration archives do not overwrite your new account or global settings. History coverage
is reported, not a promise to clone the sidebar. Keep capsules private, opt into encryption when
needed, keep keys separate, and verify files **and** project execution before retiring the old machine.
