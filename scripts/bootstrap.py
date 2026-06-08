#!/usr/bin/env python3
"""Ensure the guardians-of-the-token PyPI package is installed.

This runs as the first SessionStart hook so the plugin works for users who
installed it from the marketplace without first running
``pip install guardians-of-the-token``. The plugin's hooks and MCP server all
call into the ``guardians_of_the_token`` package, so it must be importable.

Fail-open: if installation can't happen (no network, externally managed
environment, etc.) we emit a one-line hint and exit 0 so the session still
starts. The other hooks degrade gracefully when the package is missing.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys

PACKAGE = "guardians-of-the-token"
IMPORT_NAME = "guardians_of_the_token"


def _installed() -> bool:
    return importlib.util.find_spec(IMPORT_NAME) is not None


def _pip_install() -> bool:
    """Try a normal install, then fall back to --user. Best-effort."""
    base = [sys.executable, "-m", "pip", "install", "--quiet"]
    for extra in ([], ["--user"]):
        try:
            subprocess.run(
                base + extra + [PACKAGE],
                check=True,
                capture_output=True,
                timeout=300,
            )
            return True
        except Exception:
            continue
    return False


def main() -> int:
    if _installed():
        return 0
    if _pip_install() and _installed():
        sys.stderr.write(f"Installed {PACKAGE} for the Guardians of the Token plugin.\n")
        return 0
    sys.stderr.write(
        f"Guardians of the Token: could not auto-install {PACKAGE}. "
        f"Run `pip install {PACKAGE}` (or `pipx install {PACKAGE}`) to enable the guards.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
