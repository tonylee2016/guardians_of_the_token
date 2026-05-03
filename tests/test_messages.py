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
    assert "- Inspect the beginning" in message
    assert "- Bypass once for the full file" in message
    assert "Preserve the first line exactly as written" in message
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
    assert "- Inspect metadata" in message
    assert "- Bypass once for the full response" in message


def test_output_block_uses_shared_bulleted_template():
    message = format_output_block(tool_name="Bash", tokens=99_999, soft_cap=8_000)

    assert message.startswith("🛡️ Guardians of the Token suppressed large Bash output.")
    assert "Estimate: ~99,999 tokens" in message
    assert "Limit: 8,000-token soft cap" in message
    assert "- Inspect a smaller slice" in message
    assert "- Bypass once for the full output" in message
