#!/usr/bin/env python3
"""
PostToolUse hook — Guardians of the Token.
Reads tool result from stdin, warns if output exceeds the soft cap.
Exit 0: pass through. Exit 2: block and show warning to Claude.
"""

import json
import sys

from guardians_of_the_token.config import estimate_cost, load_config
from guardians_of_the_token.events import log_event
from guardians_of_the_token.messages import format_output_block
from guardians_of_the_token.test_support import get_test_output_tokens

SOFT_CAP = 8_000  # tokens
SKIP_TOOLS = {"guardiansOfTheToken"}  # avoid double-guarding


def count_tokens(text: str) -> int:
    return int(len(text) / 4)


def extract_text(response: object) -> str:
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        # Claude Code wraps output in {"output": "..."}
        if "output" in response:
            return str(response["output"])
        return json.dumps(response)
    if isinstance(response, list):
        return " ".join(
            item.get("text", "") if isinstance(item, dict) else str(item)
            for item in response
        )
    return str(response)


def main():
    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    tool_name = payload.get("tool_name", "")
    if tool_name in SKIP_TOOLS:
        sys.exit(0)

    response = payload.get("tool_response", {})
    text = extract_text(response)

    tokens = get_test_output_tokens()
    if tokens is None:
        tokens = count_tokens(text)

    config = load_config(payload.get("cwd"))
    soft_cap = int(config.get("max_output_tokens", SOFT_CAP))
    if tokens > soft_cap:
        cost = estimate_cost(tokens, config, str(payload.get("model", "") or ""))
        print(
            format_output_block(
                tool_name=tool_name,
                tokens=tokens,
                soft_cap=soft_cap,
                estimated_cost=cost,
            )
        )
        log_event(
            {
                "client": "claude",
                "kind": "output",
                "target": f"{tool_name} output",
                "action": "suppressed",
                "estimated_tokens": tokens,
                "estimated_cost": cost,
                "risk": "warning",
            },
            config=config,
            base_dir=payload.get("cwd"),
        )
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
