#!/usr/bin/env python3
"""Build, verify, inspect, and restore complete Codex project migration capsules."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import platform
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
from typing import BinaryIO, Iterable, Sequence
import uuid
import zipfile
import time
import re
import base64
from concurrent.futures import ThreadPoolExecutor

# Works both as a source script and from the self-contained zip application.
if str(Path(__file__).parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent))
import migration_support as support
import migration_crypto as crypto


VERSION = "1.1.0-beta.4"
SCHEMA_VERSION = 3
FORMAT_NAME = "codex-project-migration"
BUFFER_SIZE = 1024 * 1024
CAPSULE_MARKER = "capsule.json"
MANIFEST_ENTRY = "manifest.json"
START_ENTRY = "START-HERE.md"


class MigrationError(RuntimeError):
    """A user-actionable migration failure."""


def now_local() -> str:
    return dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


def stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value.rstrip() + "\n", encoding="utf-8", newline="\n")


def stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(
        os.path.normcase(value).encode("utf-8")
    ).hexdigest()[:16]
    return f"{prefix}-{digest}"


def normalized_path_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def dedupe_paths(paths: Iterable[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for value in paths:
        resolved = value.expanduser().resolve()
        key = normalized_path_key(resolved)
        if key not in seen:
            seen.add(key)
            result.append(resolved)
    return result


def load_projects_file(path: Path) -> list[dict[str, object]]:
    raw = path.read_text(encoding="utf-8-sig")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return [
            {"path": line.strip()}
            for line in raw.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
    if isinstance(value, dict):
        value = value.get("projects")
    if not isinstance(value, list):
        raise MigrationError(
            "projects file must be a JSON list, an object with a projects "
            "list, or one UTF-8 path per line"
        )
    projects: list[dict[str, object]] = []
    for item in value:
        if isinstance(item, str):
            projects.append({"path": item})
        elif isinstance(item, dict) and item.get("path"):
            projects.append(dict(item))
        else:
            raise MigrationError("every project entry must contain a path")
    return projects


def parse_project_specs(args: argparse.Namespace) -> list[dict[str, object]]:
    specs: list[dict[str, object]] = [
        {"path": value} for value in args.project
    ]
    if args.projects_file:
        specs.extend(load_projects_file(args.projects_file))
    for value in getattr(args, "resource", []):
        specs.append({"path": value, "category": "resources"})
    if getattr(args, "include_codex_home", None):
        specs.extend(support.inventory_environment(args.include_codex_home, getattr(args, "agents_home", None)))
    if not specs:
        raise MigrationError("no projects were selected")

    by_path: dict[str, dict[str, object]] = {}
    ordered: list[str] = []
    for spec in specs:
        path = Path(str(spec["path"])).expanduser().resolve()
        if not path.is_dir() and not path.is_file():
            raise MigrationError(f"selected source does not exist: {path}")
        if spec.get("category", "projects") not in {"projects", "resources", "environment"}:
            raise MigrationError("source category must be projects, resources, or environment")
        if spec.get("category", "projects") == "projects" and not path.is_dir():
            raise MigrationError(f"project must be a directory: {path}")
        key = normalized_path_key(path)
        if key not in by_path:
            ordered.append(key)
            by_path[key] = dict(spec)
            by_path[key]["path"] = str(path)
        else:
            by_path[key].update(
                {k: v for k, v in spec.items() if k != "path"}
            )
    return [by_path[key] for key in ordered]


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def build_project_and_root_records(
    specs: list[dict[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    paths = [Path(str(spec["path"])) for spec in specs]
    roots: list[Path] = []
    for path in sorted(paths, key=lambda item: len(item.parts)):
        if not any(is_relative_to(path, root) for root in roots):
            roots.append(path)

    root_records: list[dict[str, object]] = []
    root_ids: dict[str, str] = {}
    for root in roots:
        root_id = stable_id("root", str(root))
        spec = next(item for item in specs if Path(str(item["path"])) == root)
        root_ids[normalized_path_key(root)] = root_id
        root_records.append(
            {
                "id": root_id,
                "originalPath": str(root),
                "name": str(spec.get("name") or root.name),
                "category": spec.get("category", "projects"),
                "sourceType": "directory" if root.is_dir() else "file",
                "discoveryWarnings": spec.get("discoveryWarnings", []),
                "payloadPrefix": f"payload/{root_id}",
                "entries": [],
                "fileCount": 0,
                "directoryCount": 0,
                "symlinkCount": 0,
                "totalBytes": 0,
            }
        )

    projects: list[dict[str, object]] = []
    for spec in specs:
        path = Path(str(spec["path"]))
        storage_root = next(root for root in roots if is_relative_to(path, root))
        relative = path.relative_to(storage_root)
        project = {
            "id": stable_id("project", str(path)),
            "name": str(spec.get("name") or path.name),
            "originalPath": str(path),
            "storageRootId": root_ids[normalized_path_key(storage_root)],
            "relativePathWithinRoot": (
                "" if not relative.parts else relative.as_posix()
            ),
        }
        for key in ("codexProjectId", "projectKind", "summary"):
            if key in spec:
                project[key] = spec[key]
        project["category"] = spec.get("category", "projects")
        if project["category"] == "projects":
            projects.append(project)
    return projects, root_records


def safe_relative(value: str) -> PurePosixPath:
    if "\\" in value or "\x00" in value or ":" in value:
        raise MigrationError(f"unsafe archive path: {value}")
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise MigrationError(f"unsafe archive path: {value}")
    if not relative.parts or relative == PurePosixPath("."):
        raise MigrationError(f"empty archive path: {value}")
    for part in relative.parts:
        try:
            support.validate_folder_name(part)
        except ValueError as exc:
            raise MigrationError(f"unsupported portable filename: {value}: {exc}") from exc
    return relative


def windows_attributes(info: os.stat_result) -> int | None:
    value = getattr(info, "st_file_attributes", None)
    return int(value) if value is not None else None


def is_junction(path: Path) -> bool:
    checker = getattr(os.path, "isjunction", None)
    if checker:
        return bool(checker(path))
    try:
        return os.name == "nt" and getattr(path.lstat(), "st_reparse_tag", None) == 0xA0000003
    except FileNotFoundError:
        return False


def entry_metadata(
    relative: str,
    kind: str,
    info: os.stat_result,
) -> dict[str, object]:
    result: dict[str, object] = {
        "path": relative,
        "type": kind,
        "mtimeNs": int(info.st_mtime_ns),
        "mode": stat.S_IMODE(info.st_mode),
    }
    attributes = windows_attributes(info)
    if attributes is not None:
        result["windowsAttributes"] = attributes
    return result


def walk_root(root: Path) -> Iterable[tuple[Path, str, os.stat_result]]:
    """Yield every descendant without following directory symlinks."""
    if root.is_file() and not root.is_symlink():
        yield root, root.name, root.lstat()
        return
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            children = sorted(
                directory.iterdir(),
                key=lambda item: os.path.normcase(item.name),
                reverse=True,
            )
        except OSError as exc:
            raise MigrationError(f"cannot list {directory}: {exc}") from exc
        directories: list[Path] = []
        for child in reversed(children):
            try:
                info = child.lstat()
            except OSError as exc:
                raise MigrationError(f"cannot stat {child}: {exc}") from exc
            relative = child.relative_to(root).as_posix()
            if stat.S_ISLNK(info.st_mode) or is_junction(child):
                yield child, relative, info
            elif stat.S_ISDIR(info.st_mode):
                yield child, relative, info
                directories.append(child)
            elif stat.S_ISREG(info.st_mode):
                yield child, relative, info
            else:
                raise MigrationError(
                    f"unsupported special filesystem entry: {child}"
                )
        pending.extend(reversed(directories))


def tree_fingerprint(root: Path) -> dict[str, tuple[object, ...]]:
    """Capture enough metadata to detect tree changes during export."""
    try:
        root_info = root.lstat()
    except OSError as exc:
        raise MigrationError(f"cannot stat {root}: {exc}") from exc
    result: dict[str, tuple[object, ...]] = {
        ".": ("directory", int(root_info.st_mtime_ns), stat.S_IMODE(root_info.st_mode), windows_attributes(root_info), root_info.st_ino)
    }
    for path, relative, info in walk_root(root):
        if is_junction(path):
            kind = "junction"
            detail: tuple[object, ...] = (
                kind,
                int(info.st_mtime_ns),
                os.readlink(path),
            )
        elif stat.S_ISLNK(info.st_mode):
            kind = "symlink"
            detail = (
                kind,
                int(info.st_mtime_ns),
                os.readlink(path),
            )
        elif stat.S_ISDIR(info.st_mode):
            detail = ("directory", int(info.st_mtime_ns))
        else:
            detail = (
                "file",
                int(info.st_size),
                int(info.st_mtime_ns),
            )
        result[relative] = detail
        result[relative] += (stat.S_IMODE(info.st_mode), windows_attributes(info), info.st_ino)
    return result


def copy_file_to_zip(
    archive: zipfile.ZipFile,
    source: Path,
    archive_name: str,
    before: os.stat_result,
    compression: int,
) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    info = zipfile.ZipInfo(archive_name)
    info.compress_type = compression
    info.external_attr = (stat.S_IMODE(before.st_mode) & 0xFFFF) << 16
    try:
        with source.open("rb") as reader, archive.open(
            info, "w", force_zip64=True
        ) as writer:
            while True:
                block = reader.read(BUFFER_SIZE)
                if not block:
                    break
                writer.write(block)
                digest.update(block)
                total += len(block)
    except OSError as exc:
        raise MigrationError(f"cannot read {source}: {exc}") from exc
    try:
        after = source.lstat()
    except OSError as exc:
        raise MigrationError(f"cannot re-stat {source}: {exc}") from exc
    identity_changed = (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or getattr(before, "st_ino", None) != getattr(after, "st_ino", None)
    )
    if identity_changed or total != before.st_size:
        raise MigrationError(f"file changed during export: {source}")
    return digest.hexdigest(), total


def add_root_payload(
    archive: zipfile.ZipFile,
    root_record: dict[str, object],
    compression: int,
) -> None:
    root = Path(str(root_record["originalPath"]))
    entries: list[dict[str, object]] = []
    for source, relative, info in walk_root(root):
        if stat.S_ISDIR(info.st_mode) and not is_junction(source):
            entry = entry_metadata(relative, "directory", info)
            root_record["directoryCount"] = (
                int(root_record["directoryCount"]) + 1
            )
        elif stat.S_ISLNK(info.st_mode) or is_junction(source):
            junction = is_junction(source)
            entry = entry_metadata(
                relative,
                "junction" if junction else "symlink",
                info,
            )
            try:
                entry["target"] = os.readlink(source)
                entry["targetIsDirectory"] = source.is_dir()
            except OSError as exc:
                raise MigrationError(
                    f"cannot read symbolic link {source}: {exc}"
                ) from exc
            root_record["symlinkCount"] = (
                int(root_record["symlinkCount"]) + 1
            )
        else:
            entry = entry_metadata(relative, "file", info)
            writer = getattr(archive, "migration_writer", None)
            if writer:
                digest, total = copy_file_to_blocks(writer, source, entry, info)
            else:
                archive_name = f"{root_record['payloadPrefix']}/{relative}"
                digest, total = copy_file_to_zip(archive, source, archive_name, info, compression)
                entry["archiveEntry"] = archive_name
            entry["size"] = total
            entry["sha256"] = digest
            root_record["fileCount"] = int(root_record["fileCount"]) + 1
            root_record["totalBytes"] = (
                int(root_record["totalBytes"]) + total
            )
        entries.append(entry)
    root_record["entries"] = entries
    if getattr(archive, "migration_writer", None):
        archive.migration_writer.finish()
    root_info = root.lstat()
    if root_record.get("sourceType") == "file":
        root_info = root.parent.lstat()
    root_record["rootMetadata"] = entry_metadata(".", "directory", root_info)


def capsule_start_text(manifest: dict[str, object]) -> str:
    projects = manifest["projects"]
    return f"""# Codex 完整迁移包

