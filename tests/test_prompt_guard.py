import json
import os
import subprocess
import sys

import pytest

from guardians_of_the_token.claude import prompt_guard
from guardians_of_the_token.transcript import read_signals


# --- evaluate() ---


def _config(**overrides):
    base = {
        "enabled": True,
        "context_window_tokens": 200_000,
        "block_context_pct": 0.75,
        "very_low_similarity": 0.22,
        "unblock_prefix": "GOT_UNBLOCK",
        "embedding_model": "all-MiniLM-L6-v2",
        "fallback_user_prompts": 3,
    }
    base.update(overrides)
    return {"prompt_guard": base}


def _stub_embed(monkeypatch, similarity: float):
    monkeypatch.setattr(
        prompt_guard,
        "_max_chunk_similarity",
        lambda chunks, prompt, model_name=None: similarity,
    )


def _stub_signals(monkeypatch, *, anchor: str, source: str, live_tokens):
    from guardians_of_the_token.transcript import TranscriptSignals

    monkeypatch.setattr(
        prompt_guard,
        "read_signals",
        lambda path, fallback_user_prompts=3: TranscriptSignals(
            anchor=anchor,
            anchor_chunks=[anchor] if anchor else [],
            anchor_source=source,
            live_tokens=live_tokens,
        ),
    )


def _assert_allowed(decision, reason):
    assert decision["action"] == "allowed"
    assert decision["reason"] == reason


def test_disabled_allows(monkeypatch):
    _stub_signals(monkeypatch, anchor="topic", source="recap", live_tokens=180_000)
    _stub_embed(monkeypatch, similarity=0.0)
    decision = prompt_guard.evaluate(
        "totally unrelated prompt that should trip every threshold",
        "ignored",
        _config(enabled=False),
    )
    _assert_allowed(decision, "disabled")


def test_control_command_allowed(monkeypatch):
    _stub_signals(monkeypatch, anchor="topic", source="recap", live_tokens=180_000)
    _stub_embed(monkeypatch, similarity=0.0)
    _assert_allowed(prompt_guard.evaluate("/compact", "x", _config()), "allowlisted")


def test_effective_window_snaps_up_to_one_million(monkeypatch):
    # 410k live_tokens vs configured 200k window: the effective denominator
    # must snap up to the next known window (1M) so context_pct is < 1.0.
    _stub_signals(monkeypatch, anchor="topic", source="recap", live_tokens=410_000)
    _stub_embed(monkeypatch, similarity=0.05)
    decision = prompt_guard.evaluate(
        "totally unrelated prompt about traveling somewhere far away",
        "x",
        _config(block_context_pct=0.30),
    )
    assert decision["action"] == "blocked"
    assert decision["context_window"] == 1_000_000
    assert decision["context_pct"] == pytest.approx(0.41)


def test_effective_window_keeps_default_under_pressure(monkeypatch):
    # 120k live_tokens fits inside the configured 200k window — no snap.
    _stub_signals(monkeypatch, anchor="topic", source="recap", live_tokens=120_000)
    _stub_embed(monkeypatch, similarity=0.05)
    decision = prompt_guard.evaluate(
        "totally unrelated prompt about traveling somewhere far away",
        "x",
        _config(block_context_pct=0.30),
    )
    assert decision["action"] == "blocked"
    assert decision["context_window"] == 200_000


def test_got_unblock_skill_invocation_allowed(monkeypatch):
    _stub_signals(monkeypatch, anchor="topic", source="recap", live_tokens=180_000)
    _stub_embed(monkeypatch, similarity=0.0)
    decision = prompt_guard.evaluate(
        "/got-unblock plan a three-day trip to Tokyo",
        "x",
        _config(),
    )
    _assert_allowed(decision, "allowlisted")


def test_short_continuation_allowed(monkeypatch):
    _stub_signals(monkeypatch, anchor="topic", source="recap", live_tokens=180_000)
    _stub_embed(monkeypatch, similarity=0.0)
    _assert_allowed(prompt_guard.evaluate("ok", "x", _config()), "allowlisted")


def test_unblock_prefix_allowed(monkeypatch):
    _stub_signals(monkeypatch, anchor="topic", source="recap", live_tokens=180_000)
    _stub_embed(monkeypatch, similarity=0.0)
    decision = prompt_guard.evaluate(
        "GOT_UNBLOCK can you plan a Tokyo trip for me please",
        "x",
        _config(),
    )
    _assert_allowed(decision, "unblock-prefix")


def test_low_pressure_allows(monkeypatch):
    _stub_signals(monkeypatch, anchor="topic", source="recap", live_tokens=10_000)
    _stub_embed(monkeypatch, similarity=0.0)
    decision = prompt_guard.evaluate(
        "totally unrelated prompt about traveling",
        "x",
        _config(),
    )
    _assert_allowed(decision, "low-pressure")
    assert decision["live_tokens"] == 10_000


