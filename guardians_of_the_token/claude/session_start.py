#!/usr/bin/env python3
"""Claude Code SessionStart hook.

Two responsibilities, both fail-open:
  - run a non-interactive update check
  - on a cold start, if a project_state snapshot exists for this project,
    nudge Claude to offer the user a resume (it does not inject the snapshot)

Anything to surface is emitted once as ``additionalContext`` so we never mix
plain text and JSON on stdout.
"""

from __future__ import annotations

import json
import os
import sys

from guardians_of_the_token.update import startup_auto_update

# Sources where the conversation starts without prior context. On "resume" and
# "compact" the context is already present, so a resume offer would be noise.
_COLD_START_SOURCES = {"startup", "clear"}


def _read_payload() -> dict:
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    try:
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def _resume_piece(payload: dict) -> str:
    if str(payload.get("source") or "") not in _COLD_START_SOURCES:
        return ""
    try:
        from guardians_of_the_token.config import load_config
        from guardians_of_the_token.events import event_base_dir
        from guardians_of_the_token.state import resume_offer

        cwd = payload.get("cwd") or os.getcwd()
        config = load_config(cwd)
        return resume_offer(event_base_dir(config, cwd), config=config) or ""
    except Exception:
        return ""


def main() -> None:
    payload = _read_payload()

    pieces: list[str] = []
    result = startup_auto_update()
    if result.status == "updated" and result.message:
        pieces.append(result.message)

    resume = _resume_piece(payload)
    if resume:
        pieces.append(resume)

    if not pieces:
        return

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": "\n\n".join(pieces),
                }
            }
        )
    )


if __name__ == "__main__":
    main()