这是一个私密的 Codex 项目迁移包，不要上传到公开仓库或发送给无关人员。

## 在新电脑上恢复

把迁移包和“开始部署”入口一起复制到新电脑。点击“开始部署”并发送已填好的提示，
或者在 Codex 输入 `@Codex一键迁移 部署`。唯一有效包可以自动关联；多个包由用户选择。
部署前必须让用户选择总文件夹的位置和名称，优先推荐容量足够的非系统盘。

```powershell
python "<迁移包路径>" inspect
python "<迁移包路径>" deploy --destination "<用户选择的父目录>" --folder-name "<用户选择的名称>" --dry-run
python "<迁移包路径>" deploy --destination "<用户选择的父目录>" --folder-name "<用户选择的名称>"
```

所有项目、关联资源、环境存档和说明都恢复到所选总文件夹。原盘符只用于说明。
加密包需要单独保存的恢复密钥；不要把密钥与迁移包一起公开或一起长期存储。
高级用户仍可以使用明确路径映射：

```powershell
python "<迁移包路径>" restore --map-root "D:\\=E:\\"
```

恢复完成后，让 Codex 阅读恢复报告目录中的 `RESTORE-CONTEXT.md`。

## 包信息

- 格式：{FORMAT_NAME}
- 模式版本：{SCHEMA_VERSION}
- 工具版本：{VERSION}
- 生成时间：{manifest["createdAt"]}
- 项目数量：{len(projects)}
- 完整性策略：项目根目录零排除；任何无法读取的条目都会使导出失败。

## 重要边界

