#!/usr/bin/env python3
"""UserPromptSubmit hook — blocks prompts that look unrelated to a large session.

Conservative by design:
  - allow when context pressure is below threshold
  - allow when no topic anchor (recap, compact summary, or prior user prompts) exists
  - allow control commands and short continuations
  - allow when the prompt is prefixed with the configured unblock prefix
  - block only when similarity to the anchor is below the threshold AND
    context pressure is above the threshold

Config lives under ``[prompt_guard]`` in the user/project Guardians config.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Optional

from guardians_of_the_token.config import estimate_cost, load_config
from guardians_of_the_token.embeddings import (
    EmbeddingUnavailable,
    cosine_similarity,
    default_similarity_threshold,
    embed,
)
from guardians_of_the_token.events import log_event
from guardians_of_the_token.transcript import read_signals

CONTROL_PREFIXES = ("/clear", "/compact", "/exit", "/help")
SHORT_CONTINUATIONS = {
    # affirmation / continuation
    "yes",
    "y",
    "no",
    "n",
    "ok",
    "okay",
    "do it",
    "continue",
    "resume",
    "same topic",
    "that",
    "fix it",
    "go ahead",
    "go",
    "next",
    "proceed",
    # short positive feedback
    "good",
    "great",
    "perfect",
    "nice",
    "cool",
    "works",
    "it works",
    "good seems working",
    "seems working",
    "seems to work",
    "looks good",
    "lgtm",
    "thanks",
    "thank you",
    "ty",
    # short corrections / direction
    "stop",
    "wait",
    "undo",
    "revert",
    "retry",
    "again",
    "more",
    "less",
    "shorter",
    "longer",
}


def _settings(config: dict[str, Any]) -> dict[str, Any]:
    return dict(config.get("prompt_guard") or {})


def _setting(settings: dict[str, Any], key: str, default):
    value = settings.get(key)
    return default if value is None else value


def _is_allowlisted(prompt: str) -> bool:
    stripped = prompt.strip()
    if not stripped:
        return True
    lower = stripped.lower()
    if any(lower.startswith(p) for p in CONTROL_PREFIXES):
        return True
    if lower in SHORT_CONTINUATIONS:
        return True
    return False


def _is_unblocked(prompt: str, prefix: str) -> bool:
    return bool(prefix) and prompt.lstrip().startswith(prefix)


def _block(reason: str) -> None:
    sys.stdout.write(json.dumps({"decision": "block", "reason": reason}))
    sys.exit(0)


def _format_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _block_reason(
    prefix: str,
    *,
    live_tokens: int,
    window: int,
    similarity: float,
    threshold: float,
    estimated_cost: float,
) -> str:
    ctx_pct = (live_tokens / window) * 100 if window else 0
    return (
        "🛡️ Guardians blocked this prompt before Claude processed it.\n\n"
        "Reason: this looks unrelated to the current large Claude session and "
        "would send a lot of unrelated context.\n"
        f"Similarity: {similarity:.2f} (block threshold {threshold:.2f})\n"
        f"Context: {_format_tokens(live_tokens)} / {_format_tokens(window)} "
        f"tokens ({ctx_pct:.0f}%)\n"
        f"Estimated cost if sent: ${estimated_cost:.4f}\n\n"
        f"To continue anyway, resend the same prompt prefixed with {prefix}."
    )


def _allow(reason: str, **extra: Any) -> dict[str, Any]:
    return {"action": "allowed", "reason": reason, **extra}


def evaluate(
    prompt: str,
    transcript_path: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Return a structured decision for the prompt.

    Always returns a dict with at least ``action`` ("allowed" | "blocked")
    and ``reason``. Separated from main() so tests can drive the decision
    logic without spawning a subprocess.
    """
    settings = _settings(config)
    if not settings.get("enabled", True):
        return _allow("disabled")

    prefix = str(_setting(settings, "unblock_prefix", "GOT_UNBLOCK"))
    if _is_unblocked(prompt, prefix):
        return _allow("unblock-prefix")
    if _is_allowlisted(prompt):
        return _allow("allowlisted")

    window = int(_setting(settings, "context_window_tokens", 200_000))
    block_pct = float(_setting(settings, "block_context_pct", 0.30))
    fallback_n = int(_setting(settings, "fallback_user_prompts", 3))

    signals = read_signals(transcript_path, fallback_user_prompts=fallback_n)
    if signals.live_tokens is None:
        return _allow("no-usage", anchor_source=signals.anchor_source)

    context_pct = signals.live_tokens / window
    base_extras = {
        "anchor_source": signals.anchor_source,
        "live_tokens": signals.live_tokens,
        "context_pct": context_pct,
    }

    if context_pct < block_pct:
        return _allow("low-pressure", **base_extras)
    if not signals.anchor or signals.anchor_source == "none":
        return _allow("no-anchor", **base_extras)

    model_name = settings.get("embedding_model") or None
    threshold = settings.get("very_low_similarity")
    if threshold is None:
        threshold = default_similarity_threshold(model_name)
    threshold = float(threshold)

    try:
        similarity = _max_chunk_similarity(
            signals.anchor_chunks or [signals.anchor],
            prompt,
            model_name=model_name or "all-MiniLM-L6-v2",
        )
    except EmbeddingUnavailable:
        return _allow("embedding-unavailable", **base_extras)
    except Exception:
        return _allow("embedding-error", **base_extras)

    base_extras["similarity"] = similarity
    base_extras["threshold"] = threshold

    if similarity >= threshold:
        return _allow("high-similarity", **base_extras)

    estimated_cost = estimate_cost(signals.live_tokens, config)
    return {
        "action": "blocked",
        "reason": "low-similarity",
        "unblock_prefix": prefix,
        "context_window": window,
        "estimated_tokens": signals.live_tokens,
        "estimated_cost": estimated_cost,
        **base_extras,
    }


