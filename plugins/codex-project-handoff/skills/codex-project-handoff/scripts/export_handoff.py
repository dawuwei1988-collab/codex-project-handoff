#!/usr/bin/env python3
"""Backward-compatible launcher for the complete migration tool."""

from __future__ import annotations

import sys

from codex_migrate import main


if __name__ == "__main__":
    arguments = sys.argv[1:]
    if arguments and arguments[0].startswith("-"):
        arguments = ["export", *arguments]
    raise SystemExit(main(arguments))
