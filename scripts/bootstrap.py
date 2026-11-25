#!/usr/bin/env python3
"""Make sure the guardians-of-the-token package is available to the plugin.

Runs on SessionStart. If the package is importable (the user's own install, or
the plugin venv at the plugin's version) this exits silently.

Otherwise it starts a background install into a private venv at
``~/.guardians/plugin-venv`` (override with ``GUARDIANS_PLUGIN_VENV``), pinned
to the plugin's version, and shows the user a one-line notice. The session is
never held up: the guards stay inactive (fail-open) until the install finishes.
A private venv avoids PEP 668 "externally managed environment" errors and
PATH problems with console scripts.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _env import PACKAGE, find_python, venv_dir, venv_python  # noqa: E402

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
LOCK_STALE_SECONDS = 15 * 60
RETRY_AFTER_FAILURE_SECONDS = 6 * 60 * 60


def plugin_version() -> str | None:
    try:
        return json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text())["version"]
    except Exception:
        return None


def _state_dir() -> Path:
    return venv_dir().parent


def _lock_file() -> Path:
    return _state_dir() / "plugin-install.lock"


def _status_file() -> Path:
    return _state_dir() / "plugin-install.json"


def _log_file() -> Path:
    return _state_dir() / "plugin-install.log"


def venv_package_version() -> str | None:
    """Installed package version in the plugin venv, from its dist-info folder."""
    for info in venv_dir().glob("lib/python*/site-packages/guardians_of_the_token-*.dist-info"):
        return info.name[len("guardians_of_the_token-") : -len(".dist-info")]
    for info in venv_dir().glob("Lib/site-packages/guardians_of_the_token-*.dist-info"):
        return info.name[len("guardians_of_the_token-") : -len(".dist-info")]
    return None


def _requirement() -> str:
    version = plugin_version()
    return f"{PACKAGE}=={version}" if version else PACKAGE


def _notify(message: str) -> None:
    # systemMessage is shown to the user in the Claude Code UI.
    print(json.dumps({"systemMessage": f"🛡️ Guardians of the Token: {message}"}))


def _read_status() -> dict:
    try:
        return json.loads(_status_file().read_text())
    except Exception:
        return {}


def _write_status(status: str) -> None:
    try:
        _status_file().write_text(json.dumps({"status": status, "at": time.time(), "requirement": _requirement()}))
    except Exception:
        pass


def worker() -> int:
    """Background install. Writes a status file the next session reads."""
    ok = False
    try:
        if not venv_python().exists():
            subprocess.run([sys.executable, "-m", "venv", str(venv_dir())], check=True, timeout=180)
        subprocess.run(
            [str(venv_python()), "-m", "pip", "install", "--quiet", "--disable-pip-version-check", _requirement()],
            check=True,
            timeout=900,
        )
        # The prompt guard's embedding model isn't on PyPI; fetch it once (~22 MB).
        subprocess.run(
            [str(venv_python()), "-m", "guardians_of_the_token.embeddings"],
            check=False,
            timeout=300,
        )
        ok = True
    except Exception as exc:  # noqa: BLE001 - logged for the user
        print(f"install failed: {exc}", file=sys.stderr)
    _write_status("ok" if ok else "failed")
    try:
        _lock_file().unlink()
    except Exception:
        pass
    return 0


def _install_running() -> bool:
    try:
        return time.time() - _lock_file().stat().st_mtime < LOCK_STALE_SECONDS
    except OSError:
        return False


def _start_worker() -> bool:
    try:
        _state_dir().mkdir(parents=True, exist_ok=True)
        _lock_file().write_text(str(time.time()))
        log = open(_log_file(), "w")
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--worker"],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        return True
    except Exception:
        return False


def _manual_hint() -> str:
    return f"Run `pipx install {PACKAGE}` (or `pip install {PACKAGE}`) to enable the guards."


def main() -> int:
    python = find_python()
    wanted = plugin_version()
    if python is not None:
        using_venv = Path(python) == venv_python()
        if not using_venv or wanted is None or venv_package_version() == wanted:
            return 0  # ready; stay silent
        verb = "updating"
    else:
        verb = "installing"

    if sys.version_info < (3, 10):
        if python is None:
            _notify(f"needs Python 3.10+ (python3 is {sys.version.split()[0]}). {_manual_hint()}")
        return 0

    if _install_running():
        _notify(f"still {verb} in the background; guards turn on when it finishes.")
        return 0

    status = _read_status()
    if (
        python is None
        and status.get("status") == "failed"
        and status.get("requirement") == _requirement()
        and time.time() - float(status.get("at") or 0) < RETRY_AFTER_FAILURE_SECONDS
    ):
        _notify(f"automatic install failed (log: {_log_file()}). {_manual_hint()}")
        return 0

    if _start_worker():
        if python is None:
            _notify(
                f"{verb} {_requirement()} in the background (about a minute). "
                "Guards turn on automatically when it finishes."
            )
        return 0  # updates are silent; the current version keeps working meanwhile
    if python is None:
        _notify(f"could not start the automatic install. {_manual_hint()}")
    return 0


if __name__ == "__main__":
    if "--worker" in sys.argv[1:]:
        raise SystemExit(worker())
    raise SystemExit(main())
