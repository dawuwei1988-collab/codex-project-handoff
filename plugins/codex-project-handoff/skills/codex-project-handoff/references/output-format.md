# Migration capsule format

Tool 1.1.0-beta.4 exports schema 3 and reads schemas 2 and 3.

## Plain schema 3

```text
migration.pyz
├── __main__.py
├── migration_support.py
├── migration_crypto.py
├── capture_context.py
├── capsule.json
├── manifest.json
├── START-HERE.md
├── context/codex-context.json   optional, original captured records
└── blobs/<sha256>              independent packed blocks
```

Each logical file has its SHA-256, size, portable path and metadata, plus block slices
(id, offset, size). Small files share a block; identical blocks are stored once. Restored files
are independently created, never hardlinked together. Blob data starts with CDB3, codec and raw size;
codec 0 stores raw bytes, codec 1 uses zlib level 1. Blocks are bounded and hash-verified.
Manifest blob references must exactly match archive entries; duplicate names, portable case collisions,
unsafe paths, missing blocks and entries through file/link ancestors are rejected.

Project nesting is stored once under the nearest selected ancestor. Roots also distinguish projects,
external resources and original environment configuration. Selected standalone resources are represented
as one-file roots, so unrelated parent directories are not copied.

## Encrypted envelope

The outer pyz contains the portable runtime, capsule.json and encrypted.bin. The inner complete ZIP
is encrypted with AES-256-GCM in 4 MiB independently authenticated chunks. A random nonce prefix plus
counter uniquely identifies each chunk; authenticated metadata binds the layout and chunk index.
A seekable authenticated reader avoids creating a decrypted temporary archive.

Public metadata contains package ID, time, source computer, project count and logical byte count.
Full paths and history remain inside encryption. Recovery keys are separate JSON files containing
32 random bytes; never echo their contents. Windows uses CNG, non-Windows uses cryptography.

## Completeness and recovery

All ordinary project files are included. File content is hashed during export, package validation and
independent disk readback. Restore verifies data while extracting, then reads each staged root once.
All roots pass before commit. Same-filesystem renames are followed by tree checks rather than another
full content scan. Commit failure reverses the new renames and restores authorized replacement backups.

Export checkpoints retain the original data end and a hashed central-directory footer, allowing recovery
even if a process stopped before ZIP closure. Resume re-reads source content and reuses already written
blocks; source metadata/context changes reject reuse. A new package may reuse validated blocks from
a schema-3 base, but it includes every required block and remains independent of that base.

Restore checkpoints pin the canonical manifest and destination mapping. Completed files and roots
are checked before reuse. Original configuration remains byte-identical; adapted copies and remapped
link targets have separate receipts. Automatically generated automation copies are paused.

Read-only inspection and preview validate metadata and mappings only; payloadVerified is false until
data restoration or explicit verify succeeds. Space estimates aggregate all roots per destination volume.

## Product boundaries

No account sign-in, OS credential vault, NTFS ACL ownership/alternate streams or private sidebar databases.
Supported task history and coverage are retained without inventing missing content. File hashes do not
prove that machine-specific dependencies or desktop UI state have been migrated.
