"""guardians-install picks the best setup per client: the Claude Code plugin
when the claude CLI is there, and hooks/MCP that run with the installing Python."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from guardians_of_the_token import cli

REAL_CLAUDE_CLI = cli.claude_cli  # captured before conftest stubs it


@pytest.fixture(autouse=True)
def _quiet_side_effects(monkeypatch):
    monkeypatch.setattr(cli, "ensure_test_files", lambda: None)


class FakeClaude:
    """Records claude CLI calls and answers them like the real one."""

    def __init__(self, installed=False, install_rc=0, fail=None):
        self.calls = []
        self.installed = installed
        self.install_rc = install_rc
        self.fail = fail

    def __call__(self, args, **kwargs):
        if self.fail:
            raise self.fail
        self.calls.append(args[1:])
        if args[1:4] == ["plugin", "list", "--json"]:
            plugins = [{"id": cli.PLUGIN_ID}] if self.installed else [{"id": "other@x"}]
            return subprocess.CompletedProcess(args, 0, json.dumps(plugins), "")
        if args[1:3] == ["plugin", "install"]:
            return subprocess.CompletedProcess(args, self.install_rc, "", "network down\nError: could not clone")
        return subprocess.CompletedProcess(args, 0, "", "")


def _use_claude(monkeypatch, fake):
    monkeypatch.setattr(cli, "claude_cli", lambda: "/usr/local/bin/claude")
    monkeypatch.setattr(cli.subprocess, "run", fake)


def _seed_old_hooks_install(home: Path):
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    user_hook = {"matcher": "Bash", "hooks": [{"type": "command", "command": "my-linter"}]}
    hooks = {"PreToolUse": [user_hook], **cli.CLAUDE_HOOKS_JSON["hooks"]}
    hooks["PreToolUse"] = [*cli.CLAUDE_HOOKS_JSON["hooks"]["PreToolUse"], user_hook]
    settings.write_text(json.dumps({"hooks": hooks, "model": "opus"}))
    skill = home / ".claude" / "skills" / "got-pause" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("Pause Guardians of the Token")
    mine = home / ".claude" / "skills" / "got-resume" / "SKILL.md"
    mine.parent.mkdir(parents=True)
    mine.write_text("my own skill")
    return settings, user_hook


def test_python_command_quotes_for_the_shell(monkeypatch):
    monkeypatch.setattr(sys, "executable", "/Users/me/my tools/bin/python")
    monkeypatch.setattr(sys, "platform", "darwin")
    assert cli.python_command() == "'/Users/me/my tools/bin/python'"
    monkeypatch.setattr(sys, "platform", "win32")
    assert cli.python_command() == '"/Users/me/my tools/bin/python"'
    monkeypatch.setattr(sys, "executable", "C:/py/python.exe")
    assert cli.python_command() == "C:/py/python.exe"
    monkeypatch.setattr(sys, "executable", "")
    assert cli.python_command() == "python3"


def test_with_python_leaves_other_commands_alone():
    hooks = {"X": [{"hooks": [{"command": "node tool.js"}, {"type": "prompt"}]}]}
    assert cli.with_python(hooks) == hooks


def test_remove_got_hooks_keeps_user_hooks():
    user = {"matcher": "Bash", "hooks": [{"type": "command", "command": "my-linter"}]}
    mixed = {
        "matcher": "Read",
        "hooks": [
            {"type": "command", "command": "/x/python -m guardians_of_the_token.claude.pre_tool_guard"},
            {"type": "command", "command": "other"},
        ],
    }
    hooks = {"PreToolUse": [user, mixed], **cli.with_python(cli.CLAUDE_HOOKS_JSON["hooks"])}
    hooks["PreToolUse"] = [user, mixed]
    cleaned = cli.remove_got_hooks(hooks)
    assert cleaned == {
        "PreToolUse": [user, {"matcher": "Read", "hooks": [{"type": "command", "command": "other"}]}]
    }


@pytest.mark.parametrize(
    ("platform", "env", "expected"),
    [
        ("darwin", {}, ("Library", "Application Support", "Claude")),
        ("linux", {}, (".config", "Claude")),
        ("linux", {"XDG_CONFIG_HOME": "XDG"}, ("XDG", "Claude")),
        ("win32", {"APPDATA": "Roaming"}, ("Roaming", "Claude")),
        ("win32", {}, ("AppData", "Roaming", "Claude")),
    ],
)
def test_claude_desktop_config_path_per_platform(monkeypatch, tmp_path, platform, env, expected):
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for key in ("APPDATA", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, str(tmp_path / value))
    path = cli.claude_desktop_config_path()
    assert path == tmp_path.joinpath(*expected, "claude_desktop_config.json")


def test_detect_prefers_plugin_when_claude_cli_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(cli, "claude_cli", lambda: "/usr/local/bin/claude")
    assert "claude_code_plugin" in cli.detect_available_integrations()
    assert "claude_code_hooks" not in cli.detect_available_integrations()


def test_install_claude_plugin_without_cli_returns_false():
    assert cli.install_claude_plugin() is False


def test_install_claude_plugin_installs_and_cleans_up_old_hooks(monkeypatch):
    home = Path.home()
    settings, user_hook = _seed_old_hooks_install(home)
    fake = FakeClaude()
    _use_claude(monkeypatch, fake)

    assert cli.install_claude_plugin() is True

    assert ["plugin", "marketplace", "add", cli.PLUGIN_MARKETPLACE] in fake.calls
    assert ["plugin", "install", cli.PLUGIN_ID, "--scope", "user"] in fake.calls
    data = json.loads(settings.read_text())
    assert data == {"hooks": {"PreToolUse": [user_hook]}, "model": "opus"}
    assert not (home / ".claude" / "skills" / "got-pause").exists()
    assert (home / ".claude" / "skills" / "got-resume" / "SKILL.md").read_text() == "my own skill"


def test_install_claude_plugin_skips_install_when_present(monkeypatch):
    fake = FakeClaude(installed=True)
    _use_claude(monkeypatch, fake)
    assert cli.install_claude_plugin() is True
    assert fake.calls == [["plugin", "list", "--json"]]


def test_install_claude_plugin_reports_failure(monkeypatch, capsys):
    _use_claude(monkeypatch, FakeClaude(install_rc=1))
    assert cli.install_claude_plugin() is False
    assert "Error: could not clone" in capsys.readouterr().out


def test_install_claude_plugin_handles_cli_errors(monkeypatch, capsys):
    _use_claude(monkeypatch, FakeClaude(fail=OSError("boom")))
    assert cli.install_claude_plugin() is False
    assert "boom" in capsys.readouterr().out


@pytest.mark.parametrize("stdout, rc", [("not json", 0), ("", 1)])
def test_claude_plugin_installed_tolerates_bad_output(monkeypatch, stdout, rc):
    monkeypatch.setattr(
        cli.subprocess, "run", lambda args, **kw: subprocess.CompletedProcess(args, rc, stdout, "")
    )
    assert cli.claude_plugin_installed("claude") is False


def test_remove_claude_settings_hooks_edge_cases():
    settings = cli.claude_settings_path()
    assert cli.remove_claude_settings_hooks() is False  # no file
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({"model": "opus"}))
    assert cli.remove_claude_settings_hooks() is False  # no hooks
    settings.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{"command": "mine"}]}]}}))
    assert cli.remove_claude_settings_hooks() is False  # nothing of ours
    settings.write_text(json.dumps({"hooks": cli.CLAUDE_HOOKS_JSON["hooks"]}))
    assert cli.remove_claude_settings_hooks() is True
    assert json.loads(settings.read_text()) == {}


def test_remove_claude_skill_copies_without_bundled_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_BUNDLED_SKILLS_DIR", tmp_path / "missing")
    assert cli.remove_claude_skill_copies() == []


def test_remove_claude_skill_copies_keeps_other_files(monkeypatch):
    skill_dir = Path.home() / ".claude" / "skills" / "got-unblock"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("Guardians of the Token unblock")
    (skill_dir / "notes.txt").write_text("keep me")
    removed = cli.remove_claude_skill_copies()
    assert removed == [skill_dir / "SKILL.md"]
    assert (skill_dir / "notes.txt").exists()


def _install_auto_with(monkeypatch, plugin_ok):
    monkeypatch.setattr(cli, "detect_available_integrations", lambda: ["claude_code_plugin"])
    monkeypatch.setattr(cli, "install_claude_plugin", lambda: plugin_ok)
    calls = []
    monkeypatch.setattr(cli, "install_statusline", lambda: calls.append("statusline"))
    monkeypatch.setattr(cli, "install_claude_global", lambda: calls.append("hooks"))
    monkeypatch.setattr(sys, "argv", ["guardians-install", "--yes"])
    cli.install_auto()
    return calls


def test_install_auto_uses_plugin(monkeypatch, capsys):
    assert _install_auto_with(monkeypatch, True) == ["statusline"]
    out = capsys.readouterr().out
    assert "OK Claude Code plugin" in out
    assert json.loads((Path.home() / ".guardians.json").read_text())["telemetry_enabled"] is False


def test_install_auto_falls_back_to_hooks(monkeypatch, capsys):
    assert _install_auto_with(monkeypatch, False) == ["hooks"]
    out = capsys.readouterr().out
    assert "Falling back to Claude Code hooks" in out
    assert "OK Claude Code hooks" in out


def test_claude_cli_uses_path_lookup(monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda name: f"/bin/{name}")
    assert REAL_CLAUDE_CLI() == "/bin/claude"


def test_codex_mcp_section_replaced_and_blank_lines_trimmed():
    existing = "[a]\nx = 1\n\n[mcp_servers.guardians]\ncommand = \"guardians-mcp\"\n\n\n"
    assert cli.ensure_codex_mcp_server(existing) == "[a]\nx = 1\n" + cli.codex_mcp_toml()
    assert cli.ensure_codex_mcp_server("") == cli.codex_mcp_toml()


def test_merge_hooks_keeps_extra_got_commands_it_does_not_replace():
    extra = "python3 -m guardians_of_the_token.claude.cap_output"
    existing = {
        "PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [
                    {"type": "command", "command": "python3 -m guardians_of_the_token.claude.pre_tool_guard"},
                    {"type": "command", "command": extra},
                ],
            }
        ]
    }
    merged = cli.merge_hooks(existing, cli.with_python(cli.CLAUDE_HOOKS_JSON["hooks"]))
    commands = [h["command"] for h in merged["PreToolUse"][0]["hooks"]]
    assert commands[0].startswith(sys.executable)
    assert commands[1] == extra
