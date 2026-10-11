# Changelog

## 1.1.0-beta.3

Windows-first beta, not a stable release or an official OpenAI product.

- Short `@` actions for pack / restore (迁移 / 部署).
- Bounded package discovery and moved-location deployment entry.
- One user-selected restore folder with non-system drive recommendations.
- Schema 3 block deduplication, small-file grouping and bounded compression; schema 2 reading.
- Resumable export / restore, independent reused-block exports, verified staging and rollback.
- Optional AES-256-GCM encryption with a separately stored recovery key.
- Readable task-history capture with coverage reporting; original/adapted configuration separation.
- Portable plugin-only installer preserving unrelated marketplace entries.
- Initial 38-test Windows acceptance; synthetic benchmarks with explicit measurement boundaries.
- Public documentation, privacy-aware feedback, publication checks and manual release workflow.

Known limits: no OS/application/session/sidebar clone; external discovery is not universal;
runtime dependencies and Git worktrees may need repair; live cross-machine UI acceptance remains required.
