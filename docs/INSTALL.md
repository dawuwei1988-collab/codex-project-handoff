# 安装 / Installation

当前版本：1.1.0-beta.3。两台电脑都需要安装；GitHub 发布不等于插件已进入官方公共插件目录。
需要本地 Codex、Python 3.10+（建议 3.11+）；Git 方式另需 Git。

## A. 便携安装包（推荐 Windows 用户）

1. 从 [GitHub Releases](https://github.com/dawuwei1988-collab/codex-project-handoff/releases)
   下载 `codex-project-handoff-1.1.0-beta.3-installer.pyz` 和 `SHA256SUMS.txt`。
   此包只含插件代码，不含作者的项目或聊天资料。不要用来源不明的安装包。
2. Windows PowerShell 可先核对下载文件的哈希：

   ```powershell
   Get-FileHash -Algorithm SHA256 -LiteralPath '.\codex-project-handoff-1.1.0-beta.3-installer.pyz'
   ```

   与该 Release 中 `SHA256SUMS.txt` 的同名记录对照。这个步骤能发现下载损坏，不能替代来源信任。
3. 把安装包的实际本地路径交给 Codex，并说：

   ```text
   请检查并安装这个 Codex 插件安装包，先运行 inspect 和 verify，再安装。
   如果已有不同版本，先说明冲突和备份方式，不要覆盖无关插件或账号配置。
   ```

   也可自己用可用的 Python 执行：

   ```powershell
   python '.\codex-project-handoff-1.1.0-beta.3-installer.pyz' inspect
   python '.\codex-project-handoff-1.1.0-beta.3-installer.pyz' verify
   python '.\codex-project-handoff-1.1.0-beta.3-installer.pyz' install --dry-run
   python '.\codex-project-handoff-1.1.0-beta.3-installer.pyz' install
   ```

   如果 `python` 不可用，可以让 Codex 查找其捆绑 Python 或自己安装 Python。
   不要把安装命令输进 Codex 聊天框当作已经执行的终端命令。
4. 安装位置默认是当前用户目录下 `plugins/codex-project-handoff`，个人市场登记在
   `.agents/plugins/marketplace.json`；保留无关市场条目。安装回执报告具体路径、校验和
   `codexRegistered` 状态。若登记未成功，先处理回执中的原因，不能把文件已复制当作已加载。
5. **完全退出并重启 Codex，新建聊天**，从 `@` 菜单选择“Codex一键迁移”。
   只有菜单能找到且新聊天加载了对应版本，才算安装可用。

## B. 从 GitHub 注册插件市场（开发者）

在能运行 `codex` 和 `git` 的终端中执行：

```text
codex plugin marketplace add dawuwei1988-collab/codex-project-handoff --ref main
codex plugin add codex-project-handoff@codex-project-handoff-marketplace
```

也可注册后重启桌面应用，在插件目录中选择对应市场并安装，再新建聊天。
想固定正式发布的代码，可把 `--ref main` 换成存在的发布标签，例如 `v1.1.0-beta.3`。
命令是否可用取决于本机 Codex 版本；可先运行 `codex plugin --help`。

安装方式依据 [OpenAI 官方插件打包与市场说明](https://developers.openai.com/plugins/build/plugins)。
本项目没有第三方服务登录要求，不需要提供 OpenAI API 密钥来安装。

## C. 源码本地安装

```text
git clone https://github.com/dawuwei1988-collab/codex-project-handoff.git
cd codex-project-handoff
codex plugin marketplace add .
codex plugin add codex-project-handoff@codex-project-handoff-marketplace
```

仓库中的 `.agents/plugins/marketplace.json` 指向 `plugins/codex-project-handoff`。
安装后仍需重启并新建聊天。仅复制 `SKILL.md` 不等于安装了完整插件及脚本。

## 更新和故障

同一便携包重复安装会检查现有文件是否一致。发现不同文件时默认停止；
确认旧版本可替换且本地改动已另存后，才使用 `install --replace-existing`。
替换会备份旧插件；市场条目冲突是另一项单独的授权，不要默认加
`--replace-marketplace-entry`。安装回执给出备份位置。

Git 市场可运行 `codex plugin marketplace upgrade codex-project-handoff-marketplace`，
再按本机的插件界面/CLI 刷新安装并重启验证版本。备份不是自动降级功能；
需要回退时明确选择旧版本并按同样的检查流程安装，不删除私人迁移包。

菜单找不到插件：检查安装回执、`codex plugin marketplace list`、是否完全重启、
是否在新聊天、Codex 是否支持当前市场命令以及插件是否启用。
报权限错误：让 Codex 说明需要访问的具体目录，再由用户按本机权限流程授权；不要关闭安全设置。

## English summary

Install on both computers. Download the plugin-only installer and checksum list from Releases.
Check its source and SHA-256, run `inspect`, `verify`, `install --dry-run`, then `install` with Python.
Read the installation receipt, fully restart Codex, and start a new chat. Select the plugin in the
`@` menu and send `pack` on the old computer or `restore` on the new computer.
Alternatively register the Git marketplace using the commands above. Never confuse a public
plugin installer with your private project migration capsule. Existing differing installations
are blocked until you explicitly authorize replacement; unrelated entries are preserved.
