from typing import Optional


GUARD_LABEL = "🛡️ Guardians of the Token"


def file_next_steps() -> str:
    return (
        "Next options:\n"
        "- Inspect the beginning\n"
        "- Inspect the end\n"
        "- Search for a term\n"
        "- Summarize a bounded section\n"
        "- Bypass once for the full file"
    )


def url_next_steps() -> str:
    return (
        "Next options:\n"
        "- Inspect metadata\n"
        "- Download without printing\n"
        "- Search a saved copy\n"
        "- Summarize a bounded section\n"
        "- Bypass once for the full response"
    )


def output_next_steps() -> str:
    return (
        "Next options:\n"
        "- Inspect a smaller slice\n"
        "- Search for a term\n"
        "- Summarize the output\n"
        "- Bypass once for the full output"
    )


def format_context_block(
    *,
    target: str,
    estimated_tokens: int,
    context_window: int,
    model_label: str,
    used_tokens: int,
    kind: str,
    action: str,
    blocked_item: str = "command",
    size_kb: Optional[int] = None,
    estimated_cost: Optional[float] = None,
    critical: bool = False,
) -> str:
    used_pct = (used_tokens / context_window) * 100
    target_pct = (estimated_tokens / context_window) * 100
    after_tokens = used_tokens + estimated_tokens
    after_pct = (after_tokens / context_window) * 100
    next_steps = url_next_steps() if kind == "url" else file_next_steps()

    lines = [
        f"{GUARD_LABEL} blocked this {blocked_item}.",
        f"Target: {target}",
    ]
    if size_kb is not None:
        lines.append(f"Size: ~{size_kb:,} KB")
    lines.append(
        f"Estimate: ~{estimated_tokens:,} tokens "
        f"({target_pct:.0f}% of the {context_window:,}-token window on {model_label})"
    )
    if estimated_cost is not None:
        lines.append(f"Estimated input cost: ~${estimated_cost:.4f}")
    if critical:
        lines.append(f"After {action}: ~{after_tokens:,} tokens ({after_pct:.0f}%)")
        lines.append("Risk: likely context compaction.")
    else:
        lines.append(f"Current context: ~{used_tokens:,} tokens ({used_pct:.0f}%)")
    lines.append(next_steps)
    relay = (
        "[Guardians of the Token] IMPORTANT: relay the above warning and options "
        "to the user as a plain chat message. Preserve the first line exactly as "
        "written, including the shield emoji and title. Preserve line breaks, "
        "target, estimate, and options. Wait for their reply before taking any action."
    )
    lines.append(relay)
    return "\n".join(lines)


def format_output_block(*, tool_name: str, tokens: int, soft_cap: int, estimated_cost: Optional[float] = None) -> str:
    lines = [
        f"{GUARD_LABEL} suppressed large {tool_name} output.",
        f"Estimate: ~{tokens:,} tokens",
    ]
    if estimated_cost is not None:
        lines.append(f"Estimated input cost avoided: ~${estimated_cost:.4f}")
    lines.extend(
        [
            f"Limit: {soft_cap:,}-token soft cap",
            output_next_steps(),
        ]
    )
    return "\n".join(
        lines
    )


def agent_feedback(kind: str) -> str:
    if kind == "url":
        return (
            f"This was blocked by {GUARD_LABEL}. "
            "Relay the full warning to the user, preserving the first line exactly "
            "as written, including the shield emoji and title, plus the target, "
            "estimate, and options. Offer metadata inspection, download without "
            "printing, saved-copy search, bounded summary, or explicit bypass."
        )
    if kind == "output":
        return (
            f"This was suppressed by {GUARD_LABEL}. "
            "Relay the full warning to the user, preserving the first line exactly "
            "as written, including the shield emoji and title, plus the estimate "
            "and options. Offer a smaller slice, targeted search, summary, or "
            "explicit bypass."
        )
    return (
        f"This was blocked by {GUARD_LABEL}. "
        "Relay the full warning to the user, preserving the first line exactly as "
        "written, including the shield emoji and title, plus the target, estimate, "
        "and options. Offer beginning, end, search, bounded summary, or explicit "
        "bypass."
    )
