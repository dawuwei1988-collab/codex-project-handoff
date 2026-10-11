# Contributing

Contributions are welcome.

1. Fork the repository and create a focused branch.
2. Keep the exporter dependency-free and compatible with Python 3.10+.
3. Add or update tests for behavior changes.
4. Run `python tests/check_publication.py` and `python -m unittest discover -s tests -v`.
5. Do not commit real credentials, private project paths, or generated handoff
   packages.

Open an issue before making a large behavior or output-schema change.

Use synthetic data for reproductions. Do not open or execute unknown migration capsules.
Keep source and destination safety, legacy compatibility, coverage reporting, and the user's
folder choice intact. Windows-first support must remain explicit; CI coverage is not a promise
of live desktop parity across platforms. Release procedure: [MAINTENANCE](docs/MAINTENANCE.md).

中文：用虚构最小项目复现，问题和 PR 中不附真实迁移包、恢复密钥、公司路径或聊天记录。
涉及格式、恢复覆盖策略或用户数据的改动须说明风险并添加回归测试；版本发布由维护者确认。
