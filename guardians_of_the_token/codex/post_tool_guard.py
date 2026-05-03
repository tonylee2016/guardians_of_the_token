#!/usr/bin/env python3
"""
Codex PostToolUse hook for Guardians of the Token.

Suppresses oversized Bash output before it enters model context.
"""

import json
import sys

from guardians_of_the_token.messages import agent_feedback, format_output_block
from guardians_of_the_token.test_support import get_test_output_tokens

SOFT_CAP = 8_000


def count_tokens(text: str) -> int:
    return len(text) // 4


def extract_text(response: object) -> str:
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        if "output" in response:
            return str(response["output"])
        if "stdout" in response or "stderr" in response:
            return f"{response.get('stdout', '')}\n{response.get('stderr', '')}"
        return json.dumps(response)
    if isinstance(response, list):
        parts = []
        for item in response:
            if isinstance(item, dict):
                parts.append(str(item.get("text", item.get("output", item))))
            else:
                parts.append(str(item))
        return " ".join(parts)
    return str(response)


def main():
    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        return

    if payload.get("tool_name") != "Bash":
        return

    text = extract_text(payload.get("tool_response", {}))
    tokens = get_test_output_tokens()
    if tokens is None:
        tokens = count_tokens(text)
    if tokens <= SOFT_CAP:
        return

    reason = format_output_block(tool_name="Bash", tokens=tokens, soft_cap=SOFT_CAP)
    feedback = agent_feedback("output")
    payload = {
        "decision": "block",
        "reason": reason,
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": "Original Bash output was replaced by a guard warning because it was too large.",
        },
        "systemMessage": feedback,
    }
    sys.stdout.write(json.dumps(payload))


if __name__ == "__main__":
    main()
