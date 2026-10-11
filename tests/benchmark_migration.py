"""Reproducible synthetic local benchmark, not a cross-computer transfer test."""
from __future__ import annotations
import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import platform
import tempfile
import time


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tree(root):
    result = {}
    for path in root.rglob("*"):
        if path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                while data := stream.read(1024 * 1024):
                    digest.update(data)
            result[path.relative_to(root).as_posix()] = digest.hexdigest()
        else:
            result[path.relative_to(root).as_posix()] = None
    return result


def run(module, arguments):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        code = module.main([str(item) for item in arguments])
    if code:
        raise RuntimeError(f"benchmark command failed ({code}): {arguments[0]}")


def measure(module, project, output, modern):
    package = output / ("new.pyz" if modern else "old.pyz")
    started = time.perf_counter()
    run(module, ["export", "--project", project, "--output", package])
    if not modern:
        run(module, ["verify", package])
    export_time = time.perf_counter() - started
    started = time.perf_counter()
    if modern:
        arguments = ["deploy", package, "--destination", output, "--folder-name", "restored-new"]
        restored = output / "restored-new" / "项目" / project.name
    else:
        destination = output / "restored-old" / project.name
        arguments = ["restore", package, "--map-root", str(project) + "=" + str(destination), "--report-dir", output / "old-report"]
        restored = destination
    run(module, [*arguments, "--dry-run"])
    run(module, arguments)
    restore_time = time.perf_counter() - started
    if tree(project) != tree(restored):
        raise RuntimeError("independent restored-tree comparison failed")
    return {"exportAndVerifySeconds": round(export_time, 3), "previewAndRestoreSeconds": round(restore_time, 3), "localTotalSeconds": round(export_time + restore_time, 3), "packageBytes": package.stat().st_size, "independentTreeVerified": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-script", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--files", type=int, default=1500)
    parser.add_argument("--binary-mib", type=int, default=64)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists")
    script = Path(__file__).parents[1] / "plugins/codex-project-handoff/skills/codex-project-handoff/scripts/codex_migrate.py"
    old, new = load("legacy_migration", args.legacy_script), load("optimized_migration", script)
    results = []
    with tempfile.TemporaryDirectory(prefix="codex-migration-benchmark-") as directory:
        temporary = Path(directory)
        for name in ("many-small-text-files", "duplicate-binary-assets"):
            parent = temporary / name
            project = parent / "source" / "project"
            project.mkdir(parents=True)
            (project / "empty").mkdir()
            if name == "many-small-text-files":
                for index in range(args.files):
                    (project / f"asset-{index:05d}.txt").write_bytes((f"asset={index}\n".encode() + b"working state, source code and notes\n") * 100)
            else:
                data = os.urandom(args.binary_mib * 1024 * 1024)
                (project / "a.mp4").write_bytes(data)
                (project / "b.mp4").write_bytes(data)
                del data
            output = parent / "output"
            output.mkdir()
            result = {"case": name, "files": sum(path.is_file() for path in project.rglob("*")), "logicalBytes": sum(path.stat().st_size for path in project.rglob("*") if path.is_file())}
            # Alternate order so the optimized engine is not always second.
            if name == "many-small-text-files":
                result["old"] = measure(old, project, output, False)
                result["new"] = measure(new, project, output, True)
            else:
                result["new"] = measure(new, project, output, True)
                result["old"] = measure(old, project, output, False)
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    record = {"python": platform.python_version(), "os": platform.platform(), "legacyVersion": old.VERSION, "newVersion": new.VERSION, "scope": "synthetic local export/verification/preview/restore on one machine, one measured run per case; no actual cross-machine transfer, no universal speed claim", "cases": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
