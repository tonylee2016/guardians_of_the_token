#!/usr/bin/env python3
"""Claude Code PreCompact hook — snapshot project_state before compaction.

PreCompact fires right before Claude Code compacts the conversation (manual
``/compact`` or automatic at the context limit), which is the canonical
"approaching the limit" moment. We capture a compact, LLM-free snapshot of
what the session was working on so a later cold session can be offered a
resume. Fail-open: any error just exits 0 so compaction is never disrupted.
"""

from __future__ import annotations

import json
import os
import sys

from guardians_of_the_token.config import load_config
from guardians_of_the_token.events import event_base_dir, log_event
from guardians_of_the_token.state import capture
from guardians_of_the_token.transcript import effective_context_window, read_signals


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    transcript_path = str(payload.get("transcript_path") or "")
    cwd = payload.get("cwd") or os.getcwd()
    trigger = str(payload.get("trigger") or "auto")
    config = load_config(cwd)

    settings = dict(config.get("project_state") or {})
    if not settings.get("enabled", True):
        sys.exit(0)

    base_dir = event_base_dir(config, cwd)

    live_tokens = None
    context_pct = None
    try:
        signals = read_signals(transcript_path)
        live_tokens = signals.live_tokens
        if live_tokens is not None:
            window = effective_context_window(
                int(settings.get("context_window_tokens") or 200_000), live_tokens
            )
            context_pct = live_tokens / window if window else None
    except Exception:
        pass

    saved = None
    try:
        saved = capture(
            transcript_path,
            base_dir=base_dir,
            trigger=f"precompact-{trigger}",
            live_tokens=live_tokens,
            context_pct=context_pct,
            config=config,
        )
    except Exception:
        saved = None

    log_event(
        {
            "client": "claude",
            "kind": "project_state",
            "action": "saved" if saved else "skipped",
            "reason": f"precompact-{trigger}",
            "live_tokens": live_tokens,
        },
        config=config,
        base_dir=cwd,
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
