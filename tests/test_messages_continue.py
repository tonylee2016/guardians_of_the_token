from guardians_of_the_token.messages import (
    CONTINUE_BOUNDED,
    format_context_block,
    format_permission_prompt,
)


def _block(kind="file"):
    return format_context_block(
        target="/x/server.log",
        estimated_tokens=1_700_000,
        context_window=200_000,
        model_label="haiku",
        used_tokens=20_000,
        kind=kind,
        action="read",
        blocked_item="request",
    )


def test_block_tells_agent_to_continue_with_bounded_approach():
    text = _block()
    assert CONTINUE_BOUNDED in text
    assert "do not stop to ask the user" in text
    assert "Wait for their reply" not in text
    # The user still sees the warning and the options.
    assert text.startswith("🛡️ Guardians of the Token blocked this request.")
    assert "5. Bypass once for the full file" in text


def test_block_never_suggests_bypass_on_its_own():
    assert "Never use the bypass unless the user explicitly asks" in _block("url")


def test_permission_prompt_continues_after_decline():
    _, context = format_permission_prompt(
        target="/x/server.log",
        estimated_tokens=1_700_000,
        context_window=200_000,
        used_tokens=20_000,
        kind="file",
    )
    assert "If they decline, do not retry it as is." in context
    assert CONTINUE_BOUNDED in context
    assert "wait for their" not in context.lower()
