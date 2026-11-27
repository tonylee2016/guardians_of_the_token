"""Context-window sizes by model name — the one place GOT derives them.

Every guard (Claude/Codex pre-tool hooks, the prompt guard, the pre-compact
snapshot) asks :func:`resolve_context_window` how big the window is, so the
percent thresholds mean the same thing everywhere.

Current Claude models (Opus 4.6+, Sonnet 4.6+, the 5.x family, Haiku 5.5,
Fable) have a 1,000,000-token window, so any ``claude-*`` id defaults to 1M.
Older models that still have 200K (or 100K) windows get explicit entries.
The longest matching prefix wins, so ``claude-3-5-sonnet`` resolves via
``claude-3`` rather than the generic ``claude-`` entry.

Users on unusual setups (a proxy, a custom model id, a smaller deployment)
can pin the window with ``"context_window": <tokens>`` in their GOT config.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

CLAUDE_DEFAULT_CONTEXT_WINDOW = 1_000_000
FALLBACK_CONTEXT_WINDOW = 200_000

# Known context windows by model-id prefix (tokens). Matching is
# longest-prefix-first, so specific entries override the generic ones.
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    # Claude: current models default to 1M ...
    "claude-": CLAUDE_DEFAULT_CONTEXT_WINDOW,
    # ... older models keep their smaller windows.
    "claude-2": 100_000,
    "claude-instant": 100_000,
    "claude-3": 200_000,             # claude-3-*, claude-3-5-*, claude-3-7-*
    "claude-haiku-4-5": 200_000,
    "claude-sonnet-4-5": 200_000,
    "claude-sonnet-4-0": 200_000,
    "claude-sonnet-4-2": 200_000,    # claude-sonnet-4-20250514
    "claude-opus-4-5": 200_000,
    "claude-opus-4-1": 200_000,
    "claude-opus-4-0": 200_000,
    "claude-opus-4-2": 200_000,      # claude-opus-4-20250514
    # OpenAI
    "gpt-5": 400_000,
    "o1": 200_000,
    "o3": 200_000,
    "gpt-4o": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4-32k": 32_768,
    "gpt-4": 8_192,
    "gpt-3.5": 16_385,
}

# Claude Code marks extended-context sessions with a "[1m]" suffix.
_ONE_MILLION_MARKER = "[1m]"


def _normalize(model: str) -> str:
    name = (model or "").strip().lower()
    # Bedrock / Vertex style ids: "us.anthropic.claude-...", "anthropic/claude-..."
    idx = name.find("claude-")
    if idx > 0:
        name = name[idx:]
    return name


def model_context_window(model: str, fallback: int = FALLBACK_CONTEXT_WINDOW) -> int:
    """Return the context window for ``model`` (longest prefix match)."""
    name = _normalize(model)
    if not name:
        return fallback
    if _ONE_MILLION_MARKER in name:
        return 1_000_000
    best = ""
    for prefix in MODEL_CONTEXT_WINDOWS:
        if name.startswith(prefix) and len(prefix) > len(best):
            best = prefix
    return MODEL_CONTEXT_WINDOWS[best] if best else fallback


def _positive_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def configured_override(config: Optional[Mapping[str, Any]], section: str = "") -> Optional[int]:
    """Return a user-pinned window, if any.

    The top-level ``context_window`` wins; ``<section>.context_window_tokens``
    (the older per-guard setting) is honoured when the top level is unset.
    """
    config = config or {}
    top = _positive_int(config.get("context_window"))
    if top is not None:
        return top
    if section:
        settings = config.get(section) or {}
        if isinstance(settings, Mapping):
            return _positive_int(settings.get("context_window_tokens"))
    return None


def resolve_context_window(
    model: str,
    config: Optional[Mapping[str, Any]] = None,
    *,
    section: str = "",
    fallback: int = FALLBACK_CONTEXT_WINDOW,
) -> int:
    """Context window for ``model``, honouring any config override."""
    override = configured_override(config, section)
    if override is not None:
        return override
    return model_context_window(model, fallback)
