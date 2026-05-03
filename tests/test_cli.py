import json

from guardians_of_the_token.cli import (
    CLAUDE_HOOKS_JSON,
    CODEX_MCP_TOML,
    color,
    detect_available_clients,
    detect_available_integrations,
    enable_codex_hooks_config,
    install_auto,
    install_codex,
    install_claude_desktop_mcp,
    init_project,
    merge_hooks,
    show_install_banner,
    workspace_parser,
)


def test_merge_hooks_updates_existing_guardians_matcher():
    existing = {
        "PreToolUse": [
            {
                "matcher": "Bash|Read",
                "hooks": [
                    {
                        "type": "command",
                        "command": "python3 -m guardians_of_the_token.claude.pre_tool_guard",
                    }
                ],
            }
        ]
    }

    merged = merge_hooks(existing, CLAUDE_HOOKS_JSON["hooks"])

    assert merged["PreToolUse"][0]["matcher"] == "Bash|Read|WebFetch"


def test_merge_hooks_prepends_missing_guardians_hook_without_dropping_user_hooks():
    user_hook = {
        "matcher": "Bash",
        "hooks": [
            {
                "type": "command",
                "command": "python3 -m user_hook",
            }
        ],
    }
    existing = {"PreToolUse": [user_hook]}

    merged = merge_hooks(existing, CLAUDE_HOOKS_JSON["hooks"])

    assert merged["PreToolUse"][0]["hooks"][0]["command"] == (
        "python3 -m guardians_of_the_token.claude.pre_tool_guard"
    )
    assert merged["PreToolUse"][1] == user_hook


def test_codex_workspace_parser_accepts_global_flag():
    parser = workspace_parser("Install Codex hooks.", include_global=True)

    args = parser.parse_args(["--global"])

    assert args.install_global is True


def test_claude_workspace_parser_accepts_global_flag():
    parser = workspace_parser("Install Claude hooks.", include_global=True)

    args = parser.parse_args(["--global"])

    assert args.install_global is True


def test_enable_codex_hooks_config_adds_feature_to_existing_features_table():
    existing = "[features]\nother = true\n[projects]\n"

    updated = enable_codex_hooks_config(existing)

    assert updated == (
        "[features]\n"
        "codex_hooks = true\n"
        "other = true\n"
        "[projects]\n"
        "[mcp_servers.guardians]\n"
        'command = "guardians-mcp"\n'
    )


def test_enable_codex_hooks_config_adds_mcp_server_only_once():
    existing = "[features]\ncodex_hooks = true\n\n[mcp_servers.guardians]\ncommand = \"guardians-mcp\"\n"

    updated = enable_codex_hooks_config(existing)

    assert updated == existing


def test_install_codex_global_merges_hooks_and_enables_config(tmp_path, monkeypatch):
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    codex_dir.mkdir(parents=True)
    hooks_path = codex_dir / "hooks.json"
    config_path = codex_dir / "config.toml"
    hooks_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "python3 -m user_hook",
                                }
                            ],
                        }
                    ]
                }
            }
        )
    )
    config_path.write_text("[features]\nother = true\n")
    fixture_marker = tmp_path / "fixture_marker"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("sys.argv", ["guardians-codex-install", "--global"])
    monkeypatch.setattr(
        "guardians_of_the_token.cli.ensure_test_files",
        lambda: fixture_marker.write_text("fixtures refreshed"),
    )

    install_codex()

    hooks = json.loads(hooks_path.read_text())
    assert hooks["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == (
        "python3 -m guardians_of_the_token.codex.pre_tool_guard"
    )
    assert hooks["hooks"]["PreToolUse"][1]["hooks"][0]["command"] == (
        "python3 -m user_hook"
    )
    assert "codex_hooks = true" in config_path.read_text()
    assert "[mcp_servers.guardians]" in config_path.read_text()
    assert 'command = "guardians-mcp"' in config_path.read_text()
    assert fixture_marker.read_text() == "fixtures refreshed"


def test_install_codex_global_creates_mcp_config_when_missing(tmp_path, monkeypatch):
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    codex_dir.mkdir(parents=True)
    fixture_marker = tmp_path / "fixture_marker"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("sys.argv", ["guardians-codex-install", "--global"])
    monkeypatch.setattr(
        "guardians_of_the_token.cli.ensure_test_files",
        lambda: fixture_marker.write_text("fixtures refreshed"),
    )

    install_codex()

    config_text = (codex_dir / "config.toml").read_text()
    assert "codex_hooks = true" in config_text
    assert CODEX_MCP_TOML in config_text


