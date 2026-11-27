"""In-process tests for the Claude Code hook entry points (edge and error paths)."""

import io
import json
import sys

import pytest

from guardians_of_the_token import pause
from guardians_of_the_token.claude import post_tool_guard, pre_compact, prompt_guard, session_start
from guardians_of_the_token.transcript import TranscriptSignals


def run_main(module, monkeypatch, stdin):
    """Run a hook's main() with the given stdin; return (exit code, stdout)."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin if isinstance(stdin, str) else json.dumps(stdin)))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    code = 0
    try:
        module.main()
    except SystemExit as exc:
        code = exc.code or 0
    return code, out.getvalue()


@pytest.fixture
def paused():
    pause.set_pause(600)
    yield
    pause.clear_pause()


# ---------- post_tool_guard ----------


@pytest.mark.parametrize(
    "response, expected",
    [
        ("plain", "plain"),
        ({"output": 12}, "12"),
        ({"a": 1}, '{"a": 1}'),
        ([{"text": "a"}, "b", {"x": 1}], "a b "),
        (42, "42"),
    ],
)
def test_extract_text(response, expected):
    assert post_tool_guard.extract_text(response) == expected


def test_post_tool_count_tokens():
    assert post_tool_guard.count_tokens("x" * 40) == 10


def test_post_tool_exits_when_paused(monkeypatch, paused):
    assert run_main(post_tool_guard, monkeypatch, "{}")[0] == 0


def test_post_tool_ignores_bad_json(monkeypatch):
    assert run_main(post_tool_guard, monkeypatch, "not json")[0] == 0


def test_post_tool_skips_own_tools(monkeypatch):
    assert run_main(post_tool_guard, monkeypatch, {"tool_name": "guardiansOfTheToken"})[0] == 0


def test_post_tool_counts_real_output(monkeypatch, tmp_path):
    monkeypatch.delenv("GUARDIANS_TEST_TOKENS", raising=False)
    payload = {"tool_name": "WebFetch", "tool_response": "small page", "cwd": str(tmp_path)}
    assert run_main(post_tool_guard, monkeypatch, payload)[0] == 0


# ---------- session_start ----------


def test_session_start_reads_bad_payload(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("{bad"))
    assert session_start._read_payload() == {}

    class Broken(io.StringIO):
        def read(self, *a):
            raise OSError("closed")

    monkeypatch.setattr(sys, "stdin", Broken())
    assert session_start._read_payload() == {}


def test_session_start_resume_piece_swallows_errors(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise RuntimeError("bad state")

    monkeypatch.setattr("guardians_of_the_token.state.resume_offer", boom)
    assert session_start._resume_piece({"source": "startup", "cwd": str(tmp_path)}) == ""


# ---------- pre_compact ----------


@pytest.fixture
def compact_env(monkeypatch, tmp_path):
    captured = {}

    def fake_capture(path, **kwargs):
        captured.update(kwargs, path=path)
        return tmp_path / ".got" / "project_state.md"

    monkeypatch.setattr(pre_compact, "capture", fake_capture)
    monkeypatch.setattr(
        pre_compact,
        "read_signals",
        lambda path: TranscriptSignals(anchor="", anchor_chunks=[], anchor_source="none", live_tokens=150_000),
    )
    return captured


def _events(tmp_path):
    path = tmp_path / ".got" / "events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_pre_compact_saves_snapshot(monkeypatch, tmp_path, compact_env):
    code, _ = run_main(
        pre_compact, monkeypatch, {"transcript_path": "t.jsonl", "cwd": str(tmp_path), "trigger": "manual"}
    )
    assert code == 0
    assert compact_env["trigger"] == "precompact-manual"
    assert compact_env["live_tokens"] == 150_000
    assert compact_env["context_pct"] == pytest.approx(0.75)
    event = _events(tmp_path)[-1]
    assert event["action"] == "saved"
    assert event["reason"] == "precompact-manual"


def test_pre_compact_logs_skip_when_capture_fails(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise RuntimeError("disk full")

    def bad_signals(path):
        raise ValueError("unreadable transcript")

    monkeypatch.setattr(pre_compact, "capture", boom)
    monkeypatch.setattr(pre_compact, "read_signals", bad_signals)
    assert run_main(pre_compact, monkeypatch, {"cwd": str(tmp_path)})[0] == 0
    event = _events(tmp_path)[-1]
    assert event["action"] == "skipped"
    assert event["reason"] == "precompact-auto"
    assert event["live_tokens"] is None


def test_pre_compact_without_usage(monkeypatch, tmp_path, compact_env):
    monkeypatch.setattr(
        pre_compact,
        "read_signals",
        lambda path: TranscriptSignals(anchor="", anchor_chunks=[], anchor_source="none", live_tokens=None),
    )
    run_main(pre_compact, monkeypatch, {"cwd": str(tmp_path)})
    assert compact_env["context_pct"] is None


def test_pre_compact_respects_disabled_setting(monkeypatch, tmp_path, compact_env):
    (tmp_path / ".guardians.json").write_text(json.dumps({"project_state": {"enabled": False}}))
    assert run_main(pre_compact, monkeypatch, {"cwd": str(tmp_path)})[0] == 0
    assert compact_env == {}


def test_pre_compact_exits_when_paused_or_bad_input(monkeypatch, tmp_path, compact_env, paused):
    assert run_main(pre_compact, monkeypatch, {"cwd": str(tmp_path)})[0] == 0
    assert compact_env == {}


def test_pre_compact_bad_json(monkeypatch, compact_env):
    assert run_main(pre_compact, monkeypatch, "nope")[0] == 0
    assert compact_env == {}


# ---------- prompt_guard ----------


def test_empty_prompt_is_allowlisted():
    assert prompt_guard._is_allowlisted("   ") is True


@pytest.mark.parametrize("n, text", [(2_500_000, "2.5M"), (1_500, "1.5k"), (999, "999")])
def test_format_tokens(n, text):
    assert prompt_guard._format_tokens(n) == text


def test_embedding_error_is_allowed(monkeypatch):
    monkeypatch.setattr(
        prompt_guard,
        "read_signals",
        lambda path, fallback_user_prompts=3: TranscriptSignals(
            anchor="topic", anchor_chunks=["topic"], anchor_source="recap", live_tokens=180_000
        ),
    )

    def boom(*a, **k):
        raise RuntimeError("onnx crashed")

    monkeypatch.setattr(prompt_guard, "_max_chunk_similarity", boom)
    decision = prompt_guard.evaluate("plan a trip", "x", {"prompt_guard": {}})
    assert decision["reason"] == "embedding-error"


def test_max_chunk_similarity_variants(monkeypatch):
    import numpy as np

    assert prompt_guard._max_chunk_similarity(["", None], "p", model_name="m") == 0.0
    monkeypatch.setattr(prompt_guard, "cosine_similarity", lambda a, b, model_name: 0.42)
    assert prompt_guard._max_chunk_similarity(["one"], "p", model_name="m") == 0.42
    monkeypatch.setattr(
        prompt_guard,
        "embed",
        lambda texts, model_name: np.array([[1.0, 0.0], [0.6, 0.8], [0.6, 0.8]]),
    )
    assert prompt_guard._max_chunk_similarity(["a", "b"], "p", model_name="m") == pytest.approx(1.0)


def test_prompt_guard_main_exits_early(monkeypatch, paused):
    assert run_main(prompt_guard, monkeypatch, {"prompt": "hi"})[0] == 0


def test_prompt_guard_main_bad_json_and_empty_prompt(monkeypatch):
    assert run_main(prompt_guard, monkeypatch, "garbage")[0] == 0
    assert run_main(prompt_guard, monkeypatch, {"prompt": ""})[0] == 0


def test_session_start_reports_update(monkeypatch):
    class Result:
        status = "updated"
        message = "Guardians updated to 9.9.9."

    monkeypatch.setattr(session_start, "startup_auto_update", lambda: Result())
    code, out = run_main(session_start, monkeypatch, {"source": "resume"})
    assert code == 0
    assert json.loads(out)["hookSpecificOutput"]["additionalContext"] == "Guardians updated to 9.9.9."