def test_missing_usage_allows(monkeypatch):
    _stub_signals(monkeypatch, anchor="topic", source="recap", live_tokens=None)
    _stub_embed(monkeypatch, similarity=0.0)
    decision = prompt_guard.evaluate(
        "totally unrelated prompt about traveling",
        "x",
        _config(),
    )
    _assert_allowed(decision, "no-usage")


def test_missing_anchor_allows(monkeypatch):
    _stub_signals(monkeypatch, anchor="", source="none", live_tokens=180_000)
    _stub_embed(monkeypatch, similarity=0.0)
    decision = prompt_guard.evaluate(
        "totally unrelated prompt about traveling",
        "x",
        _config(),
    )
    _assert_allowed(decision, "no-anchor")


def test_high_similarity_allows(monkeypatch):
    _stub_signals(monkeypatch, anchor="topic", source="recap", live_tokens=180_000)
    _stub_embed(monkeypatch, similarity=0.5)
    decision = prompt_guard.evaluate(
        "follow-up question on the same topic anchor please",
        "x",
        _config(),
    )
    _assert_allowed(decision, "high-similarity")
    assert decision["similarity"] == pytest.approx(0.5)


def test_low_similarity_with_pressure_blocks(monkeypatch):
    _stub_signals(monkeypatch, anchor="topic", source="recap", live_tokens=180_000)
    _stub_embed(monkeypatch, similarity=0.05)
    decision = prompt_guard.evaluate(
        "can you help plan a three-day trip to Tokyo please",
        "x",
        _config(),
    )
    assert decision["action"] == "blocked"
    assert decision["reason"] == "low-similarity"
    assert decision["anchor_source"] == "recap"
    assert decision["similarity"] == pytest.approx(0.05)
    assert decision["unblock_prefix"] == "GOT_UNBLOCK"


def test_embedding_failure_fails_open(monkeypatch):
    _stub_signals(monkeypatch, anchor="topic", source="recap", live_tokens=180_000)

    def boom(*_args, **_kwargs):
        raise prompt_guard.EmbeddingUnavailable("missing")

    monkeypatch.setattr(prompt_guard, "_max_chunk_similarity", boom)
    decision = prompt_guard.evaluate(
        "totally unrelated long prompt about something else entirely",
        "x",
        _config(),
    )
    _assert_allowed(decision, "embedding-unavailable")


# --- transcript anchor extraction ---


def _write_jsonl(path, entries):
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")


def test_recap_preferred_over_compact(tmp_path):
    transcript = tmp_path / "t.jsonl"
    _write_jsonl(
        transcript,
        [
            {"type": "system", "subtype": "away_summary", "content": "RECAP"},
            {"type": "system", "isCompactSummary": True, "content": "COMPACT"},
            {
                "type": "assistant",
                "message": {
                    "usage": {
                        "input_tokens": 500,
                        "cache_read_input_tokens": 128_000,
                        "cache_creation_input_tokens": 200,
                    }
                },
            },
        ],
    )
    signals = read_signals(str(transcript))
    assert signals.anchor == "RECAP"
    assert signals.anchor_source == "recap"
    # Matches ccstatusline: input + cache_read + cache_creation.
    assert signals.live_tokens == 128_700


def test_sidechain_usage_ignored(tmp_path):
    transcript = tmp_path / "t.jsonl"
    _write_jsonl(
        transcript,
        [
            {"type": "system", "subtype": "away_summary", "content": "RECAP"},
            {
                "type": "assistant",
                "isSidechain": True,
                "message": {"usage": {"input_tokens": 999_999}},
            },
            {
                "type": "assistant",
                "message": {"usage": {"input_tokens": 1000, "cache_read_input_tokens": 50_000}},
            },
        ],
    )
    signals = read_signals(str(transcript))
    assert signals.live_tokens == 51_000


def test_compact_summary_falls_back_when_no_recap(tmp_path):
    transcript = tmp_path / "t.jsonl"
    _write_jsonl(
        transcript,
        [{"type": "system", "isCompactSummary": True, "content": "COMPACT"}],
    )
    signals = read_signals(str(transcript))
    assert signals.anchor == "COMPACT"
    assert signals.anchor_source == "compact"


def test_user_prompts_fallback_when_no_summaries(tmp_path):
    transcript = tmp_path / "t.jsonl"
    _write_jsonl(
        transcript,
        [
            {"type": "user", "message": {"role": "user", "content": "first session prompt"}},
            {"type": "user", "message": {"role": "user", "content": "middle prompt"}},
            {"type": "user", "message": {"role": "user", "content": "latest prompt"}},
        ],
    )
    signals = read_signals(str(transcript), fallback_user_prompts=2)
    assert signals.anchor_source == "conversation"
    assert "first session prompt" in signals.anchor
    assert "latest prompt" in signals.anchor


