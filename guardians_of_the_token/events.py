from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def got_dir(base_dir: Optional[str | Path] = None) -> Path:
    override = os.environ.get("GUARDIANS_INBOX")
    if override:
        return Path(override).expanduser().resolve()
    anchor = Path(base_dir).expanduser().resolve() if base_dir else Path.cwd().resolve()
    return anchor / ".got"


def events_path(base_dir: Optional[str | Path] = None) -> Path:
    return got_dir(base_dir) / "events.jsonl"


def event_base_dir(config: Optional[dict[str, Any]] = None, fallback: Optional[str | Path] = None) -> Path:
    if config and config.get("project_root"):
        return Path(str(config["project_root"]))
    if fallback:
        path = Path(fallback).expanduser().resolve()
        return path.parent if path.is_file() else path
    return Path.cwd().resolve()


def log_event(event: dict[str, Any], *, config: Optional[dict[str, Any]] = None, base_dir: Optional[str | Path] = None) -> None:
    try:
        target_dir = event_base_dir(config, base_dir)
        path = events_path(target_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "time": datetime.now(timezone.utc).isoformat(),
            **event,
        }
        with path.open("a") as f:
            f.write(json.dumps(payload, sort_keys=True) + "\n")
    except Exception:
        return


def read_events(base_dir: Optional[str | Path] = None) -> list[dict[str, Any]]:
    path = events_path(base_dir)
    if not path.exists():
        return []
    events = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events