- 项目文件、隐藏文件、`.git`、未提交修改、依赖目录和构建结果均会复制。
- 文件内容会用 SHA-256 逐项校验。
- Codex 账号登录、操作系统凭据和应用内部聊天列表不能由本文件强行恢复。
- 若包中包含 `context/codex-context.json`，Codex 应读取其中可导出的任务摘要继续工作。
"""


def capsule_output_path(value: Path | None) -> Path:
    if value is None:
        return Path.cwd() / f"codex-migration-{stamp()}.pyz"
    path = value.expanduser().resolve()
    if path.suffix.lower() != ".pyz":
        path = path.with_name(path.name + ".pyz")
    return path


def ensure_safe_output(
    output: Path,
    roots: Sequence[dict[str, object]],
    force: bool,
) -> None:
    for root in roots:
        root_path = Path(str(root["originalPath"]))
        if is_relative_to(output, root_path):
            raise MigrationError(
                f"output must be outside every exported root: {output}"
            )
    if output.exists():
        if not force:
            raise MigrationError(
                f"output already exists: {output}; use --force only for "
                "that exact file"
            )
        if not output.is_file():
            raise MigrationError(f"output is not a file: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)


def build_manifest(
    projects: list[dict[str, object]],
    roots: list[dict[str, object]],
    context: dict[str, object] | None,
    compression: str,
    runtime: dict | None = None,
) -> dict[str, object]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "format": FORMAT_NAME,
        "toolVersion": VERSION,
        "createdAt": now_local(),
        "sourceComputer": {
            "hostname": socket.gethostname(),
            "os": platform.platform(),
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "compression": compression,
        "completeness": {
            "zeroExclusionProjectCopy": True,
            "fileContentHashes": "SHA-256",
            "includesGitInternals": True,
            "includesHiddenFiles": True,
            "includesBuildAndDependencyDirectories": True,
            "accountCredentialsCopied": False,
            "codexUiStateRestorable": False,
        },
        "projects": projects,
        "roots": roots,
        "context": context,
        "capsuleId": str(uuid.uuid4()),
        "runtime": runtime if runtime is not None else support.runtime_inventory(),
    }


def add_context(
    archive: zipfile.ZipFile,
    context_path: Path | None,
    compression: int,
) -> dict[str, object] | None:
    if context_path is None:
        return None
    context_path = context_path.expanduser().resolve()
    if not context_path.is_file():
        raise MigrationError(f"context file does not exist: {context_path}")
    before = context_path.lstat()
    digest, size = copy_file_to_zip(
        archive,
        context_path,
        "context/codex-context.json",
        before,
        compression,
    )
    return {
        "archiveEntry": "context/codex-context.json",
        "sha256": digest,
        "size": size,
        "sourceName": context_path.name,
    }


def copy_file_to_blocks(writer, source: Path, entry: dict, before) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    entry["chunks"] = []
    media = source.suffix.lower() in support.MEDIA_SUFFIXES
    with source.open("rb") as reader:
        if before.st_size <= min(256 * 1024, writer.chunk_size):
            data = reader.read()
            digest.update(data)
            total = len(data)
            writer.add_small(data, entry, media)
        else:
            while data := reader.read(writer.chunk_size):
                digest.update(data)
                total += len(data)
                reference = {"id": "", "offset": 0, "size": len(data)}
                entry["chunks"].append(reference)
                writer.submit(data, [reference], media)
    after = source.lstat()
    if total != before.st_size or before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns or before.st_ino != after.st_ino:
        raise MigrationError(f"file changed during export: {source}")
    return digest.hexdigest(), total


def runtime_members() -> dict[str, bytes]:
    if self_capsule_path() is not None:
        with zipfile.ZipFile(self_capsule_path()) as archive:
            result = {"__main__.py": archive.read("__main__.py")}
    else:
        result = {"__main__.py": Path(__file__).read_bytes()}
    for name in support.RUNTIME_FILES:
        if self_capsule_path() is not None:
            with zipfile.ZipFile(self_capsule_path()) as archive:
                result[name] = archive.read(name)
        else:
            result[name] = Path(__file__).with_name(name).read_bytes()
    return result


def checkpoint_archive(archive, work: Path, fingerprint: str, force: bool = False) -> None:
    import io
    offset = archive.fp.tell()
    previous = getattr(archive, "migration_checkpoint_offset", 0)
    last_time = getattr(archive, "migration_checkpoint_time", 0)
    if not force and offset - previous < 128 * 1024 * 1024 and time.monotonic() - last_time < 5:
        return
    archive.fp.flush()
    os.fsync(archive.fp.fileno())
    class OffsetBuffer(io.BytesIO):
        def tell(self):
            return super().tell() + offset
        def seek(self, value, whence=0):
            return super().seek(value - offset if whence == 0 else value, whence) + offset
    buffer = OffsetBuffer()
    index = zipfile.ZipFile(buffer, "w", allowZip64=True)
    index.filelist = list(archive.filelist)
    index.NameToInfo = dict(archive.NameToInfo)
    index.close()
    footer = buffer.getvalue()
    footer_name = "footer-" + hashlib.sha256(footer).hexdigest() + ".bin"
    (work / footer_name).write_bytes(footer)
    support.atomic_json(work / "checkpoint.json", {"version": VERSION, "fingerprint": fingerprint, "dataEnd": offset, "footer": footer_name, "footerSha256": hashlib.sha256(footer).hexdigest()})
    archive.migration_checkpoint_offset = offset
    archive.migration_checkpoint_time = time.monotonic()


def recover_partial(partial: Path, work: Path, fingerprint: str) -> None:
    state = json.loads((work / "checkpoint.json").read_text(encoding="utf-8"))
    if state.get("version") != VERSION or state.get("fingerprint") != fingerprint:
        raise MigrationError("source/configuration changed since the checkpoint; choose a new output filename instead of resuming")
    footer_name = str(state["footer"])
    if not re.fullmatch(r"footer-[0-9a-f]{64}\.bin", footer_name):
        raise MigrationError("invalid export checkpoint")
    footer = (work / footer_name).read_bytes()
    if hashlib.sha256(footer).hexdigest() != state["footerSha256"]:
        raise MigrationError("export checkpoint index is damaged")
    end = int(state["dataEnd"])
    if end < 0 or end > partial.stat().st_size:
        raise MigrationError("invalid export checkpoint offset")
    with partial.open("r+b") as stream:
        stream.seek(end)
        stream.truncate()
        stream.write(footer)
        stream.flush()
        os.fsync(stream.fileno())


def export_capsule(args: argparse.Namespace) -> int:
    specs = parse_project_specs(args)
    projects, roots = build_project_and_root_records(specs)
    output = capsule_output_path(args.output)
    if args.dry_run:
        print(json.dumps({"output": str(output), "projects": projects, "roots": [{k: v for k, v in r.items() if k != "entries"} for r in roots]}, ensure_ascii=False, indent=2))
        return 0
    ensure_safe_output(output, roots, args.force)
    started = time.monotonic()
    initial = {r["id"]: tree_fingerprint(Path(r["originalPath"])) for r in roots}
    context_bytes = args.codex_context.read_bytes() if args.codex_context else b""
    signature = {"sources": initial, "context": hashlib.sha256(context_bytes).hexdigest(), "compression": args.compression, "chunkMiB": args.chunk_mib}
    fingerprint = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()
    estimated = sum(int(value[1]) for tree in initial.values() for value in tree.values() if value[0] == "file")
    entries = sum(len(tree) for tree in initial.values())
    reserve = max(16 * 1024 * 1024, entries * 2048)
    required = (estimated + len(context_bytes)) * (2 if args.encrypt else 1) + reserve
    work = output.with_name(output.name + ".work")
    partial = work / "payload.partial"
    if work.is_symlink() or is_junction(work) or partial.is_symlink():
        raise MigrationError("export checkpoint paths must not be links")
    for extension in (".cmd", ".ps1"):
        if output.with_name(output.stem + "-开始部署" + extension).exists():
            raise MigrationError("deployment entry already exists; choose a new output filename")
    if work.exists():
        if not args.resume or not (work / "checkpoint.json").is_file():
            raise MigrationError(f"unfinished export exists: {work}; use --resume or choose a new output filename")
        if not partial.is_file():
            raise MigrationError("export checkpoint has no partial archive")
        recover_partial(partial, work, fingerprint)
    else:
        if shutil.disk_usage(output.parent).free < required:
            raise MigrationError(f"insufficient free space: need up to {required} bytes including staging")
        work.mkdir()
    resume = partial.exists()
    members = runtime_members()
    archive = zipfile.ZipFile(partial, "a" if resume else "w", allowZip64=True)
    base = None
    writer = None
    runtime_pool = ThreadPoolExecutor(max_workers=1)
    runtime_future = runtime_pool.submit(support.runtime_inventory)
    try:
        if args.base_package:
            base, base_manifest = open_manifest(args.base_package, args.key_file)
            if base_manifest["schemaVersion"] != 3:
                raise MigrationError("incremental block reuse requires a schema-3 base package")
        if not resume:
            for name, data in members.items():
                archive.writestr(name, data)
        checkpoint = lambda value: checkpoint_archive(value, work, fingerprint)
        writer = support.BlockWriter(archive, args.compression, args.workers, args.chunk_mib * 1024 * 1024, checkpoint, base)
        archive.migration_writer = writer
        checkpoint_archive(archive, work, fingerprint, True)
        context = None
        if args.codex_context:
            if "context/codex-context.json" not in archive.NameToInfo:
                archive.writestr("context/codex-context.json", context_bytes, compress_type=zipfile.ZIP_DEFLATED, compresslevel=1)
            context = {"archiveEntry": "context/codex-context.json", "sha256": hashlib.sha256(context_bytes).hexdigest(), "size": len(context_bytes)}
        for root in roots:
            print(f"Archiving: {root['originalPath']}", flush=True)
            add_root_payload(archive, root, zipfile.ZIP_STORED)
            if tree_fingerprint(Path(root["originalPath"])) != initial[root["id"]]:
                raise MigrationError(f"project tree changed during export: {root['originalPath']}")
            checkpoint_archive(archive, work, fingerprint, True)
        manifest = build_manifest(projects, roots, context, args.compression, runtime_future.result())
        manifest["continuity"] = support.continuity_summary(json.loads(context_bytes) if context_bytes else None)
        manifest["blockFormat"] = {"version": 1, "chunkMiB": args.chunk_mib, "smallFileGrouping": True, "deduplicated": True, "reusedLogicalBytes": writer.reused}
        used = {chunk["id"] for root in roots for entry in root["entries"] if entry["type"] == "file" for chunk in entry["chunks"]}
        manifest["blobs"] = sorted(used)
        existing = {name[6:] for name in archive.namelist() if name.startswith("blobs/")}
        if used != existing:
            raise MigrationError("checkpoint contains unused data blocks; choose a fresh export filename")
        manifest["performance"] = {"exportSecondsBeforeVerification": round(time.monotonic() - started, 3), "logicalBytes": estimated, "uniqueBlocks": len(used), "workers": args.workers}
        archive.writestr(MANIFEST_ENTRY, json.dumps(manifest, ensure_ascii=False, separators=(",", ":")))
        archive.writestr(START_ENTRY, capsule_start_text(manifest))
        archive.writestr(CAPSULE_MARKER, json.dumps({"format": FORMAT_NAME, "schemaVersion": SCHEMA_VERSION, "encrypted": False}))
    finally:
        runtime_pool.shutdown(wait=True)
        if writer:
            writer.close()
        archive.close()
        if base:
            close_archive(base)
    verify_capsule_path(partial, verbose=False)
    final = partial
    key_file = args.key_file
    if args.encrypt:
        key_file = key_file or output.with_suffix(".recovery-key.json")
        for root in roots:
            if is_relative_to(key_file.resolve(), Path(root["originalPath"])):
                raise MigrationError("recovery key must be outside all exported sources")
        encrypted = work / "encrypted.partial"
        summary = {"capsuleId": manifest["capsuleId"], "createdAt": manifest["createdAt"], "sourceComputer": manifest["sourceComputer"]["hostname"], "projectCount": len(projects), "payloadBytes": estimated}
        crypto.wrap_package(partial, encrypted, key_file, members, summary)
        verify_capsule_path(encrypted, verbose=False, key_file=key_file)
        final = encrypted
    os.replace(final, output)
    entry = support.write_deployment_entry(output, args.plugin_identity)
    # Only generated files within this validated work directory are removed.
    for child in work.iterdir():
        if child.is_file() and child.name in {"payload.partial", "encrypted.partial", "checkpoint.json", "checkpoint.json.next"} or child.is_file() and re.fullmatch(r"footer-[0-9a-f]{64}\.bin", child.name):
            child.unlink()
    if not any(work.iterdir()):
        work.rmdir()
    print(json.dumps({"capsule": str(output), "deploymentEntry": str(entry), "version": VERSION, "projects": len(projects), "sources": len(roots), "files": sum(r["fileCount"] for r in roots), "logicalBytes": estimated, "packageBytes": output.stat().st_size, "elapsedSeconds": round(time.monotonic() - started, 3), "verified": True, "encrypted": args.encrypt, "recoveryKeyFile": str(key_file) if args.encrypt else None, "continuity": manifest["continuity"]}, ensure_ascii=False, indent=2))
    if args.encrypt:
        print("Keep the recovery key separately. It is required on the new computer.")
    return 0


def self_capsule_path() -> Path | None:
    candidate = Path(sys.argv[0]).expanduser().resolve()
    try:
        if candidate.is_file() and zipfile.is_zipfile(candidate):
            with zipfile.ZipFile(candidate) as archive:
                if CAPSULE_MARKER in archive.namelist():
                    return candidate
    except (OSError, zipfile.BadZipFile):
        return None
    return None


def resolve_capsule(argument: Path | None) -> Path:
    candidate = (
        argument.expanduser().resolve()
        if argument is not None
        else self_capsule_path()
    )
    if candidate is None:
        raise MigrationError(
            "package path is required when running outside a capsule"
        )
    if not candidate.is_file():
        raise MigrationError(f"capsule does not exist: {candidate}")
    return candidate


def close_archive(archive) -> None:
    pool = getattr(archive, "migration_runtime_pool", None)
    if pool:
        pool.shutdown(wait=True)
    archive.close()
    reader = getattr(archive, "migration_reader", None)
    if reader:
        reader.close()


def open_manifest(package: Path, key_file: Path | None = None):
    outer = zipfile.ZipFile(package, "r")
    archive = outer
    reader = None
    try:
        info = outer.getinfo(CAPSULE_MARKER)
        if info.file_size > 64 * 1024:
            raise MigrationError("oversized capsule marker")
        marker = json.loads(outer.read(info))
        if marker.get("format") != FORMAT_NAME:
            raise MigrationError("unsupported capsule format")
        if marker.get("encrypted"):
            expected = {"__main__.py", CAPSULE_MARKER, "encrypted.bin", *support.RUNTIME_FILES}
            if set(outer.namelist()) != expected or len(outer.namelist()) != len(expected):
                raise MigrationError("invalid encrypted capsule entries")
            reader = crypto.DecryptedReader(outer, marker["encryption"], crypto.read_key(key_file))
            archive = zipfile.ZipFile(reader)
            archive.migration_reader = reader
        info = archive.getinfo(MANIFEST_ENTRY)
        if info.file_size > 256 * 1024 * 1024:
            raise MigrationError("oversized manifest")
        manifest = json.loads(archive.read(info))
        if manifest.get("schemaVersion") not in (2, 3) or manifest.get("format") != FORMAT_NAME:
            raise MigrationError("unsupported manifest schema or format")
    except Exception:
        archive.close()
        if reader:
            reader.close()
        outer.close()
        raise
    return archive, manifest


def validate_manifest_paths(manifest: dict[str, object]) -> set[str]:
    expected = {"__main__.py", CAPSULE_MARKER, MANIFEST_ENTRY, START_ENTRY}
    schema = manifest["schemaVersion"]
    if schema == 3:
        expected.update(support.RUNTIME_FILES)
    roots = manifest.get("roots")
    if not isinstance(roots, list):
        raise MigrationError("manifest roots must be a list")
    seen_root_ids, used_blobs = set(), set()
    blobs = manifest.get("blobs", [])
    if schema == 3 and (not isinstance(blobs, list) or len(blobs) != len(set(blobs))):
        raise MigrationError("invalid block index")
    for identifier in blobs:
        if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{64}", identifier):
            raise MigrationError("invalid block identifier")
        expected.add("blobs/" + identifier)
    for root in roots:
        if not isinstance(root, dict):
            raise MigrationError("invalid root record")
        root_id = str(root.get("id", ""))
        if not re.fullmatch(r"[a-zA-Z0-9-]+", root_id) or root_id in seen_root_ids:
            raise MigrationError("invalid or duplicate root id")
        seen_root_ids.add(root_id)
        if root.get("payloadPrefix") != "payload/" + root_id:
            raise MigrationError("invalid payload prefix")
        entries = root.get("entries")
        if not isinstance(entries, list):
            raise MigrationError("root entries must be a list")
        seen_paths, file_paths = set(), set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise MigrationError("invalid entry")
            relative = safe_relative(str(entry.get("path", ""))).as_posix()
            key = relative.casefold()
            if key in seen_paths:
                raise MigrationError("duplicate/case-colliding portable path: " + relative)
            seen_paths.add(key)
            kind = entry.get("type")
            if kind == "file":
                file_paths.add(key)
                if not isinstance(entry.get("size"), int) or entry["size"] < 0 or not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256", ""))):
                    raise MigrationError("invalid file size or hash")
                if schema == 2:
                    archive_entry = str(entry.get("archiveEntry", ""))
                    if archive_entry != f"{root['payloadPrefix']}/{relative}":
                        raise MigrationError("payload path mismatch")
                    expected.add(archive_entry)
                else:
                    chunks = entry.get("chunks")
                    if not isinstance(chunks, list):
                        raise MigrationError("file has no block list")
                    total = 0
                    for reference in chunks:
                        if not isinstance(reference, dict) or reference.get("id") not in blobs:
                            raise MigrationError("missing referenced data block")
                        offset, size = reference.get("offset"), reference.get("size")
                        if not isinstance(offset, int) or not isinstance(size, int) or offset < 0 or size <= 0 or offset + size > support.MAX_BLOCK:
                            raise MigrationError("invalid data block slice")
                        used_blobs.add(reference["id"])
                        total += size
                    if total != entry["size"]:
                        raise MigrationError("block list size does not match file")
            elif kind in {"symlink", "junction"}:
                file_paths.add(key)
                if not isinstance(entry.get("target"), str):
                    raise MigrationError("symbolic link target missing")
            elif kind != "directory":
                raise MigrationError("unsupported entry type")
        # Links are created last; no entry may descend through a file or link.
        for key in seen_paths:
            parents = PurePosixPath(key).parents
            if any(str(parent) in file_paths for parent in parents if str(parent) != "."):
                raise MigrationError("entry descends through a file or link")
    if schema == 3 and used_blobs != set(blobs):
        raise MigrationError("unexpected unreferenced data blocks")
    context = manifest.get("context")
    if context:
        if not isinstance(context, dict) or context.get("archiveEntry") != "context/codex-context.json":
            raise MigrationError("invalid context record")
        expected.add(context["archiveEntry"])
    return expected


def hash_stream(reader: BinaryIO) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    while True:
        block = reader.read(BUFFER_SIZE)
        if not block:
            break
        digest.update(block)
        total += len(block)
    return digest.hexdigest(), total


def verify_capsule_path(package: Path, verbose: bool = True, key_file: Path | None = None) -> dict[str, int]:
    archive, manifest = open_manifest(package, key_file)
    try:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise MigrationError("capsule contains duplicate archive names")
        expected = validate_manifest_paths(manifest)
        actual = set(names)
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        if missing:
            raise MigrationError(
                "capsule entries are missing: " + ", ".join(missing[:10])
            )
        if unexpected:
            raise MigrationError(
                "capsule has unexpected entries: "
                + ", ".join(unexpected[:10])
            )

        file_count = 0
        total_bytes = 0
        blocks = support.BlockReader(archive)
        for root in manifest["roots"]:
            for entry in root["entries"]:
                if entry["type"] != "file":
                    continue
                if manifest["schemaVersion"] == 3:
                    file_hash, size = hashlib.sha256(), 0
                    for reference in entry["chunks"]:
                        data = blocks.read(reference)
                        file_hash.update(data)
                        size += len(data)
                    digest = file_hash.hexdigest()
                else:
                    with archive.open(entry["archiveEntry"]) as reader:
                        digest, size = hash_stream(reader)
                if digest != entry["sha256"] or size != entry["size"]:
                    raise MigrationError(
                        f"payload checksum mismatch: "
                        f"{root['originalPath']}::{entry['path']}"
                    )
                file_count += 1
                total_bytes += size
        context = manifest.get("context")
        if context:
            with archive.open(context["archiveEntry"]) as reader:
                digest, size = hash_stream(reader)
            if digest != context["sha256"] or size != context["size"]:
                raise MigrationError("Codex context checksum mismatch")
    finally:
        close_archive(archive)
    if verbose:
        print(f"VALID: {package}")
        print(f"Files checked: {file_count}; bytes checked: {total_bytes}")
    return {"files": file_count, "bytes": total_bytes}


def inspect_capsule(args: argparse.Namespace) -> int:
    package = resolve_capsule(args.package)
    summary = support.package_summary(package)
    if summary["encrypted"] and args.key_file is None:
        print(json.dumps({"publicSummary": summary, "keyRequiredForFullManifest": True}, ensure_ascii=False, indent=2))
        return 0
    archive, manifest = open_manifest(package, args.key_file)
    close_archive(archive)
    print(f"Capsule: {package}")
    print(f"Created: {manifest['createdAt']}")
    print(f"Source computer: {manifest['sourceComputer']['hostname']}")
    print(f"Projects: {len(manifest['projects'])}")
    print(f"Storage roots: {len(manifest['roots'])}")
    print(
        "Payload bytes: "
        + str(sum(int(root["totalBytes"]) for root in manifest["roots"]))
    )
    print(
        "Codex context: "
        + ("included" if manifest.get("context") else "not included")
    )
    for project in manifest["projects"]:
        print(f"  - {project['originalPath']}")
    return 0


def parse_mappings(values: Sequence[str]) -> list[tuple[Path, Path]]:
    mappings: list[tuple[Path, Path]] = []
    for value in values:
        if "=" not in value:
            raise MigrationError(
                f"path mapping must use OLD=NEW syntax: {value}"
            )
        old_value, new_value = value.split("=", 1)
        if not old_value or not new_value:
            raise MigrationError(f"invalid path mapping: {value}")
        old = Path(old_value).expanduser()
        new = Path(new_value).expanduser()
        mappings.append((old, new))
    return sorted(mappings, key=lambda pair: len(pair[0].parts), reverse=True)


def mapped_target(
    original: str,
    mappings: Sequence[tuple[Path, Path]],
    use_original_paths: bool,
) -> Path:
    source = Path(original)
    for old, new in mappings:
        try:
            relative = source.relative_to(old)
        except ValueError:
            continue
        return (new / relative).resolve()
    if use_original_paths:
        return source.resolve()
    raise MigrationError(
        f"no destination mapping covers {original}; add --map-root or "
        "--use-original-paths"
    )


def existing_ancestor(path: Path) -> Path:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def destination_plan(
    manifest: dict[str, object],
    mappings: Sequence[tuple[Path, Path]],
    use_original_paths: bool,
) -> list[dict[str, object]]:
    plan: list[dict[str, object]] = []
    targets: list[Path] = []
    for root in manifest["roots"]:
        target = mapped_target(
            str(root["originalPath"]),
            mappings,
            use_original_paths,
        )
        if target == Path(target.anchor):
            raise MigrationError(f"refusing to restore onto drive root: {target}")
        for existing in targets:
            if (
                normalized_path_key(existing) == normalized_path_key(target)
                or is_relative_to(target, existing)
                or is_relative_to(existing, target)
            ):
                raise MigrationError(
                    f"destination roots overlap: {existing} and {target}"
                )
        targets.append(target)
        ancestor = existing_ancestor(target.parent)
        if not ancestor.exists():
            raise MigrationError(
                f"no existing destination ancestor for {target}"
            )
        free = shutil.disk_usage(ancestor).free
        required = int(root["totalBytes"])
        plan.append(
            {
                "root": root,
                "target": target,
                "freeBytes": free,
                "requiredBytes": required,
                "exists": target.exists() or target.is_symlink(),
            }
        )
    return plan


def safe_destination(base: Path, relative: str) -> Path:
    safe = safe_relative(relative)
    destination = base.joinpath(*safe.parts)
    try:
        destination.relative_to(base)
    except ValueError as exc:
        raise MigrationError(
            f"entry escapes restore root: {relative}"
        ) from exc
    ancestor = destination.parent
    while True:
        if ancestor.is_symlink() or is_junction(ancestor):
            raise MigrationError(f"restore path descends through an existing link: {ancestor}")
        if ancestor == base:
            break
        ancestor = ancestor.parent
    return destination


def set_windows_attributes(path: Path, value: object) -> None:
    if os.name != "nt" or value is None:
        return
    import ctypes

    if not ctypes.windll.kernel32.SetFileAttributesW(
        str(path), int(value)
    ):
        error = ctypes.get_last_error()
        raise OSError(error, f"SetFileAttributesW failed for {path}")


def apply_metadata(path: Path, entry: dict[str, object]) -> list[str]:
    warnings: list[str] = []
    timestamp = int(entry["mtimeNs"])
    try:
        if entry["type"] in {"symlink", "junction"}:
            os.utime(
                path,
                ns=(timestamp, timestamp),
                follow_symlinks=False,
            )
        else:
            # Windows does not support follow_symlinks=False for os.utime,
            # even for ordinary files and directories.
            os.utime(path, ns=(timestamp, timestamp))
    except (NotImplementedError, OSError) as exc:
        warnings.append(f"timestamp warning for {path}: {exc}")
    if entry["type"] not in {"symlink", "junction"}:
        try:
            os.chmod(path, int(entry["mode"]))
        except OSError as exc:
            warnings.append(f"mode warning for {path}: {exc}")
    try:
        set_windows_attributes(path, entry.get("windowsAttributes"))
    except OSError as exc:
        warnings.append(f"attribute warning for {path}: {exc}")
    return warnings


def restore_root_to_staging(
    archive: zipfile.ZipFile,
    root: dict[str, object],
    staging: Path,
) -> list[str]:
    warnings: list[str] = []
    resume = getattr(archive, "migration_resume", False)
    if staging.is_symlink() or is_junction(staging):
        raise MigrationError("restore staging directory must not be a link")
    staging.mkdir(parents=False, exist_ok=resume)
    entries = list(root["entries"])
    for entry in entries:
        if entry["type"] == "directory":
            safe_destination(staging, entry["path"]).mkdir(
                parents=True, exist_ok=resume
            )
    for entry in entries:
        kind = entry["type"]
        destination = safe_destination(staging, entry["path"])
        if kind == "file":
            destination.parent.mkdir(parents=True, exist_ok=True)
            token = root["id"] + "/" + entry["path"]
            unfinished = token in getattr(archive, "migration_inflight", set())
            if resume and destination.is_file() and not destination.is_symlink():
                with destination.open("rb") as existing_reader:
                    existing_digest, existing_size = hash_stream(existing_reader)
                if existing_digest == entry["sha256"] and existing_size == entry["size"]:
                    warnings.extend(apply_metadata(destination, entry))
                    continue
                if not unfinished:
                    raise MigrationError(f"resume found a changed completed file: {destination}")
            digest = hashlib.sha256()
            total = 0
            journal = getattr(archive, "migration_file_journal", None)
            if journal:
                journal.write(json.dumps(["begin", token], ensure_ascii=False) + "\n")
                journal.flush()
            with destination.open("wb" if resume and unfinished else "xb") as writer:
                if "chunks" in entry:
                    for reference in entry["chunks"]:
                        block = archive.migration_blocks.read(reference)
                        writer.write(block)
                        digest.update(block)
                        total += len(block)
                else:
                    with archive.open(entry["archiveEntry"]) as reader:
                        while block := reader.read(BUFFER_SIZE):
                            writer.write(block)
                            digest.update(block)
                            total += len(block)
                writer.flush()
            if digest.hexdigest() != entry["sha256"] or total != entry["size"]:
                raise MigrationError(
                    f"checksum mismatch while restoring {destination}"
                )
            warnings.extend(apply_metadata(destination, entry))
            if journal:
                journal.write(json.dumps(["complete", token], ensure_ascii=False) + "\n")
                journal.flush()
    # Create links only after ordinary files, so links cannot redirect writes.
    for entry in entries:
        kind = entry["type"]
        if kind in {"symlink", "junction"}:
            destination = safe_destination(staging, entry["path"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            if resume and (destination.exists() or destination.is_symlink()):
                if os.readlink(destination) == entry["target"]:
                    continue
                raise MigrationError(f"resume found a changed symbolic link: {destination}")
            try:
                if kind == "junction":
                    if os.name != "nt":
                        raise OSError(
                            "Windows junction cannot be restored on this OS"
                        )
                    completed = subprocess.run(
                        [
                            "cmd.exe",
                            "/d",
                            "/c",
                            "mklink",
                            "/J",
                            str(destination),
                            str(entry["target"]),
                        ],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        check=False,
                    )
                    if completed.returncode != 0:
                        raise OSError(completed.stdout.strip())
                else:
                    os.symlink(
                        str(entry["target"]),
                        destination,
                        target_is_directory=bool(
                            entry.get("targetIsDirectory", False)
                        ),
                    )
            except OSError as exc:
                raise MigrationError(
                    f"cannot recreate symbolic link {destination}: {exc}"
                ) from exc
            warnings.extend(apply_metadata(destination, entry))
    directory_entries = sorted(
        (entry for entry in entries if entry["type"] == "directory"),
        key=lambda entry: len(PurePosixPath(entry["path"]).parts),
        reverse=True,
    )
    for entry in directory_entries:
        warnings.extend(
            apply_metadata(
                safe_destination(staging, entry["path"]),
                entry,
            )
        )
    warnings.extend(apply_metadata(staging, root["rootMetadata"]))
    return warnings


def actual_entries(root: Path) -> dict[str, tuple[str, Path]]:
    result: dict[str, tuple[str, Path]] = {}
    for path, relative, info in walk_root(root):
        if is_junction(path):
            kind = "junction"
        elif stat.S_ISLNK(info.st_mode):
            kind = "symlink"
        elif stat.S_ISDIR(info.st_mode):
            kind = "directory"
        else:
            kind = "file"
        result[relative] = (kind, path)
    return result


def verify_restored_root(
    root_record: dict[str, object],
    target: Path,
) -> dict[str, int]:
    expected = {
        str(entry["path"]): entry for entry in root_record["entries"]
    }
    actual = actual_entries(target)
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    if missing or extra:
        raise MigrationError(
            f"restored tree mismatch at {target}; "
            f"missing={missing[:5]}, extra={extra[:5]}"
        )
    files = 0
    total_bytes = 0
    for relative, entry in expected.items():
        kind, path = actual[relative]
        if kind != entry["type"]:
            raise MigrationError(
                f"restored type mismatch: {path}: {kind} != {entry['type']}"
            )
        info = path.lstat()
        if kind not in {"symlink", "junction"} and (
            int(info.st_mtime_ns) != int(entry["mtimeNs"])
        ):
            raise MigrationError(
                f"restored modification time mismatch: {path}: "
                f"{info.st_mtime_ns} != {entry['mtimeNs']}"
            )
        if kind not in {"symlink", "junction"} and (
            stat.S_IMODE(info.st_mode) != int(entry["mode"])
        ):
            raise MigrationError(
                f"restored mode mismatch: {path}: "
                f"{stat.S_IMODE(info.st_mode)} != {entry['mode']}"
            )
        expected_attributes = entry.get("windowsAttributes")
        if expected_attributes is not None and (
            windows_attributes(info) != int(expected_attributes)
        ):
            raise MigrationError(
                f"restored Windows attributes mismatch: {path}: "
                f"{windows_attributes(info)} != {expected_attributes}"
            )
        if kind == "file":
            with path.open("rb") as reader:
                digest, size = hash_stream(reader)
            if digest != entry["sha256"] or size != entry["size"]:
                raise MigrationError(f"restored checksum mismatch: {path}")
            files += 1
            total_bytes += size
        elif kind in {"symlink", "junction"} and (
            os.readlink(path) != entry["target"]
        ):
            raise MigrationError(f"restored symlink mismatch: {path}")
    root_metadata = root_record["rootMetadata"]
    root_info = target.lstat()
    if int(root_info.st_mtime_ns) != int(root_metadata["mtimeNs"]):
        raise MigrationError(
            f"restored root modification time mismatch: {target}: "
            f"{root_info.st_mtime_ns} != {root_metadata['mtimeNs']}"
        )
    if stat.S_IMODE(root_info.st_mode) != int(root_metadata["mode"]):
        raise MigrationError(
            f"restored root mode mismatch: {target}: "
            f"{stat.S_IMODE(root_info.st_mode)} != {root_metadata['mode']}"
        )
    root_attributes = root_metadata.get("windowsAttributes")
    if root_attributes is not None and (
        windows_attributes(root_info) != int(root_attributes)
    ):
        raise MigrationError(
            f"restored root Windows attributes mismatch: {target}: "
            f"{windows_attributes(root_info)} != {root_attributes}"
        )
    return {"files": files, "bytes": total_bytes}


def report_directory(package: Path, argument: Path | None) -> Path:
    if argument is not None:
        return argument.expanduser().resolve()
    return package.with_name(package.name + ".restore-report")


def extract_context_and_write_report(
    archive: zipfile.ZipFile,
    manifest: dict[str, object],
    package: Path,
    report_dir: Path,
    restored: list[dict[str, object]],
    warnings: list[str],
) -> None:
    if report_dir.exists():
        raise MigrationError(
            f"restore report directory already exists: {report_dir}"
        )
    report_dir.mkdir(parents=True)
    (report_dir / START_ENTRY).write_bytes(archive.read(START_ENTRY))
    write_json(report_dir / MANIFEST_ENTRY, manifest)
    context = manifest.get("context")
    if context:
        context_dir = report_dir / "context"
        context_dir.mkdir()
        (context_dir / "codex-context.json").write_bytes(
            archive.read(context["archiveEntry"])
        )
    report = {
        "format": FORMAT_NAME,
        "schemaVersion": SCHEMA_VERSION,
        "restoredAt": now_local(),
        "capsule": str(package),
        "restored": restored,
        "warnings": warnings,
        "verified": True,
        "capsuleId": manifest.get("capsuleId"),
        "continuity": manifest.get("continuity"),
    }
    write_json(report_dir / "RESTORE-REPORT.json", report)
    lines = [
        "# Codex 恢复上下文入口",
        "",
        "项目文件已经恢复并通过逐文件 SHA-256 校验。",
        "",
        "## 恢复的项目",
        "",
    ]
    target_by_root = {
        item["rootId"]: Path(str(item["targetPath"])) for item in restored
    }
    for project in manifest["projects"]:
        target = target_by_root[project["storageRootId"]]
        relative = str(project["relativePathWithinRoot"])
        if relative:
            target = target.joinpath(*PurePosixPath(relative).parts)
        lines.append(
            f"- `{project['name']}`：`{project['originalPath']}` → `{target}`"
        )
    lines.extend(
        [
            "",
            "## Codex 续接指令",
            "",
            "1. 阅读本目录的 `manifest.json` 与 `RESTORE-REPORT.json`。",
            "2. 如果存在 `context/codex-context.json`，读取其中的任务、决定和待办摘要。",
            "3. 检查每个恢复项目内的 `AGENTS.md`、README、文档及 Git 状态。",
            "4. 继续工作前先向用户简短汇报：已恢复项目、未解决任务和任何警告。",
            "5. 不得声称原 Codex 侧边栏或聊天界面已被重新创建。",
        ]
    )
    if not context:
        lines.extend(
            [
                "",
                "> 本迁移包没有附带 Codex 任务摘要；项目文件已经完整恢复，"
                "但仅存在于旧聊天中的决定无法从文件中推断。",
            ]
        )
    write_text(report_dir / "RESTORE-CONTEXT.md", "\n".join(lines))


def restore_capsule(args: argparse.Namespace) -> int:
    import copy
    package = resolve_capsule(args.package)
    archive, manifest = open_manifest(package, getattr(args, "key_file", None))
    started = time.monotonic()
    try:
        expected = validate_manifest_paths(manifest)
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != expected:
            raise MigrationError("archive entries do not exactly match the manifest")
        mappings = parse_mappings(args.map_root)
        report_dir = report_directory(package, args.report_dir)
        plan = destination_plan(manifest, mappings, args.use_original_paths)
        manifest_hash = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        capsule_id = manifest.get("capsuleId", manifest_hash[:24])
        required_by_volume = {}
        for item in plan:
            volume = existing_ancestor(Path(item["target"]).parent).stat().st_dev
            record = required_by_volume.setdefault(volume, {"required": 0, "free": item["freeBytes"]})
            needed = item["requiredBytes"]
            if item["exists"] and args.allow_existing_identical:
                needed = 0
            elif args.resume and item["exists"] and not args.replace_existing:
                needed = 0
            elif args.resume:
                stage = Path(item["target"]).parent / ("." + Path(item["target"]).name + ".codex-restore-" + str(capsule_id)[:12])
                present = sum(safe_destination(stage, entry["path"]).stat().st_size for entry in item["root"]["entries"] if entry["type"] == "file" and safe_destination(stage, entry["path"]).is_file() and not safe_destination(stage, entry["path"]).is_symlink())
                needed = max(0, needed - present)
            record["required"] += needed
            if item["exists"] and not (args.replace_existing or args.allow_existing_identical or args.resume):
                raise MigrationError(f"destination already exists: {item['target']}")
        report_ancestor = existing_ancestor(report_dir.parent)
        report_volume = report_ancestor.stat().st_dev
        report_record = required_by_volume.setdefault(report_volume, {"required": 0, "free": shutil.disk_usage(report_ancestor).free})
        report_record["required"] += len(json.dumps(manifest, ensure_ascii=False).encode("utf-8")) * 4 + int((manifest.get("context") or {}).get("size", 0))
        for record in required_by_volume.values():
            if record["required"] + 16 * 1024 * 1024 > record["free"]:
                raise MigrationError("insufficient aggregate free space for all sources on a destination volume")
        preview = {"package": str(package), "schemaVersion": manifest["schemaVersion"], "payloadVerified": False, "targets": [{"source": item["root"]["originalPath"], "target": str(item["target"]), "bytes": item["requiredBytes"]} for item in plan], "continuity": manifest.get("continuity", {"available": bool(manifest.get("context")), "complete": False})}
        if args.dry_run:
            print(json.dumps(preview, ensure_ascii=False, indent=2))
            print("DRY RUN: metadata inspected; no payload was read and no files were written.")
            return 0
        canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        manifest_hash = hashlib.sha256(canonical).hexdigest()
        capsule_id = manifest.get("capsuleId", manifest_hash[:24])
        work = args.state_dir or report_dir.with_name("." + report_dir.name + ".state")
        work = work.resolve()
        if args.state_dir and (args.state_dir.is_symlink() or is_junction(args.state_dir)):
            raise MigrationError("restore state directory must not be a link")
        state_path = work / "restore-state.json"
        signature = {"manifestSha256": manifest_hash, "targets": [str(item["target"]) for item in plan]}
        if work.exists():
            if not args.resume or not state_path.is_file():
                raise MigrationError(f"unfinished restore exists: {work}; use --resume to continue")
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if state.get("signature") != signature:
                raise MigrationError("restore checkpoint belongs to another package or destination")
        else:
            if report_dir.exists():
                raise MigrationError("restore report directory already exists")
            if any(item["exists"] for item in plan) and args.resume and not (args.replace_existing or args.allow_existing_identical):
                raise MigrationError("resume needs this package's existing restore checkpoint")
            work.mkdir(parents=True)
            state = {"signature": signature, "capsuleId": capsule_id, "phase": "extracting", "committed": [], "backups": {}}
            support.atomic_json(state_path, state)
        archive.migration_resume = args.resume
        archive.migration_temp_dir = work
        archive.migration_blocks = support.BlockReader(archive)
        archive.migration_runtime_pool = ThreadPoolExecutor(max_workers=1)
        archive.migration_runtime_future = archive.migration_runtime_pool.submit(support.runtime_inventory)
        journal_path = work / "file-progress.jsonl"
        inflight = set()
        if args.resume and journal_path.exists():
            raw_journal = journal_path.read_bytes()
            valid_end = 0
            for line in raw_journal.splitlines(keepends=True):
                try:
                    action, token = json.loads(line)
                    if action not in {"begin", "complete"} or not isinstance(token, str):
                        raise ValueError("invalid file progress record")
                except (ValueError, TypeError, UnicodeDecodeError):
                    break  # A truncated final journal line is not a completed file.
                valid_end += len(line)
                if action == "begin":
                    inflight.add(token)
                elif action == "complete":
                    inflight.discard(token)
            if valid_end < len(raw_journal) or raw_journal and not raw_journal[:valid_end].endswith(b"\n"):
                with journal_path.open("r+b") as journal_repair:
                    journal_repair.truncate(valid_end)
                    journal_repair.seek(valid_end)
                    if valid_end and not raw_journal[:valid_end].endswith(b"\n"):
                        journal_repair.write(b"\n")
        archive.migration_inflight = inflight
        archive.migration_file_journal = journal_path.open("a", encoding="utf-8")
        context = manifest.get("context")
        if context:
            data = archive.read(context["archiveEntry"])
            if len(data) != context["size"] or hashlib.sha256(data).hexdigest() != context["sha256"]:
                raise MigrationError("context checksum mismatch")
        effective = []
        link_mappings = [(Path(item["root"]["originalPath"]), Path(item["target"]) / Path(item["root"]["originalPath"]).name if item["root"].get("sourceType") == "file" else Path(item["target"])) for item in plan]
        adaptations = []
        for item in plan:
            root = copy.deepcopy(item["root"])
            if args.remap_links:
                for entry in root["entries"]:
                    if entry["type"] in {"symlink", "junction"}:
                        mapped = support.remap_link(entry["target"], link_mappings)
                        if mapped != entry["target"]:
                            adaptations.append({"rootId": root["id"], "path": entry["path"], "originalTarget": entry["target"], "newTarget": mapped})
                            entry["target"] = mapped
            effective.append((item, root))
        stages, restored, warnings = [], [], []
        for item, root in effective:
            target = Path(item["target"])
            staging = target.parent / ("." + target.name + ".codex-restore-" + str(capsule_id)[:12])
            already_committed = root["id"] in state["committed"]
            # A power loss can happen between rename and journal update.
            if args.resume and not staging.exists() and target.exists() and not args.replace_existing:
                stats = verify_restored_root(root, target)
                if not already_committed:
                    state["committed"].append(root["id"])
                    support.atomic_json(state_path, state)
                already_committed = True
            if already_committed or item["exists"] and args.allow_existing_identical:
                stats = verify_restored_root(root, target)
                restored.append({"rootId": root["id"], "originalPath": root["originalPath"], "targetPath": str(target), "filesVerified": stats["files"], "bytesVerified": stats["bytes"], "action": "kept-existing-identical" if not already_committed else "resumed-completed"})
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            root_warnings = restore_root_to_staging(archive, root, staging)
            stats = verify_restored_root(root, staging)  # Exactly one independent disk readback.
            warnings.extend(root_warnings)
            stages.append((root, target, staging, stats))
            state["phase"] = "extracting"
            support.atomic_json(state_path, state)
        state["phase"] = "committing"
        support.atomic_json(state_path, state)
        committed_now = []
        try:
            for root, target, staging, stats in stages:
                backup = None
                if target.exists():
                    if not args.replace_existing:
                        raise MigrationError(f"destination appeared during restore: {target}")
                    backup = target.with_name(target.name + ".codex-backup-" + stamp())
                    if backup.exists():
                        raise MigrationError("backup destination already exists")
                    os.replace(target, backup)
                    state["backups"][root["id"]] = str(backup)
                    support.atomic_json(state_path, state)
                try:
                    os.replace(staging, target)
                except BaseException:
                    if backup and not target.exists():
                        os.replace(backup, target)
                    raise
                committed_now.append((root, target, staging, backup))
                state["committed"].append(root["id"])
                support.atomic_json(state_path, state)
                # Rename stays on the same filesystem; file contents were already read back.
                if len(actual_entries(target)) != len(root["entries"]):
                    raise MigrationError("tree changed at commit")
                restored.append({"rootId": root["id"], "originalPath": root["originalPath"], "targetPath": str(target), "filesVerified": stats["files"], "bytesVerified": stats["bytes"], "action": "restored", "backupPath": str(backup) if backup else None})
        except BaseException:
            for root, target, staging, backup in reversed(committed_now):
                if target.exists() and not staging.exists():
                    os.replace(target, staging)
                if backup and backup.exists() and not target.exists():
                    os.replace(backup, target)
                if root["id"] in state["committed"]:
                    state["committed"].remove(root["id"])
            state["phase"] = "staged"
            support.atomic_json(state_path, state)
            raise
        if not report_dir.exists():
            report_staging = work / ("report-" + uuid.uuid4().hex)
            extract_context_and_write_report(archive, manifest, package, report_staging, restored, warnings)
            support.atomic_json(report_staging / "PATH-ADAPTATIONS.json", {"links": adaptations, "ordinaryFilesModified": False})
            environment_adaptations = support.prepare_environment_adaptations(plan, report_dir.parent / "环境配置" / "适配")
            support.atomic_json(report_staging / "ENVIRONMENT-ADAPTATIONS.json", environment_adaptations)
            support.atomic_json(report_staging / "ENVIRONMENT-CHECK.json", {"source": manifest.get("runtime"), "destination": archive.migration_runtime_future.result(), "activation": "Configuration is staged under the selected total folder. Existing global configuration and credentials are not overwritten.", "elapsedSeconds": round(time.monotonic() - started, 3)})
            report_dir.parent.mkdir(parents=True, exist_ok=True)
            if report_dir.exists():
                raise MigrationError("report destination appeared during restore")
            os.replace(report_staging, report_dir)
        else:
            existing_report = json.loads((report_dir / "RESTORE-REPORT.json").read_text(encoding="utf-8"))
            if existing_report.get("capsuleId") != manifest.get("capsuleId"):
                raise MigrationError("existing report belongs to another package")
        state["phase"] = "complete"
        support.atomic_json(state_path, state)
        print(json.dumps({"report": str(report_dir), "restored": restored, "verified": True, "linkAdaptations": len(adaptations), "elapsedSeconds": round(time.monotonic() - started, 3), "continuity": preview["continuity"]}, ensure_ascii=False, indent=2))
        return 0
    finally:
        journal = getattr(archive, "migration_file_journal", None)
        if journal:
            journal.close()
        close_archive(archive)


def resolve_discovered(args) -> Path:
    if args.package:
        path = args.package.expanduser().resolve()
        support.package_summary(path)
        return path
    directories = args.search_dir or [Path.cwd()]
    result = support.discover_packages(directories, args.search_depth)
    if result["status"] != "unique":
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise MigrationError("choose one migration package or provide its local path; no package is selected automatically")
    return Path(result["candidates"][0]["path"])


def deploy_capsule(args) -> int:
    package = resolve_discovered(args)
    if not args.destination or not args.folder_name:
        summary = support.package_summary(package)
        required = summary.get("payloadBytes", 0) + 16 * 1024 * 1024
        drives = support.enumerate_drives()
        for drive in drives:
            drive["enoughSpace"] = drive["freeBytes"] >= required
        print(json.dumps({"package": summary, "needsUserChoice": ["destination", "folderName"], "drives": drives, "suggestedFolderNames": ["Codex工作空间", "Codex迁移_" + dt.datetime.now().strftime("%Y-%m-%d"), str(summary.get("sourceComputer") or "原电脑") + "_工作备份"], "customNameAllowed": True}, ensure_ascii=False, indent=2))
        return 0
    folder = support.validate_folder_name(args.folder_name)
    total = args.destination.expanduser().resolve() / folder
    archive, manifest = open_manifest(package, args.key_file)
    close_archive(archive)
    categories = {"projects": "项目", "resources": "关联资源", "environment": "环境配置/原始"}
    used, mappings = set(), []
    for root in manifest["roots"]:
        name = support.validate_folder_name(root.get("name") or Path(root["originalPath"]).name)
        category = categories.get(root.get("category", "projects"), "关联资源")
        relative = category + "/" + name
        if relative.casefold() in used:
            name += "-" + root["id"][-8:]
            relative = category + "/" + name
        used.add(relative.casefold())
        target = total.joinpath(*PurePosixPath(relative).parts)
        mappings.append(root["originalPath"] + "=" + str(target))
    if total.exists() and not args.resume:
        raise MigrationError(f"total destination already exists: {total}; choose a new folder name or explicitly resume this package")
    restore_args = argparse.Namespace(package=package, key_file=args.key_file, map_root=mappings, use_original_paths=False, report_dir=total / "迁移说明", state_dir=total / ".迁移状态", dry_run=args.dry_run, resume=args.resume, remap_links=True, replace_existing=False, allow_existing_identical=False)
    return restore_capsule(restore_args)


def discover_command(args) -> int:
    print(json.dumps(support.discover_packages(args.search_dir or [Path.cwd()], args.search_depth), ensure_ascii=False, indent=2))
    return 0


def drives_command(args) -> int:
    print(json.dumps(support.enumerate_drives(), ensure_ascii=False, indent=2))
    return 0


def inventory_command(args) -> int:
    specs = load_projects_file(args.projects_file) if args.projects_file else [{"path": str(Path(value).resolve()), "category": "projects"} for value in args.project]
    resources, warnings = [], []
    selected = [Path(str(item["path"])).resolve() for item in specs]
    codex_home = (args.codex_home or Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))).resolve()
    agents_home = (args.agents_home or Path.home() / ".agents").resolve()
    broad = {Path.home().resolve(), codex_home, agents_home}
    excluded = [codex_home / value for value in ("auth.json", "auth", "sessions", "archived_sessions", "sqlite", ".sandbox-secrets")]
    def allowed_resource(candidate):
        if candidate == Path(candidate.anchor) or candidate in broad or any(candidate == value or is_relative_to(candidate, value) for value in excluded):
            warnings.append(f"Referenced path requires explicit scope or is excluded account state: {candidate}")
            return False
        return True
    # Linked Git worktrees and external symlink targets are explicit dependencies.
    for root in selected:
        for path, relative, info in walk_root(root):
            if is_junction(path) or stat.S_ISLNK(info.st_mode):
                candidate = path.resolve()
                if candidate.exists() and allowed_resource(candidate) and not any(is_relative_to(candidate, parent) for parent in selected):
                    resources.append({"path": str(candidate), "category": "resources", "name": candidate.name})
            if relative == ".git" and stat.S_ISREG(info.st_mode):
                text = path.read_text(encoding="utf-8", errors="replace").strip()
                if text.startswith("gitdir:"):
                    gitdir = (path.parent / text[7:].strip()).resolve()
                    common_file = gitdir / "commondir"
                    common = (gitdir / common_file.read_text(encoding="utf-8").strip()).resolve() if common_file.exists() else gitdir
                    if common.exists() and not any(is_relative_to(common, parent) for parent in selected):
                        resources.append({"path": str(common), "category": "resources", "name": "git-common-" + common.name})
                        warnings.append(f"Linked worktree {root}: common Git directory is included; worktree metadata may need Git repair after relocation.")
    if args.codex_context:
        context = json.loads(args.codex_context.read_text(encoding="utf-8-sig"))
        def strings(value):
            if isinstance(value, str):
                yield value
                if len(value) <= 10 * 1024 * 1024 and value.lstrip().startswith(("{", "[")):
                    try:
                        decoded = json.loads(value)
                    except (ValueError, RecursionError):
                        decoded = None
                    if isinstance(decoded, (dict, list)):
                        yield from strings(decoded)
            elif isinstance(value, dict):
                for item in value.values():
                    yield from strings(item)
            elif isinstance(value, list):
                for item in value:
                    yield from strings(item)
        seen = set()
        for value in strings(context):
            values = [value]
            values.extend(re.findall(r"\]\(([^)\r\n]+)\)", value))
            values.extend(match.replace("\\\\", "\\") for match in re.findall(r'[A-Za-z]:[\\/][^\r\n"<>|]*?\.(?:png|jpe?g|webp|gif|mp4|mov|mp3|wav|pdf|docx|pptx|xlsx|csv|blend|glb|json|md|py|js)(?=[\s"`)]|$)', value, flags=re.IGNORECASE))
            for candidate in values:
                if len(candidate) > 32767:
                    continue
                candidate = candidate.strip().strip("<>")
                if not (Path(candidate).is_absolute() or PureWindowsPath(candidate).is_absolute()):
                    continue
                path = Path(candidate)
                try:
                    exists = path.exists()
                except OSError:
                    continue
                if exists:
                    resolved = path.resolve()
                    key = normalized_path_key(resolved)
                    if key not in seen and allowed_resource(resolved) and not any(is_relative_to(resolved, parent) for parent in selected):
                        seen.add(key)
                        resources.append({"path": str(resolved), "category": "resources", "name": resolved.name})
    if args.codex_home:
        resources.extend(support.inventory_environment(args.codex_home, args.agents_home))
    unique = {normalized_path_key(Path(str(item["path"]))): item for item in [*specs, *resources]}
    print(json.dumps({"projects": list(unique.values()), "warnings": warnings, "scope": "registered projects, explicit dependencies, referenced local assets and allowlisted Codex configuration"}, ensure_ascii=False, indent=2))
    return 0


def verify_command(args: argparse.Namespace) -> int:
    package = resolve_capsule(args.package)
    verify_capsule_path(package, key_file=args.key_file)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="完整迁移 Codex 工作内容；pack/迁移 导出，deploy/部署 识别迁移包并恢复。")
    parser.add_argument("--version", action="version", version=VERSION)
    subparsers = parser.add_subparsers(dest="command")

    export = subparsers.add_parser("export", aliases=["pack", "迁移", "打包"])
    export.add_argument("--project", action="append", default=[])
    export.add_argument("--projects-file", type=Path)
    export.add_argument("--resource", action="append", default=[])
    export.add_argument("--include-codex-home", type=Path)
    export.add_argument("--agents-home", type=Path)
    export.add_argument("--codex-context", type=Path)
    export.add_argument("--output", type=Path)
    export.add_argument("--compression", choices=("auto", "stored", "deflate"), default="auto")
    export.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    export.add_argument("--chunk-mib", type=int, default=8)
    export.add_argument("--resume", action="store_true")
    export.add_argument("--base-package", type=Path)
    export.add_argument("--encrypt", action="store_true")
    export.add_argument("--key-file", type=Path)
    export.add_argument("--plugin-identity", default="codex-project-handoff@personal")
    export.add_argument("--dry-run", action="store_true")
    export.add_argument("--force", action="store_true")
    export.set_defaults(handler=export_capsule)

    for name, handler in (("inspect", inspect_capsule), ("verify", verify_command)):
        command = subparsers.add_parser(name)
        command.add_argument("package", nargs="?", type=Path)
        command.add_argument("--key-file", type=Path)
        command.set_defaults(handler=handler)

    restore = subparsers.add_parser("restore")
    restore.add_argument("package", nargs="?", type=Path)
    restore.add_argument("--map-root", action="append", default=[])
    restore.add_argument("--use-original-paths", action="store_true")
    restore.add_argument("--report-dir", type=Path)
    restore.add_argument("--state-dir", type=Path)
    restore.add_argument("--key-file", type=Path)
    restore.add_argument("--dry-run", action="store_true")
    restore.add_argument("--resume", action="store_true")
    restore.add_argument("--remap-links", action="store_true")
    restore.add_argument("--allow-existing-identical", action="store_true")
    restore.add_argument("--replace-existing", action="store_true")
    restore.set_defaults(handler=restore_capsule)

    deploy = subparsers.add_parser("deploy", aliases=["部署", "恢复"])
    deploy.add_argument("package", nargs="?", type=Path)
    deploy.add_argument("--search-dir", action="append", type=Path)
    deploy.add_argument("--search-depth", type=int, default=2)
    deploy.add_argument("--destination", type=Path)
    deploy.add_argument("--folder-name")
    deploy.add_argument("--key-file", type=Path)
    deploy.add_argument("--dry-run", action="store_true")
    deploy.add_argument("--resume", action="store_true")
    deploy.set_defaults(handler=deploy_capsule)

    discover = subparsers.add_parser("discover")
    discover.add_argument("--search-dir", action="append", type=Path)
    discover.add_argument("--search-depth", type=int, default=2)
    discover.set_defaults(handler=discover_command)
    drives = subparsers.add_parser("drives")
    drives.set_defaults(handler=drives_command)
    inventory = subparsers.add_parser("inventory")
    inventory.add_argument("--project", action="append", default=[])
    inventory.add_argument("--projects-file", type=Path)
    inventory.add_argument("--codex-home", type=Path)
    inventory.add_argument("--agents-home", type=Path)
    inventory.add_argument("--codex-context", type=Path)
    inventory.set_defaults(handler=inventory_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(argv if argv is not None else sys.argv[1:])
    if not arguments and self_capsule_path() is not None:
        arguments = ["inspect"]
    parser = build_parser()
    args = parser.parse_args(arguments)
    if not getattr(args, "command", None):
        parser.print_help()
        return 2
    try:
        if hasattr(args, "workers") and not 1 <= args.workers <= 16:
            raise MigrationError("workers must be between 1 and 16")
        if hasattr(args, "chunk_mib") and not 1 <= args.chunk_mib <= 32:
            raise MigrationError("chunk-mib must be between 1 and 32")
        if hasattr(args, "search_depth") and not 0 <= args.search_depth <= 4:
            raise MigrationError("search-depth must be between 0 and 4")
        return int(args.handler(args))
    except MigrationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("ERROR: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
