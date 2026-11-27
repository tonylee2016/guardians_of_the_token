#!/usr/bin/env python3
"""Run a guardians_of_the_token module with whichever Python has the package.

Usage: python3 run.py <module> [args...]

Fail-open: if the package isn't installed yet (first session, bootstrap still
running or failed), exit 0 silently so hooks never show errors or block work.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _env import find_python  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        sys.stderr.write("usage: run.py <module> [args...]\n")
        return 0
    python = find_python()
    if python is None:
        return 0
    args = [python, "-m", *sys.argv[1:]]
    if sys.platform == "win32":
        import subprocess

        return subprocess.call(args)
    os.execv(python, args)
    return 0  # unreachable


if __name__ == "__main__":
    raise SystemExit(main())
