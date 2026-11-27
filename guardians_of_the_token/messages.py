from typing import Optional


GUARD_LABEL = "🛡️ Guardians of the Token"
BYPASS_FILE = "/tmp/guardians_bypass"

# (label shown to the user, how the agent carries it out)
FILE_OPTIONS = [
    ("Inspect the beginning", "read only the first ~200 lines (Read with a limit)"),
    ("Inspect the end", "read only the last ~200 lines (Read with an offset and limit)"),
    ("Search for a term", "ask which term, then search the file and show matching lines only"),
    ("Summarize a bounded section", "read one bounded section and summarize it"),
    ("Bypass once for the full file", f"run `touch {BYPASS_FILE}`, then retry the original request once"),
]

URL_OPTIONS = [
    ("Inspect metadata", "fetch only headers (status, content type, size)"),
    ("Download without printing", "save the response to a file instead of loading it into context"),
    ("Search a saved copy", "save it to a file, then search it for the user's term"),
    ("Summarize a bounded section", "save it to a file and summarize one bounded section"),
    ("Bypass once for the full response", f"run `touch {BYPASS_FILE}`, then retry the original request once"),
]

OUTPUT_OPTIONS = [
    ("Inspect a smaller slice", "rerun with output limited to a small slice"),
    ("Search for a term", "rerun filtered to lines matching the user's term"),
    ("Summarize the output", "save the output to a file and summarize it"),
    ("Bypass once for the full output", f"run `touch {BYPASS_FILE}`, then rerun once"),
]


def _numbered(options: list[tuple[str, str]]) -> str:
    lines = ["Next options (reply with a number):"]
    lines.extend(f"{i}. {label}" for i, (label, _) in enumerate(options, 1))
    return "\n".join(lines)


def _agent_actions(options: list[tuple[str, str]]) -> str:
    return "; ".join(f"{i} = {action}" for i, (_, action) in enumerate(options, 1))


def output_next_steps() -> str:
    return _numbered(OUTPUT_OPTIONS)


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
    options = URL_OPTIONS if kind == "url" else FILE_OPTIONS
    next_steps = _numbered(options)

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
        "target, estimate, and options. Wait for their reply before taking any action. "
        "When they pick an option (by number or name): " + _agent_actions(options) + "."
    )
    lines.append(relay)
    return "\n".join(lines)


def format_permission_prompt(
    *,
    target: str,
    estimated_tokens: int,
    context_window: int,
    used_tokens: int,
    kind: str,
    estimated_cost: Optional[float] = None,
    critical: bool = False,
) -> tuple[str, str]:
    """Text for Claude Code's Yes/No permission prompt.

    Returns (reason, context): the reason is shown to the user in the prompt,
    the context tells Claude what to offer if the user picks No.
    """
    is_url = kind == "url"
    noun, verb = ("page", "fetch") if is_url else ("file", "read")
    target_pct = (estimated_tokens / context_window) * 100
    used_pct = (used_tokens / context_window) * 100
    after_pct = ((used_tokens + estimated_tokens) / context_window) * 100
    size = f"~{estimated_tokens:,} tokens, {target_pct:.0f}% of the window"
    if estimated_cost is not None:
        size += f", ~${estimated_cost:.2f}"
    effect = f"Context {used_pct:.0f}% → {after_pct:.0f}%"
    effect += ", likely to trigger compaction." if critical else "."
    reason = "\n".join(
        [
            f"{GUARD_LABEL}: large {noun} ({size}).",
            effect,
            f"Yes loads it in full. No lets Claude suggest a smaller {verb}.",
        ]
    )
    options = (URL_OPTIONS if is_url else FILE_OPTIONS)[:-1]  # the prompt's Yes is the bypass
    choices = "; ".join(
        f"{i}. {label} ({action})" for i, (label, action) in enumerate(options, 1)
    )
    context = (
        f"{GUARD_LABEL} asked the user to approve this {verb} of {target} "
        f"(~{estimated_tokens:,} tokens). If they decline, do not retry it as is. "
        "Offer these smaller options as a short numbered list and wait for their "
        f"pick: {choices}."
    )
    return reason, context


def format_output_block(
    *,
    tool_name: str,
    tokens: int,
    soft_cap: int,
    estimated_cost: Optional[float] = None,
    verb: str = "suppressed",
    relay: bool = False,
) -> str:
    lines = [
        f"{GUARD_LABEL} {verb} large {tool_name} output.",
        f"Estimate: ~{tokens:,} tokens",
    ]
    if estimated_cost is not None:
        label = "Estimated input cost avoided" if verb == "suppressed" else "Estimated input cost"
        lines.append(f"{label}: ~${estimated_cost:.4f}")
    lines.extend(
        [
            f"Limit: {soft_cap:,}-token soft cap",
            output_next_steps(),
        ]
    )
    if relay:
        lines.append(
            "[Guardians of the Token] Relay the above warning and options to the "
            "user as a plain chat message, preserving the first line exactly. "
            "Don't quote or summarize the large output unless they ask. "
            "When they pick an option (by number or name): "
            + _agent_actions(OUTPUT_OPTIONS)
            + "."
        )
    return "\n".join(lines)


def agent_feedback(kind: str) -> str:
    options = {"url": URL_OPTIONS, "output": OUTPUT_OPTIONS}.get(kind, FILE_OPTIONS)
    return _agent_feedback_text(kind) + (
        " When the user picks an option (by number or name): " + _agent_actions(options) + "."
    )


def _agent_feedback_text(kind: str) -> str:
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
