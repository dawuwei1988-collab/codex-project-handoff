# 验证范围 / Validation scope

当前 1.1.0-beta.4：本地 Windows / Python 3.12.14 的 44 项隔离测试全部通过，
包括新增公开文件检查与跨平台认证异常回归。GitHub 矩阵结果以实际 Actions 记录为准。

1.1.0-beta.3 的本地 Windows / Python 3.12.14 隔离验收：38 项通过，0 失败、0 错误、0 跳过。
覆盖文件往返、新旧包、去重、错误密钥/篡改拒绝、中断续作、回滚、找包歧义和便携安装。
发布准备阶段另加公开文件检查测试；以具体 Release 验收记录为准。

这是本地测试，不代表所有 GitHub CI 组合已通过，也不能替代真实跨电脑手动验收。
CI 配置 Windows/Ubuntu × Python 3.10/3.12；Python 3.10 的 TOML 依赖发现用例明确跳过，
部分原生文件属性仅在相应平台验证。Python 3.11+ 推荐用于 TOML 清点。

## 合成基准（每种场景一次测量）

同一台 Windows 11 机器，对比旧 1.0.0-beta.1 与新 1.1.0-beta.3，
计时包含本地导出与验包、预览与恢复，不包含跨电脑/网络/移动硬盘传输。
两个版本都独立核对恢复文件树。以下数值保留原测试记录，不由单次结果推断普遍加速。

| 合成内容 | 旧本地总耗时 | 新本地总耗时 | 旧包大小 | 新包大小 |
| --- | --- | --- | --- | --- |
| 1,500 个文本文件，7,089,000 字节 | 4.040 秒 | 3.173 秒 | 7,977,775 字节 | 655,278 字节 |
| 两个相同二进制文件，共 128 MiB | 0.843 秒 | 0.799 秒 | 134,273,546 字节 | 67,245,950 字节 |

第一场景本地总耗时约少 21%，包约小 92%；第二场景约少 5%，包约小 50%。
第二场景新版本导出阶段实际更慢（0.444 秒 vs 0.272 秒），不能说各阶段都加速。
大包去重能减少搬运量，但实际收益取决于文件内容和设备，未做 TB 级或真实跨机计时。
可运行 `python tests/benchmark_migration.py --help` 查看复现入口；旧脚本需自己提供可信版本。

## 正式版前仍需验证

- 全新电脑上的安装、菜单发现、短指令、移动后的部署入口。
- 真实任务历史读取的覆盖率，以及部分接口不可用时的缺失报告。
- 大量真实资产、移动盘空间不足、休眠/强制中断后的续作。
- Git worktree、软链接、文件属性与环境适配后的实际项目运行。
- 原生加密实现和不可信输入解析的独立安全审查。

手动记录须注明版本、平台、步骤、实际结果和残留问题；不包含私人迁移数据。
验收步骤见 [MANUAL-TEST](../MANUAL-TEST.zh-CN.md)。

English: 44 current beta.4 local Windows/Python 3.12 tests passed; the initial beta.3 baseline had 38.
CI and live two-computer workflows
are separate evidence. Benchmarks are synthetic, single-run, local-only measurements; do not
interpret them as a universal speed or platform-compatibility guarantee.
