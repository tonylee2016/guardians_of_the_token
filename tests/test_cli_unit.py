"""Unit tests for cli.py commands and the interactive installer."""

import io
import json
import sys
from pathlib import Path

import pytest

from guardians_of_the_token import cli
from guardians_of_the_token.cli import _ensure_prompt_guard_model as real_ensure_model


@pytest.fixture(autouse=True)
def _no_fixture_files(monkeypatch):
    """ensure_test_files writes to fixed /tmp paths; keep the CLI away from them."""
    monkeypatch.setattr(cli, "ensure_test_files", lambda: None)


def argv(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["guardians", *args])


class Tty(io.StringIO):
    def isatty(self):
        return True

    def fileno(self):
        return 0


# ---------- color ----------


def test_color_on_a_terminal(monkeypatch):
    monkeypatch.setattr(sys, "stdout", Tty())
    assert cli.color("hi", "bold", "green") == "\033[1m\033[32mhi\033[0m"


# ---------- install_codex ----------


def test_install_codex_workspace(monkeypatch, tmp_path, capsys):
    argv(monkeypatch, str(tmp_path))
    cli.install_codex()
    assert (tmp_path / ".codex" / "hooks.json").exists()
    assert (tmp_path / ".codex" / "config.toml").read_text() == cli.CONFIG_TOML
    assert "Installed Guardians of the Token Codex hooks in" in capsys.readouterr().out


def test_install_codex_refuses_existing_hooks(monkeypatch, tmp_path):
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "hooks.json").write_text("{}")
    argv(monkeypatch, str(tmp_path))
    with pytest.raises(SystemExit, match="--force"):
        cli.install_codex()


def test_install_codex_updates_existing_config(monkeypatch, tmp_path):
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "config.toml").write_text('model = "gpt-5.5"\n')
    argv(monkeypatch, str(tmp_path))
    cli.install_codex()
    text = (tmp_path / ".codex" / "config.toml").read_text()
    assert 'model = "gpt-5.5"' in text
    assert cli.has_feature_line(text, "hooks")


def test_install_codex_refuses_config_it_cannot_update(monkeypatch, tmp_path):
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "config.toml").write_text("# custom\n")
    monkeypatch.setattr(cli, "enable_codex_hooks_config", lambda existing: existing)
    argv(monkeypatch, str(tmp_path))
    with pytest.raises(SystemExit, match="does not mention hooks"):
        cli.install_codex()


# ---------- install_claude ----------


def test_install_claude_workspace_and_existing(monkeypatch, tmp_path, capsys):
    argv(monkeypatch, str(tmp_path))
    cli.install_claude()
    assert json.loads((tmp_path / ".claude" / "settings.json").read_text()) == cli.CLAUDE_HOOKS_JSON
    assert "Claude Code hooks in" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="--force"):
        cli.install_claude()


def test_install_claude_global(monkeypatch, capsys):
    monkeypatch.setattr(cli, "install_claude_global", lambda: Path("/fake/settings.json"))
    argv(monkeypatch, "--global")
    cli.install_claude()
    assert "globally in /fake/settings.json" in capsys.readouterr().out


# ---------- skills / model ----------


