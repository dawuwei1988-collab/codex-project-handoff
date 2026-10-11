"""Build public plugin-only assets, inspect/verify them, and emit checksums."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import zipfile

from check_publication import ROOT, public_path_issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "release-assets")
    args = parser.parse_args()
    if not re.fullmatch(r"\d+\.\d+\.\d+-beta\.\d+", args.version):
        parser.error("only explicit beta versions are supported")
    subprocess.run([sys.executable, str(ROOT / "tests" / "check_publication.py")], check=True)
    plugin = ROOT / "plugins" / "codex-project-handoff"
    manifest = json.loads((plugin / "plugin.json").read_text(encoding="utf-8"))
    if manifest["version"] != args.version:
        parser.error("requested release version differs from source")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    package = output / f"codex-project-handoff-{args.version}-installer.pyz"
    sums = output / "SHA256SUMS.txt"
    record = output / "validation.json"
    if any(path.exists() for path in (package, sums, record)):
        parser.error("release assets already exist; refusing replacement")
    subprocess.run([sys.executable, str(ROOT / "tests" / "run_checks.py"), "--output", str(record)], check=True)
    scripts = plugin / "skills" / "codex-project-handoff" / "scripts"
    subprocess.run([sys.executable, str(scripts / "build_portable_installer.py"), "--output", str(package)], check=True)
    # Do not execute a package before verifying its type and all public payload paths.
    with zipfile.ZipFile(package) as archive:
        bundle = json.loads(archive.read("installer-manifest.json"))
        if bundle["packageType"] != "codex-plugin-portable-installer":
            raise ValueError("not a plugin installer")
        for item in bundle["plugin"]["files"]:
            name = f"plugins/codex-project-handoff/{item['path']}"
            if public_path_issues(name):
                raise ValueError("private or unexpected installer payload")
    subprocess.run([sys.executable, str(package), "inspect"], check=True)
    subprocess.run([sys.executable, str(package), "verify"], check=True)
    sums.write_text("".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
                            for path in (package, record)), encoding="utf-8")
    print(f"Public assets built and verified: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
