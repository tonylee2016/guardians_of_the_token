"""Tests for the shared model -> context-window resolver."""

import json

import pytest

from guardians_of_the_token import context_window as cw
from guardians_of_the_token import transcript
from guardians_of_the_token.claude import pre_tool_guard as claude_guard
from guardians_of_the_token.codex import pre_tool_guard as codex_guard
from guardians_of_the_token.config import DEFAULT_CONFIG


@pytest.mark.parametrize(
    "model, expected",
    [
        # Current Claude models: 1M
        ("claude-opus-5-5", 1_000_000),
        ("claude-opus-5", 1_000_000),
        ("claude-sonnet-5-5", 1_000_000),
        ("claude-sonnet-5", 1_000_000),
        ("claude-fable-5-2", 1_000_000),
        ("claude-haiku-5-5", 1_000_000),
        ("claude-opus-4-8", 1_000_000),
        ("claude-opus-4-6", 1_000_000),
        ("claude-sonnet-4-6", 1_000_000),
        ("claude-some-future-model", 1_000_000),
        # Older Claude models keep 200K / 100K
        ("claude-haiku-4-5", 200_000),
        ("claude-haiku-4-5-20251001", 200_000),
        ("claude-sonnet-4-5-20250929", 200_000),
        ("claude-opus-4-5", 200_000),
        ("claude-opus-4-1-20250805", 200_000),
        ("claude-opus-4-20250514", 200_000),
        ("claude-sonnet-4-20250514", 200_000),
        ("claude-sonnet-4-0", 200_000),
        ("claude-3-5-sonnet-20241022", 200_000),
        ("claude-3-7-sonnet-latest", 200_000),
        ("claude-3-opus-20240229", 200_000),
        ("claude-2.1", 100_000),
        ("claude-instant-1.2", 100_000),
        # Provider-prefixed ids and the extended-context marker
        ("us.anthropic.claude-opus-5-5-v1:0", 1_000_000),
        ("anthropic/claude-haiku-4-5", 200_000),
        ("CLAUDE-OPUS-5-5", 1_000_000),
        ("claude-sonnet-4-5[1m]", 1_000_000),
        # Non-Claude models
        ("gpt-5.5-codex", 400_000),
        ("o3-pro", 200_000),
        ("gpt-4o-mini", 128_000),
        ("gpt-4", 8_192),
    ],
)
def test_model_context_window(model, expected):
    assert cw.model_context_window(model) == expected


def test_unknown_and_empty_use_fallback():
    assert cw.model_context_window("") == cw.FALLBACK_CONTEXT_WINDOW
    assert cw.model_context_window("mystery", fallback=123) == 123


@pytest.mark.parametrize(
    "config, section, expected",
    [
        (None, "", None),
        ({"context_window": 500_000}, "", 500_000),
        ({"context_window": "300000"}, "", 300_000),
        ({"context_window": 0}, "", None),
        ({"context_window": True}, "", None),
        ({"context_window": "big"}, "", None),
        ({"prompt_guard": {"context_window_tokens": 250_000}}, "prompt_guard", 250_000),
        ({"prompt_guard": {"context_window_tokens": 250_000}}, "", None),
        ({"prompt_guard": "not-a-dict"}, "prompt_guard", None),
        ({"context_window": 600_000, "prompt_guard": {"context_window_tokens": 250_000}}, "prompt_guard", 600_000),
    ],
)
def test_configured_override(config, section, expected):
    assert cw.configured_override(config, section) == expected


def test_resolve_prefers_override_then_model():
    assert cw.resolve_context_window("claude-opus-5-5") == 1_000_000
    assert cw.resolve_context_window("claude-opus-5-5", {"context_window": 200_000}) == 200_000
    assert cw.resolve_context_window("claude-opus-5-5", DEFAULT_CONFIG, section="prompt_guard") == 1_000_000


def test_guards_route_through_shared_resolver():
    assert claude_guard.model_to_context_window("claude-opus-5-5") == 1_000_000
    assert codex_guard.model_to_context_window("claude-opus-5-5") == 1_000_000
    assert claude_guard.model_to_context_window("claude-opus-5-5", {"context_window": 64_000}) == 64_000
    assert codex_guard.model_to_context_window("gpt-5.5", {"context_window": 64_000}) == 64_000
    assert codex_guard.model_to_context_window("") == codex_guard.FALLBACK_CONTEXT_WINDOW == 400_000


def test_read_signals_reports_latest_main_chain_model(tmp_path):
    lines = [
        {"type": "assistant", "message": {"model": "claude-haiku-4-5", "usage": {"input_tokens": 5}}},
        {"type": "assistant", "message": {"model": "claude-opus-5-5", "usage": {"input_tokens": 7}}},
        {"type": "assistant", "isSidechain": True, "message": {"model": "claude-haiku-5-5"}},
        {"type": "assistant", "isApiErrorMessage": True, "message": {"model": "<synthetic>"}},
        {"type": "assistant", "message": {"model": "<synthetic>"}},
        {"type": "assistant", "message": "not a dict"},
        {"type": "user", "message": {"content": "hi", "model": "ignored"}},
    ]
    path = tmp_path / "t.jsonl"
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    assert transcript.read_signals(str(path)).model == "claude-opus-5-5"
    assert transcript.read_signals(str(tmp_path / "missing")).model == ""
