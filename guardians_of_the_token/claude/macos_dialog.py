#!/usr/bin/env python3
"""
macOS native dialog for Guardians of the Token.

NOT used in production — kept as an alternative to the chat-reply approach.
Can be swapped in by calling prompt_user() from pre_tool_guard.py.

Requires macOS (osascript). Shows a native dialog with:
  - Yes (full file)
  - First N lines  → prompts for N
  - Cancel

Returns 'ok' or 'cancel'. Modifies tool_input["limit"] for "First N lines".
Writes /tmp/guardians_bypass for "Yes".

Usage (in pre_tool_guard.py):
    from guardians_of_the_token.claude.macos_dialog import macos_prompt
    choice = macos_prompt(header, tool_input)
"""

import json
import os
import subprocess
import sys

BYPASS_FILE = "/tmp/guardians_bypass"


def macos_prompt(message: str, tool_input: dict) -> str:
    """Show a macOS dialog. Returns 'ok' or 'cancel'."""
    lines = [l.strip() for l in message.splitlines() if l.strip()]
    summary = "\n".join(
        l for l in lines
        if any(kw in l for kw in ["would add", "will trigger", "Current context", "After reading", "This file"])
        and not l.startswith("[")
    ) or "This file may consume significant context."
    summary = summary.replace("**", "").replace("`", "").replace("🚨 CRITICAL — ", "").replace("⚠️ ", "")

    title = "Guardians of the Token"

    def run(script: str):
        return subprocess.run(["osascript", "-e", script], capture_output=True, text=True)

    result = run(
        f'display dialog "{summary}" '
        f'with title "{title}" '
        f'buttons {{"Cancel", "First N lines", "Yes (full file)"}} '
        f'default button "Cancel" with icon caution'
    )
    if result.returncode != 0 or "button returned:Cancel" in result.stdout:
        return "cancel"

    if "First N lines" in result.stdout:
        n_result = run(
            f'display dialog "How many lines to read?" '
            f'default answer "100" '
            f'with title "{title}" '
            f'buttons {{"Cancel", "Read"}} default button "Read"'
        )
        if n_result.returncode != 0 or "Cancel" in n_result.stdout:
            return "cancel"
        try:
            n = int(n_result.stdout.split("text returned:")[-1].strip().split(",")[0])
        except (ValueError, IndexError):
            n = 100
        tool_input["limit"] = n
        sys.stdout.write(json.dumps({"tool_input": tool_input}))
        return "ok"

    open(BYPASS_FILE, "w").close()
    return "ok"
