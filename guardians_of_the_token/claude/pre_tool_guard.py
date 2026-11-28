#!/usr/bin/env python3
"""
PreToolUse hook — Guardians of the Token.

Intercepts tool calls BEFORE execution:
  - Read / WebFetch: estimates the size against the context window. Over
           warn_threshold_pct (default 20%) Claude Code shows its Yes/No
           permission prompt with GOT's estimate (or blocks, when
           confirm_with_prompt is false or permissions are bypassed).
  - Bash:  pipes command through cap_output.py to cap raw output size.

Config file (~/.guardians.json) — all fields optional:
  {
    "warn_threshold_pct": 20,   # warn if file > this % of context window
    "context_window": 200000,   # optional: pin the window instead of using the model's
    "confirm_with_prompt": true # Yes/No permission prompt instead of a hard block
  }

Bypass (single-use): touch /tmp/guardians_bypass before retrying.
Test mode: set GUARDIANS_TEST_SIZE=<bytes> to fake file size.
"""

import json
import os
import shlex
import sys
from typing import Optional

from guardians_of_the_token import context_window as shared_context_window
from guardians_of_the_token.config import estimate_cost, load_config, policy_decision
from guardians_of_the_token.events import log_event
from guardians_of_the_token.estimate import estimate_file, estimate_url, url_head_metadata
from guardians_of_the_token.messages import format_context_block, format_permission_prompt
from guardians_of_the_token.pause import is_paused

BYPASS_FILE = "/tmp/guardians_bypass"
CAP_MODULE = "guardians_of_the_token.claude.cap_output"

DEFAULT_WARN_PCT = 20
COMPACT_PCT = 90           # Claude Code compacts at ~95%; warn at 90%
FALLBACK_CONTEXT_WINDOW = shared_context_window.FALLBACK_CONTEXT_WINDOW

# How many bytes to read from the end of the transcript to find model/usage
TRANSCRIPT_TAIL = 32_000


def read_tail(path: str, n: int) -> str:
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - n))
            return f.read().decode("utf-8", errors="ignore")
    except Exception:
        return ""


def detect_model_and_usage(transcript_path: str) -> tuple[str, int]:
    """Returns (model_name, current_input_tokens) from transcript tail."""
    if not transcript_path or not os.path.exists(transcript_path):
        return "", 0

    tail = read_tail(transcript_path, TRANSCRIPT_TAIL)
    lines = tail.splitlines()

    model = ""
    used_tokens = 0

    # Skip first line — may be a partial line due to tail read
    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            if entry.get("type") != "assistant":
                continue
            msg = entry.get("message", {})
            if "model" in msg:
                model = msg["model"]
            usage = msg.get("usage", {})
            if usage:
                used_tokens = (
                    usage.get("input_tokens", 0)
                    + usage.get("cache_read_input_tokens", 0)
                    + usage.get("cache_creation_input_tokens", 0)
                    + usage.get("output_tokens", 0)
                )
        except (json.JSONDecodeError, AttributeError):
            pass

    return model, used_tokens


def model_to_context_window(model: str, config: Optional[dict] = None) -> int:
    return shared_context_window.resolve_context_window(
        model, config, fallback=FALLBACK_CONTEXT_WINDOW
    )


def is_bypass() -> bool:
    if os.environ.get("GUARDIANS_BYPASS"):
        return True
    if os.path.exists(BYPASS_FILE):
        os.remove(BYPASS_FILE)
        return True
    return False


# Permission modes where Claude Code would not show the prompt, so GOT blocks instead.
NO_PROMPT_MODES = {"bypassPermissions"}


def use_permission_prompt(config: dict, permission_mode: str) -> bool:
    return bool(config.get("confirm_with_prompt", True)) and permission_mode not in NO_PROMPT_MODES


def ask_permission(reason: str, context: str):
    """Hand the call to Claude Code's own Yes/No permission prompt."""
    sys.stdout.write(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": reason,
                    "additionalContext": context,
                }
            }
        )
    )
    sys.exit(0)


def modify(tool_input: dict):
    """Rewrite the tool call. Claude Code reads only hookSpecificOutput.updatedInput;
    a top-level "tool_input" key is ignored. No permissionDecision is set, so the
    user's normal permission rules still apply to the rewritten call."""
    sys.stdout.write(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "updatedInput": tool_input,
                }
            }
        )
    )
    sys.exit(0)