def test_assistant_text_mixed_into_anchor(tmp_path):
    transcript = tmp_path / "t.jsonl"
    long_assistant = "x" * 200  # well above _ASSISTANT_MIN_CHARS
    _write_jsonl(
        transcript,
        [
            {"type": "user", "message": {"role": "user", "content": "first user message"}},
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": f"detailed assistant reply about topic: {long_assistant}"}
                    ]
                },
            },
            {"type": "user", "message": {"role": "user", "content": "follow up question"}},
        ],
    )
    signals = read_signals(str(transcript))
    assert signals.anchor_source == "conversation"
    assert "first user message" in signals.anchor
    assert "follow up question" in signals.anchor
    assert "detailed assistant reply" in signals.anchor


def test_short_assistant_text_excluded_from_anchor(tmp_path):
    transcript = tmp_path / "t.jsonl"
    _write_jsonl(
        transcript,
        [
            {"type": "user", "message": {"role": "user", "content": "first prompt"}},
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "ok."}]},  # too short
            },
        ],
    )
    signals = read_signals(str(transcript))
    assert "ok." not in signals.anchor


def test_blocked_user_prompts_skipped_from_anchor(tmp_path):
    transcript = tmp_path / "t.jsonl"
    _write_jsonl(
        transcript,
        [
            {"type": "user", "message": {"role": "user", "content": "real first prompt about prompt-guard design"}},
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": (
                        "⏺ UserPromptSubmit operation blocked by hook:\n  "
                        "Guardians blocked this prompt before Claude processed it.\n\n"
                        "  Original prompt: where is mongolia's navy located ?"
                    ),
                },
            },
            {"type": "user", "message": {"role": "user", "content": "second real prompt about embedding similarity"}},
        ],
    )
    signals = read_signals(str(transcript))
    assert signals.anchor_source == "conversation"
    assert "mongolia" not in signals.anchor.lower()
    assert "real first prompt" in signals.anchor
    assert "second real prompt" in signals.anchor


def test_no_transcript_returns_none(tmp_path):
    signals = read_signals(str(tmp_path / "missing.jsonl"))
    assert signals.anchor_source == "none"
    assert signals.live_tokens is None


# --- main() event logging ---


def _run_hook(payload, cwd):
    return subprocess.run(
        [sys.executable, "-m", "guardians_of_the_token.claude.prompt_guard"],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env={**os.environ, "GUARDIANS_CONFIG": str(cwd / "no-such-config.json")},
        cwd=str(cwd),
        check=False,
    )


def test_main_logs_allowed_event_locally(tmp_path):
    payload = {"prompt": "ok", "transcript_path": "", "cwd": str(tmp_path)}
    result = _run_hook(payload, tmp_path)
    assert result.returncode == 0
    assert result.stdout == ""
    events_path = tmp_path / ".got" / "events.jsonl"
    assert events_path.exists(), result.stderr
    entry = json.loads(events_path.read_text().splitlines()[-1])
    assert entry["kind"] == "prompt"
    assert entry["action"] == "allowed"
    assert entry["reason"] == "allowlisted"


def test_main_logs_blocked_event_locally(tmp_path, monkeypatch):
    # Set pressure floor to 0 so any transcript with a recap triggers the
    # embedding check, regardless of file size.
    config_file = tmp_path / "guardians.json"
    config_file.write_text(
        json.dumps({"prompt_guard": {"block_context_pct": 0.0}})
    )
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        "\n".join(
            json.dumps(e)
            for e in [
                {
                    "type": "system",
                    "subtype": "away_summary",
                    "content": "Designing a UserPromptSubmit hook for local prompt blocking.",
                },
                {
                    "type": "assistant",
                    "message": {
                        "usage": {
                            "input_tokens": 500,
                            "cache_read_input_tokens": 60_000,
                            "cache_creation_input_tokens": 0,
                        }
                    },
                },
            ]
        )
        + "\n"
    )
    payload = {
        "prompt": "Can you help plan a three-day trip to Tokyo?",
        "transcript_path": str(transcript),
        "cwd": str(tmp_path),
    }
    result = subprocess.run(
        [sys.executable, "-m", "guardians_of_the_token.claude.prompt_guard"],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env={**os.environ, "GUARDIANS_CONFIG": str(config_file)},
        cwd=str(tmp_path),
        check=False,
    )
    assert result.returncode == 0
    out = json.loads(result.stdout)
    assert out["decision"] == "block"
    assert "/got-unblock" in out["reason"]
    assert "GOT_UNBLOCK" in out["reason"]
    assert "Context:" in out["reason"]
    assert "Estimated cost" in out["reason"]
    events_path = tmp_path / ".got" / "events.jsonl"
    entry = json.loads(events_path.read_text().splitlines()[-1])
    assert entry["action"] == "blocked"
    assert entry["reason"] == "low-similarity"
    assert entry["anchor_source"] == "recap"
    assert entry["estimated_tokens"] == entry["live_tokens"]
    assert entry["estimated_cost"] is not None and entry["estimated_cost"] > 0
