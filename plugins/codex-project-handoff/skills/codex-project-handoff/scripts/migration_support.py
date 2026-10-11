"""Portable discovery, indexed blocks, continuity reports and deployment entry."""
from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import ctypes
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import struct
import subprocess
import sys
import time
from urllib.parse import quote
import zipfile
import zlib

FORMAT = "codex-project-migration"
HEADER = struct.Struct("<4sBI")
MAX_BLOCK = 32 * 1024 * 1024
MEDIA_SUFFIXES = {".zip", ".7z", ".gz", ".bz2", ".xz", ".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov", ".mp3", ".ogg", ".pdf", ".pyz", ".woff2"}
RUNTIME_FILES = ("migration_support.py", "migration_crypto.py", "capture_context.py")


def atomic_json(path: Path, data: object) -> None:
    temporary = path.with_name(path.name + ".next")
    temporary.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(temporary, path)


def pack_block(data: bytes, mode: str, media: bool = False, identifier: str | None = None) -> tuple[str, bytes]:
    if len(data) > MAX_BLOCK:
        raise ValueError("block is too large")
    body, codec = data, 0
    if mode == "deflate" or (mode == "auto" and not media):
        candidate = zlib.compress(data, 1)
        if mode == "deflate" or len(candidate) + 32 < len(data) * 0.95:
            body, codec = candidate, 1
    return identifier or hashlib.sha256(data).hexdigest(), HEADER.pack(b"CDB3", codec, len(data)) + body


def unpack_block(payload: bytes, expected: str) -> bytes:
    if len(payload) < HEADER.size:
        raise ValueError("truncated block")
    magic, codec, size = HEADER.unpack(payload[:HEADER.size])
    if magic != b"CDB3" or size > MAX_BLOCK or codec not in (0, 1):
        raise ValueError("invalid block header")
    body = payload[HEADER.size:]
    if codec:
        decoder = zlib.decompressobj()
        try:
            data = decoder.decompress(body, size + 1)
        except zlib.error as exc:
            raise ValueError("compressed block is damaged") from exc
        if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError("invalid or oversized compressed block")
    else:
        data = body
    if len(data) != size or hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("block checksum mismatch")
    return data


class BlockReader:
    def __init__(self, archive: zipfile.ZipFile, budget: int = 64 * 1024 * 1024):
        self.archive = archive
        self.budget = budget
        self.cache: OrderedDict[str, bytes] = OrderedDict()
        self.used = 0

    def read(self, reference: dict) -> bytes:
        identifier = reference["id"]
        if not re.fullmatch(r"[0-9a-f]{64}", identifier):
            raise ValueError("invalid block identifier")
        if identifier in self.cache:
            data = self.cache.pop(identifier)
            self.cache[identifier] = data
        else:
            info = self.archive.getinfo("blobs/" + identifier)
            if info.file_size > MAX_BLOCK + HEADER.size:
                raise ValueError("oversized block entry")
            data = unpack_block(self.archive.read(info), identifier)
            while self.cache and self.used + len(data) > self.budget:
                _, removed = self.cache.popitem(last=False)
                self.used -= len(removed)
            self.cache[identifier] = data
            self.used += len(data)
        offset, size = int(reference["offset"]), int(reference["size"])
        if offset < 0 or size < 0 or offset + size > len(data):
            raise ValueError("block slice escapes payload")
        return data[offset:offset + size]


