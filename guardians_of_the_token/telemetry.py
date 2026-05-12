from __future__ import annotations

import json
import os
import platform
import uuid
import urllib.request
from pathlib import Path
from typing import Any, Optional

from guardians_of_the_token import __version__

TELEMETRY_ID = Path("~/.guardians/telemetry_id").expanduser()
DEFAULT_TIMEOUT = 1.0

def telemetry_enabled(config: Optional[dict[str, Any]] = None) -> bool:
    env = os.environ.get("GUARDIANS_TELEMETRY")
    if env is not None:
        return env.strip().lower() in {"1", "true", "yes", "on"}
    return bool(config and config.get("telemetry_enabled"))


def telemetry_host(config: Optional[dict[str, Any]] = None) -> str:
    return str(
        os.environ.get("GUARDIANS_TELEMETRY_HOST")
        or (config or {}).get("telemetry_host")
        or "https://us.i.posthog.com"
    ).rstrip("/")


def telemetry_api_key(config: Optional[dict[str, Any]] = None) -> str:
    return str(
        os.environ.get("GUARDIANS_TELEMETRY_API_KEY")
        or (config or {}).get("telemetry_api_key")
        or ""
    )


def anonymous_id() -> str:
    override = os.environ.get("GUARDIANS_TELEMETRY_ID")
    if override:
        return override
    try:
        if TELEMETRY_ID.exists():
            return TELEMETRY_ID.read_text().strip()
        TELEMETRY_ID.parent.mkdir(parents=True, exist_ok=True)
        value = str(uuid.uuid4())
        TELEMETRY_ID.write_text(value + "\n")
        return value
    except Exception:
        return str(uuid.uuid4())


def install_properties() -> dict[str, Any]:
    return {
        "$geoip_disable": True,
        "$ip": None,
        "got_version": __version__,
        "python_version": platform.python_version(),
        "platform": platform.system().lower(),
    }


def capture_install(*, config: Optional[dict[str, Any]] = None) -> None:
    if not telemetry_enabled(config):
        return
    api_key = telemetry_api_key(config)
    if not api_key:
        return
    payload = {
        "api_key": api_key,
        "event": "got_install",
        "distinct_id": anonymous_id(),
        "properties": install_properties(),
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{telemetry_host(config)}/capture/",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT).close()
    except Exception:
        return
