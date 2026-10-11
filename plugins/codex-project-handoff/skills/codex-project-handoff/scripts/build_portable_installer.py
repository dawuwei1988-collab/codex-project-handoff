#!/usr/bin/env python3
"""Build one self-contained portable installer for this Codex plugin."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import zipfile


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_PLUGIN_ROOT = SCRIPT_DIR.parents[2]
RUNTIME = SCRIPT_DIR / "portable_installer_runtime.py"
IGNORED_PARTS = {"__pycache__", ".DS_Store"}
IGNORED_SUFFIXES = {".pyc", ".pyo"}


class BuildError(RuntimeError):
    """Raised when a portable installer cannot be built safely."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as reader:
        while block := reader.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def plugin_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(part in IGNORED_PARTS for part in relative.parts):
            continue
        if path.is_symlink():
            raise BuildError(f"plugin installer does not accept symlinks: {path}")
        if path.is_file() and path.suffix not in IGNORED_SUFFIXES:
            files.append(path)
    return sorted(files, key=lambda item: item.relative_to(root).as_posix())


def read_plugin_manifest(root: Path) -> dict[str, object]:
    path = root / ".codex-plugin" / "plugin.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BuildError(f"cannot read plugin manifest: {path}") from exc
    if not isinstance(manifest, dict):
        raise BuildError("plugin manifest must be an object")
    for key in ("name", "version", "description"):
        if not isinstance(manifest.get(key), str) or not manifest[key]:
            raise BuildError(f"plugin manifest field is missing: {key}")
    if root.name != manifest["name"]:
        raise BuildError("plugin folder and manifest name do not match")
    return manifest


def build_manifest(
    plugin_manifest: dict[str, object],
    root: Path,
    files: list[Path],
) -> dict[str, object]:
    records: list[dict[str, object]] = []
    total_bytes = 0
    for path in files:
        size = path.stat().st_size
        total_bytes += size
        records.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size": size,
                "sha256": sha256_file(path),
            }
        )
    return {
        "schemaVersion": 1,
        "packageType": "codex-plugin-portable-installer",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "plugin": {
            "name": plugin_manifest["name"],
            "version": plugin_manifest["version"],
            "description": plugin_manifest["description"],
            "fileCount": len(records),
            "totalBytes": total_bytes,
            "files": records,
        },
        "installation": {
            "pluginTarget": "<user-profile>/plugins/<plugin-name>",
            "marketplaceTarget": (
                "<user-profile>/.agents/plugins/marketplace.json"
            ),
            "existingPluginPolicy": "fail-unless-identical-or-explicit-replace",
            "uploadsData": False,
        },
    }


def verify_built_package(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise BuildError("built package has duplicate entries")
        manifest = json.loads(archive.read("installer-manifest.json"))
        plugin = manifest["plugin"]
        for record in plugin["files"]:
            archive_name = f"payload/{plugin['name']}/{record['path']}"
            digest = hashlib.sha256(archive.read(archive_name)).hexdigest()
            if digest != record["sha256"]:
                raise BuildError(
                    f"built package checksum mismatch: {record['path']}"
                )


def build_installer(
    plugin_root: Path,
    output: Path,
    *,
    dry_run: bool,
) -> dict[str, object]:
    root = plugin_root.resolve()
    destination = output.expanduser().resolve()
    if not root.is_dir():
        raise BuildError(f"plugin root does not exist: {root}")
    if not RUNTIME.is_file():
        raise BuildError(f"installer runtime is missing: {RUNTIME}")
    if is_relative_to(destination, root):
        raise BuildError("installer output must be outside the plugin root")
    if destination.exists():
        raise BuildError(f"installer output already exists: {destination}")
    plugin_manifest = read_plugin_manifest(root)
    files = plugin_files(root)
    manifest = build_manifest(plugin_manifest, root, files)
    print(f"Plugin: {plugin_manifest['name']} {plugin_manifest['version']}")
    print(f"Files: {manifest['plugin']['fileCount']}")
    print(f"Payload bytes: {manifest['plugin']['totalBytes']}")
    print(f"Output: {destination}")
    if dry_run:
        print("DRY RUN: no installer was written.")
        return manifest
    destination.parent.mkdir(parents=True, exist_ok=True)
    readme = (
        "Codex 一键迁移插件便携安装包\n\n"
        "在 B 电脑上把本 .pyz 文件交给 Codex，并说：\n"
        "请检查并安装这个 Codex 插件包。\n\n"
        "Codex 应先运行 inspect 和 verify，再运行 install。\n"
        "安装后完全退出并重新打开 Codex，然后新建任务。\n"
        "安装包不会上传任何内容。\n"
    ).encode("utf-8")
    with zipfile.ZipFile(
        destination,
        "x",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
    ) as archive:
        archive.write(RUNTIME, "__main__.py")
        archive.writestr(
            "installer-manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        )
        archive.writestr("README-安装.txt", readme)
        name = str(plugin_manifest["name"])
        for path in files:
            relative = path.relative_to(root).as_posix()
            archive.write(path, f"payload/{name}/{relative}")
        archive.comment = b"Codex portable plugin installer"
    verify_built_package(destination)
    print(f"Created and verified: {destination}")
    print(f"Installer bytes: {destination.stat().st_size}")
    print(f"Installer SHA-256: {sha256_file(destination)}")
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a self-contained portable Codex plugin installer."
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--plugin-root",
        type=Path,
        default=DEFAULT_PLUGIN_ROOT,
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        build_installer(
            args.plugin_root,
            args.output,
            dry_run=args.dry_run,
        )
        return 0
    except (BuildError, OSError, zipfile.BadZipFile) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