class BlockWriter:
    """Group small files; compress a bounded queue in native zlib workers."""
    def __init__(self, archive: zipfile.ZipFile, mode: str, workers: int, chunk_size: int, checkpoint=None, base=None):
        self.archive, self.mode, self.chunk_size = archive, mode, chunk_size
        self.checkpoint, self.base = checkpoint, base
        self.pool = ThreadPoolExecutor(max_workers=workers)
        self.pending = []
        self.limit = workers * 2
        self.small = bytearray()
        self.slices = []
        self.identifiers = {name[6:] for name in archive.namelist() if name.startswith("blobs/")}
        self.records = {}
        self.reused = 0

    def submit(self, data: bytes, slices: list, media: bool = False) -> None:
        identifier = hashlib.sha256(data).hexdigest()
        for reference in slices:
            reference["id"] = identifier
        if identifier in self.identifiers:
            self.reused += len(data)
            return
        self.identifiers.add(identifier)
        if self.base is not None and "blobs/" + identifier in self.base.NameToInfo:
            payload = self.base.read("blobs/" + identifier)
            unpack_block(payload, identifier)
            self.pending.append((identifier, None, payload))
            self.reused += len(data)
        else:
            self.pending.append((identifier, self.pool.submit(pack_block, data, self.mode, media, identifier), None))
        if len(self.pending) >= self.limit:
            self.drain_one()

    def drain_one(self) -> None:
        identifier, future, payload = self.pending.pop(0)
        if future is not None:
            actual, payload = future.result()
            if actual != identifier:
                raise ValueError("compression worker returned the wrong block")
        self.archive.writestr("blobs/" + identifier, payload, compress_type=zipfile.ZIP_STORED)
        if self.checkpoint:
            self.checkpoint(self.archive)

    def add_small(self, data: bytes, entry: dict, media: bool) -> None:
        if media:
            reference = {"id": "", "offset": 0, "size": len(data)}
            entry["chunks"] = [reference] if data else []
            if data:
                self.submit(data, [reference], True)
            return
        if self.small and len(self.small) + len(data) > self.chunk_size:
            self.flush_small()
        reference = {"id": "", "offset": len(self.small), "size": len(data)}
        entry["chunks"] = [reference] if data else []
        if data:
            self.small.extend(data)
            self.slices.append(reference)

    def flush_small(self) -> None:
        if self.small:
            self.submit(bytes(self.small), self.slices)
            self.small = bytearray()
            self.slices = []

    def finish(self) -> None:
        self.flush_small()
        while self.pending:
            self.drain_one()

    def close(self) -> None:
        self.pool.shutdown(wait=True, cancel_futures=True)


def package_summary(path: Path) -> dict:
    """Read bounded metadata only. Never execute a discovered package."""
    with zipfile.ZipFile(path) as archive:
        marker_info = archive.getinfo("capsule.json")
        if marker_info.file_size > 64 * 1024:
            raise ValueError("oversized capsule marker")
        marker = json.loads(archive.read(marker_info))
        if marker.get("format") != FORMAT or marker.get("schemaVersion") not in (2, 3):
            raise ValueError("not a supported migration capsule")
        if marker.get("encrypted"):
            summary = dict(marker.get("publicSummary", {}))
        else:
            info = archive.getinfo("manifest.json")
            if info.file_size > 256 * 1024 * 1024:
                raise ValueError("oversized manifest")
            manifest = json.loads(archive.read(info))
            if manifest.get("format") != FORMAT:
                raise ValueError("invalid manifest")
            summary = {"capsuleId": manifest.get("capsuleId"), "createdAt": manifest.get("createdAt"), "sourceComputer": manifest.get("sourceComputer", {}).get("hostname"), "projectCount": len(manifest.get("projects", [])), "payloadBytes": sum(int(r["totalBytes"]) for r in manifest.get("roots", []))}
        summary.update(path=str(path.resolve()), packageBytes=path.stat().st_size, encrypted=bool(marker.get("encrypted")))
        return summary


