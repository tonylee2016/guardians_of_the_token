#!/usr/bin/env python3
"""
Codex PreToolUse hook for Guardians of the Token.

Codex CLI exposes shell execution as Bash, so this hook guards risky file-load
commands and URL fetch commands before they run.
"""

import json
import os
import shlex
import sys

from guardians_of_the_token.estimate import estimate_file, estimate_url, url_head_metadata
from guardians_of_the_token.messages import agent_feedback, format_context_block

BYPASS_FILE = "/tmp/guardians_bypass"
CONFIG_FILE = os.path.expanduser("~/.guardians.json")

DEFAULT_WARN_PCT = 20
COMPACT_PCT = 90
FALLBACK_CONTEXT_WINDOW = 400_000
TRANSCRIPT_TAIL = 32_000

MODEL_CONTEXT_WINDOWS = {
    "gpt-5": 400_000,
    "gpt-5.1": 400_000,
    "gpt-5.2": 400_000,
    "gpt-5.3": 400_000,
    "gpt-5.4": 400_000,
    "gpt-5.5": 400_000,
    "gpt-5-codex": 400_000,
    "o3": 200_000,
    "o1": 200_000,
    "claude-": 200_000,
}

FULL_FILE_COMMANDS = {"cat", "bat", "batcat", "nl", "more", "less"}
FETCH_COMMANDS = {"curl", "wget", "fetch"}


def load_config() -> dict:
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def read_tail(path: str, n: int) -> str:
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - n))
            return f.read().decode("utf-8", errors="ignore")
    except Exception:
        return ""


def detect_model_and_usage(transcript_path: str) -> tuple:
    if not transcript_path or not os.path.exists(transcript_path):
        return "", 0

    model = ""
    used_tokens = 0
    lines = read_tail(transcript_path, TRANSCRIPT_TAIL).splitlines()

    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        if entry.get("type") != "assistant":
            continue

        msg = entry.get("message", {})
        if isinstance(msg, dict):
            model = msg.get("model", model)
            usage = msg.get("usage", {})
            if isinstance(usage, dict):
                used_tokens = (
                    usage.get("input_tokens", 0)
                    + usage.get("cache_read_input_tokens", 0)
                    + usage.get("cache_creation_input_tokens", 0)
                    + usage.get("output_tokens", 0)
                )

    return model, used_tokens


def model_to_context_window(model: str) -> int:
    for prefix, window in MODEL_CONTEXT_WINDOWS.items():
        if model.startswith(prefix):
            return window
    return FALLBACK_CONTEXT_WINDOW


def is_bypass() -> bool:
    if os.environ.get("GUARDIANS_BYPASS"):
        return True
    if os.path.exists(BYPASS_FILE):
        os.remove(BYPASS_FILE)
        return True
    return False


def deny(reason: str, feedback: str):
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
        "systemMessage": feedback,
    }
    sys.stdout.write(json.dumps(payload))
    sys.exit(0)


def resolve_candidate_files(parts: list, cwd: str) -> list:
    if not parts:
        return []

    command = os.path.basename(parts[0])
    if command not in FULL_FILE_COMMANDS:
        return []

    files = []
    for token in parts[1:]:
        if token.startswith("-"):
            continue
        path = token if os.path.isabs(token) else os.path.join(cwd, token)
        if os.path.isfile(path):
            files.append(os.path.abspath(path))
    return files


def resolve_candidate_urls(parts: list) -> list:
    if not parts:
        return []

    command = os.path.basename(parts[0])
    if command not in FETCH_COMMANDS:
        return []

    return [
        token
        for token in parts[1:]
        if token.startswith(("http://", "https://"))
    ]


def guard_url(url: str, used_tokens: int, context_window: int, warn_tokens: int, model_label: str):
    try:
        head = url_head_metadata(url)
    except Exception:
        return
    estimate = estimate_url(
        url,
        context_window=context_window,
        warn_pct=int((warn_tokens / context_window) * 100),
        content_length=head["content_length"],
        content_type=head.get("content_type"),
    )
    estimated_tokens = estimate["estimated_tokens"]
    if estimate["risk"] in {"safe", "unknown"}:
        return

    after_pct = ((used_tokens + estimated_tokens) / context_window) * 100
    critical = after_pct >= COMPACT_PCT
    content_length = head["content_length"]
    size_kb = content_length // 1024 if content_length is not None else None

    reason = format_context_block(
        target=url,
        estimated_tokens=estimated_tokens,
        context_window=context_window,
        model_label=model_label,
        used_tokens=used_tokens,
        kind="url",
        action="fetch",
        blocked_item="command",
        size_kb=None if critical else size_kb,
        critical=critical,
    )
    feedback = agent_feedback("url")
    deny(reason, feedback)


def context_from_payload(payload: dict) -> tuple:
    config = load_config()
    warn_pct = config.get("warn_threshold_pct", DEFAULT_WARN_PCT)
    model, used_tokens = detect_model_and_usage(payload.get("transcript_path", ""))
    if not model:
        model = str(payload.get("model", "") or "")
    context_window = model_to_context_window(model)
    warn_tokens = int(context_window * warn_pct / 100)
    model_label = model or "unknown model"
    return used_tokens, context_window, warn_tokens, model_label


def guard_file(path: str, used_tokens: int, context_window: int, warn_tokens: int, model_label: str):
    estimate = estimate_file(
        path,
        context_window=context_window,
        warn_pct=int((warn_tokens / context_window) * 100),
    )
    file_tokens = estimate["estimated_tokens"]
    if file_tokens <= warn_tokens:
        return

    after_pct = ((used_tokens + file_tokens) / context_window) * 100
    critical = after_pct >= COMPACT_PCT

    reason = format_context_block(
        target=path,
        estimated_tokens=file_tokens,
        context_window=context_window,
        model_label=model_label,
        used_tokens=used_tokens,
        kind="file",
        action="read",
        blocked_item="command",
        critical=critical,
    )
    feedback = agent_feedback("file")
    deny(reason, feedback)


def main():
    if is_bypass():
        return

    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        return

    if payload.get("tool_name") != "Bash":
        return

    tool_input = payload.get("tool_input", {})
    used_tokens, context_window, warn_tokens, model_label = context_from_payload(payload)
    command = tool_input.get("command", "")
    cwd = payload.get("cwd") or os.getcwd()

    try:
        parts = shlex.split(command)
    except ValueError:
        return

    for url in resolve_candidate_urls(parts):
        guard_url(url, used_tokens, context_window, warn_tokens, model_label)

    for path in resolve_candidate_files(parts, cwd):
        guard_file(path, used_tokens, context_window, warn_tokens, model_label)


if __name__ == "__main__":
    main()
