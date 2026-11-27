"""Edge-case tests for codex post-tool guard, estimate, transcript, and update."""

import io
import json
import subprocess
import sys
from importlib import metadata

import pytest

from guardians_of_the_token import estimate, pause, transcript, update
from guardians_of_the_token.update import latest_pypi_version as real_latest_pypi_version
from guardians_of_the_token.codex import post_tool_guard as codex_post


def run_main(module, monkeypatch, stdin):
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin if isinstance(stdin, str) else json.dumps(stdin)))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    module.main()
    return out.getvalue()


# ---------- codex post_tool_guard ----------


@pytest.mark.parametrize(
    "response, expected",
    [
        ("plain", "plain"),
        ({"output": "o"}, "o"),
        ({"stdout": "a", "stderr": "b"}, "a\nb"),
        ({"x": 1}, '{"x": 1}'),
        ([{"text": "t"}, {"output": "o"}, 3], "t o 3"),
        (7, "7"),
    ],
)
def test_codex_extract_text(response, expected):
    assert codex_post.extract_text(response) == expected


def test_codex_post_edge_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(codex_post, "startup_auto_update", lambda: None)
    monkeypatch.delenv("GUARDIANS_TEST_TOKENS", raising=False)
    assert run_main(codex_post, monkeypatch, "bad json") == ""
    assert run_main(codex_post, monkeypatch, {"tool_name": "Read"}) == ""
    small = {"tool_name": "Bash", "tool_response": "tiny", "cwd": str(tmp_path)}
    assert run_main(codex_post, monkeypatch, small) == ""
    assert codex_post.count_tokens("x" * 8) == 2
    pause.set_pause(60)
    try:
        assert run_main(codex_post, monkeypatch, small) == ""
    finally:
        pause.clear_pause()


# ---------- estimate ----------


def test_base_ratio_for_binary_extension():
    assert estimate._base_ratio_for_extension(".png") == 0.12
    assert estimate._base_ratio_for_extension("photo.png") == 0.12


def test_is_likely_binary_sample():
    assert estimate._is_likely_binary_sample(b"") is False
    assert estimate._is_likely_binary_sample(b"a\x00b") is True
    assert estimate._is_likely_binary_sample(b"\x01\x02\x03abc") is True


def test_sample_ratio_variants(tmp_path):
    binary = tmp_path / "b.dat"
    binary.write_bytes(b"\x00" * 100)
    assert estimate._sample_ratio(binary, 0.05, 4096) == (0.12, False)

    undecodable = tmp_path / "u.txt"
    undecodable.write_bytes(b"\xff\xfe" * 10)
    assert estimate._sample_ratio(undecodable, 0.2, 4096) == (0.2, False)

    b64 = tmp_path / "b64.txt"
    b64.write_text("QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo=" * 20)
    assert estimate._sample_ratio(b64, 0.25, 4096)[0] >= 0.38

    unicode_text = tmp_path / "jp.txt"
    unicode_text.write_text("日本語のテキストです。" * 50)
    assert estimate._sample_ratio(unicode_text, 0.25, 4096)[0] >= 0.40

    prose = tmp_path / "prose.txt"
    prose.write_text("a b c d e f g h\n" * 100)
    ratio, sampled = estimate._sample_ratio(prose, 0.5, 4096)
    assert sampled is True
    assert 0.23 <= ratio <= 0.26


@pytest.mark.parametrize(
    "url, content_type, expected",
    [
        ("https://x/a", "image/png", (0.12, "binary-content-type")),
        ("https://x/a", "text/csv", (0.31, "csv-content-type")),
        ("https://x/a.py", None, (estimate._base_ratio_for_extension(".py"), "url-extension")),
        ("https://x/a", None, (0.25, "default")),
    ],
)
def test_base_ratio_for_url(url, content_type, expected):
    assert estimate._base_ratio_for_url(url, content_type) == expected


# ---------- transcript ----------


def test_effective_context_window_beyond_known():
    assert transcript.effective_context_window(200_000, 2_500_000) == 2_500_000


