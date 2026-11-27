"""Tests for the small helper modules: guard, utils, proxy, cap_output, status."""

import io
import json
import subprocess
import sys

import pytest

from guardians_of_the_token import guard, status, utils


# ---------- utils / guard ----------


def test_count_tokens_and_cost():
    assert utils.count_tokens("abcd" * 10) == 10
    assert utils.count_tokens([{"a": 1}, "xyz"]) == len("{'a': 1}xyz") // 4
    assert utils.estimate_cost(1_000_000) == 3.0


@pytest.mark.parametrize(
    "size, expected",
    [(10, "ok"), (guard.MAX_SAFE * 4 + 40, "confirm"), (guard.HARD_CAP * 4 + 40, "blocked")],
)
def test_check_messages(size, expected):
    result = guard.check_messages(["x" * size])
    assert result["status"] == expected


def test_handle_tool_output():
    assert guard.handle_tool_output("short")["status"] == "ok"
    assert guard.handle_tool_output("x" * (guard.TOOL_SOFT_CAP * 4 + 8))["status"] == "confirm_tool"


# ---------- proxy ----------


@pytest.fixture
def proxy_client(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from guardians_of_the_token import proxy

    sent = []

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"ok": True}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, headers, json, timeout):
            sent.append((url, headers, json))
            return FakeResponse()

    monkeypatch.setattr(proxy.httpx, "AsyncClient", FakeClient)
    return TestClient(proxy.app), sent, proxy


@pytest.mark.parametrize("route, upstream", [("anthropic", "https://api.anthropic.com"), ("openai", "https://api.openai.com")])
def test_proxy_forwards_small_requests(proxy_client, route, upstream):
    client, sent, _ = proxy_client
    resp = client.post(f"/{route}/v1/messages", json={"messages": [{"content": "hi"}]}, headers={"x-api-key": "k"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    url, headers, body = sent[0]
    assert url == f"{upstream}/v1/messages"
    assert "host" not in {k.lower() for k in headers}
    assert body == {"messages": [{"content": "hi"}]}


def test_proxy_asks_for_confirmation(proxy_client):
    client, sent, _ = proxy_client
    resp = client.post("/anthropic/v1/messages", json={"messages": ["x" * 100_000]})
    assert resp.status_code == 202
    assert resp.json()["guardians_warning"] is True
    assert sent == []


def test_proxy_blocks_huge_requests(proxy_client):
    client, _, _ = proxy_client
    resp = client.post("/openai/v1/chat/completions", json={"messages": ["x" * 500_000]})
    assert resp.status_code == 413


def test_proxy_health_and_main(proxy_client, monkeypatch):
    client, _, proxy = proxy_client
    assert client.get("/health").json()["status"] == "ok"
    calls = []
    monkeypatch.setattr(proxy.uvicorn, "run", lambda *a, **k: calls.append((a, k)))
    proxy.main()
    assert calls[0][1]["port"] == 8080


# ---------- cap_output ----------


def _cap(data: bytes):
    return subprocess.run(
        [sys.executable, "-m", "guardians_of_the_token.claude.cap_output"],
        input=data,
        capture_output=True,
        check=True,
    ).stdout


def test_cap_output_passes_small_output_through():
    assert _cap(b"hello\n") == b"hello\n"


def test_cap_output_truncates_large_output():
    out = _cap(b"y" * 50_000)
    assert out.startswith(b"y" * 32_000)
    assert b"output capped at 8,000 tokens (12,500 estimated total)" in out


# ---------- status ----------


def _write_events(base, events):
    got = base / ".got"
    got.mkdir()
    (got / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")


def test_status_line_walks_up_to_got_dir(tmp_path, monkeypatch):
    _write_events(tmp_path, [{"action": "blocked", "estimated_tokens": 500, "estimated_cost": 0.01}])
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    assert status.status_line() == "🛡️ 1× · 500 tok · $0.01"


def test_status_line_without_got_dir_uses_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert status.status_line() == "🛡️ on"


def test_cwd_from_stdin(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"cwd": "/x"}'))
    assert status._cwd_from_stdin() == "/x"
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert status._cwd_from_stdin() is None

    class Tty(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr(sys, "stdin", Tty(""))
    assert status._cwd_from_stdin() is None


def test_status_main_with_project_arg(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["guardians-status", str(tmp_path)])
    status.main()
    assert capsys.readouterr().out.strip() == "🛡️ on"


def test_status_main_reads_cwd_from_stdin(tmp_path, monkeypatch, capsys):
    _write_events(tmp_path, [{"action": "suppressed", "estimated_tokens": 2_000}])
    monkeypatch.setattr(sys, "argv", ["guardians-status"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"cwd": str(tmp_path)})))
    status.main()
    assert capsys.readouterr().out.strip() == "🛡️ 1× · 2k tok · $0.00"
