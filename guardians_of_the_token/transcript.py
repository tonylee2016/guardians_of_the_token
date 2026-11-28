"""Bounded transcript inspection for the prompt guard.

Streams the JSONL transcript once, parsing only the lines that can hold a
user prompt or a summary, and reads a bounded tail for recent replies and
usage. Surfaces:
  - a topic anchor: the latest recap and compact summary, the first prompt,
    the user's recent substantive prompts from the whole session, and the
    last few assistant replies, kept as separate chunks
  - the live context size in tokens, computed the same way ccstatusline
    does (input + cache_read + cache_creation from the most recent
    main-chain assistant turn). This is what Claude Code's "Ctx: 128.3k"
    indicator shows.
"""

from __future__ import annotations

import json
import os
from collections import deque
from dataclasses import dataclass
from typing import Optional

_HEAD_BYTES = 16_000
_TAIL_BYTES = 128_000  # wider tail so we capture enough recent text-only assistant turns
_ANCHOR_MAX_CHARS = 1200
_ANCHOR_PROMPTS = 30      # recent user prompts kept from the whole session
_ANCHOR_MIN_WORDS = 4     # shorter prompts ("yes", "fix it") carry no topic

# Snap a pressure denominator up to the smallest known Claude context window
# that contains the observed live_tokens. The window normally comes from the
# model (see context_window.py); this is a safety net for an unknown model or
# a stale override, so context_pct never exceeds 1.0. Shared by the prompt
# guard and the project-state capture so both report the same percentage.
KNOWN_CONTEXT_WINDOWS = (200_000, 1_000_000)


def effective_context_window(configured: int, live_tokens: int) -> int:
    target = max(int(configured), int(live_tokens))
    for window in KNOWN_CONTEXT_WINDOWS:
        if window >= target:
            return window
    return target


@dataclass
class TranscriptSignals:
    anchor: str                 # backwards-compat: concatenated chunks
    anchor_chunks: list         # individual chunks for per-chunk similarity scoring
    anchor_source: str          # "recap" | "compact" | "conversation" | "none"
    live_tokens: Optional[int]  # input + cache_read + cache_creation; None if absent
    model: str = ""             # model id of the latest main-chain assistant turn


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
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            yield entry


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
    "[Request interrupted",
)


def _is_synthetic_user_entry(entry: dict) -> bool:
    text = _entry_text(entry).lstrip()
    if not text:
        return False
    return any(text.startswith(p) for p in _SYNTHETIC_PREFIXES)


def _is_user_prompt(entry: dict) -> bool:
    if entry.get("isMeta") or entry.get("isCompactSummary"):
        return False  # injected context (skills, reminders), not typed by the user
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


def _latest_model(text: str) -> str:
    """Model id of the latest main-chain assistant turn ("" if none)."""
    latest = ""
    for entry in _iter_entries(text):
        if entry.get("isSidechain") is True or entry.get("isApiErrorMessage"):
            continue
        if entry.get("type") != "assistant":
            continue
        msg = entry.get("message", {})
        model = msg.get("model") if isinstance(msg, dict) else None
        if isinstance(model, str) and model and model != "<synthetic>":
            latest = model
    return latest


def _truncate(text: str, limit: int = _ANCHOR_MAX_CHARS) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _scan_session(path: str):
    """One streaming pass over the whole transcript.

    Returns (first_prompt, recent_prompts, latest_recap, latest_compact).
    Only lines that can hold a user prompt or a summary are JSON-parsed;
    tool results (the bulk of a long session) are skipped by a byte check.
    """
    first = ""
    recent: deque = deque(maxlen=_ANCHOR_PROMPTS)
    recap = compact = ""
    try:
        f = open(path, "rb")
    except OSError:
        return first, [], recap, compact
    with f:
        for raw in f:
            is_summary = b"away_summary" in raw or b"isCompactSummary" in raw
            if not is_summary and (b'"user"' not in raw or b'"tool_result"' in raw):
                continue
            try:
                entry = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(entry, dict):
                continue
            if _is_recap(entry):
                recap = _entry_text(entry) or recap
            elif _is_compact_summary(entry):
                compact = _entry_text(entry) or compact
            elif _is_user_prompt(entry) and not _is_synthetic_user_entry(entry):
                text = _entry_text(entry).strip()
                if not text:
                    continue
                if not first:
                    first = text
                elif len(text.split()) >= _ANCHOR_MIN_WORDS and text not in recent:
                    recent.append(text)
    return first, list(recent), recap, compact


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
    recent_text = tail or head

    assistant_texts: list[str] = []
    for entry in _iter_entries(recent_text):
        if _is_assistant_text(entry):
            text = _entry_text(entry).strip()
            if len(text) >= _ASSISTANT_MIN_CHARS:
                assistant_texts.append(text)

    first, prompts, recap, compact = _scan_session(transcript_path)
    tail_assistants = assistant_texts[-fallback_user_prompts:] if fallback_user_prompts > 0 else []

    # Each piece stays a separate chunk; the guard scores the prompt against
    # their centroid, so no single chunk (or lucky overlap) decides alone.
    chunks: list[str] = []
    for piece in (recap, compact, first, *prompts, *tail_assistants):
        piece = _truncate(piece) if piece else ""
        if piece and piece not in chunks:
            chunks.append(piece)

    if recap:
        source = "recap"
    elif compact:
        source = "compact"
    elif chunks:
        source = "conversation"
    else:
        source = "none"

    return TranscriptSignals(
        anchor=_truncate("\n".join(chunks)),
        anchor_chunks=chunks,
        anchor_source=source,
        live_tokens=_live_context_tokens(recent_text),
        model=_latest_model(recent_text),
    )