def test_install_claude_skills_global_edge_cases(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_BUNDLED_SKILLS_DIR", tmp_path / "missing")
    assert cli.install_claude_skills_global() == []

    bundled = tmp_path / "skills"
    (bundled / "empty-skill").mkdir(parents=True)
    (bundled / "real-skill").mkdir()
    (bundled / "real-skill" / "SKILL.md").write_text("# skill")
    (bundled / "README.txt").write_text("not a skill")
    monkeypatch.setattr(cli, "_BUNDLED_SKILLS_DIR", bundled)
    installed = cli.install_claude_skills_global()
    assert [p.parent.name for p in installed] == ["real-skill"]


def test_ensure_prompt_guard_model(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr("guardians_of_the_token.embeddings.ensure_model", lambda: tmp_path)
    real_ensure_model()
    assert f"Prompt-guard model ready in {tmp_path}" in capsys.readouterr().out

    def offline():
        raise OSError("no network")

    monkeypatch.setattr("guardians_of_the_token.embeddings.ensure_model", offline)
    real_ensure_model()
    assert "Could not pre-fetch prompt-guard model: no network" in capsys.readouterr().out


# ---------- interactive selection ----------


def test_select_clients_interactive_uses_tty_selector(monkeypatch):
    monkeypatch.setattr(sys, "stdin", Tty())
    monkeypatch.setattr(sys, "stdout", Tty())
    monkeypatch.setattr(cli, "select_clients_tty", lambda options, integrations: ["picked"])
    assert cli.select_clients_interactive(["codex_hooks"]) == ["picked"]


def test_select_clients_interactive_without_termios_uses_text(monkeypatch):
    monkeypatch.setattr(sys, "stdin", Tty())
    monkeypatch.setattr(sys, "stdout", Tty())
    monkeypatch.setattr(cli, "termios", None)
    monkeypatch.setattr(cli, "select_clients_tty", lambda *a: pytest.fail("tty selector used without termios"))
    monkeypatch.setattr(cli, "select_clients_text", lambda options, integrations: ["typed"])
    assert cli.select_clients_interactive(["codex_hooks"]) == ["typed"]


def test_cli_imports_without_termios(monkeypatch):
    """On Windows termios/tty don't exist; the module must still import."""
    import importlib

    monkeypatch.setitem(sys.modules, "termios", None)
    monkeypatch.setitem(sys.modules, "tty", None)
    try:
        reloaded = importlib.reload(cli)
        assert reloaded.termios is None and reloaded.tty is None
    finally:
        monkeypatch.undo()
        importlib.reload(cli)
    assert cli.termios is not None


def test_select_clients_text_requires_a_choice_and_rejects_bad_input(monkeypatch, capsys):
    answers = iter(["1,2,3,4", "", "x", "9", "1", ""])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    options = ["codex_hooks", "claude_code_hooks", cli.TELEMETRY_OPTION, cli.AUTO_UPDATE_OPTION]
    assert cli.select_clients_text(options, options[:2]) == ["codex_hooks"]
    out = capsys.readouterr().out
    assert "Select at least one client before installing." in out
    assert out.count("Invalid selection.") == 2


@pytest.mark.parametrize(
    "typed, key",
    [("\x1b[A", "up"), ("\x1b[B", "down"), ("\x1b[C", "escape"), ("\r", "enter"), (" ", "space"), ("A", "a")],
)
def test_read_terminal_key(monkeypatch, typed, key):
    monkeypatch.setattr(sys, "stdin", io.StringIO(typed))
    assert cli.read_terminal_key() == key


def test_read_terminal_key_ctrl_c(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("\x03"))
    with pytest.raises(KeyboardInterrupt):
        cli.read_terminal_key()


def test_render_tty_selector_shows_message(capsys):
    cli.render_tty_selector(["codex_hooks", "claude_code_hooks"], ["codex_hooks"], 1, "Pick one")
    out = capsys.readouterr().out
    assert "Codex CLI hooks" in out
    assert "Pick one" in out


@pytest.fixture
def fake_terminal(monkeypatch):
    restored = []
    monkeypatch.setattr(sys, "stdin", Tty())
    monkeypatch.setattr(cli.termios, "tcgetattr", lambda fd: ["saved"])
    monkeypatch.setattr(cli.termios, "tcsetattr", lambda fd, when, settings: restored.append(settings))
    monkeypatch.setattr(cli.tty, "setcbreak", lambda fd: None)
    monkeypatch.setattr(cli, "render_tty_selector", lambda *a: None)

    def keys(*sequence):
        it = iter(sequence)
        monkeypatch.setattr(cli, "read_terminal_key", lambda: next(it))

    return keys, restored


def test_select_clients_tty_navigation(fake_terminal):
    keys, restored = fake_terminal
    options = ["codex_hooks", "claude_code_hooks", cli.TELEMETRY_OPTION]
    integrations = options[:2]
    # Deselect everything, try to install (refused), re-select all with "a", toggle, then install.
    keys("space", "down", "space", "enter", "a", "up", "up", "down", "space", "space", "x", "enter")
    assert set(cli.select_clients_tty(options, integrations)) == set(options)
    assert restored == [["saved"]]


def test_select_clients_tty_cancel(fake_terminal):
    keys, restored = fake_terminal
    keys("q")
    with pytest.raises(SystemExit, match="Install cancelled."):
        cli.select_clients_tty(["codex_hooks"], ["codex_hooks"])
    assert restored == [["saved"]]


# ---------- install_auto / config ----------


def test_configure_user_telemetry_replaces_corrupt_config(tmp_path):
    config = Path.home() / ".guardians.json"
    config.write_text("{not json")
    cli.configure_user_telemetry(False)
    assert json.loads(config.read_text())["telemetry_enabled"] is False


def test_install_auto_claude_desktop(monkeypatch, capsys):
    monkeypatch.setattr(cli, "detect_available_integrations", lambda: ["claude_desktop_mcp"])
    calls = []
    monkeypatch.setattr(cli, "install_claude_desktop_mcp", lambda: calls.append("desktop"))
    monkeypatch.setattr(cli, "configure_user_telemetry", lambda enabled: None)
    monkeypatch.setattr(cli, "configure_user_auto_update", lambda enabled: None)
    argv(monkeypatch, "--yes")
    cli.install_auto()
    assert calls == ["desktop"]
    assert "OK Claude Desktop MCP" in capsys.readouterr().out


# ---------- small commands ----------


@pytest.mark.parametrize(
    "mode, text",
    [
        ("ccstatusline", "Added Guardians widget to ccstatusline"),
        ("standalone", "Set Guardians standalone statusLine"),
        ("skipped", "statusLine already configured"),
    ],
)
def test_install_statusline_cmd(monkeypatch, capsys, mode, text):
    monkeypatch.setattr(cli, "install_statusline", lambda: mode)
    argv(monkeypatch)
    cli.install_statusline_cmd()
    assert text in capsys.readouterr().out


def test_status_command(monkeypatch, capsys, tmp_path):
    argv(monkeypatch, str(tmp_path))
    cli.status()
    assert capsys.readouterr().out.strip() == "🛡️ on"


def test_preflight_with_overrides_and_warning(monkeypatch, capsys, tmp_path):
    big = tmp_path / "big.log"
    big.write_text("word " * 20_000)
    argv(monkeypatch, str(big), "--context-window", "10000", "--warn-threshold-pct", "5")
    cli.preflight()
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith(("warning:", "critical:"))
    assert len(out) > 1


def test_doctor_without_codex_config(monkeypatch, capsys, tmp_path):
    argv(monkeypatch, str(tmp_path))
    cli.doctor()
    assert "Codex config: missing" in capsys.readouterr().out


@pytest.mark.parametrize("flag, status, code", [([], "current", None), (["--check"], "error", 1)])
def test_update_cmd(monkeypatch, capsys, flag, status, code):
    from guardians_of_the_token import update

    seen = {}

    def fake(force, notify_only):
        seen["notify_only"] = notify_only
        return update.UpdateResult(status, "1.0", None, f"result {status}")

    monkeypatch.setattr(update, "maybe_auto_update", fake)
    argv(monkeypatch, *flag)
    if code:
        with pytest.raises(SystemExit):
            cli.update_cmd()
    else:
        cli.update_cmd()
    assert seen["notify_only"] is bool(flag)
    assert f"result {status}" in capsys.readouterr().out


def test_pause_and_resume_commands(monkeypatch, capsys):
    argv(monkeypatch, "off")
    cli.pause_cmd()
    assert "were not paused" in capsys.readouterr().out

    argv(monkeypatch, "soon")
    with pytest.raises(SystemExit):
        cli.pause_cmd()
    assert "Could not understand duration 'soon'" in capsys.readouterr().out

    argv(monkeypatch, "1h30m")
    cli.pause_cmd()
    assert "Guardians paused for 1h 30m" in capsys.readouterr().out

    argv(monkeypatch)
    cli.resume_cmd()
    assert "Guardians resumed." in capsys.readouterr().out
    cli.resume_cmd()
    assert "were not paused" in capsys.readouterr().out


def test_main_dispatches_dashboard(monkeypatch):
    seen = []
    monkeypatch.setattr("guardians_of_the_token.dashboard.main", lambda: seen.append(list(sys.argv)))
    argv(monkeypatch, "dashboard", "--port", "1234")
    cli.main()
    assert seen == [["guardians dashboard", "--port", "1234"]]
