"""Bounded transcript inspection for the prompt guard.

Reads only the head + tail of the JSONL transcript so the hook stays fast
even on long sessions. Surfaces:
  - a topic anchor (recap > compact summary > first/last user prompts)
  - the live context size in tokens, computed the same way ccstatusline
    does (input + cache_read + cache_creation from the most recent
    main-chain assistant turn). This is what Claude Code's "Ctx: 128.3k"
    indicator shows.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional

_HEAD_BYTES = 16_000
_TAIL_BYTES = 128_000  # wider tail so we capture enough recent text-only assistant turns
_ANCHOR_MAX_CHARS = 1200


@dataclass
class TranscriptSignals:
    anchor: str                 # backwards-compat: concatenated chunks
    anchor_chunks: list         # individual chunks for per-chunk similarity scoring
    anchor_source: str          # "recap" | "compact" | "conversation" | "none"
    live_tokens: Optional[int]  # input + cache_read + cache_creation; None if absent


def _read_segment(path: str, *, offset: int, size: int) -> str:
    try:
        file_size = os.path.getsize(path)
        if file_size == 0:
            return ""
        with open(path, "rb") as f:
            f.seek(max(0, offset))
            return f.read(size).decode("utf-8", errors="ignore")
    except OSError:
        return ""


def _iter_entries(text: str):
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _entry_text(entry: dict) -> str:
    content = entry.get("content")
    if isinstance(content, str):
        return content
    msg = entry.get("message", {})
    if isinstance(msg, dict):
        inner = msg.get("content")
        if isinstance(inner, str):
            return inner
        if isinstance(inner, list):
            parts = []
            for block in inner:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    parts.append(block["text"])
            return "\n".join(parts)
    return ""


# Claude Code wraps a blocked UserPromptSubmit into a user entry whose
# content starts with this marker. We must skip these when building the
# anchor — they were rejected from the conversation and including them
# would bias the topic toward unrelated content.
_BLOCKED_PROMPT_MARKER = "⏺ UserPromptSubmit operation blocked by hook"

# Local-command notifications (Claude Code wraps `!command`, `/exit`, `/effort`
# etc. into a user entry with this caveat wrapper). They aren't real user
# prompts and shouldn't seed the anchor.
_LOCAL_COMMAND_MARKER = "<local-command-caveat>"


_SYNTHETIC_PREFIXES = (
    _BLOCKED_PROMPT_MARKER,
    _LOCAL_COMMAND_MARKER,
    "<command-name>",
    "<command-message>",
    "<command-stderr>",
    "<local-command-stdout>",
)


def _is_synthetic_user_entry(entry: dict) -> bool:
    text = _entry_text(entry).lstrip()
    if not text:
        return False
    return any(text.startswith(p) for p in _SYNTHETIC_PREFIXES)


def _is_user_prompt(entry: dict) -> bool:
    if entry.get("type") == "user":
        return True
    msg = entry.get("message", {})
    return isinstance(msg, dict) and msg.get("role") == "user"




def _is_assistant_text(entry: dict) -> bool:
    if entry.get("type") != "assistant":
        return False
    if entry.get("isSidechain") is True:
        return False
    if entry.get("isApiErrorMessage"):
        return False
    return True


_ASSISTANT_MIN_CHARS = 80  # ignore very short assistant turns (likely tool dispatch)


def _is_recap(entry: dict) -> bool:
    return entry.get("type") == "system" and entry.get("subtype") == "away_summary"


def _is_compact_summary(entry: dict) -> bool:
    return bool(entry.get("isCompactSummary"))


def _live_context_tokens(text: str) -> Optional[int]:
    """Match ccstatusline's contextLength formula.

    Sums input + cache_read + cache_creation from the latest main-chain
    assistant turn. Skips sidechain agent invocations and API-error
    messages because they reflect their own context, not the user's
    conversation.
    """
    latest: Optional[int] = None
    for entry in _iter_entries(text):
        if entry.get("isSidechain") is True:
            continue
        if entry.get("isApiErrorMessage"):
            continue
        if entry.get("type") != "assistant":
            continue
        msg = entry.get("message", {})
        usage = msg.get("usage") if isinstance(msg, dict) else None
        if not isinstance(usage, dict):
            continue
        live = (
            int(usage.get("input_tokens") or 0)
            + int(usage.get("cache_read_input_tokens") or 0)
            + int(usage.get("cache_creation_input_tokens") or 0)
        )
        if live > 0:
            latest = live
    return latest


def _truncate(text: str, limit: int = _ANCHOR_MAX_CHARS) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def read_signals(
    transcript_path: str, *, fallback_user_prompts: int = 3
) -> TranscriptSignals:
    if not transcript_path or not os.path.exists(transcript_path):
        return TranscriptSignals(anchor="", anchor_chunks=[], anchor_source="none", live_tokens=None)

    size = os.path.getsize(transcript_path)
    head = _read_segment(transcript_path, offset=0, size=_HEAD_BYTES)
    tail = (
        _read_segment(transcript_path, offset=max(0, size - _TAIL_BYTES), size=_TAIL_BYTES)
        if size > _HEAD_BYTES
        else ""
    )

    latest_recap = ""
    latest_compact = ""
    user_prompts: list[str] = []
    assistant_texts: list[str] = []
    for entry in _iter_entries(head + "\n" + tail):
        if _is_recap(entry):
            text = _entry_text(entry)
            if text:
                latest_recap = text
        elif _is_compact_summary(entry):
            text = _entry_text(entry)
            if text:
                latest_compact = text
        elif _is_user_prompt(entry):
            if _is_synthetic_user_entry(entry):
                continue
            text = _entry_text(entry).strip()
            if text:
                user_prompts.append(text)
        elif _is_assistant_text(entry):
            text = _entry_text(entry).strip()
            if len(text) >= _ASSISTANT_MIN_CHARS:
                assistant_texts.append(text)

    if latest_recap:
        chunks = [latest_recap]
        source = "recap"
    elif latest_compact:
        chunks = [latest_compact]
        source = "compact"
    elif user_prompts or assistant_texts:
        # Per-chunk anchor: first user prompt + last N user prompts + last N
        # assistant text turns, each kept SEPARATE so the prompt guard can
        # score against each and take the max. Concatenating dilutes signal.
        first = [user_prompts[0]] if user_prompts else []
        tail_users = user_prompts[-fallback_user_prompts:] if user_prompts else []
        tail_assistants = assistant_texts[-fallback_user_prompts:]
        seen = set()
        chunks = []
        for piece in (*first, *tail_users, *tail_assistants):
            piece = piece.strip()
            if piece and piece not in seen:
                chunks.append(piece)
                seen.add(piece)
        source = "conversation"
    else:
        chunks = []
        source = "none"

    chunks = [_truncate(c) for c in chunks]
    return TranscriptSignals(
        anchor=_truncate("\n".join(chunks)),
        anchor_chunks=chunks,
        anchor_source=source,
        live_tokens=_live_context_tokens(tail or head),
    )
