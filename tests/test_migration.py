from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile


SCRIPT = (
    Path(__file__).parents[1]
    / "plugins"
    / "codex-project-handoff"
    / "skills"
    / "codex-project-handoff"
    / "scripts"
    / "codex_migrate.py"
)
SPEC = importlib.util.spec_from_file_location("codex_migrate", SCRIPT)
assert SPEC and SPEC.loader
codex_migrate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(codex_migrate)


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as reader:
        while block := reader.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def snapshot(root: Path) -> dict[str, tuple[str, str | None]]:
    result: dict[str, tuple[str, str | None]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[relative] = ("symlink", path.readlink().as_posix())
        elif path.is_dir():
            result[relative] = ("directory", None)
        else:
            result[relative] = ("file", file_digest(path))
    return result


def mtime_snapshot(root: Path) -> dict[str, int]:
    result = {".": root.lstat().st_mtime_ns}
    for path in sorted(root.rglob("*")):
        if not path.is_symlink():
            result[path.relative_to(root).as_posix()] = (
                path.lstat().st_mtime_ns
            )
    return result


class MigrationTests(unittest.TestCase):
    def create_project(self, root: Path) -> Path:
        project = root / "项目甲"
        project.mkdir(parents=True)
        (project / ".git").mkdir()
        (project / ".git" / "config").write_text(
            "[core]\nrepositoryformatversion = 0\n",
            encoding="utf-8",
        )
        (project / ".env").write_text(
            "PASSWORD=must-be-copied",
            encoding="utf-8",
        )
        (project / "node_modules" / "demo").mkdir(parents=True)
        (project / "node_modules" / "demo" / "index.js").write_text(
            "module.exports = 42;\n",
            encoding="utf-8",
        )
        (project / "构建结果").mkdir()
        (project / "构建结果" / "asset.bin").write_bytes(
            bytes(range(256)) * 4
        )
        (project / "空目录").mkdir()
        (project / "README.md").write_text(
            "# 完整迁移测试\n",
            encoding="utf-8",
        )
        return project

    def test_complete_export_verify_restore_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            source_parent = temp / "computer-a"
            project = self.create_project(source_parent)
            nested = project / "nested-project"
            nested.mkdir()
            (nested / "state.json").write_text(
                '{"progress":"ready"}',
                encoding="utf-8",
            )
            context = temp / "codex-context.json"
            context.write_text(
                json.dumps(
                    {
                        "threads": [
                            {
                                "title": "Continue migration work",
                                "summary": "Restore byte-identical files.",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            projects_file = temp / "projects.json"
            projects_file.write_text(
                json.dumps(
                    {
                        "projects": [
                            {
                                "path": str(project),
                                "codexProjectId": "project-a",
                            },
                            {
                                "path": str(nested),
                                "codexProjectId": "project-nested",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            capsule = temp / "migration.pyz"

            result = codex_migrate.main(
                [
                    "export",
                    "--projects-file",
                    str(projects_file),
                    "--codex-context",
                    str(context),
                    "--output",
                    str(capsule),
                ]
            )
            self.assertEqual(result, 0)
            self.assertTrue(capsule.is_file())
            self.assertEqual(
                subprocess.run(
                    [sys.executable, str(capsule), "inspect"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                ).returncode,
                0,
            )
            self.assertEqual(
                subprocess.run(
                    [sys.executable, str(capsule), "verify"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                ).returncode,
                0,
            )
            verification = codex_migrate.verify_capsule_path(
                capsule,
                verbose=False,
            )
            self.assertEqual(verification["files"], 6)
            self.assertEqual(
                verification["bytes"],
                sum(
                    path.stat().st_size
                    for path in project.rglob("*")
                    if path.is_file()
                ),
            )

            with zipfile.ZipFile(capsule) as archive:
                manifest = json.loads(archive.read("manifest.json"))
                self.assertEqual(manifest["schemaVersion"], 3)
                self.assertTrue(
                    manifest["completeness"]["zeroExclusionProjectCopy"]
                )
                self.assertEqual(len(manifest["projects"]), 2)
                self.assertEqual(len(manifest["roots"]), 1)
                logical_names = {entry["path"] for root in manifest["roots"] for entry in root["entries"]}
                self.assertIn(".env", logical_names)
                self.assertIn(".git/config", logical_names)
                self.assertIn("node_modules/demo/index.js", logical_names)
                self.assertTrue(manifest["blockFormat"]["smallFileGrouping"])
                self.assertGreater(len(manifest["blobs"]), 0)

            destination_parent = temp / "computer-b"
            destination_parent.mkdir()
            report = temp / "restore-report"
            result = codex_migrate.main(
                [
                    "restore",
                    str(capsule),
                    "--map-root",
                    f"{source_parent}={destination_parent}",
                    "--report-dir",
                    str(report),
                ]
            )
            self.assertEqual(result, 0)
            restored = destination_parent / project.name
            self.assertEqual(snapshot(project), snapshot(restored))
            self.assertEqual(
                mtime_snapshot(project),
                mtime_snapshot(restored),
            )
            restore_report = json.loads(
                (report / "RESTORE-REPORT.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(restore_report["warnings"], [])
            self.assertTrue(restore_report["verified"])
            self.assertTrue((report / "RESTORE-CONTEXT.md").is_file())
            self.assertEqual(
                json.loads(
                    (report / "context" / "codex-context.json").read_text(
                        encoding="utf-8"
                    )
                )["threads"][0]["title"],
                "Continue migration work",
            )

    def test_export_dry_run_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            project = self.create_project(temp / "a")
            capsule = temp / "dry-run.pyz"
            result = codex_migrate.main(
                [
                    "export",
                    "--project",
                    str(project),
                    "--output",
                    str(capsule),
                    "--dry-run",
                ]
            )
            self.assertEqual(result, 0)
            self.assertFalse(capsule.exists())

    def test_restore_refuses_existing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            source_parent = temp / "a"
            project = self.create_project(source_parent)
            capsule = temp / "migration.pyz"
            self.assertEqual(
                codex_migrate.main(
                    [
                        "export",
                        "--project",
                        str(project),
                        "--output",
                        str(capsule),
                    ]
                ),
                0,
            )
            destination_parent = temp / "b"
            destination = destination_parent / project.name
            destination.mkdir(parents=True)
            (destination / "unrelated.txt").write_text(
                "keep me",
                encoding="utf-8",
            )
            result = codex_migrate.main(
                [
                    "restore",
                    str(capsule),
                    "--map-root",
                    f"{source_parent}={destination_parent}",
                ]
            )
            self.assertEqual(result, 1)
            self.assertEqual(
                (destination / "unrelated.txt").read_text(encoding="utf-8"),
                "keep me",
            )

    def test_restore_requires_explicit_destination_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            project = self.create_project(temp / "a")
            capsule = temp / "migration.pyz"
            self.assertEqual(
                codex_migrate.main(
                    [
                        "export",
                        "--project",
                        str(project),
                        "--output",
                        str(capsule),
                    ]
                ),
                0,
            )
            report = temp / "restore-report"
            result = codex_migrate.main(
                [
                    "restore",
                    str(capsule),
                    "--report-dir",
                    str(report),
                ]
            )
            self.assertEqual(result, 1)
            self.assertFalse(report.exists())

    def test_duplicate_archive_entry_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            project = self.create_project(temp / "a")
            capsule = temp / "migration.pyz"
            self.assertEqual(
                codex_migrate.main(
                    [
                        "export",
                        "--project",
                        str(project),
                        "--output",
                        str(capsule),
                    ]
                ),
                0,
            )
            with zipfile.ZipFile(capsule, "a") as archive:
                archive.writestr("manifest.json", "{}")
            with self.assertRaises(codex_migrate.MigrationError):
                codex_migrate.verify_capsule_path(
                    capsule,
                    verbose=False,
                )


if __name__ == "__main__":
    unittest.main()