def test_read_segment_empty_and_missing(tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    assert transcript._read_segment(str(empty), offset=0, size=10) == ""
    assert transcript._read_segment(str(tmp_path / "missing"), offset=0, size=10) == ""


def test_iter_entries_skips_bad_json():
    assert list(transcript._iter_entries('{"a": 1}\n\nbad\n')) == [{"a": 1}]


def test_entry_classifiers():
    assert transcript._is_synthetic_user_entry({"type": "user", "message": {"content": ""}}) is False
    assert transcript._is_assistant_text({"type": "assistant", "isSidechain": True}) is False
    assert transcript._is_assistant_text({"type": "assistant", "isApiErrorMessage": True}) is False


def test_live_context_tokens_skips_api_errors():
    lines = [
        {"type": "assistant", "message": {"usage": {"input_tokens": 10}}},
        {"type": "assistant", "isApiErrorMessage": True, "message": {"usage": {"input_tokens": 999}}},
    ]
    assert transcript._live_context_tokens("\n".join(json.dumps(x) for x in lines)) == 10


def test_truncate_long_text():
    assert transcript._truncate("word " * 100, limit=12) == "word word wo..."


def test_read_signals_without_any_anchor(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text(json.dumps({"type": "assistant", "message": {"usage": {"input_tokens": 5}}}) + "\n")
    signals = transcript.read_signals(str(path))
    assert signals.anchor_source == "none"
    assert signals.anchor_chunks == []


# ---------- update ----------


def test_write_user_config_ignores_errors(monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setattr(update, "user_config_path", lambda: blocker / "sub" / ".guardians.json")
    update._write_user_config({"a": 1})


def test_current_version_falls_back(monkeypatch):
    def missing(name):
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(update.metadata, "version", missing)
    assert update.current_version() == update.__version__


def test_parse_time_variants():
    assert update._parse_time("not a date") is None
    assert update._parse_time("2026-01-01T00:00:00").tzinfo is not None


def test_run_pip_upgrade(monkeypatch):
    calls = []
    monkeypatch.setattr(update.subprocess, "run", lambda args, **k: calls.append(args) or "done")
    assert update.run_pip_upgrade() == "done"
    assert calls[0][-1] == update.PACKAGE_NAME


def _set_config(tmp_path, **values):
    path = tmp_path / ".guardians.json"
    path.write_text(json.dumps(values))
    return path


@pytest.fixture
def user_config(monkeypatch, tmp_path):
    path = tmp_path / ".guardians.json"
    monkeypatch.setattr(update, "user_config_path", lambda: path)
    monkeypatch.setattr(update, "current_version", lambda: "1.4.0")
    return path


def test_update_checks_disabled(user_config):
    user_config.write_text(json.dumps({"update_check_enabled": False}))
    assert update.maybe_auto_update().status == "disabled"


def test_update_check_failure(user_config, monkeypatch):
    def boom():
        raise OSError("offline")

    monkeypatch.setattr(update, "latest_pypi_version", boom)
    result = update.maybe_auto_update(force=True)
    assert result.status == "error"
    assert json.loads(user_config.read_text())["last_update_error"] == "offline"


def test_update_already_current(user_config, monkeypatch):
    monkeypatch.setattr(update, "latest_pypi_version", lambda: "1.4.0")
    assert update.maybe_auto_update(force=True).status == "current"


def test_update_pip_raises(user_config, monkeypatch):
    user_config.write_text(json.dumps({"auto_update_enabled": True}))
    monkeypatch.setattr(update, "latest_pypi_version", lambda: "1.5.0")

    def boom():
        raise subprocess.TimeoutExpired("pip", 20)

    monkeypatch.setattr(update, "run_pip_upgrade", boom)
    assert update.maybe_auto_update().status == "error"


def test_update_pip_fails(user_config, monkeypatch):
    user_config.write_text(json.dumps({"auto_update_enabled": True}))
    monkeypatch.setattr(update, "latest_pypi_version", lambda: "1.5.0")
    monkeypatch.setattr(
        update, "run_pip_upgrade", lambda: subprocess.CompletedProcess([], 1, stdout="", stderr="line1\nERROR: no space")
    )
    result = update.maybe_auto_update()
    assert result.status == "error"
    assert result.message.endswith("ERROR: no space")


def test_update_main(monkeypatch, capsys):
    monkeypatch.setattr(update, "maybe_auto_update", lambda force: update.UpdateResult("current", "1", "1", "ok"))
    update.main()
    assert capsys.readouterr().out.strip() == "ok"
    monkeypatch.setattr(update, "maybe_auto_update", lambda force: update.UpdateResult("error", "1", None, "bad"))
    with pytest.raises(SystemExit):
        update.main()


def test_non_assistant_entry_is_not_assistant_text():
    assert transcript._is_assistant_text({"type": "user"}) is False


def test_latest_pypi_version(monkeypatch):
    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(
        update.urllib.request, "urlopen", lambda url, timeout: Resp(json.dumps({"info": {"version": "9.0.0"}}).encode())
    )
    assert real_latest_pypi_version() == "9.0.0"


def test_update_rate_limited(user_config):
    user_config.write_text(
        json.dumps({"auto_update_enabled": True, "last_update_check": update._now().isoformat()})
    )
    assert update.maybe_auto_update().status == "skipped"