def test_detect_available_clients(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setattr("pathlib.Path.home", lambda: home)

    assert detect_available_clients() == ["codex", "claude"]


def test_detect_available_integrations(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".claude").mkdir(parents=True)
    (home / "Library" / "Application Support" / "Claude").mkdir(parents=True)
    monkeypatch.setattr("pathlib.Path.home", lambda: home)

    assert detect_available_integrations() == [
        "codex_hooks",
        "codex_mcp",
        "claude_code_hooks",
        "claude_desktop_mcp",
    ]


def test_install_auto_installs_selected_client_interactively(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".claude").mkdir(parents=True)
    fixture_marker = tmp_path / "fixture_marker"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    monkeypatch.setattr("sys.argv", ["guardians-install"])
    responses = iter(["2", "3", ""])
    monkeypatch.setattr("builtins.input", lambda _: next(responses))
    monkeypatch.setattr(
        "guardians_of_the_token.cli.ensure_test_files",
        lambda: fixture_marker.write_text("fixtures refreshed"),
    )

    install_auto()

    out = capsys.readouterr().out
    assert "Guardians of the Token is now watching the risky paths." in out
    assert "Detected installable integrations:" in out
    assert "Codex CLI hooks" in out
    assert "Codex app MCP" in out
    assert "Claude Code hooks" in out
    assert "[x] 1) Codex CLI hooks" in out
    assert "[ ] 2) Codex app MCP" in out
    assert "[ ] 3) Claude Code hooks" in out
    assert "Installed Guardians into:" in out
    installed_section = out.split("Installed Guardians into:\n", 1)[1]
    assert "- OK Codex CLI hooks" in installed_section
    assert "Codex app MCP" not in installed_section
    assert "Claude Code hooks" not in installed_section
    assert (home / ".codex" / "hooks.json").exists()
    assert not (home / ".claude" / "settings.json").exists()
    assert fixture_marker.read_text() == "fixtures refreshed"


def test_install_auto_yes_installs_all_detected_clients(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".claude").mkdir(parents=True)
    fixture_marker = tmp_path / "fixture_marker"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    monkeypatch.setattr("sys.argv", ["guardians-install", "--yes"])
    monkeypatch.setattr(
        "guardians_of_the_token.cli.ensure_test_files",
        lambda: fixture_marker.write_text("fixtures refreshed"),
    )

    install_auto()

    out = capsys.readouterr().out
    assert "Guardians of the Token is now watching the risky paths." in out
    assert "Detected installable integrations:" not in out
    assert "Installing Codex CLI hooks..." in out
    assert "Installing Codex app MCP..." in out
    assert "Installing Claude Code hooks..." in out
    assert "- OK Codex CLI hooks" in out
    assert "- OK Codex app MCP" in out
    assert "- OK Claude Code hooks" in out
    assert (home / ".codex" / "hooks.json").exists()
    assert (home / ".claude" / "settings.json").exists()


def test_install_claude_desktop_mcp_writes_desktop_config(tmp_path, monkeypatch):
    home = tmp_path / "home"
    config_dir = home / "Library" / "Application Support" / "Claude"
    config_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    config_path = install_claude_desktop_mcp()

    config = json.loads(config_path.read_text())
    assert config["mcpServers"]["guardians"] == {
        "command": "guardians-mcp",
        "args": [],
    }


def test_install_banner_shows_each_time(capsys):
    show_install_banner()
    first = capsys.readouterr().out
    assert "Guardians of the Token is now watching the risky paths." in first

    show_install_banner()
    second = capsys.readouterr().out
    assert "Guardians of the Token is now watching the risky paths." in second


def test_color_is_disabled_for_non_tty_stdout():
    assert color("plain", "green", "bold") == "plain"


def test_project_init_cli_uses_existing_mcp_handler(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["guardians-project-init", str(tmp_path)])

    init_project()

    out = capsys.readouterr().out
    assert "Initialized Guardians project storage" in out
    assert (tmp_path / ".got" / "GUARDIANS_PROJECT_POLICY.md").exists()
    assert "<!-- guardians-of-the-token:start -->" in (tmp_path / "CLAUDE.md").read_text()


def test_install_auto_reprompts_on_invalid_selection(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".claude").mkdir(parents=True)
    fixture_marker = tmp_path / "fixture_marker"
    responses = iter(["9", "1", ""])
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    monkeypatch.setattr("sys.argv", ["guardians-install"])
    monkeypatch.setattr("builtins.input", lambda _: next(responses))
    monkeypatch.setattr(
        "guardians_of_the_token.cli.ensure_test_files",
        lambda: fixture_marker.write_text("fixtures refreshed"),
    )

    install_auto()

    out = capsys.readouterr().out
    assert "Invalid selection. Enter item numbers like 1 or 1,2." in out
    assert "Claude Code" in out


def test_install_auto_accepts_comma_separated_checkbox_toggle(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".claude").mkdir(parents=True)
    fixture_marker = tmp_path / "fixture_marker"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    monkeypatch.setattr("sys.argv", ["guardians-install"])
    responses = iter(["1,2,3", "1,2,3", ""])
    monkeypatch.setattr("builtins.input", lambda _: next(responses))
    monkeypatch.setattr(
        "guardians_of_the_token.cli.ensure_test_files",
        lambda: fixture_marker.write_text("fixtures refreshed"),
    )

    install_auto()

    out = capsys.readouterr().out
    assert "Installing Codex CLI hooks..." in out
    assert "Installing Codex app MCP..." in out
    assert "Installing Claude Code hooks..." in out
    assert (home / ".codex" / "hooks.json").exists()
    assert (home / ".claude" / "settings.json").exists()


def test_install_auto_fails_when_no_client_home_detected(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    monkeypatch.setattr("sys.argv", ["guardians-install"])

    try:
        install_auto()
    except SystemExit as exc:
        assert str(exc) == (
            "No supported client home was detected. Expected ~/.codex and/or ~/.claude."
        )
    else:
        raise AssertionError("install_auto should exit when no client home is detected")
