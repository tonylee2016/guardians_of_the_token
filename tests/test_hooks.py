import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

import pytest

from guardians_of_the_token.test_server import make_server


class NoLengthHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

    def do_GET(self):
        body = b"small response"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args):
        return


def make_no_length_server():
    return ThreadingHTTPServer(("127.0.0.1", 0), NoLengthHandler)


def run_module(module: str, payload: dict, env: Optional[dict] = None):
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", module],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=full_env,
        check=False,
    )


def test_codex_pre_hook_blocks_large_file_with_shared_template(tmp_path):
    target = tmp_path / "large.txt"
    target.write_text("small")
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": f"cat {target.name}"},
        "cwd": str(tmp_path),
        "model": "gpt-5.5",
        "transcript_path": "",
    }

    result = run_module(
        "guardians_of_the_token.codex.pre_tool_guard",
        payload,
        {"GUARDIANS_TEST_SIZE": "800000"},
    )

    assert result.returncode == 0
    response = json.loads(result.stdout)
    reason = response["hookSpecificOutput"]["permissionDecisionReason"]
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert reason.startswith("🛡️ Guardians of the Token blocked this command.")
    assert f"Target: {target}" in reason
    assert "Estimate: ~200,000 tokens (50% of the 400,000-token window on gpt-5.5)" in reason
    assert "- Inspect the beginning" in reason
    assert "- Bypass once for the full file" in reason
    assert "sed -n" not in reason


def test_codex_pre_hook_blocks_large_url_with_shared_template():
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "curl -i https://guardians-test/compact"},
        "model": "gpt-5.5",
        "transcript_path": "",
    }

    result = run_module("guardians_of_the_token.codex.pre_tool_guard", payload)

    assert result.returncode == 0
    response = json.loads(result.stdout)
    reason = response["hookSpecificOutput"]["permissionDecisionReason"]
    assert reason.startswith("🛡️ Guardians of the Token blocked this command.")
    assert "Target: https://guardians-test/compact" in reason
    assert "- Inspect metadata" in reason
    assert "- Bypass once for the full response" in reason


def test_codex_post_hook_suppresses_large_output_with_shared_template():
    payload = {"tool_name": "Bash", "tool_response": {"output": "small"}}

    result = run_module(
        "guardians_of_the_token.codex.post_tool_guard",
        payload,
        {"GUARDIANS_TEST_TOKENS": "99999"},
    )

    assert result.returncode == 0
    response = json.loads(result.stdout)
    assert response["decision"] == "block"
    assert response["reason"].startswith("🛡️ Guardians of the Token suppressed large Bash output.")
    assert "- Inspect a smaller slice" in response["reason"]


def test_claude_pre_hook_blocks_read_with_shared_template(tmp_path):
    target = tmp_path / "large.txt"
    target.write_text("small")
    payload = {
        "tool_name": "Read",
        "tool_input": {"file_path": str(target)},
        "transcript_path": "",
    }

    result = run_module(
        "guardians_of_the_token.claude.pre_tool_guard",
        payload,
        {"GUARDIANS_TEST_SIZE": "800000"},
    )

    assert result.returncode == 2
    assert result.stderr.startswith("🛡️ Guardians of the Token blocked this request.")
    assert f"Target: {target}" in result.stderr
    assert "- Inspect the beginning" in result.stderr
    assert "- Bypass once for the full file" in result.stderr


def test_claude_pre_hook_blocks_webfetch_with_shared_template():
    payload = {
        "tool_name": "WebFetch",
        "tool_input": {"url": "https://guardians-test/compact"},
        "transcript_path": "",
    }

    result = run_module("guardians_of_the_token.claude.pre_tool_guard", payload)

    assert result.returncode == 2
    assert result.stderr.startswith("🛡️ Guardians of the Token blocked this request.")
    assert "Target: https://guardians-test/compact" in result.stderr
    assert "- Inspect metadata" in result.stderr
    assert "- Bypass once for the full response" in result.stderr


def test_claude_pre_hook_blocks_webfetch_from_real_head_content_length():
    try:
        server = make_server("127.0.0.1", 0)
    except PermissionError:
        pytest.skip("sandbox does not allow binding localhost sockets")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    url = f"http://{host}:{port}/compact"
    payload = {
        "tool_name": "WebFetch",
        "tool_input": {"url": url},
        "transcript_path": "",
    }

    try:
        result = run_module("guardians_of_the_token.claude.pre_tool_guard", payload)
    finally:
        server.shutdown()
        server.server_close()

    assert result.returncode == 2
    assert result.stderr.startswith("🛡️ Guardians of the Token blocked this request.")
    assert f"Target: {url}" in result.stderr
    assert "Estimate: ~200,000 tokens" in result.stderr
    assert "Risk: likely context compaction." in result.stderr


def test_claude_pre_hook_allows_webfetch_when_head_has_no_content_length():
    try:
        server = make_no_length_server()
    except PermissionError:
        pytest.skip("sandbox does not allow binding localhost sockets")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    url = f"http://{host}:{port}/dynamic"
    payload = {
        "tool_name": "WebFetch",
        "tool_input": {"url": url},
        "transcript_path": "",
    }

    try:
        result = run_module("guardians_of_the_token.claude.pre_tool_guard", payload)
    finally:
        server.shutdown()
        server.server_close()

    assert result.returncode == 0
    assert result.stderr == ""


def test_codex_pre_hook_allows_fetch_when_head_has_no_content_length():
    try:
        server = make_no_length_server()
    except PermissionError:
        pytest.skip("sandbox does not allow binding localhost sockets")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    url = f"http://{host}:{port}/dynamic"
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": f"curl {url}"},
        "model": "gpt-5.5",
        "transcript_path": "",
    }

    try:
        result = run_module("guardians_of_the_token.codex.pre_tool_guard", payload)
    finally:
        server.shutdown()
        server.server_close()

    assert result.returncode == 0
    assert result.stdout == ""


def test_claude_post_hook_suppresses_large_output_with_shared_template():
    payload = {"tool_name": "Bash", "tool_response": {"output": "small"}}

    result = run_module(
        "guardians_of_the_token.claude.post_tool_guard",
        payload,
        {"GUARDIANS_TEST_TOKENS": "99999"},
    )

    assert result.returncode == 2
    assert result.stdout.startswith("🛡️ Guardians of the Token suppressed large Bash output.")
    assert "- Inspect a smaller slice" in result.stdout