def guard_read(
    tool_input: dict, transcript_path: str, config: dict, cwd: str = "", permission_mode: str = ""
):
    file_path = tool_input.get("file_path", "")
    try:
        os.path.getsize(file_path)
    except OSError:
        return
    file_config = load_config(file_path)
    decision = policy_decision(file_path, file_config)
    if decision in {"whitelisted", "ignored"}:
        return

    warn_pct = file_config.get("warn_threshold_pct", DEFAULT_WARN_PCT)
    model, used_tokens = detect_model_and_usage(transcript_path)
    context_window = model_to_context_window(model, file_config)
    model_label = model if model else "unknown model"

    estimate = estimate_file(file_path, context_window=context_window, warn_pct=warn_pct)
    file_tokens = estimate["estimated_tokens"]
    warn_tokens = int(context_window * warn_pct / 100)

    if file_tokens <= warn_tokens:
        return  # within threshold, pass through

    current_limit = tool_input.get("limit")
    if current_limit is not None and current_limit <= (warn_tokens // 10):
        return  # already capped tightly

    file_pct = (file_tokens / context_window) * 100
    used_pct = (used_tokens / context_window) * 100
    after_pct = ((used_tokens + file_tokens) / context_window) * 100
    will_compact = after_pct >= COMPACT_PCT
    cost = estimate_cost(file_tokens, file_config, model_label)

    if use_permission_prompt(file_config, permission_mode):
        log_event(
            {
                "client": "claude",
                "kind": "file",
                "target": file_path,
                "action": "asked",
                "estimated_tokens": file_tokens,
                "estimated_cost": cost,
                "risk": estimate["risk"],
            },
            config=config,
            base_dir=cwd,
        )
        ask_permission(
            *format_permission_prompt(
                target=file_path,
                estimated_tokens=file_tokens,
                context_window=context_window,
                used_tokens=used_tokens,
                kind="file",
                estimated_cost=cost,
                critical=will_compact,
            )
        )

    print(
        format_context_block(
            target=file_path,
            estimated_tokens=file_tokens,
            context_window=context_window,
            model_label=model_label,
            used_tokens=used_tokens,
            kind="file",
            action="read",
            blocked_item="request",
            estimated_cost=cost,
            critical=will_compact,
        ),
        file=sys.stderr,
    )
    log_event(
        {
            "client": "claude",
            "kind": "file",
            "target": file_path,
            "action": "blocked",
            "estimated_tokens": file_tokens,
            "estimated_cost": cost,
            "risk": estimate["risk"],
        },
        config=config,
        base_dir=cwd,
    )
    sys.exit(2)


def guard_webfetch(tool_input: dict, transcript_path: str, config: dict, permission_mode: str = ""):
    url = tool_input.get("url", "")
    if not url:
        return

    warn_pct = config.get("warn_threshold_pct", DEFAULT_WARN_PCT)
    model, used_tokens = detect_model_and_usage(transcript_path)
    context_window = model_to_context_window(model, config)
    model_label = model if model else "unknown model"
    try:
        head = url_head_metadata(url)
    except Exception:
        return  # can't determine metadata — allow through, PostToolUse handles it
    estimate = estimate_url(
        url,
        context_window=context_window,
        warn_pct=warn_pct,
        content_length=head["content_length"],
        content_type=head.get("content_type"),
    )
    estimated_tokens = estimate["estimated_tokens"]
    warn_tokens = int(context_window * warn_pct / 100)

    if estimate["risk"] in {"safe", "unknown"}:
        return

    after_pct = ((used_tokens + estimated_tokens) / context_window) * 100
    will_compact = after_pct >= COMPACT_PCT
    content_length = head["content_length"]
    size_kb = content_length // 1024 if content_length is not None else None
    cost = estimate_cost(estimated_tokens, config, model_label)

    if use_permission_prompt(config, permission_mode):
        log_event(
            {
                "client": "claude",
                "kind": "url",
                "target": url,
                "action": "asked",
                "estimated_tokens": estimated_tokens,
                "estimated_cost": cost,
                "risk": estimate["risk"],
            },
            config=config,
        )
        ask_permission(
            *format_permission_prompt(
                target=url,
                estimated_tokens=estimated_tokens,
                context_window=context_window,
                used_tokens=used_tokens,
                kind="url",
                estimated_cost=cost,
                critical=will_compact,
            )
        )

    print(
        format_context_block(
            target=url,
            estimated_tokens=estimated_tokens,
            context_window=context_window,
            model_label=model_label,
            used_tokens=used_tokens,
            kind="url",
            action="fetch",
            blocked_item="request",
            size_kb=None if will_compact else size_kb,
            estimated_cost=cost,
            critical=will_compact,
        ),
        file=sys.stderr,
    )
    log_event(
        {
            "client": "claude",
            "kind": "url",
            "target": url,
            "action": "blocked",
            "estimated_tokens": estimated_tokens,
            "estimated_cost": cost,
            "risk": estimate["risk"],
        },
        config=config,
    )
    sys.exit(2)


def wrap_command(command: str) -> str:
    """Pipe a command's output through cap_output and keep its exit code.

    The exit code is sent as a final marker line that cap_output strips and
    exits with, so the pipeline reports the command's status, not cap_output's.
    The newline before ")" keeps a trailing "# comment" from swallowing it.
    It uses this hook's own Python, which has the package: a plugin install
    keeps it in a private venv that a bare "python3" would not see.
    """
    python = shlex.quote(sys.executable or "python3")
    return (
        f"{{ ({command}\n) 2>&1; printf '\\036GOT_EXIT=%s\\n' \"$?\"; }}"
        f" | {python} -m {CAP_MODULE}"
    )


def guard_bash(tool_input: dict):
    command = tool_input.get("command", "")
    if CAP_MODULE in command or "cap_output.py" in command:
        return
    wrapped = wrap_command(command)
    new_input = dict(tool_input)
    new_input["command"] = wrapped
    modify(new_input)


def main():
    if is_paused():
        sys.exit(0)

    if is_bypass():
        sys.exit(0)

    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})
    transcript_path = payload.get("transcript_path", "")
    cwd = payload.get("cwd") or os.getcwd()
    config = load_config(cwd)
    permission_mode = payload.get("permission_mode", "")

    if tool_name == "Read":
        guard_read(tool_input, transcript_path, config, cwd=cwd, permission_mode=permission_mode)
    elif tool_name == "Bash":
        guard_bash(tool_input)
    elif tool_name == "WebFetch":
        guard_webfetch(tool_input, transcript_path, config, permission_mode=permission_mode)

    sys.exit(0)


if __name__ == "__main__":
    main()
