"""Run isolated checks with a concise, durable result and preserved failure detail."""
from __future__ import annotations
import argparse
import contextlib
import io
import json
from pathlib import Path
import sys
import unittest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output and args.output.exists():
        parser.error("result file already exists")
    report, noisy = io.StringIO(), io.StringIO()
    suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent))
    with contextlib.redirect_stdout(noisy), contextlib.redirect_stderr(noisy):
        result = unittest.TextTestRunner(stream=report, verbosity=1).run(suite)
    record = {"tests": result.testsRun, "passed": result.wasSuccessful(), "failures": [{"test": str(test), "detail": detail} for test, detail in result.failures], "errors": [{"test": str(test), "detail": detail} for test, detail in result.errors], "skipped": len(result.skipped), "python": sys.version.split()[0]}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report.getvalue())
    print(json.dumps({k: v for k, v in record.items() if k not in {"failures", "errors"}}))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
