from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from test_migration import codex_migrate as engine, snapshot

support, crypto = engine.support, engine.crypto


class OptimizedTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source" / "项目"
        self.source.mkdir(parents=True)
        (self.source / ".env").write_bytes(b"SECRET=preserved\n")
        (self.source / "empty").mkdir()
        (self.source / "readme.txt").write_bytes(b"working context\n" * 1000)
        self.package = self.root / "migration.pyz"

    def tearDown(self):
        self.temporary.cleanup()

    def call(self, *arguments):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return engine.main([str(item) for item in arguments])

    def export(self, *arguments):
        self.assertEqual(self.call("pack", "--project", self.source, "--output", self.package, *arguments), 0)

    def manifest(self):
        with zipfile.ZipFile(self.package) as archive:
            return json.loads(archive.read("manifest.json"))

    def deploy(self, *arguments):
        return self.call("部署", self.package, "--destination", self.root / "new", "--folder-name", "完整工作空间", *arguments)

    def test_short_aliases_and_default_auto_compression(self):
        parsed = engine.build_parser().parse_args(["迁移", "--project", str(self.source)])
        self.assertIs(parsed.handler, engine.export_capsule)
        self.assertEqual(parsed.compression, "auto")
        parsed = engine.build_parser().parse_args(["部署"])
        self.assertIs(parsed.handler, engine.deploy_capsule)

    def test_total_directory_contains_projects_resources_environment_and_reports(self):
        asset = self.root / "generated.png"
        asset.write_bytes(bytes(range(256)) * 30)
        home = self.root / "codex-home"
        home.mkdir()
        config = home / "config.toml"
        config.write_text('cwd = "' + str(self.source).replace("\\", "\\\\") + '"\n', encoding="utf-8")
        (home / "auth.json").write_text('DO NOT CAPTURE ACCOUNT AUTH', encoding="utf-8")
        (home / "generated_images").mkdir()
        (home / "generated_images" / "picture.png").write_bytes(b"generated-asset")
        self.export("--resource", asset, "--include-codex-home", home)
        self.assertEqual(self.deploy(), 0)
        total = self.root / "new" / "完整工作空间"
        self.assertEqual(snapshot(self.source), snapshot(total / "项目" / self.source.name))
        self.assertEqual((total / "关联资源" / asset.name / asset.name).read_bytes(), asset.read_bytes())
        self.assertEqual((total / "环境配置" / "原始" / "codex-config.toml" / "config.toml").read_bytes(), config.read_bytes())
        self.assertFalse(any(p.name == "auth.json" for p in total.rglob("*")))
        self.assertEqual((total / "关联资源" / "codex-generated_images" / "picture.png").read_bytes(), b"generated-asset")
        self.assertTrue((total / "迁移说明" / "RESTORE-CONTEXT.md").is_file())
        adaptations = json.loads((total / "迁移说明" / "ENVIRONMENT-ADAPTATIONS.json").read_text(encoding="utf-8"))
        self.assertEqual(len(adaptations["files"]), 1)
        self.assertIn(str(total / "项目" / self.source.name), Path(adaptations["files"][0]["adapted"]).read_text(encoding="utf-8").replace("\\\\", "\\"))

    @unittest.skipIf(sys.version_info < (3, 11), "TOML dependency parsing uses bundled Python 3.11+")
    def test_configured_mcp_server_directory_is_included(self):
        home = self.root / "codex"
        home.mkdir()
        service = self.root / "local-service"
        service.mkdir()
        script = service / "server.py"
        script.write_text("print('local workflow')")
        (service / "data.json").write_text('{"resource": true}')
        path = str(script).replace("\\", "\\\\")
        (home / "config.toml").write_text('[mcp_servers.local]\ncommand = "python"\nargs = ["' + path + '"]\n', encoding="utf-8")
        items = support.inventory_environment(home)
        self.assertTrue(any(item["path"] == str(service) for item in items))

    def test_embedded_tool_json_asset_reference_is_discovered(self):
        asset = self.root / "external.png"
        asset.write_bytes(b"external artifact")
        context = self.root / "context.json"
        context.write_text(json.dumps({"toolOutput": json.dumps({"output_file": str(asset)})}), encoding="utf-8")
        capture = io.StringIO()
        with contextlib.redirect_stdout(capture):
            result = engine.main(["inventory", "--project", str(self.source), "--codex-context", str(context)])
        self.assertEqual(result, 0)
        self.assertTrue(any(item["path"] == str(asset) for item in json.loads(capture.getvalue())["projects"]))

    def test_report_failure_can_resume_after_project_commit(self):
        self.export()
        with patch.object(support, "prepare_environment_adaptations", side_effect=OSError("simulated report interruption")):
            self.assertEqual(self.deploy(), 1)
        report = self.root / "new" / "完整工作空间" / "迁移说明"
        self.assertFalse(report.exists())
        self.assertEqual(self.deploy("--resume"), 0)
        self.assertTrue((report / "RESTORE-REPORT.json").is_file())

    def test_deploy_without_choice_and_preview_write_nothing(self):
        self.export()
        target = self.root / "new"
        self.assertEqual(self.call("deploy", self.package), 0)
        self.assertFalse(target.exists())
        with patch.object(engine, "verify_capsule_path", side_effect=AssertionError("preview must not read payload")):
            self.assertEqual(self.deploy("--dry-run"), 0)
        self.assertFalse(target.exists())

    def test_existing_total_folder_is_preserved(self):
        self.export()
        target = self.root / "new" / "完整工作空间"
        target.mkdir(parents=True)
        (target / "user.txt").write_text("preserve")
        self.assertEqual(self.deploy(), 1)
        self.assertEqual((target / "user.txt").read_text(), "preserve")

    def test_invalid_folder_names(self):
        for value in ("", "..", "a/b", "a\\b", "C:", "CON", "aux.txt", "name.", " name "):
            with self.subTest(value=value), self.assertRaises(ValueError):
                support.validate_folder_name(value)
        self.assertEqual(support.validate_folder_name("Codex工作空间"), "Codex工作空间")

    def test_local_discovery_ignores_installer_and_never_executes_payload(self):
        self.export()
        with zipfile.ZipFile(self.root / "installer.pyz", "w") as archive:
            archive.writestr("capsule.json", json.dumps({"format": "codex-plugin-portable-installer"}))
            archive.writestr("__main__.py", "raise RuntimeError('must not execute')")
        result = support.discover_packages([self.root], max_depth=0)
        self.assertEqual(result["status"], "unique")
        self.assertEqual(result["candidates"][0]["path"], str(self.package))
        shutil.copyfile(self.package, self.root / "copy.pyz")
        self.assertEqual(len(support.discover_packages([self.root], 0)["candidates"]), 1)

    def test_multiple_packages_require_selection(self):
        self.export()
        self.assertEqual(self.call("pack", "--project", self.source, "--output", self.root / "second.pyz"), 0)
        result = support.discover_packages([self.root], 0)
        self.assertEqual(result["status"], "choose")
        self.assertEqual(self.call("deploy", "--search-dir", self.root, "--search-depth", "0"), 1)

    def test_incomplete_search_does_not_assert_uniqueness(self):
        self.export()
        result = support.discover_packages([self.root], 2, 1)
        self.assertTrue(result["searchIncomplete"])
        self.assertNotEqual(result["status"], "unique")

    def test_block_deduplication_and_independent_restored_files(self):
        content = bytes(range(256)) * 4096
        (self.source / "a.bin").write_bytes(content)
        (self.source / "b.bin").write_bytes(content)
        self.export("--chunk-mib", "1")
        manifest = self.manifest()
        entries = {item["path"]: item for item in manifest["roots"][0]["entries"]}
        self.assertEqual(entries["a.bin"]["chunks"], entries["b.bin"]["chunks"])
        self.assertGreaterEqual(manifest["blockFormat"]["reusedLogicalBytes"], len(content))
        self.assertEqual(self.deploy(), 0)
        restored = self.root / "new" / "完整工作空间" / "项目" / self.source.name
        (restored / "a.bin").write_bytes(b"changed")
        self.assertEqual((restored / "b.bin").read_bytes(), content)

    def test_small_files_are_grouped(self):
        for index in range(30):
            (self.source / f"file-{index}.txt").write_text(f"value-{index}\n")
        self.export()
        self.assertLess(len(self.manifest()["blobs"]), 5)
        self.assertEqual(self.deploy(), 0)

    def test_export_checkpoint_recovers_even_without_zip_central_directory(self):
        (self.source / "large.bin").write_bytes(os.urandom(3 * 1024 * 1024))
        original = engine.checkpoint_archive
        failed = [False]
        def interrupt(archive, work, fingerprint, force=False):
            original(archive, work, fingerprint, True)
            if any(name.startswith("blobs/") for name in archive.namelist()) and not failed[0]:
                failed[0] = True
                raise KeyboardInterrupt()
        with patch.object(engine, "checkpoint_archive", side_effect=interrupt):
            self.assertEqual(self.call("pack", "--project", self.source, "--output", self.package, "--chunk-mib", "1"), 130)
        work = self.package.with_name(self.package.name + ".work")
        state = json.loads((work / "checkpoint.json").read_text())
        partial = work / "payload.partial"
        with partial.open("r+b") as stream:
            stream.truncate(state["dataEnd"])
        self.assertEqual(self.call("pack", "--project", self.source, "--output", self.package, "--chunk-mib", "1", "--resume"), 0)
        self.assertEqual(self.deploy(), 0)

    def test_changed_source_rejects_export_resume(self):
        original = engine.checkpoint_archive
        def interrupt(archive, work, fingerprint, force=False):
            original(archive, work, fingerprint, True)
            raise KeyboardInterrupt()
        with patch.object(engine, "checkpoint_archive", side_effect=interrupt):
            self.assertEqual(self.call("pack", "--project", self.source, "--output", self.package), 130)
        (self.source / "readme.txt").write_bytes(b"new version")
        self.assertEqual(self.call("pack", "--project", self.source, "--output", self.package, "--resume"), 1)
        self.assertFalse(self.package.exists())

    def test_restore_resume_and_completed_restore_are_verifiable(self):
        self.export()
        original = engine.verify_restored_root
        failed = [False]
        def interrupt(root, target):
            if "codex-restore-" in target.name and not failed[0]:
                failed[0] = True
                raise KeyboardInterrupt()
            return original(root, target)
        with patch.object(engine, "verify_restored_root", side_effect=interrupt):
            self.assertEqual(self.deploy(), 130)
        self.assertFalse((self.root / "new" / "完整工作空间" / "项目" / self.source.name).exists())
        self.assertEqual(self.deploy("--resume"), 0)
        self.assertEqual(self.deploy("--resume"), 0)

    def test_restore_resumes_incomplete_file_but_rejects_changed_completed_file(self):
        (self.source / "large.bin").write_bytes(os.urandom(3 * 1024 * 1024))
        self.export("--chunk-mib", "1")
        original = support.BlockReader.read
        reads = [0]
        def interrupt(reader, reference):
            reads[0] += 1
            if reads[0] == 3:
                raise KeyboardInterrupt()
            return original(reader, reference)
        with patch.object(support.BlockReader, "read", new=interrupt):
            self.assertEqual(self.deploy(), 130)
        self.assertEqual(self.deploy("--resume"), 0)
        target = self.root / "new" / "完整工作空间" / "项目" / self.source.name
        self.assertEqual(snapshot(self.source), snapshot(target))
        (target / "large.bin").write_bytes(b"user changed completed file")
        self.assertEqual(self.deploy("--resume"), 1)

    def test_commit_failure_rolls_back_all_roots_then_resumes(self):
        second = self.root / "second"
        second.mkdir()
        (second / "hello.txt").write_bytes(b"second project")
        self.export("--project", second)
        original = engine.os.replace
        def fail(source, target):
            if "codex-restore-" in str(source) and Path(target).name == "second":
                raise OSError("simulated commit failure")
            return original(source, target)
        with patch.object(engine.os, "replace", side_effect=fail):
            self.assertEqual(self.deploy(), 1)
        total = self.root / "new" / "完整工作空间" / "项目"
        self.assertFalse((total / self.source.name).exists())
        self.assertFalse((total / second.name).exists())
        self.assertEqual(self.deploy("--resume"), 0)
        self.assertEqual(snapshot(second), snapshot(total / second.name))

    def test_incremental_package_is_independent_of_base(self):
        (self.source / "unchanged.bin").write_bytes(os.urandom(1024 * 1024))
        self.export()
        old = self.root / "base.pyz"
        self.package.rename(old)
        (self.source / "new.txt").write_text("new asset")
        second = self.root / "updated.pyz"
        self.assertEqual(self.call("pack", "--project", self.source, "--base-package", old, "--output", second), 0)
        old.unlink()
        self.package = second
        self.assertGreater(self.manifest()["blockFormat"]["reusedLogicalBytes"], 0)
        self.assertEqual(self.deploy(), 0)

    def test_damaged_block_cannot_commit_a_project(self):
        self.export()
        replacement = self.root / "changed.pyz"
        with zipfile.ZipFile(self.package) as source, zipfile.ZipFile(replacement, "w") as destination:
            for info in source.infolist():
                data = source.read(info)
                if info.filename.startswith("blobs/"):
                    data = data[:-1] + bytes([data[-1] ^ 1])
                destination.writestr(info, data)
        self.package = replacement
        self.assertEqual(self.deploy(), 1)
        self.assertFalse((self.root / "new" / "完整工作空间" / "项目" / self.source.name).exists())

    def test_history_capture_checks_expected_task_ids_and_full_items(self):
        import capture_context
        root = str(self.source)
        class FakeRpc:
            def pages(self, method, params):
                if method == "thread/list":
                    return [] if params["archived"] else [{"id": "present", "cwd": root}]
                return [{"id": "turn-one", "items": [{"type": "userMessage", "text": "original text"}]}]
        context = capture_context.capture(FakeRpc(), [root], ["present", "missing"])
        self.assertFalse(context["coverage"]["complete"])
        self.assertEqual(context["coverage"]["detailedTaskCount"], 1)
        self.assertEqual(context["detailedThreads"][0]["turns"][0]["items"][0]["text"], "original text")
        self.assertIn("missing", context["warnings"][0])

    def test_portable_installer_is_self_contained_and_preserves_unrelated_entries(self):
        import build_portable_installer
        installer = self.root / "plugin-installer.pyz"
        plugin = Path(engine.__file__).parents[3]
        with contextlib.redirect_stdout(io.StringIO()):
            build_portable_installer.build_installer(plugin, installer, dry_run=False)
        home = self.root / "new-user"
        market = home / ".agents" / "plugins" / "marketplace.json"
        market.parent.mkdir(parents=True)
        other = {"name": "unrelated", "source": {"source": "local", "path": "./plugins/unrelated"}}
        market.write_text(json.dumps({"name": "personal", "plugins": [other]}), encoding="utf-8")
        result = subprocess.run([sys.executable, "-X", "utf8", str(installer), "install", "--home", str(home), "--skip-codex-register"], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(other, json.loads(market.read_text(encoding="utf-8"))["plugins"])
        installed = home / "plugins" / "codex-project-handoff"
        self.assertEqual(json.loads((installed / "plugin.json").read_text(encoding="utf-8"))["version"], engine.VERSION)
        self.assertEqual((installed / "skills/codex-project-handoff/scripts/codex_migrate.py").read_bytes(), Path(engine.__file__).read_bytes())

    def test_native_aes_gcm_matches_reference_and_authenticates_data(self):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        key, nonce, data, aad = bytes(range(32)), bytes(range(12)), b"data\0with bytes" * 100, b"associated metadata"
        cipher = crypto.Gcm(key)
        try:
            encrypted = cipher.encrypt(nonce, data, aad)
            self.assertEqual(encrypted, AESGCM(key).encrypt(nonce, data, aad))
            self.assertEqual(cipher.decrypt(nonce, encrypted, aad), data)
            with self.assertRaises(Exception):
                cipher.decrypt(nonce, encrypted[:-1] + bytes([encrypted[-1] ^ 1]), aad)
        finally:
            cipher.close()

    def test_encrypted_self_running_package_round_trip_and_wrong_key(self):
        self.export("--encrypt")
        key = self.package.with_suffix(".recovery-key.json")
        self.assertEqual(self.call("inspect", self.package), 0)
        self.assertEqual(self.call("verify", self.package), 1)
        self.assertEqual(self.call("verify", self.package, "--key-file", key), 0)
        wrong = self.root / "wrong-key.json"
        crypto.create_key(wrong)
        self.assertEqual(self.call("verify", self.package, "--key-file", wrong), 1)
        self.assertEqual(self.deploy("--key-file", key), 0)
        completed = subprocess.run([sys.executable, "-X", "utf8", str(self.package), "verify", "--key-file", str(key)], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_schema_two_package_remains_readable(self):
        data = b"legacy file\n"
        source = self.source
        info = (source / "readme.txt").stat()
        entry = engine.entry_metadata("old.txt", "file", info)
        entry.update(size=len(data), sha256=engine.hashlib.sha256(data).hexdigest(), archiveEntry="payload/root-old/old.txt")
        root = {"id": "root-old", "name": "legacy", "originalPath": str(source), "payloadPrefix": "payload/root-old", "entries": [entry], "rootMetadata": engine.entry_metadata(".", "directory", source.stat()), "totalBytes": len(data)}
        manifest = {"schemaVersion": 2, "format": engine.FORMAT_NAME, "createdAt": "2026-08-01", "sourceComputer": {"hostname": "old"}, "projects": [{"name": "legacy", "originalPath": str(source), "storageRootId": "root-old", "relativePathWithinRoot": ""}], "roots": [root], "context": None}
        with zipfile.ZipFile(self.package, "w") as archive:
            archive.writestr("__main__.py", "# old restore tool")
            archive.writestr("capsule.json", json.dumps({"format": engine.FORMAT_NAME, "schemaVersion": 2}))
            archive.writestr("manifest.json", json.dumps(manifest))
            archive.writestr("START-HERE.md", "legacy")
            archive.writestr(entry["archiveEntry"], data)
        self.assertEqual(self.call("verify", self.package), 0)
        self.assertEqual(self.deploy(), 0)
        self.assertEqual((self.root / "new" / "完整工作空间" / "项目" / "legacy" / "old.txt").read_bytes(), data)

    def test_deployment_entry_uses_new_folder_after_move(self):
        self.export()
        script = self.package.with_name(self.package.stem + "-开始部署.ps1")
        text = script.read_text(encoding="utf-8-sig")
        self.assertIn("$PSScriptRoot", text)
        self.assertNotIn(str(self.package.parent), text)
        uri = support.deployment_uri(self.package)
        self.assertTrue(uri.startswith("codex://new?"))
        self.assertIn("&prompt=", uri)

    def test_cannot_restore_through_link_ancestor_or_case_collision(self):
        self.export()
        manifest = self.manifest()
        root = manifest["roots"][0]
        root["entries"].append({"path": "READme.TXT", "type": "directory"})
        with self.assertRaises(engine.MigrationError):
            engine.validate_manifest_paths(manifest)
        root["entries"].pop()
        root["entries"].extend([{"path": "link", "type": "symlink", "target": str(self.root)}, {"path": "link/escape", "type": "directory"}])
        with self.assertRaises(engine.MigrationError):
            engine.validate_manifest_paths(manifest)

    def test_disk_space_is_aggregated_per_volume(self):
        second = self.root / "second"
        second.mkdir()
        (second / "data").write_bytes(b"second data" * 1000)
        self.export("--project", second)
        sizes = [r["totalBytes"] for r in self.manifest()["roots"]]
        free = 16 * 1024 * 1024 + max(sizes)
        usage = shutil._ntuple_diskusage(total=free * 2, used=free, free=free)
        with patch.object(engine.shutil, "disk_usage", return_value=usage):
            self.assertEqual(self.deploy(), 1)
        self.assertFalse((self.root / "new").exists())


if __name__ == "__main__":
    unittest.main()
