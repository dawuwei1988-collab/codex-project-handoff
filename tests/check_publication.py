"""Read-only guard for public source files; never print matching secret values."""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {".gitignore", ".gitattributes", "README.md", "README.zh-CN.md", "LICENSE",
              "CONTRIBUTING.md", "SECURITY.md", "CHANGELOG.md", "MANUAL-TEST.zh-CN.md"}
PREFIXES = (".github/", "docs/", "tests/", "plugins/codex-project-handoff/")
SUFFIXES = {".py", ".md", ".json", ".yaml", ".yml"}
PRIVATE_NAMES = {"codex-context.json", "context.json", "projects.json", "auth.json",
                 "credentials.json", "restore-context.md", "restore-report.json",
                 "environment-check.json"}
SECRET_PATTERNS = {
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "github-token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    "openai-key": re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{30,}\b"),
    "credential-literal": re.compile(r'''(?im)["']?(?:api[_-]?key|access[_-]?token|password|secret)["']?\s*[:=]\s*["']([A-Za-z0-9_+/=-]{24,})["']'''),
    "personal-path": re.compile(r"(?i)[A-Z]:[\\/]+Users[\\/]+(?!<|public\b|default\b)[A-Za-z0-9_.-]+[\\/]"),
}


def public_path_issues(name: str) -> list[str]:
    path = PurePosixPath(name)
    lower = name.lower()
    problems: list[str] = []
    if path.is_absolute() or ".." in path.parts or "\\" in name:
        problems.append("unsafe relative path")
    if (path.name.lower() in PRIVATE_NAMES or path.name.startswith(".env")
            or "recovery-key" in lower or any(part.lower().endswith((".work", ".restore-report")) for part in path.parts)
            or any(part in {"private", "release-assets", ".迁移状态"} for part in path.parts)):
        problems.append("private migration or credential artifact")
    if name not in ROOT_FILES and name != ".agents/plugins/marketplace.json":
        if not name.startswith(PREFIXES) or path.suffix.lower() not in SUFFIXES:
            problems.append("outside public source allowlist")
    return problems


def content_issues(content: str) -> list[tuple[str, int]]:
    return [(label, content.count("\n", 0, match.start()) + 1)
            for label, pattern in SECRET_PATTERNS.items() for match in pattern.finditer(content)]


def candidate_files(root: Path) -> list[str]:
    result = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                            cwd=root, capture_output=True, check=True)
    return sorted(set(item.decode("utf-8") for item in result.stdout.split(b"\0") if item))


def inspect_files(root: Path, names: list[str]) -> list[str]:
    problems: list[str] = []
    for name in names:
        problems.extend(f"{name}: {reason}" for reason in public_path_issues(name))
        path = root / name
        if path.is_symlink():
            problems.append(f"{name}: source symlink requires manual review")
            continue
        if not path.is_file():
            continue  # tracked deletion is not a public upload
        if path.stat().st_size > 2 * 1024 * 1024:
            problems.append(f"{name}: unexpectedly large source file")
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            problems.append(f"{name}: not readable UTF-8 source")
            continue
        problems.extend(f"{name}:{line}: {label} (value redacted)" for label, line in content_issues(content))
        if path.suffix == ".md":
            for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", content):
                if target.startswith(("https://", "http://", "#")):
                    continue
                relative = target.split("#", 1)[0]
                if relative and not (path.parent / relative).is_file():
                    problems.append(f"{name}: missing local documentation link: {relative}")
    return problems


def check_versions(root: Path) -> list[str]:
    plugin = root / "plugins" / "codex-project-handoff"
    portable = json.loads((plugin / "plugin.json").read_text(encoding="utf-8"))
    compatibility = json.loads((plugin / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    engine = (plugin / "skills" / "codex-project-handoff" / "scripts" / "codex_migrate.py").read_text(encoding="utf-8")
    match = re.search(r'^VERSION\s*=\s*["\']([^"\']+)', engine, re.MULTILINE)
    if not match or len({portable["version"], compatibility["version"], match.group(1)}) != 1:
        return ["plugin manifest and runtime versions differ"]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        names = candidate_files(root)
        if not names:
            raise ValueError("no public source candidates")
        problems = inspect_files(root, names) + check_versions(root)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(f"PUBLICATION CHECK FAILED: {type(exc).__name__}", file=sys.stderr)
        return 1
    for problem in problems:
        print(problem, file=sys.stderr)
    print(json.dumps({"publicFiles": len(names), "passed": not problems, "issues": len(problems)}))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
