"""Edge-case tests for config, state, mcp_server, and the default pause path."""

import io
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from guardians_of_the_token import config, mcp_server, pause, state


# ---------- config ----------


def test_load_json_bad_file(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{nope")
    assert config._load_json(bad) == {}


@pytest.mark.parametrize(
    "raw, value",
    [('"text"', "text"), ("'text'", "text"), ("TRUE", True), ("false", False), ("1.5", 1.5), ("7", 7), ("abc", "abc")],
)
def test_parse_scalar(raw, value):
    assert config._parse_scalar(raw) == value


def test_parse_array_variants():
    assert config._parse_array("[]") == []
    assert config._parse_array("[\"a,b\", 'c', 3, ]") == ["a,b", "c", 3]


def test_load_toml_subset_full(tmp_path):
    path = tmp_path / ".guardians.toml"
    path.write_text(
        "\n".join(
            [
                "# comment",
                "",
                "warn_threshold_pct = 15  # inline comment",
                "whitelist = [\"README.md\", \"docs/**\"]",
                "not a key value line",
                "[]",
                "[prompt_guard]",
                "enabled = false",
            ]
        )
    )
    assert config._load_toml_subset(path) == {
        "warn_threshold_pct": 15,
        "whitelist": ["README.md", "docs/**"],
        "prompt_guard": {"enabled": False},
    }


def test_load_toml_subset_unreadable(tmp_path):
    assert config._load_toml_subset(tmp_path) == {}  # a directory can't be read as text


def test_load_file_config_unknown_suffix(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("a: 1")
    assert config.load_file_config(path) == {}


def test_input_price_for_model_prices():
    cfg = {
        "default_input_price_per_million": 3.0,
        "prices": {"claude-opus": {"input_per_million": 15}, "gpt-5": 1.25, "broken": "n/a"},
    }
    assert config.input_price_for(cfg, "claude-opus-5-5") == 15.0
    assert config.input_price_for(cfg, "gpt-5.5") == 1.25
    assert config.input_price_for(cfg, "broken-model") == 3.0
    assert config.input_price_for({"default_input_price_per_million": "bad"}) == 3.0


def test_path_candidates_outside_project(tmp_path):
    candidates = config._path_candidates("/elsewhere/file.txt", str(tmp_path))
    assert candidates[0] == "/elsewhere/file.txt"


def test_policy_decision_ignored(tmp_path):
    cfg = {"ignore": ["*.log"], "whitelist": []}
    assert config.policy_decision(str(tmp_path / "x.log"), cfg) == "ignored"


# ---------- pause ----------


def test_default_pause_file_location(monkeypatch):
    monkeypatch.delenv("GUARDIANS_PAUSE_FILE")
    assert pause.pause_file() == Path.home() / ".got" / "pause_until"


# ---------- state ----------


def _entry(**kw):
    return json.dumps(kw)


def test_tool_uses_skips_malformed_messages():
    assert list(state._tool_uses({"type": "user"})) == []
    assert list(state._tool_uses({"type": "assistant", "message": "text"})) == []
    assert list(state._tool_uses({"type": "assistant", "message": {"content": "text"}})) == []


def test_dedupe_recent_first_respects_limit():
    assert state._dedupe_recent_first(["a", "b", "c", "b"], 2) == ["b", "c"]


@pytest.mark.parametrize(
    "head, branch",
    [("ref: refs/heads/feature/x", "feature/x"), ("ref:main", "main"), ("0123456789abcdef0123", "0123456789ab")],
)
def test_git_branch_variants(tmp_path, head, branch):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text(head)
    assert state._git_branch(tmp_path) == branch


def test_read_transcript_head_and_tail(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "_HEAD_BYTES", 10)
    monkeypatch.setattr(state, "_STATE_TAIL_BYTES", 10)
    path = tmp_path / "t.jsonl"
    path.write_text("A" * 10 + "middle" + "Z" * 10)
    assert state._read_transcript(str(path)) == "A" * 10 + "\n" + "Z" * 10
    assert state._read_transcript("") == ""


def test_build_state_uses_compact_summary_and_skips_synthetic(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text(
        "\n".join(
            [
                _entry(type="user", isCompactSummary=True, message={"content": "Compacted summary of work"}),
                _entry(type="user", message={"content": "<command-name>/clear</command-name>"}),
                _entry(type="user", message={"content": "Fix the login bug"}),
            ]
        )
    )
    built = state.build_state(str(path), trigger="t", base_dir=tmp_path, settings={}, live_tokens=None, context_pct=None)
    assert built.summary_source == "compact"
    assert built.goals == ["Fix the login bug"]


def test_build_state_without_summary(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text(_entry(type="user", message={"content": "hello"}))
    assert state.build_state(str(path), trigger="t", base_dir=tmp_path, settings={}, live_tokens=None, context_pct=None).summary_source == "none"


def test_render_markdown_includes_branch(tmp_path):
    built = state.build_state("", trigger="t", base_dir=tmp_path, settings={}, live_tokens=None, context_pct=None)
    built.git_branch = "main"
    assert "- Git branch: main" in state.render_markdown(built)


def test_save_state_unwritable(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("GUARDIANS_INBOX", str(blocker / "got"))
    built = state.build_state("", trigger="t", base_dir=tmp_path, settings={}, live_tokens=None, context_pct=None)
    assert state.save_state(built, base_dir=tmp_path) is None


def test_parse_iso_variants():
    assert state._parse_iso("") is None
    assert state._parse_iso("nope") is None
    assert state._parse_iso("2026-01-01T00:00:00").tzinfo is not None


def test_capture_disabled(tmp_path):
    assert state.capture("", base_dir=tmp_path, trigger="t", config={"project_state": {"enabled": False}}) is None


def _write_snapshot(tmp_path, **data):
    got = tmp_path / ".got"
    got.mkdir(exist_ok=True)
    (got / "project_state.json").write_text(json.dumps(data))


def test_should_refresh_with_bad_timestamp(tmp_path):
    _write_snapshot(tmp_path, saved_at="garbage")
    assert state._should_refresh(tmp_path, min_interval_seconds=300) is True


def test_maybe_capture_on_pressure_guards(tmp_path, monkeypatch):
    disabled = {"project_state": {"enabled": False}}
    assert state.maybe_capture_on_pressure("", base_dir=tmp_path, live_tokens=1, context_pct=0.9, config=disabled) is None
    assert state.maybe_capture_on_pressure("", base_dir=tmp_path, live_tokens=None, context_pct=None) is None

    def boom(*a, **k):
        raise RuntimeError("disk")

    monkeypatch.setattr(state, "capture", boom)
    assert state.maybe_capture_on_pressure("", base_dir=tmp_path, live_tokens=1, context_pct=0.9) is None


def test_resume_offer_rejections(tmp_path):
    assert state.resume_offer(tmp_path, config={"project_state": {"enabled": False}}) is None
    assert state.resume_offer(tmp_path) is None  # no snapshot yet
    _write_snapshot(tmp_path, project="/somewhere/else", saved_at=datetime.now(timezone.utc).isoformat())
    assert state.resume_offer(tmp_path) is None
    _write_snapshot(tmp_path, project=str(tmp_path), saved_at="garbage")
    assert state.resume_offer(tmp_path) is None


@pytest.mark.parametrize("hours, text", [(0.2, "min ago"), (5, "saved 5h ago"), (72, "saved 3 days ago")])
def test_humanize_age(hours, text):
    assert text in state._humanize_age(hours)


# ---------- mcp_server ----------


def test_safe_path_requires_value():
    with pytest.raises(ValueError, match="path is required"):
        mcp_server.safe_path("")


def test_source_metadata_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        mcp_server.source_metadata(tmp_path / "missing", {})


@pytest.mark.parametrize(
    "meta, text",
    [
        ({"policy": "whitelisted"}, "whitelisted"),
        ({"policy": "ignored"}, "ignored"),
    ],
)
def test_warning_for_policy(meta, text):
    assert text in mcp_server.warning_for(meta)


def test_warning_for_url_safe():
    assert "within the configured" in mcp_server.warning_for_url({"risk": "safe"})


def test_url_storage_root_variants(tmp_path, monkeypatch):
    monkeypatch.delenv("GUARDIANS_INBOX", raising=False)
    f = tmp_path / "a.txt"
    f.write_text("x")
    assert mcp_server.url_storage_root({"project_path": str(f)}) == f.parent.resolve()
    monkeypatch.chdir(tmp_path)
    assert mcp_server.url_storage_root({}) == tmp_path.resolve()


def test_url_metadata_rejects_non_http():
    with pytest.raises(ValueError, match="http"):
        mcp_server.url_metadata("ftp://x", {})


def test_project_init_variants(tmp_path):
    with pytest.raises(FileNotFoundError):
        mcp_server.got_project_init({"path": str(tmp_path / "missing")})
    f = tmp_path / "file.txt"
    f.write_text("x")
    result = mcp_server.got_project_init({"path": str(f)})
    assert Path(result["got_dir"]) == tmp_path.resolve() / ".got"


def test_read_url_index_corrupt(tmp_path, monkeypatch):
    monkeypatch.setenv("GUARDIANS_INBOX", str(tmp_path / ".got"))
    (path,) = mcp_server.url_index_paths("https://x/y", mcp_server.url_storage_root({}))
    path.parent.mkdir(parents=True)
    path.write_text("{corrupt")
    assert mcp_server.read_url_index("https://x/y", {}) is None


def test_file_tail_search_and_chunk(tmp_path):
    f = tmp_path / "log.txt"
    f.write_text("".join(f"line {i}\n" for i in range(1, 11)))
    assert mcp_server.got_file_tail({"path": str(f), "lines": 2}) == "9\tline 9\n10\tline 10"
    with pytest.raises(ValueError, match="query is required"):
        mcp_server.got_file_search({"path": str(f)})
    assert mcp_server.got_file_search({"path": str(f), "query": "line", "max_matches": 2}).count("\n") == 1
    chunk = mcp_server.got_file_chunk_summary({"path": str(f), "start_line": 3, "line_count": 2})
    assert f"Chunk: {f.resolve()}:3-4" in chunk
    assert "line 3\nline 4" in chunk


def test_dispatch_misc_methods():
    assert mcp_server.dispatch({"method": "notifications/initialized"}) is None
    assert mcp_server.dispatch({"method": "ping", "id": 1})["result"] == {}
    assert mcp_server.dispatch({"method": "tools/call", "id": 2, "params": {"name": "nope"}})["error"]["code"] == -32602
    assert mcp_server.dispatch({"method": "bogus", "id": 3})["error"]["code"] == -32601
    assert "error" in mcp_server.make_error(1, -1, "x")


def test_main_handles_blank_bad_and_failing_requests(monkeypatch):
    monkeypatch.setattr(mcp_server, "startup_auto_update", lambda: None)
    lines = [
        "",
        "{not json",
        json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                    "params": {"name": "got_file_tail", "arguments": {"path": "/definitely/missing"}}}),
    ]
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n".join(lines) + "\n"))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    mcp_server.main()
    responses = [json.loads(line) for line in out.getvalue().splitlines()]
    assert responses[0]["error"]["code"] == -32700
    assert responses[1]["error"]["code"] == -32603
    assert responses[1]["id"] == 7
