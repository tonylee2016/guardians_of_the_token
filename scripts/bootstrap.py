#!/usr/bin/env python3
"""Ensure the guardians-of-the-token PyPI package is available to the plugin.

Runs on SessionStart so the plugin works for users who installed it from the
marketplace without first running ``pip install guardians-of-the-token``.

If the package is already importable (system Python or the plugin venv) this
is a no-op. Otherwise it creates a private venv at ``~/.guardians/plugin-venv``
(override with ``GUARDIANS_PLUGIN_VENV``) and installs the package there, which
avoids PEP 668 "externally managed environment" errors and PATH issues.

Fail-open: on any failure, print a one-line hint and exit 0.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _env import PACKAGE, find_python, venv_dir, venv_python  # noqa: E402


def _install_venv() -> bool:
    if sys.version_info < (3, 10):
        return False
    try:
        if not venv_python().exists():
            venv_dir().parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [sys.executable, "-m", "venv", str(venv_dir())],
                check=True,
                capture_output=True,
                timeout=120,
            )
        subprocess.run(
            [str(venv_python()), "-m", "pip", "install", "--quiet", "--upgrade", PACKAGE],
            check=True,
            capture_output=True,
            timeout=540,
        )
        return True
    except Exception:
        return False


def main() -> int:
    if find_python() is not None:
        return 0
    if _install_venv() and find_python() is not None:
        sys.stderr.write(f"Guardians of the Token: installed {PACKAGE} into {venv_dir()}.\n")
        return 0
    hint = f"`pipx install {PACKAGE}` or `pip install {PACKAGE}`"
    if sys.version_info < (3, 10):
        hint = f"Python 3.10+ is required (python3 is {sys.version.split()[0]}); then {hint}"
    sys.stderr.write(f"Guardians of the Token: could not auto-install {PACKAGE}. Run {hint} to enable the guards.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
