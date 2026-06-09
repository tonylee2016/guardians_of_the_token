"""Global pause switch for all Guardians guards.

A single pause covers every client (Claude Code, Codex, and any future CLI):
each guard's ``main()`` checks :func:`is_paused` first and exits silently while
a pause is active. The pause is stored as an expiry timestamp in a global file
so ``guardians pause 1h`` run from any project quiets the guards everywhere.

Everything here is fail-open: any error is treated as "not paused" so a broken
or unreadable pause file never blocks a guard from running.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# Matches a run of <number><unit> tokens, e.g. "1h", "30m", "1h30m", "90s".
_DURATION_TOKEN = re.compile(r"(\d+)\s*([smhd])", re.IGNORECASE)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}

_MAX_PAUSE_SECONDS = 7 * 86400  # 7 days — a safety cap so a typo can't pause forever


def pause_file() -> Path:
    """Location of the global pause marker (overridable for tests)."""
    override = os.environ.get("GUARDIANS_PAUSE_FILE")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".got" / "pause_until"


def parse_duration(text: str) -> int:
    """Parse a human duration into seconds.

    Accepts ``1h``, ``30m``, ``90s``, ``2d``, combinations like ``1h30m``, and
    a bare integer interpreted as minutes (``guardians pause 90`` -> 90 min).
    Raises ``ValueError`` on anything else.
    """
    raw = (text or "").strip().lower()
    if not raw:
        raise ValueError("empty duration")

    if raw.isdigit():
        seconds = int(raw) * 60
    else:
        matches = _DURATION_TOKEN.findall(raw)
        # Reject input with leftover junk so "1h banana" doesn't silently pass.
        if not matches or _DURATION_TOKEN.sub("", raw).strip():
            raise ValueError(f"could not parse duration: {text!r}")
        seconds = sum(int(value) * _UNIT_SECONDS[unit] for value, unit in matches)

    if seconds <= 0:
        raise ValueError("duration must be positive")
    return min(seconds, _MAX_PAUSE_SECONDS)


def format_duration(seconds: float) -> str:
    """Render a rough human duration, e.g. ``1h 30m`` or ``45s``."""
    seconds = int(round(seconds))
    if seconds <= 0:
        return "0s"
    parts = []
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        if seconds >= size:
            parts.append(f"{seconds // size}{unit}")
            seconds %= size
    return " ".join(parts[:2])  # keep it short: top two units


def set_pause(seconds: int) -> datetime:
    """Pause the guards for ``seconds`` from now. Returns the expiry time."""
    until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    path = pause_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(until.isoformat())
    return until


def clear_pause() -> bool:
    """Remove any active pause. Returns True if a pause was cleared."""
    path = pause_file()
    try:
        if path.exists():
            path.unlink()
            return True
    except OSError:
        pass
    return False


def pause_remaining() -> Optional[float]:
    """Seconds left on the active pause, or ``None`` if not paused.

    Expired pauses are cleaned up on read so the file doesn't linger.
    """
    path = pause_file()
    try:
        if not path.exists():
            return None
        until = datetime.fromisoformat(path.read_text().strip())
    except (OSError, ValueError):
        return None

    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    remaining = (until - datetime.now(timezone.utc)).total_seconds()
    if remaining <= 0:
        clear_pause()
        return None
    return remaining


def is_paused() -> bool:
    """True while a pause is active. Fail-open: any error means not paused."""
    try:
        return pause_remaining() is not None
    except Exception:
        return False
