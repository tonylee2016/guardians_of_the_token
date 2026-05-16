import json
import io
from datetime import datetime, timedelta, timezone

from guardians_of_the_token import update


def test_update_available_skips_major_by_default():
    assert update.update_available("1.1.1", "1.1.2") is True
    assert update.update_available("1.1.1", "2.0.0") is False
    assert update.update_available("1.1.1", "2.0.0", include_major=True) is True


def test_check_due_respects_interval():
    config = {
        "auto_update_interval_hours": 24,
        "last_update_check": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
    }

    assert update.check_due(config) is False


def test_set_auto_update_writes_user_config(tmp_path, monkeypatch):
    config_path = tmp_path / ".guardians.json"
    monkeypatch.setattr(update, "user_config_path", lambda: config_path)

    update.set_auto_update(True)

    config = json.loads(config_path.read_text())
    assert config["update_check_enabled"] is True
    assert config["auto_update_enabled"] is True


def test_maybe_auto_update_notifies_without_install(tmp_path, monkeypatch):
    config_path = tmp_path / ".guardians.json"
    config_path.write_text(json.dumps({"update_check_enabled": True, "auto_update_enabled": False}))
    monkeypatch.setattr(update, "user_config_path", lambda: config_path)
    monkeypatch.setattr(update, "current_version", lambda: "1.1.1")
    monkeypatch.setattr(update, "latest_pypi_version", lambda: "1.1.2")

    result = update.maybe_auto_update(force=True)

    assert result.status == "available"
    assert "1.1.1 -> 1.1.2" in result.message


def test_maybe_auto_update_does_not_check_when_auto_update_disabled(tmp_path, monkeypatch):
    config_path = tmp_path / ".guardians.json"
    config_path.write_text(json.dumps({"update_check_enabled": True, "auto_update_enabled": False}))
    monkeypatch.setattr(update, "user_config_path", lambda: config_path)
    monkeypatch.setattr(update, "current_version", lambda: "1.1.1")

    def fail_latest():
        raise AssertionError("should not check PyPI")

    monkeypatch.setattr(update, "latest_pypi_version", fail_latest)

    result = update.maybe_auto_update()

    assert result.status == "disabled"


def test_maybe_auto_update_runs_pip_when_enabled(tmp_path, monkeypatch):
    config_path = tmp_path / ".guardians.json"
    config_path.write_text(json.dumps({"update_check_enabled": True, "auto_update_enabled": True}))
    monkeypatch.setattr(update, "user_config_path", lambda: config_path)
    monkeypatch.setattr(update, "current_version", lambda: "1.1.1")
    monkeypatch.setattr(update, "latest_pypi_version", lambda: "1.1.2")

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    called = []
    monkeypatch.setattr(update, "run_pip_upgrade", lambda: called.append(True) or Result())

    result = update.maybe_auto_update(force=True)

    assert result.status == "updated"
    assert called == [True]


def test_startup_auto_update_fails_open(monkeypatch):
    monkeypatch.setattr(update, "maybe_auto_update", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(update, "current_version", lambda: "1.1.1")

    result = update.startup_auto_update()

    assert result.status == "error"
    assert "boom" in result.message


def test_claude_session_start_triggers_startup_update(monkeypatch, capsys):
    from guardians_of_the_token.claude import session_start

    called = []

    monkeypatch.setattr(
        session_start,
        "startup_auto_update",
        lambda: called.append(True) or update.UpdateResult("updated", "1.1.0", "1.1.1", "updated"),
    )

    session_start.main()

    assert called == [True]
    assert "updated" in capsys.readouterr().out


def test_codex_pre_hook_triggers_startup_update(monkeypatch):
    from guardians_of_the_token.codex import pre_tool_guard

    called = []
    monkeypatch.setattr(pre_tool_guard, "startup_auto_update", lambda: called.append(True))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"tool_name": "Other"})))

    pre_tool_guard.main()

    assert called == [True]


def test_codex_post_hook_triggers_startup_update(monkeypatch):
    from guardians_of_the_token.codex import post_tool_guard

    called = []
    monkeypatch.setattr(post_tool_guard, "startup_auto_update", lambda: called.append(True))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"tool_name": "Other"})))

    post_tool_guard.main()

    assert called == [True]


def test_mcp_server_triggers_startup_update(monkeypatch, capsys):
    from guardians_of_the_token import mcp_server

    called = []
    payload = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    monkeypatch.setattr(mcp_server, "startup_auto_update", lambda: called.append(True))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload) + "\n"))

    mcp_server.main()

    assert called == [True]
    assert '"serverInfo"' in capsys.readouterr().out
