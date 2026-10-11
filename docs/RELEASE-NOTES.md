# Codex Project Handoff 1.1.0-beta.3

Windows 优先的社区测试版，不是 OpenAI 官方产品。请保留旧电脑和独立备份直到迁移验收完成。

## 安装与使用

下载本 Release 的 `*-installer.pyz` 和 `SHA256SUMS.txt`，核对哈希，再交给 Codex
检查、验证并安装。安装包只含插件代码。两台电脑都需安装；完全退出并重启 Codex，
新建聊天，从 @ 菜单选择真实插件。

- 旧电脑：`@Codex一键迁移 迁移`（pack）。
- 新电脑：`@Codex一键迁移 部署`（restore）；选择总文件夹的位置与名称后校验恢复。
- 搬运迁移包时将其 `.cmd` 和 `.ps1` 入口一起搬运，入口会预填包的新位置，不自动发送。

[安装教程](https://github.com/dawuwei1988-collab/codex-project-handoff/blob/main/docs/INSTALL.md)
· [中文说明](https://github.com/dawuwei1988-collab/codex-project-handoff/blob/main/README.zh-CN.md)
· [常见问题](https://github.com/dawuwei1988-collab/codex-project-handoff/blob/main/docs/FAQ.md)

## 安全和支持范围

你的项目迁移包可能含项目密钥、私有 Git 历史、公司资料和聊天记录，**不要上传 GitHub**。
普通迁移包不加密，可明确启用 AES-256-GCM；恢复密钥单独保管，遗失无法恢复。
只运行可信来源的包。SHA256SUMS 是完整性校验，不是作者签名。

保留项目文件、可读取历史、关联资产和白名单配置；不承诺软件已安装、登录状态复制、
原聊天侧边栏克隆或跨操作系统完全一致。外部资源发现和历史采集有边界，缺失项必须报告。
原始配置与适配副本分开，新机已有账号/配置不直接覆盖。实际项目运行须另外验收。

本 Release 的 `validation.json` 记录构建时隔离测试结果。
真实跨电脑 UI、超大项目及所有平台尚不具备完整验收；性能基准仅为单机合成场景。
反馈时使用虚构最小项目和脱敏错误，不附真实迁移资料。

English: Windows-first community beta. Install the plugin on both computers, restart Codex and
start a new chat. Select the plugin with `@`, send `pack` on the old computer and `restore` on the
new one, then choose the destination folder. The installer contains plugin code only; migration
capsules are private and must never be uploaded here. Plain capsules are unencrypted. Keep
recovery keys separate and keep the original data until file and project-runtime validation.
This release does not clone OS applications, account sessions or the desktop sidebar.
