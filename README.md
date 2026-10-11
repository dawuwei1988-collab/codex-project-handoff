# Codex Project Handoff

[中文说明](README.zh-CN.md) · [Install / 安装](docs/INSTALL.md) · [FAQ](docs/FAQ.md) · [Security](SECURITY.md)

Version 1.1.0-beta.3, a Windows-first public beta. This is a community plugin,
not an official OpenAI product. Keep the old computer and an independent backup
until restored files and actual project execution have been verified.

## Quick start

Install on **both** computers using the [installation guide](docs/INSTALL.md).
Fully restart Codex, start a new chat and select the real plugin from the `@` menu.
Send `@Codex一键迁移 pack` on the old computer and `@Codex一键迁移 restore` on the new one.
Copy the private capsule and its generated `.cmd` / `.ps1` deployment entries together.
You choose the restore parent folder and name before any extraction takes place.

| Package | Contains | Where it belongs |
| --- | --- | --- |
| `*-installer.pyz` | Plugin code and instructions only | [GitHub Releases](https://github.com/dawuwei1988-collab/codex-project-handoff/releases) |
| Your migration `.pyz` | Your projects, history, assets and configuration; may contain secrets | Trusted private storage only; **never** public GitHub or Issues |

Plain capsules are **not encrypted**. Optional encryption uses a separate recovery key;
losing that key makes recovery impossible. Only execute trusted capsules/installers.
Checksums do not prove publisher identity.

## Behavior and boundaries

Select the plugin in the @ menu and send **迁移** (pack) or **部署** (restore).
The generated local deployment entry opens Codex with the package's current path prefilled.
It does not submit a prompt, bypass permissions, or install the plugin without the user's action.

The export includes complete project trees, supported task-history exports, explicit external assets,
and allowlisted workflow configuration. Ordinary project files have no exclusion list.
Account authentication, operating-system credentials and private product databases are not copied.

Deployment discovers packages by format markers, not filenames. A unique valid package can be selected
automatically; ambiguous or incomplete discovery requires selection. Native Windows drive inventory
recommends sufficiently large non-system volumes. Users choose the total parent and folder name.

All contents restore beneath that total folder: projects, associated resources, original environment
configuration, separate adapted configuration, reports and recovery state. Existing destinations are
preserved by default. Source configuration remains unchanged; activation uses supported product APIs.

Schema 3 uses independent indexed blocks, small-file grouping, content deduplication, bounded native
compression workers and direct storage for compressed media. Each final export is independently
restorable, including exports that reuse a previous package. Source content is still hashed.

Interrupted exports retain a durable ZIP-index checkpoint; resume rejects changed source metadata
or context. Restore resumes by validating completed files. All staged roots are independently
read back before commit; commit errors roll back roots. Read-only preview does not scan payload.

AES-256-GCM encryption is optional. Windows uses built-in CNG; other systems need cryptography.
Store the recovery key separately and use --key-file when verifying/restoring encrypted packages.
Schema 2 legacy capsules remain readable. Python 3.10+ is required; bundled Codex Python is supported.

Task history is captured through supported app-server read APIs and compared with an app inventory.
Coverage gaps are reported. This is not a promise to clone the desktop sidebar or account sessions.
External assets hidden in unparseable outputs, machine-specific dependencies and Git worktree metadata
can require follow-up. Runtime checks and path adaptation reports make those limitations inspectable.

The initial local Windows/Python 3.12 acceptance record passed 38 isolated tests.
See [validation scope and synthetic benchmarks](docs/VALIDATION.md); these do not establish
universal transfer speed or a complete live two-computer UI flow. GitHub Actions is configured
for Windows/Ubuntu and Python 3.10/3.12; consult the actual run status.
Python 3.11+ is recommended for TOML-based dependency discovery.

## Development and feedback

```text
python tests/check_publication.py
python -m unittest discover -s tests -v
```

See [the Chinese guide](README.zh-CN.md), [manual acceptance](MANUAL-TEST.zh-CN.md),
[CONTRIBUTING](CONTRIBUTING.md), [MAINTENANCE](docs/MAINTENANCE.md), [CHANGELOG](CHANGELOG.md)
and the packaged skill references. Use synthetic reproductions and sanitized error messages
in Issues; do not attach real capsules, keys, task histories or company assets.
The private migration capsule must never be published with the plugin source or installer.

MIT License.