def _round(value: Optional[float], digits: int = 4) -> Optional[float]:
    return round(value, digits) if isinstance(value, (int, float)) else None


def _max_chunk_similarity(chunks: list, prompt: str, *, model_name: str) -> float:
    """Embed the prompt against each anchor chunk separately and return the max.

    Per-chunk scoring is more discriminative than concatenating chunks into a
    single anchor — a 1200-char concat dilutes topical signal, while scoring
    against individual recent turns lets one strong topical match carry the
    decision.
    """
    chunks = [c for c in chunks if c]
    if not chunks:
        return 0.0
    if len(chunks) == 1:
        return cosine_similarity(chunks[0], prompt, model_name=model_name)
    vectors = embed([*chunks, prompt], model_name=model_name)
    sims = vectors[:-1] @ vectors[-1]
    return float(sims.max())


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    prompt = str(payload.get("prompt") or "")
    if not prompt:
        sys.exit(0)

    transcript_path = str(payload.get("transcript_path") or "")
    cwd = payload.get("cwd") or os.getcwd()
    config = load_config(cwd)

    decision = evaluate(prompt, transcript_path, config)
    log_event(
        {
            "client": "claude",
            "kind": "prompt",
            "action": decision["action"],
            "reason": decision["reason"],
            "anchor_source": decision.get("anchor_source", "none"),
            "similarity": _round(decision.get("similarity")),
            "threshold": _round(decision.get("threshold")),
            "context_pct": _round(decision.get("context_pct")),
            "live_tokens": decision.get("live_tokens"),
            "estimated_tokens": decision.get("estimated_tokens"),
            "estimated_cost": decision.get("estimated_cost"),
            "risk": "prompt-drift" if decision["action"] == "blocked" else "ok",
        },
        config=config,
        base_dir=cwd,
    )

    if decision["action"] == "blocked":
        _block(
            _block_reason(
                decision["unblock_prefix"],
                live_tokens=int(decision["live_tokens"]),
                window=int(decision["context_window"]),
                similarity=float(decision["similarity"]),
                threshold=float(decision["threshold"]),
                estimated_cost=float(decision["estimated_cost"]),
            )
        )
    sys.exit(0)


if __name__ == "__main__":
    main()