def discover_packages(directories: list[Path], max_depth: int = 2, max_entries: int = 5000) -> dict:
    candidates, errors, seen, visited = [], [], set(), 0
    truncated = False
    for directory in directories:
        directory = directory.expanduser().resolve()
        if not directory.is_dir():
            continue
        pending = [(directory, 0)]
        while pending and visited < max_entries:
            parent, depth = pending.pop()
            try:
                children = list(parent.iterdir())
            except OSError as exc:
                errors.append({"path": str(parent), "error": str(exc)})
                continue
            for child in sorted(children, key=lambda p: p.name.casefold()):
                visited += 1
                if visited > max_entries:
                    truncated = True
                    break
                if child.is_symlink():
                    continue
                if child.is_dir() and depth < max_depth and child.name not in {".git", "node_modules", ".venv", ".codex", "$RECYCLE.BIN"}:
                    pending.append((child, depth + 1))
                elif child.is_file() and child.suffix.lower() in {".pyz", ".zip"}:
                    key = os.path.normcase(str(child.resolve()))
                    if key in seen:
                        continue
                    seen.add(key)
                    try:
                        candidates.append(package_summary(child))
                    except (OSError, ValueError, KeyError, zipfile.BadZipFile, json.JSONDecodeError):
                        pass
        if pending:
            truncated = True
    unique = {}
    for item in candidates:
        # Old packages without a UUID remain separate unless explicitly selected.
        identifier = item.get("capsuleId") or item["path"]
        if identifier not in unique:
            unique[identifier] = item
        else:
            unique[identifier].setdefault("otherCopies", []).append(item["path"])
    result = sorted(unique.values(), key=lambda item: item.get("createdAt") or "", reverse=True)
    return {"status": "unique" if len(result) == 1 and not truncated and not errors else "choose" if result else "not-found", "candidates": result, "searchIncomplete": truncated or bool(errors), "errors": errors, "entriesVisited": visited}


def validate_folder_name(value: str) -> str:
    if not value or value in {".", ".."} or len(value) > 255 or re.search(r'[<>:"/\\|?*\x00-\x1f]', value) or value.endswith((".", " ")):
        raise ValueError("总文件夹名称无效：请使用单个文件夹名称，不能包含路径分隔符或 Windows 禁用字符。")
    if value.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}:
        raise ValueError("总文件夹名称不能使用 Windows 保留设备名。")
    return value


def enumerate_drives() -> list[dict]:
    if os.name != "nt":
        usage = shutil.disk_usage(Path.cwd())
        return [{"path": str(Path.cwd().anchor), "system": True, "freeBytes": usage.free, "totalBytes": usage.total, "fileSystem": None}]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    buffer = ctypes.create_unicode_buffer(512)
    if not kernel.GetLogicalDriveStringsW(len(buffer), buffer):
        raise OSError(ctypes.get_last_error(), "cannot enumerate drives")
    windows_directory = ctypes.create_unicode_buffer(512)
    if kernel.GetWindowsDirectoryW(windows_directory, len(windows_directory)):
        system_drive = str(Path(windows_directory.value).anchor)[:2].casefold()
    else:
        system_drive = os.environ.get("SystemDrive", str(Path(sys.executable).anchor)[:2]).casefold()
    result = []
    for root in buffer[:].split("\x00"):
        if not root:
            continue
        kind = kernel.GetDriveTypeW(ctypes.c_wchar_p(root))
        if kind not in (2, 3):
            continue
        filesystem, label = ctypes.create_unicode_buffer(64), ctypes.create_unicode_buffer(261)
        kernel.GetVolumeInformationW(ctypes.c_wchar_p(root), label, len(label), None, None, None, filesystem, len(filesystem))
        try:
            usage = shutil.disk_usage(root)
        except OSError:
            continue
        result.append({"path": root, "system": root[:2].casefold() == system_drive, "removable": kind == 2, "label": label.value, "fileSystem": filesystem.value, "freeBytes": usage.free, "totalBytes": usage.total})
    return sorted(result, key=lambda d: (d["system"], d["removable"], d["fileSystem"].upper() != "NTFS", -d["freeBytes"]))


