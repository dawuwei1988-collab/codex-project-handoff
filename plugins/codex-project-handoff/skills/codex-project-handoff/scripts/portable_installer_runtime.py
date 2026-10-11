#!/usr/bin/env python3
"""Self-contained installer runtime for a portable Codex plugin bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import uuid
import zipfile


MANIFEST_NAME = "installer-manifest.json"
SCHEMA_VERSION = 1


class InstallerError(RuntimeError):
    """Raised when a portable plugin installation cannot continue safely."""


def sha256_reader(reader: object) -> str:
    digest = hashlib.sha256()
    while True:
        block = reader.read(1024 * 1024)
        if not block:
            break
        digest.update(block)
    return digest.hexdigest()


def package_path() -> Path:
    return Path(sys.argv[0]).resolve()


def safe_relative_path(value: str) -> PurePosixPath:
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise InstallerError(f"unsafe payload path: {value}")
    return relative


def load_manifest(
    archive: zipfile.ZipFile,
) -> dict[str, object]:
    names = archive.namelist()
    if len(names) != len(set(names)):
        raise InstallerError("duplicate archive entry detected")
    try:
        manifest = json.loads(archive.read(MANIFEST_NAME))
    except KeyError as exc:
        raise InstallerError("installer manifest is missing") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InstallerError("installer manifest is invalid") from exc
    if not isinstance(manifest, dict):
        raise InstallerError("installer manifest must be an object")
    if manifest.get("schemaVersion") != SCHEMA_VERSION:
        raise InstallerError("unsupported installer schema version")
    if manifest.get("packageType") != "codex-plugin-portable-installer":
        raise InstallerError("unexpected package type")
    plugin = manifest.get("plugin")
    if not isinstance(plugin, dict):
        raise InstallerError("plugin manifest section is missing")
    name = plugin.get("name")
    version = plugin.get("version")
    files = plugin.get("files")
    if not isinstance(name, str) or not name:
        raise InstallerError("plugin name is missing")
    if PurePosixPath(name).name != name or name in {".", ".."}:
        raise InstallerError("plugin name is unsafe")
    if not isinstance(version, str) or not version:
        raise InstallerError("plugin version is missing")
    if not isinstance(files, list) or not files:
        raise InstallerError("plugin payload file list is missing")
    seen_paths: set[str] = set()
    expected_prefix = f"payload/{name}/"
    for record in files:
        if not isinstance(record, dict):
            raise InstallerError("invalid payload file record")
        relative = safe_relative_path(str(record.get("path", "")))
        normalized = relative.as_posix()
        if normalized in seen_paths:
            raise InstallerError(f"duplicate payload path: {normalized}")
        seen_paths.add(normalized)
        archive_name = expected_prefix + normalized
        if archive_name not in names:
            raise InstallerError(f"payload file is missing: {normalized}")
        if (
            not isinstance(record.get("size"), int)
            or isinstance(record.get("size"), bool)
            or record["size"] < 0
        ):
            raise InstallerError(f"invalid payload size: {normalized}")
        digest = record.get("sha256")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise InstallerError(f"invalid payload hash: {normalized}")
    allowed_names = {
        "__main__.py",
        MANIFEST_NAME,
        "README-安装.txt",
        *(expected_prefix + path for path in seen_paths),
    }
    unexpected = set(names) - allowed_names
    if unexpected:
        raise InstallerError(
            f"unexpected archive entry: {sorted(unexpected)[0]}"
        )
    return manifest


def verify_package(
    path: Path,
    *,
    verbose: bool = True,
) -> dict[str, object]:
    with zipfile.ZipFile(path) as archive:
        manifest = load_manifest(archive)
        plugin = manifest["plugin"]
        assert isinstance(plugin, dict)
        name = str(plugin["name"])
        checked_bytes = 0
        for record in plugin["files"]:
            assert isinstance(record, dict)
            relative = str(record["path"])
            with archive.open(f"payload/{name}/{relative}") as reader:
                digest = sha256_reader(reader)
            if digest != record["sha256"]:
                raise InstallerError(
                    f"payload checksum mismatch: {relative}"
                )
            info = archive.getinfo(f"payload/{name}/{relative}")
            if info.file_size != record["size"]:
                raise InstallerError(f"payload size mismatch: {relative}")
            checked_bytes += info.file_size
        if checked_bytes != plugin.get("totalBytes"):
            raise InstallerError("payload byte total mismatch")
        if len(plugin["files"]) != plugin.get("fileCount"):
            raise InstallerError("payload file count mismatch")
    result = {
        "plugin": plugin["name"],
        "version": plugin["version"],
        "files": plugin["fileCount"],
        "bytes": plugin["totalBytes"],
    }
    if verbose:
        print(f"VALID: {path}")
        print(
            f"Plugin: {result['plugin']} {result['version']}; "
            f"files: {result['files']}; bytes: {result['bytes']}"
        )
    return result


def transient_plugin_path(path: Path) -> bool:
    return "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}


def verify_installed_tree(
    manifest: dict[str, object],
    root: Path,
) -> bool:
    plugin = manifest["plugin"]
    assert isinstance(plugin, dict)
    expected = {
        str(record["path"]): record
        for record in plugin["files"]
        if isinstance(record, dict)
    }
    if not root.is_dir():
        return False
    actual: set[str] = set()
    for path in root.rglob("*"):
        if path.is_file() and not transient_plugin_path(path):
            actual.add(path.relative_to(root).as_posix())
    if actual != set(expected):
        return False
    for relative, record in expected.items():
        path = root.joinpath(*PurePosixPath(relative).parts)
        if path.stat().st_size != record["size"]:
            return False
        with path.open("rb") as reader:
            if sha256_reader(reader) != record["sha256"]:
                return False
    return True


def extract_payload(
    archive: zipfile.ZipFile,
    manifest: dict[str, object],
    staging: Path,
) -> None:
    plugin = manifest["plugin"]
    assert isinstance(plugin, dict)
    name = str(plugin["name"])
    staging.mkdir(parents=False, exist_ok=False)
    for record in plugin["files"]:
        assert isinstance(record, dict)
        relative = safe_relative_path(str(record["path"]))
        destination = staging.joinpath(*relative.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(
            f"payload/{name}/{relative.as_posix()}"
        ) as reader, destination.open("xb") as writer:
            shutil.copyfileobj(reader, writer, 1024 * 1024)
    if not verify_installed_tree(manifest, staging):
        raise InstallerError("staged plugin verification failed")


def expected_marketplace_entry(name: str) -> dict[str, object]:
    return {
        "name": name,
        "source": {
            "source": "local",
            "path": f"./plugins/{name}",
        },
        "policy": {
            "installation": "INSTALLED_BY_DEFAULT",
            "authentication": "ON_INSTALL",
        },
        "category": "Productivity",
    }


def prepare_marketplace(
    path: Path,
    plugin_name: str,
    *,
    replace_entry: bool,
) -> tuple[dict[str, object], str, bool]:
    if path.exists():
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InstallerError(
                f"cannot read personal marketplace: {path}"
            ) from exc
        if not isinstance(document, dict):
            raise InstallerError("personal marketplace must be an object")
    else:
        document = {
            "name": "personal",
            "interface": {"displayName": "Personal"},
            "plugins": [],
        }
    marketplace_name = document.get("name")
    if not isinstance(marketplace_name, str) or not marketplace_name:
        raise InstallerError("personal marketplace name is missing")
    plugins = document.get("plugins")
    if not isinstance(plugins, list):
        raise InstallerError("personal marketplace plugins must be an array")
    expected = expected_marketplace_entry(plugin_name)
    changed = False
    matches = [
        index
        for index, entry in enumerate(plugins)
        if isinstance(entry, dict) and entry.get("name") == plugin_name
    ]
    if len(matches) > 1:
        raise InstallerError("duplicate plugin marketplace entries detected")
    if matches:
        index = matches[0]
        existing = plugins[index]
        assert isinstance(existing, dict)
        if existing.get("source") != expected["source"] and not replace_entry:
            raise InstallerError(
                "existing marketplace entry points elsewhere; "
                "explicit replacement authorization is required"
            )
        if existing != expected:
            plugins[index] = expected
            changed = True
    else:
        plugins.append(expected)
        changed = True
    return document, marketplace_name, changed


def timestamp_token() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def write_marketplace_atomic(
    path: Path,
    document: dict[str, object],
) -> Path | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup: Path | None = None
    if path.exists():
        backup = path.with_name(
            f"{path.name}.backup-{timestamp_token()}"
        )
        if backup.exists():
            raise InstallerError(f"marketplace backup exists: {backup}")
        shutil.copy2(path, backup)
    temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex[:8]}")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    try:
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return backup


def locate_codex_executable(explicit: str | None) -> str | None:
    if explicit:
        candidate = Path(explicit).expanduser()
        if not candidate.is_file():
            raise InstallerError(f"Codex executable not found: {candidate}")
        return str(candidate)
    desktop_internal = (
        Path.home()
        / ".codex"
        / "plugins"
        / ".plugin-appserver"
        / ("codex.exe" if os.name == "nt" else "codex")
    )
    if desktop_internal.is_file():
        return str(desktop_internal)
    return shutil.which("codex") or shutil.which("codex.exe")


def register_with_codex(
    executable: str | None,
    plugin_name: str,
    marketplace_name: str,
) -> tuple[bool, str]:
    if executable is None:
        return False, "Codex CLI was not found; restart Codex to rescan the personal marketplace."
    command = [
        executable,
        "plugin",
        "add",
        f"{plugin_name}@{marketplace_name}",
    ]
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"Plugin files are verified, but Codex registration needs retry: {exc}"
    output = completed.stdout.strip()
    if completed.returncode != 0:
        return False, output or f"Codex CLI exited with {completed.returncode}."
    return True, output or "Codex plugin registration completed."


def install_package(args: argparse.Namespace) -> int:
    source = package_path()
    verify_package(source, verbose=True)
    home = Path(args.home).expanduser().resolve() if args.home else Path.home().resolve()
    if home == Path(home.anchor) or not home.exists():
        raise InstallerError(f"unsafe or missing target home: {home}")
    with zipfile.ZipFile(source) as archive:
        manifest = load_manifest(archive)
        plugin = manifest["plugin"]
        assert isinstance(plugin, dict)
        plugin_name = str(plugin["name"])
        plugins_dir = home / "plugins"
        target = plugins_dir / plugin_name
        marketplace_path = home / ".agents" / "plugins" / "marketplace.json"
        document, marketplace_name, marketplace_changed = prepare_marketplace(
            marketplace_path,
            plugin_name,
            replace_entry=args.replace_marketplace_entry,
        )
        existing_identical = verify_installed_tree(manifest, target)
        if target.exists() and not existing_identical and not args.replace_existing:
            raise InstallerError(
                f"plugin already exists and differs: {target}; "
                "explicit replacement authorization is required"
            )
        print("Install plan:")
        print(f"  Plugin: {plugin_name} {plugin['version']}")
        print(f"  Target: {target}")
        print(f"  Marketplace: {marketplace_path}")
        print(f"  Existing target: {target.exists()}")
        print(f"  Existing target identical: {existing_identical}")
        if args.dry_run:
            print("DRY RUN: no files were written.")
            return 0

        plugins_dir.mkdir(parents=True, exist_ok=True)
        staging = plugins_dir / (
            f".{plugin_name}.install-{uuid.uuid4().hex[:8]}"
        )
        plugin_backup: Path | None = None
        marketplace_backup: Path | None = None
        installed_new_tree = False
        try:
            if not existing_identical:
                extract_payload(archive, manifest, staging)
                if target.exists():
                    plugin_backup = plugins_dir / (
                        f"{plugin_name}.backup-{timestamp_token()}"
                    )
                    if plugin_backup.exists():
                        raise InstallerError(
                            f"plugin backup already exists: {plugin_backup}"
                        )
                    os.replace(target, plugin_backup)
                os.replace(staging, target)
                installed_new_tree = True
            if not verify_installed_tree(manifest, target):
                raise InstallerError("installed plugin verification failed")
            if marketplace_changed or not marketplace_path.exists():
                marketplace_backup = write_marketplace_atomic(
                    marketplace_path,
                    document,
                )
        except Exception:
            if staging.exists():
                shutil.rmtree(staging)
            if installed_new_tree and target.exists():
                shutil.rmtree(target)
            if plugin_backup is not None and plugin_backup.exists():
                os.replace(plugin_backup, target)
            raise

    if not verify_installed_tree(manifest, target):
        raise InstallerError("installed plugin verification failed")

    registered = False
    registration_message = "Codex registration was skipped for this installation."
    if not args.skip_codex_register:
        executable = locate_codex_executable(args.codex_executable)
        registered, registration_message = register_with_codex(
            executable,
            plugin_name,
            marketplace_name,
        )

    receipt_dir = home / ".agents" / "plugins" / "install-reports"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt = receipt_dir / f"{plugin_name}.json"
    receipt.write_text(
        json.dumps(
            {
                "plugin": plugin_name,
                "version": plugin["version"],
                "target": str(target),
                "marketplace": str(marketplace_path),
                "pluginBackup": str(plugin_backup) if plugin_backup else None,
                "marketplaceBackup": (
                    str(marketplace_backup) if marketplace_backup else None
                ),
                "codexRegistered": registered,
                "registrationMessage": registration_message,
                "verified": True,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print("INSTALLATION COMPLETE")
    print(f"Installed plugin: {target}")
    print(f"Installation receipt: {receipt}")
    print(f"Codex registration: {registration_message}")
    print("Completely restart Codex and start a new task before using the plugin.")
    return 0


def inspect_package(path: Path) -> int:
    result = verify_package(path, verbose=False)
    print("Codex portable plugin installer")
    print(f"Package: {path}")
    print(f"Plugin: {result['plugin']}")
    print(f"Version: {result['version']}")
    print(f"Files: {result['files']}")
    print(f"Payload bytes: {result['bytes']}")
    print("Integrity: VALID")
    print("Default install target: <user-profile>\\plugins\\<plugin-name>")
    print("No files were written.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect, verify, and install a portable Codex plugin bundle."
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("inspect", help="Show package contents without writing.")
    subparsers.add_parser("verify", help="Verify every embedded plugin file.")
    install = subparsers.add_parser(
        "install",
        help="Install into the target user's personal Codex marketplace.",
    )
    install.add_argument("--home", help="Target user profile; defaults to current user.")
    install.add_argument("--dry-run", action="store_true")
    install.add_argument("--replace-existing", action="store_true")
    install.add_argument(
        "--replace-marketplace-entry",
        action="store_true",
    )
    install.add_argument("--codex-executable")
    install.add_argument("--skip-codex-register", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "inspect"
    try:
        if command == "inspect":
            return inspect_package(package_path())
        if command == "verify":
            verify_package(package_path(), verbose=True)
            return 0
        if command == "install":
            return install_package(args)
    except (InstallerError, OSError, zipfile.BadZipFile) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    parser.error(f"unsupported command: {command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
