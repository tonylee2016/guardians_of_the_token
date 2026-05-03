import json
import pytest

from guardians_of_the_token import mcp_server


def call_tool(name: str, arguments: dict):
    response = mcp_server.dispatch(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )
    text = response["result"]["content"][0]["text"]
    return json.loads(text)


def test_mcp_lists_guardians_project_tools():
    response = mcp_server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

    names = {tool["name"] for tool in response["result"]["tools"]}

    assert "got_project_init" in names
    assert "got_file_size" in names
    assert "got_url_size" in names
    assert "got_file_head" in names
    assert "got_file_search" in names


def test_mcp_project_policy_does_not_route_safe_files_through_guardians():
    response = mcp_server.dispatch(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "got_project_policy", "arguments": {}},
        }
    )
    policy = response["result"]["content"][0]["text"]

    assert "If risk is `safe`, proceed normally with native tools." in policy
    assert "Do not route ordinary small-file work through GOT" in policy


def test_mcp_project_init_writes_policy_and_storage_dirs(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# Existing Instructions\n")

    result = call_tool("got_project_init", {"path": str(tmp_path)})

    got_dir = tmp_path / ".got"
    policy_path = got_dir / "GUARDIANS_PROJECT_POLICY.md"
    claude_path = tmp_path / "CLAUDE.md"
    assert result["got_dir"] == str(got_dir)
    assert result["claude_path"] == str(claude_path)
    assert policy_path.exists()
    assert (got_dir / "index").is_dir()
    assert "Before reading" in policy_path.read_text()
    assert "# Existing Instructions" in claude_path.read_text()
    assert "<!-- guardians-of-the-token:start -->" in claude_path.read_text()

    call_tool("got_project_init", {"path": str(tmp_path)})

    assert claude_path.read_text().count("<!-- guardians-of-the-token:start -->") == 1


def test_mcp_file_size_returns_file_metadata_without_cache(tmp_path):
    target = tmp_path / "source.log"
    target.write_text("alpha\nbeta\n")

    result = call_tool("got_file_size", {"path": str(target), "context_window": 100, "warn_threshold_pct": 20})

    assert result["kind"] == "file"
    assert result["path"] == str(target)
    assert result["risk"] == "safe"
    assert "cached" not in result
    assert "index_path" not in result


def test_mcp_file_size_does_not_fall_back_to_root_got_when_cwd_is_root(tmp_path, monkeypatch):
    target = tmp_path / "source.log"
    target.write_text("alpha\n")
    monkeypatch.delenv("GUARDIANS_INBOX", raising=False)
    monkeypatch.chdir("/")

    result = call_tool("got_file_size", {"path": str(target)})

    assert result["path"] == str(target)
    assert result["bytes"] > 0


def test_mcp_file_size_warns_for_large_source(tmp_path):
    target = tmp_path / "source.log"
    target.write_text("x" * 1000)

    result = call_tool(
        "got_file_size",
        {"path": str(target), "context_window": 1000, "warn_threshold_pct": 20},
    )

    assert result["risk"] == "warning"
    assert "🛡️ Guardians of the Token blocked this source." in result["warning"]


def test_mcp_uses_fake_size_for_project_local_guardians_fixture(tmp_path):
    target = tmp_path / "guardians_test_compact"
    target.write_text("small fixture\n")

    result = call_tool(
        "got_file_size",
        {"path": str(target), "context_window": 200_000, "warn_threshold_pct": 20},
    )

    assert result["bytes"] == 800_000
    assert result["estimated_tokens"] == 200_000
    assert result["risk"] == "critical"
    assert "Target:" in result["warning"]


def test_mcp_url_size_uses_fixture_size_and_warns(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    monkeypatch.setenv("GUARDIANS_INBOX", str(inbox))

    result = call_tool(
        "got_url_size",
        {"url": "https://guardians-test/compact", "context_window": 200_000},
    )

    assert result["kind"] == "url"
    assert result["bytes"] == 800_000
    assert result["estimated_tokens"] == 200_000
    assert result["risk"] == "critical"
    assert "Target: https://guardians-test/compact" in result["warning"]


def test_mcp_url_size_uses_cache(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    monkeypatch.setenv("GUARDIANS_INBOX", str(inbox))

    result = call_tool(
        "got_url_size",
        {"url": "https://guardians-test/large", "context_window": 200_000},
    )

    assert result["cached"] is False
    assert result["index_path"].startswith(str(inbox / "index"))

    cached = call_tool(
        "got_url_size",
        {"url": "https://guardians-test/large", "context_window": 200_000},
    )

    assert cached["cached"] is True


def test_mcp_url_size_handles_missing_content_length(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    monkeypatch.setenv("GUARDIANS_INBOX", str(inbox))
    monkeypatch.setattr(
        mcp_server,
        "url_head_metadata",
        lambda url: {"content_length": None, "content_type": "text/html"},
    )

    result = call_tool(
        "got_url_size",
        {"url": "https://example.test/no-length", "context_window": 200_000},
    )

    assert result["kind"] == "url"
    assert result["bytes"] is None
    assert result["risk"] == "unknown"
    assert "Content-Length was missing" in result["warning"]


def test_mcp_url_size_uses_project_path_for_storage_when_cwd_is_root(tmp_path, monkeypatch):
    monkeypatch.delenv("GUARDIANS_INBOX", raising=False)
    monkeypatch.chdir("/")

    result = call_tool(
        "got_url_size",
        {
            "url": "https://guardians-test/large",
            "context_window": 200_000,
            "project_path": str(tmp_path),
        },
    )

    assert result["index_path"].startswith(str(tmp_path / ".got"))


def test_mcp_file_size_rejects_directories(tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()

    with pytest.raises(ValueError, match="is not a regular file"):
        call_tool("got_file_size", {"path": str(source_dir)})


def test_mcp_bounded_file_tools(tmp_path):
    target = tmp_path / "source.log"
    target.write_text("alpha\nbeta\ngamma\nalpha again\n")

    head_response = mcp_server.dispatch(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "got_file_head",
                "arguments": {"path": str(target), "lines": 2},
            },
        }
    )
    search_response = mcp_server.dispatch(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "got_file_search",
                "arguments": {"path": str(target), "query": "alpha"},
            },
        }
    )

    assert head_response["result"]["content"][0]["text"] == "1\talpha\n2\tbeta"
    assert "4\talpha again" in search_response["result"]["content"][0]["text"]