def inventory_environment(codex_home: Path, agents_home: Path | None = None) -> list[dict]:
    items = []
    for relative in ("AGENTS.md", "config.toml", "skills", "rules", "prompts", "automations"):
        path = codex_home / relative
        if path.exists():
            items.append({"path": str(path.resolve()), "category": "environment", "name": "codex-" + relative, "activation": "staged-only"})
    personal = codex_home / "plugins" / "cache" / "personal"
    if personal.is_dir():
        items.append({"path": str(personal.resolve()), "category": "environment", "name": "personal-plugins", "activation": "staged-only"})
    if agents_home:
        for relative in ("skills", "plugins/marketplace.json"):
            path = agents_home / relative
            if path.exists():
                items.append({"path": str(path.resolve()), "category": "environment", "name": "agents-" + path.name, "activation": "staged-only"})
    for relative in ("generated_images", "visualizations", "attachments"):
        path = codex_home / relative
        if path.is_dir():
            items.append({"path": str(path.resolve()), "category": "resources", "name": "codex-" + relative})
    config_path = codex_home / "config.toml"
    if config_path.is_file():
        try:
            import tomllib
        except ImportError:
            tomllib = None
            for item in items:
                if item["path"] == str(config_path.resolve()):
                    item["discoveryWarnings"] = ["Python 3.10 did not parse local MCP dependencies; use bundled Python 3.11+ or include those directories explicitly."]
        if tomllib:
            with config_path.open("rb") as stream:
                config = tomllib.load(stream)
            def values(value):
                if isinstance(value, str):
                    yield value
                elif isinstance(value, dict):
                    for child in value.values():
                        yield from values(child)
                elif isinstance(value, list):
                    for child in value:
                        yield from values(child)
            for server, settings in config.get("mcp_servers", {}).items():
                for value in values(settings):
                    path = Path(value)
                    if path.is_absolute() and path.is_file() and path.suffix.lower() in {".py", ".js", ".mjs", ".cjs", ".ps1", ".cmd", ".bat"}:
                        directory = path.parent.resolve()
                        if directory not in {Path(directory.anchor), Path.home().resolve(), codex_home.resolve()} and not any(directory == codex_home / p or (codex_home / p) in directory.parents for p in ("sessions", "archived_sessions", "auth", "sqlite", ".sandbox-secrets")):
                            items.append({"path": str(directory), "category": "environment", "name": "mcp-" + server, "activation": "staged-only"})
    return items


def continuity_summary(context: dict | None) -> dict:
    if not context:
        return {"available": False, "complete": False, "taskCount": 0, "detailedTaskCount": 0, "limitations": ["未附带任务记录。"]}
    summaries = context.get("threadSummaries", context.get("threads", []))
    if isinstance(summaries, dict):
        summaries = summaries.get("threads", []) + summaries.get("pinnedThreads", [])
    details = context.get("detailedThreads", [])
    ids = {str(item.get("id") or item.get("threadId")) for item in summaries if isinstance(item, dict)}
    full = {str(item.get("id") or item.get("threadId") or item.get("thread", {}).get("id")) for item in details if isinstance(item, dict) and not item.get("error")}
    limitations = list(context.get("warnings", []))
    if context.get("limitation"):
        limitations.append(context["limitation"])
    coverage = context.get("coverage", {})
    complete = bool(coverage.get("complete", False))
    return {"available": True, "complete": complete, "taskCount": coverage.get("taskCount", len(ids)), "detailedTaskCount": coverage.get("detailedTaskCount", len(full & ids)), "limitations": limitations}


def runtime_inventory() -> dict:
    def probe(spec):
        name, arguments = spec
        executable = shutil.which(name)
        if executable:
            try:
                completed = subprocess.run([executable, *arguments], capture_output=True, text=True, timeout=5, encoding="utf-8", errors="replace")
                return name, {"path": executable, "version": (completed.stdout or completed.stderr).strip()[:300], "exitCode": completed.returncode}
            except (OSError, subprocess.TimeoutExpired) as exc:
                return name, {"path": executable, "error": str(exc)}
        return name, None
    with ThreadPoolExecutor(max_workers=4) as pool:
        tools = {name: value for name, value in pool.map(probe, (("git", ["--version"]), ("node", ["--version"]), ("npm", ["--version"]), ("codex", ["--version"]))) if value is not None}
    return {"python": sys.version.split()[0], "tools": tools}


def deployment_uri(package: Path, plugin_identity: str = "codex-project-handoff@personal") -> str:
    package = package.resolve()
    prompt = f"[@Codex一键迁移](plugin://{plugin_identity}) 部署。迁移包本地路径：{json.dumps(str(package), ensure_ascii=False)}。先读取清单、检查磁盘，让我选择总文件夹位置和名称，再恢复。"
    return "codex://new?path=" + quote(str(package.parent), safe="") + "&prompt=" + quote(prompt, safe="")


