from guardians_of_the_token.messages import format_context_block, format_output_block


def test_file_context_block_uses_shared_bulleted_template():
    message = format_context_block(
        target="/tmp/large.txt",
        estimated_tokens=200_000,
        context_window=400_000,
        model_label="gpt-5.5",
        used_tokens=10_000,
        kind="file",
        action="read",
        blocked_item="command",
        critical=False,
    )

    assert message.startswith("🛡️ Guardians of the Token blocked this command.")
    assert "Target: /tmp/large.txt" in message
    assert "Estimate: ~200,000 tokens (50% of the 400,000-token window on gpt-5.5)" in message
    assert "Current context: ~10,000 tokens (2%)" in message
    assert "1. Inspect the beginning" in message
    assert "5. Bypass once for the full file" in message
    assert "quote the first line above exactly" in message
    assert "Next options (reply with a number):" in message
    assert "5 = run `touch /tmp/guardians_bypass`" in message
    assert "sed -n" not in message
    assert "tail -n" not in message


def test_url_context_block_marks_compaction_risk():
    message = format_context_block(
        target="https://guardians-test/compact",
        estimated_tokens=200_000,
        context_window=200_000,
        model_label="claude-sonnet",
        used_tokens=0,
        kind="url",
        action="fetch",
        blocked_item="request",
        critical=True,
    )

    assert message.startswith("🛡️ Guardians of the Token blocked this request.")
    assert "After fetch: ~200,000 tokens (100%)" in message
    assert "Risk: likely context compaction." in message
    assert "1. Inspect metadata" in message
    assert "5. Bypass once for the full response" in message


def test_output_block_uses_shared_bulleted_template():
    message = format_output_block(tool_name="Bash", tokens=99_999, soft_cap=8_000)

    assert message.startswith("🛡️ Guardians of the Token suppressed large Bash output.")
    assert "Estimate: ~99,999 tokens" in message
    assert "Limit: 8,000-token soft cap" in message
    assert "1. Inspect a smaller slice" in message
    assert "4. Bypass once for the full output" in message


def test_permission_prompt_without_cost_and_for_files():
    from guardians_of_the_token.messages import format_permission_prompt

    reason, context = format_permission_prompt(
        target="logs/build.log",
        estimated_tokens=120_000,
        context_window=200_000,
        used_tokens=40_000,
        kind="file",
    )
    assert reason.splitlines() == [
        "🛡️ Guardians of the Token: large file (~120,000 tokens, 60% of the window).",
        "Context 20% → 80%.",
        "Yes loads it in full. No lets Claude suggest a smaller read.",
    ]
    assert "logs/build.log" in context
    assert "4. Summarize a bounded section" in context
    assert "Bypass" not in context
