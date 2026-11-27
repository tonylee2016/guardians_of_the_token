"""Shared helpers for the plugin's bootstrap and launcher scripts.

The plugin needs the ``guardians_of_the_token`` package. It may already be
importable from the ``python3`` that runs the hooks (the user ran
``pip install guardians-of-the-token``); otherwise the plugin installs it into
a private venv so it never touches a system or Homebrew Python (PEP 668) and
never depends on console scripts being on PATH.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PACKAGE = "guardians-of-the-token"
IMPORT_NAME = "guardians_of_the_token"


def venv_dir() -> Path:
    override = os.environ.get("GUARDIANS_PLUGIN_VENV")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".guardians" / "plugin-venv"


def venv_python() -> Path:
    if sys.platform == "win32":
        return venv_dir() / "Scripts" / "python.exe"
    return venv_dir() / "bin" / "python"


def can_import(python: str) -> bool:
    try:
        result = subprocess.run(
            [python, "-c", f"import {IMPORT_NAME}"],
            capture_output=True,
            timeout=30,
        )
    except Exception:
        return False
    return result.returncode == 0


def find_python() -> str | None:
    """Return an interpreter that can import the package, preferring the current one."""
    try:
        import importlib.util

        if importlib.util.find_spec(IMPORT_NAME) is not None:
            return sys.executable
    except Exception:
        pass
    vpy = venv_python()
    if vpy.exists() and can_import(str(vpy)):
        return str(vpy)
    return None