def write_deployment_entry(package: Path, plugin_identity: str = "codex-project-handoff@personal") -> Path:
    path = package.with_name(package.stem + "-开始部署.ps1")
    if path.exists():
        raise FileExistsError(f"部署入口已经存在：{path}")
    filename = package.name.replace("'", "''")
    identity = plugin_identity.replace("'", "''")
    script = f"""# 此入口只在用户主动运行时打开 Codex 并填好部署提示；不会自动发送或解压。
$ErrorActionPreference = 'Stop'
$migrationPackage = Join-Path -Path $PSScriptRoot -ChildPath '{filename}'
if (-not (Test-Path -LiteralPath $migrationPackage -PathType Leaf)) {{ throw '同目录迁移包未找到，请保持入口和迁移包一起搬运。' }}
$migrationPrompt = '[@Codex一键迁移](plugin://{identity}) 部署。迁移包本地路径：' + (ConvertTo-Json -InputObject $migrationPackage -Compress) + '。先检查磁盘，让我选择总文件夹位置和名称，再恢复。'
$migrationUri = 'codex://new?path=' + [Uri]::EscapeDataString($PSScriptRoot) + '&prompt=' + [Uri]::EscapeDataString($migrationPrompt)
Start-Process -FilePath $migrationUri
"""
    path.write_text(script, encoding="utf-8-sig")
    # Explorer can double-click the .cmd. Paths never appear in shell source.
    command = path.with_suffix(".cmd")
    if command.exists():
        raise FileExistsError(f"部署入口已经存在：{command}")
    command.write_text('@echo off\r\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dpn0.ps1"\r\n', encoding="ascii")
    return command


def remap_link(target: str, mappings: list[tuple[Path, Path]]) -> str:
    prefix = "\\\\?\\" if target.startswith("\\\\?\\") else ""
    plain = target[len(prefix):]
    windows = bool(PureWindowsPath(plain).drive)
    target_path = PureWindowsPath(plain) if windows else Path(plain)
    if not target_path.is_absolute():
        return target
    for old, new in sorted(mappings, key=lambda pair: len(str(pair[0])), reverse=True):
        old_path = PureWindowsPath(str(old)) if windows else old
        try:
            relative = target_path.relative_to(old_path)
        except ValueError:
            continue
        return prefix + str(new.joinpath(*relative.parts))
    return target


def prepare_environment_adaptations(plan: list[dict], output: Path) -> dict:
    """Write adapted copies, preserving original configuration bytes."""
    substitutions = [(str(item["root"]["originalPath"]), str(item["target"]) + (os.sep + Path(item["root"]["originalPath"]).name if item["root"].get("sourceType") == "file" else "")) for item in plan]
    substitutions.sort(key=lambda pair: len(pair[0]), reverse=True)
    changes = []
    for item in plan:
        root = item["root"]
        if root.get("category") != "environment":
            continue
        target = Path(item["target"])
        for entry in root["entries"]:
            if entry["type"] != "file" or entry["size"] > 5 * 1024 * 1024:
                continue
            source = target.joinpath(*Path(entry["path"]).parts)
            if source.suffix.lower() not in {".toml", ".json", ".yaml", ".yml", ".md", ".ps1", ".cmd"}:
                continue
            try:
                original = source.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            adapted = original
            for old, new in substitutions:
                adapted = adapted.replace(old.replace("\\", "\\\\"), new.replace("\\", "\\\\")).replace(old, new)
            if "automation" in str(root.get("name", "")).lower() and source.suffix == ".toml":
                adapted = re.sub(r'(?m)^(status\s*=\s*)[\"\x27]ACTIVE[\"\x27]', r'\1"PAUSED"', adapted)
            if adapted != original:
                destination = output / root["id"] / entry["path"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                encoded = adapted.encode("utf-8")
                if destination.exists():
                    if destination.read_bytes() != encoded:
                        raise FileExistsError(f"adapted configuration already exists and differs: {destination}")
                else:
                    with destination.open("xb") as stream:
                        stream.write(encoded)
                changes.append({"original": str(source), "adapted": str(destination), "originalSha256": entry["sha256"], "adaptedSha256": hashlib.sha256(destination.read_bytes()).hexdigest()})
    return {"files": changes, "originalsModified": False, "automations": "adapted copies are paused", "activation": "Review/use adapted copies with supported installation or configuration APIs. Existing global configuration is preserved."}
