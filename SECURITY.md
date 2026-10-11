# Security policy

## Complete capsules are highly sensitive

The complete-copy mode intentionally includes project credentials, private Git
history, local databases, dependency trees, build artifacts, and any other
ordinary files stored below selected project roots.

Encryption is optional with --encrypt. AES-256-GCM authenticates independently
encrypted chunks; Windows uses system CNG. Keep the recovery key separate from
the capsule. Plain capsules remain unencrypted and need trusted storage.
Do not upload private capsules to GitHub, public cloud storage or issue trackers.

The plugin never uploads a capsule automatically.

## Safe restore behavior

- Package verification recalculates every SHA-256 checksum.
- Archive traversal, duplicate names, unexpected entries, and payload
  mismatches are rejected.
- Portable case collisions and entries descending through file/link ancestors
  are rejected. Discovery reads bounded metadata without executing package code.
- Restore uses a temporary sibling directory and verifies it before commit.
- Existing targets are blocked by default.
- All staged roots pass independent readback before commit; commit failures roll
  back new roots. Checkpoints pin the manifest and destination mapping.
- Authorized replacement moves the old target to a timestamped backup instead
  of deleting it.

SHA-256 and AES authentication prove content integrity relative to a manifest or
key, not publisher identity. Run self-contained package code only from a trusted
export. The deployment entry pre-fills a local Codex request and does not send it.

## Product and filesystem boundaries

The plugin does not copy Codex/ChatGPT authentication sessions, OS credential
vaults, installed applications, NTFS ACL ownership, NTFS alternate data
streams, or Codex's private application database.

## Reporting a vulnerability

Use GitHub's private "Report a vulnerability" option in this repository's
Security tab **if it is enabled**. Otherwise contact the maintainer through an
available private channel; do not put exploitable details or private data in a
public Issue. No response-time or independent-audit guarantee is made for this beta.
Never attach real credentials, recovery keys, private project files, task histories,
or migration capsules to a public report. Synthetic reproductions are preferred.

中文：安全问题使用仓库 Security 页的私密报告入口（如已开启），否则联系维护者的
私密渠道。不要将真实迁移包、恢复密钥、公司资产或完整错误报告公开上传。

The publication guard checks candidate/tracked files for prohibited artifacts and
common credential patterns. It is a defense in depth, not an exhaustive secret scanner
or a replacement for reviewing the actual staged diff before publication.
