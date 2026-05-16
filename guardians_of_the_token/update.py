from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from importlib import metadata
from pathlib import Path
from typing import Any, Optional

from guardians_of_the_token import __version__
from guardians_of_the_token.config import DEFAULT_CONFIG

PACKAGE_NAME = "guardians-of-the-token"
PYPI_JSON_URL = f"https://pypi.org/pypi/{PACKAGE_NAME}/json"
UPDATE_TIMEOUT_SECONDS = 20


@dataclass
class UpdateResult:
    status: str
    current_version: str
    latest_version: Optional[str] = None
    message: str = ""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def user_config_path() -> Path:
    return Path.home() / ".guardians.json"


def _read_user_config() -> dict[str, Any]:
    try:
        return json.loads(user_config_path().read_text())
    except Exception:
        return {}


def _write_user_config(config: dict[str, Any]) -> None:
    path = user_config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n")
    except Exception:
        return


def current_version() -> str:
    try:
        return metadata.version(PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        return __version__


def latest_pypi_version(timeout: float = 1.5) -> str:
    with urllib.request.urlopen(PYPI_JSON_URL, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return str(payload["info"]["version"])


def _version_tuple(version: str) -> tuple[int, ...]:
    parts = []
    for part in version.split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        parts.append(int(digits or 0))
    return tuple(parts)


def _same_major(current: str, latest: str) -> bool:
    current_parts = _version_tuple(current)
    latest_parts = _version_tuple(latest)
    return bool(current_parts and latest_parts and current_parts[0] == latest_parts[0])


def update_available(current: str, latest: str, *, include_major: bool = False) -> bool:
    if _version_tuple(latest) <= _version_tuple(current):
        return False
    return include_major or _same_major(current, latest)


def _parse_time(raw: object) -> Optional[datetime]:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def check_due(config: dict[str, Any], *, now: Optional[datetime] = None) -> bool:
    now = now or _now()
    interval = float(config.get("auto_update_interval_hours") or DEFAULT_CONFIG["auto_update_interval_hours"])
    last = _parse_time(config.get("last_update_check"))
    return last is None or now - last >= timedelta(hours=interval)


def _mark_checked(config: dict[str, Any], *, latest: Optional[str] = None) -> None:
    config["last_update_check"] = _now().isoformat()
    if latest:
        config["last_update_version"] = latest
    _write_user_config(config)


def run_pip_upgrade(timeout: int = UPDATE_TIMEOUT_SECONDS) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", PACKAGE_NAME],
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def maybe_auto_update(*, force: bool = False, notify_only: bool = False) -> UpdateResult:
    config = {**DEFAULT_CONFIG, **_read_user_config()}
    current = current_version()
    if not config.get("update_check_enabled", True) and not force:
        return UpdateResult("disabled", current, message="Update checks are disabled.")
    if not force and not notify_only and not config.get("auto_update_enabled", False):
        return UpdateResult("disabled", current, message="Automatic updates are disabled.")
    if not force and not check_due(config):
        return UpdateResult("skipped", current, message="Update check skipped by rate limit.")

    try:
        latest = latest_pypi_version()
    except Exception as exc:
        config["last_update_error"] = str(exc)
        _mark_checked(config)
        return UpdateResult("error", current, message=f"Update check failed: {exc}")

    include_major = bool(config.get("auto_update_include_major"))
    if not update_available(current, latest, include_major=include_major):
        _mark_checked(config, latest=latest)
        return UpdateResult("current", current, latest, "Guardians is up to date.")

    if notify_only or not config.get("auto_update_enabled", False):
        _mark_checked(config, latest=latest)
        return UpdateResult(
            "available",
            current,
            latest,
            f"Guardians update available: {current} -> {latest}. Run `guardians update`.",
        )

    config["last_update_attempt"] = _now().isoformat()
    _write_user_config(config)
    try:
        result = run_pip_upgrade()
    except Exception as exc:
        config["last_update_error"] = str(exc)
        _mark_checked(config, latest=latest)
        return UpdateResult("error", current, latest, f"Guardians auto-update failed: {exc}")

    _mark_checked(config, latest=latest)
    if result.returncode == 0:
        return UpdateResult("updated", current, latest, f"Guardians updated: {current} -> {latest}.")

    message = (result.stderr or result.stdout or "pip exited non-zero").strip().splitlines()[-1]
    config["last_update_error"] = message
    _write_user_config(config)
    return UpdateResult("error", current, latest, f"Guardians auto-update failed: {message}")


def startup_auto_update() -> UpdateResult:
    """Run a fail-open update check for agent startup hooks."""
    try:
        return maybe_auto_update()
    except Exception as exc:
        return UpdateResult("error", current_version(), message=f"Guardians auto-update failed: {exc}")


def set_auto_update(enabled: bool, *, update_check_enabled: bool = True) -> None:
    config = _read_user_config()
    config["update_check_enabled"] = update_check_enabled
    config["auto_update_enabled"] = enabled
    config.setdefault("auto_update_interval_hours", DEFAULT_CONFIG["auto_update_interval_hours"])
    config.setdefault("auto_update_include_major", DEFAULT_CONFIG["auto_update_include_major"])
    _write_user_config(config)


def main() -> None:
    result = maybe_auto_update(force=True)
    print(result.message)
    if result.status == "error":
        raise SystemExit(1)
