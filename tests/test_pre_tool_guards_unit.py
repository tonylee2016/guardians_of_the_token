"""In-process tests for the Claude and Codex PreToolUse guards."""

import io
import json
import sys

import pytest

from guardians_of_the_token import pause
from guardians_of_the_token.claude import pre_tool_guard as claude_guard
from guardians_of_the_token.codex import pre_tool_guard as codex_guard


@pytest.fixture(autouse=True)
def _bypass_file(tmp_path, monkeypatch):
    path = tmp_path / "guardians_bypass"
    monkeypatch.setattr(claude_guard, "BYPASS_FILE", str(path))
    monkeypatch.setattr(codex_guard, "BYPASS_FILE", str(path))
    monkeypatch.delenv("GUARDIANS_BYPASS", raising=False)
    monkeypatch.delenv("GUARDIANS_TEST_SIZE", raising=False)
    monkeypatch.setattr(codex_guard, "startup_auto_update", lambda: None)
    return path


def run_main(module, monkeypatch, stdin):
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin if isinstance(stdin, str) else json.dumps(stdin)))
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    code = 0
    try:
        module.main()
    except SystemExit as exc:
        code = exc.code or 0
    return code, out.getvalue(), err.getvalue()


def _transcript(tmp_path):
    lines = [
        '{"partial line from the tail read',
        "",
        "not json",
        json.dumps({"type": "user", "message": {"content": "hi"}}),
        json.dumps({"type": "assistant", "message": ["not", "a", "dict"]}),
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "model": "claude-opus-5-5",
                    "usage": {"input_tokens": 100, "cache_read_input_tokens": 20, "output_tokens": 5},
                },
            }
        ),
    ]
    path = tmp_path / "transcript.jsonl"
    path.write_text("\n".join(lines) + "\n")
    return str(path)


@pytest.mark.parametrize("guard", [claude_guard, codex_guard])
def test_detect_model_and_usage(guard, tmp_path):
    assert guard.detect_model_and_usage("") == ("", 0)
    assert guard.detect_model_and_usage(_transcript(tmp_path)) == ("claude-opus-5-5", 125)


@pytest.mark.parametrize("guard", [claude_guard, codex_guard])
def test_read_tail_missing_file(guard, tmp_path):
    assert guard.read_tail(str(tmp_path / "missing"), 10) == ""


def test_model_windows():
    assert claude_guard.model_to_context_window("gpt-4o-mini") == 128_000
    assert claude_guard.model_to_context_window("mystery") == claude_guard.FALLBACK_CONTEXT_WINDOW
    assert codex_guard.model_to_context_window("o3-pro") == 200_000
    assert codex_guard.model_to_context_window("mystery") == codex_guard.FALLBACK_CONTEXT_WINDOW


@pytest.mark.parametrize("guard", [claude_guard, codex_guard])
def test_bypass_env_and_single_use_file(guard, monkeypatch, _bypass_file):
    assert guard.is_bypass() is False
    _bypass_file.write_text("")
    assert guard.is_bypass() is True
    assert not _bypass_file.exists()
    monkeypatch.setenv("GUARDIANS_BYPASS", "1")
    assert guard.is_bypass() is True


# ---------- Claude guard ----------


def test_claude_main_exits_when_paused(monkeypatch):
    pause.set_pause(60)
    try:
        assert run_main(claude_guard, monkeypatch, "{}")[0] == 0
    finally:
        pause.clear_pause()


def test_claude_main_bypass_and_bad_json(monkeypatch, _bypass_file):
    _bypass_file.write_text("")
    assert run_main(claude_guard, monkeypatch, "{}")[0] == 0
    assert run_main(claude_guard, monkeypatch, "not json")[0] == 0


def test_claude_bash_command_gets_wrapped(monkeypatch):
    code, out, _ = run_main(claude_guard, monkeypatch, {"tool_name": "Bash", "tool_input": {"command": "ls"}})
    assert code == 0
    assert json.loads(out)["tool_input"]["command"] == f"(ls) 2>&1 | python3 -m {claude_guard.CAP_MODULE}"


def test_claude_bash_already_capped_is_left_alone(monkeypatch):
    command = f"ls | python3 -m {claude_guard.CAP_MODULE}"
    code, out, _ = run_main(claude_guard, monkeypatch, {"tool_name": "Bash", "tool_input": {"command": command}})
    assert (code, out) == (0, "")


def _read_payload(path, tmp_path, **tool_input):
    return {"tool_name": "Read", "tool_input": {"file_path": str(path), **tool_input}, "cwd": str(tmp_path)}


def test_claude_read_missing_file_passes(monkeypatch, tmp_path):
    assert run_main(claude_guard, monkeypatch, _read_payload(tmp_path / "missing", tmp_path))[0] == 0


def test_claude_read_whitelisted_passes(monkeypatch, tmp_path):
    (tmp_path / ".guardians.json").write_text(json.dumps({"whitelist": ["big.log"]}))
    big = tmp_path / "big.log"
    big.write_text("x")
    monkeypatch.setenv("GUARDIANS_TEST_SIZE", "5000000")
    assert run_main(claude_guard, monkeypatch, _read_payload(big, tmp_path))[0] == 0


def test_claude_read_small_file_passes(monkeypatch, tmp_path):
    small = tmp_path / "small.txt"
    small.write_text("hello")
    assert run_main(claude_guard, monkeypatch, _read_payload(small, tmp_path))[0] == 0


def test_claude_read_with_tight_limit_passes(monkeypatch, tmp_path):
    big = tmp_path / "big.log"
    big.write_text("x")
    monkeypatch.setenv("GUARDIANS_TEST_SIZE", "5000000")
    assert run_main(claude_guard, monkeypatch, _read_payload(big, tmp_path, limit=50))[0] == 0


def test_claude_webfetch_without_url_passes(monkeypatch):
    assert run_main(claude_guard, monkeypatch, {"tool_name": "WebFetch", "tool_input": {}})[0] == 0


def test_claude_webfetch_metadata_failure_passes(monkeypatch):
    def boom(url):
        raise OSError("offline")

    monkeypatch.setattr(claude_guard, "url_head_metadata", boom)
    payload = {"tool_name": "WebFetch", "tool_input": {"url": "https://example.invalid/x"}}
    assert run_main(claude_guard, monkeypatch, payload)[0] == 0


# ---------- Codex guard ----------


def test_codex_main_exits_when_paused(monkeypatch):
    pause.set_pause(60)
    try:
        assert run_main(codex_guard, monkeypatch, "{}")[1] == ""
    finally:
        pause.clear_pause()


def test_codex_main_bypass_bad_json_and_bad_shell(monkeypatch, _bypass_file):
    _bypass_file.write_text("")
    assert run_main(codex_guard, monkeypatch, "{}")[1] == ""
    assert run_main(codex_guard, monkeypatch, "not json")[1] == ""
    unbalanced = {"tool_name": "Bash", "tool_input": {"command": "cat 'unterminated"}}
    assert run_main(codex_guard, monkeypatch, unbalanced)[1] == ""


def test_resolve_candidate_files(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    assert codex_guard.resolve_candidate_files([], str(tmp_path)) == []
    assert codex_guard.resolve_candidate_files(["ls", "a.txt"], str(tmp_path)) == []
    assert codex_guard.resolve_candidate_files(["cat", "-n", "a.txt", "nope"], str(tmp_path)) == [str(f)]


@pytest.mark.parametrize(
    "parts, reaches",
    [
        (["curl", "-sLO", "https://x"], False),
        (["curl", "-fsSLo", "out", "https://x"], False),
        (["curl", "--output=f", "https://x"], False),
        (["wget", "-O", "-", "https://x"], True),
        (["wget", "--output-document=-", "https://x"], True),
        (["wget", "-O-", "https://x"], True),
        (["wget", "-qO-", "https://x"], True),
        (["wget", "https://x"], False),
    ],
)
def test_fetch_output_reaches_context(parts, reaches):
    assert codex_guard.fetch_output_reaches_context(parts) is reaches


def test_resolve_candidate_urls_empty():
    assert codex_guard.resolve_candidate_urls([]) == []


def test_codex_url_metadata_failure_allows(monkeypatch):
    def boom(url):
        raise OSError("offline")

    monkeypatch.setattr(codex_guard, "url_head_metadata", boom)
    payload = {"tool_name": "Bash", "tool_input": {"command": "curl https://example.invalid/big"}}
    assert run_main(codex_guard, monkeypatch, payload)[1] == ""


def test_codex_whitelisted_file_allows(monkeypatch, tmp_path):
    (tmp_path / ".guardians.json").write_text(json.dumps({"whitelist": ["big.log"]}))
    (tmp_path / "big.log").write_text("x")
    monkeypatch.setenv("GUARDIANS_TEST_SIZE", "5000000")
    payload = {"tool_name": "Bash", "tool_input": {"command": "cat big.log"}, "cwd": str(tmp_path)}
    assert run_main(codex_guard, monkeypatch, payload)[1] == ""


def test_codex_small_file_and_other_tools_allow(monkeypatch, tmp_path):
    (tmp_path / "small.txt").write_text("hello")
    payload = {"tool_name": "Bash", "tool_input": {"command": "cat small.txt"}, "cwd": str(tmp_path)}
    assert run_main(codex_guard, monkeypatch, payload)[1] == ""
    assert run_main(codex_guard, monkeypatch, {"tool_name": "Read"})[1] == ""
