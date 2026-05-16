import argparse
import json
import os
import sys
import termios
import tty
from pathlib import Path
from typing import Any

from guardians_of_the_token.test_support import ensure_test_files
from guardians_of_the_token.config import DEFAULT_CONFIG


HOOKS_JSON = {
    "hooks": {
        "PreToolUse": [
            {
                "matcher": "^Bash$",
                "hooks": [
                    {
                        "type": "command",
                        "command": "python3 -m guardians_of_the_token.codex.pre_tool_guard",
                        "timeout": 30,
                        "statusMessage": "Checking Bash command",
                    }
                ],
            }
        ],
        "PostToolUse": [
            {
                "matcher": "^Bash$",
                "hooks": [
                    {
                        "type": "command",
                        "command": "python3 -m guardians_of_the_token.codex.post_tool_guard",
                        "timeout": 30,
                        "statusMessage": "Reviewing Bash output",
                    }
                ],
            }
        ],
    }
}


CLAUDE_HOOKS_JSON = {
    "hooks": {
        "PreToolUse": [
            {
                "matcher": "Bash|Read|WebFetch",
                "hooks": [
                    {
                        "type": "command",
                        "command": "python3 -m guardians_of_the_token.claude.pre_tool_guard",
                    }
                ],
            }
        ],
        "PostToolUse": [
            {
                "matcher": "WebFetch|WebSearch",
                "hooks": [
                    {
                        "type": "command",
                        "command": "python3 -m guardians_of_the_token.claude.post_tool_guard",
                    }
                ],
            }
        ],
        "UserPromptSubmit": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": "python3 -m guardians_of_the_token.claude.prompt_guard",
                    }
                ],
            }
        ],
    }
}


CONFIG_TOML = """[features]
hooks = true
"""

CODEX_MCP_TOML = """[mcp_servers.guardians]
command = "guardians-mcp"
"""

FIRST_INSTALL_BANNER = r"""
   ____ ___ _____
  / ___/ _ \_   _|
 | |  | | | || |
 | |__| |_| || |
  \____\___/ |_|

Guardians of the Token is now watching the risky paths.
"""

COLORS = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "green": "\033[32m",
    "cyan": "\033[36m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
}


def color(text: str, *styles: str) -> str:
    if not sys.stdout.isatty():
        return text
    prefix = "".join(COLORS[style] for style in styles)
    return f"{prefix}{text}{COLORS['reset']}"


def merge_hooks(existing: dict[str, Any], additions: dict[str, Any]) -> dict[str, Any]:
    """Merge Guardians hook entries into existing hooks dict, skipping duplicates."""
    result = dict(existing)
    for event, entries in additions.items():
        current = list(result.get(event, []))
        for entry in entries:
            addition_cmds = hook_commands(entry)
            matching_entry = next(
                (
                    existing_entry
                    for existing_entry in current
                    if hook_commands(existing_entry) & addition_cmds
                ),
                None,
            )
            if matching_entry is None:
                current = [entry] + current
                continue
            matching_entry["matcher"] = merge_matcher(
                str(matching_entry.get("matcher", "")),
                str(entry.get("matcher", "")),
            )
        result[event] = current
    return result


def hook_commands(entry: dict[str, Any]) -> set[str]:
    return {
        hook["command"]
        for hook in entry.get("hooks", [])
        if "command" in hook
    }


def merge_matcher(existing: str, addition: str) -> str:
    values = []
    for matcher in (addition, existing):
        for value in matcher.split("|"):
            if value and value not in values:
                values.append(value)
    return "|".join(values)


def workspace_parser(
    description: str,
    *,
    include_global: bool = False,
    global_help: str = "Install into user-global settings.",
) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "workspace",
        nargs="?",
        default=".",
        help="Workspace root where client settings should be written.",
    )
    if include_global:
        parser.add_argument(
            "--global",
            dest="install_global",
            action="store_true",
            help=global_help,
        )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing generated settings (workspace install only).",
    )
    return parser


def enable_codex_hooks_config(existing: str) -> str:
    migrated = migrate_codex_hooks_feature(existing)
    if has_feature_line(migrated, "hooks"):
        return ensure_codex_mcp_server(migrated)
    if "[features]" not in existing:
        separator = "" if not existing or existing.endswith("\n") else "\n"
        return ensure_codex_mcp_server(f"{existing}{separator}{CONFIG_TOML}")

    lines = existing.splitlines()
    for index, line in enumerate(lines):
        if line.strip() == "[features]":
            lines.insert(index + 1, "hooks = true")
            break
    result = "\n".join(lines)
    result = result + ("\n" if existing.endswith("\n") else "")
    return ensure_codex_mcp_server(result)


def has_feature_line(config: str, feature: str) -> bool:
    prefix = f"{feature} "
    return any(line.strip().startswith(prefix) for line in config.splitlines())


def migrate_codex_hooks_feature(existing: str) -> str:
    lines = []
    saw_hooks_feature = has_feature_line(existing, "hooks")
    replaced_codex_hooks = False
    for line in existing.splitlines():
        stripped = line.strip()
        if stripped.startswith("codex_hooks "):
            if not saw_hooks_feature:
                lines.append(line.replace("codex_hooks", "hooks", 1))
                saw_hooks_feature = True
            replaced_codex_hooks = True
            continue
        lines.append(line)
    result = "\n".join(lines)
    if existing.endswith("\n"):
        result += "\n"
    if replaced_codex_hooks:
        return result
    return existing


def ensure_codex_mcp_server(existing: str) -> str:
    if "[mcp_servers.guardians]" in existing:
        return existing
    separator = "" if not existing or existing.endswith("\n") else "\n"
    return f"{existing}{separator}{CODEX_MCP_TOML}"


def install_codex():
    parser = workspace_parser(
        "Install Guardians of the Token hooks into a Codex workspace.",
        include_global=True,
        global_help=(
            "Install into ~/.codex/hooks.json and ~/.codex/config.toml "
            "(merges with existing hooks)."
        ),
    )
    args = parser.parse_args()

    if args.install_global:
        codex_dir = install_codex_global()
        ensure_test_files()
        print(f"Installed Guardians of the Token Codex hooks globally in {codex_dir}")
        return

    workspace = Path(args.workspace).expanduser().resolve()
    codex_dir = workspace / ".codex"
    hooks_path = codex_dir / "hooks.json"
    config_path = codex_dir / "config.toml"

    codex_dir.mkdir(parents=True, exist_ok=True)

    if hooks_path.exists() and not args.force:
        raise SystemExit(
            f"{hooks_path} already exists. Re-run with --force to replace it."
        )
    hooks_path.write_text(json.dumps(HOOKS_JSON, indent=2) + "\n")

    if config_path.exists() and not args.force:
        existing = config_path.read_text()
        updated = enable_codex_hooks_config(existing)
        if not has_feature_line(updated, "hooks"):
            raise SystemExit(
                f"{config_path} already exists and does not mention hooks. "
                "Enable codex hooks manually or re-run with --force."
            )
        config_path.write_text(updated)
    else:
        config_path.write_text(CONFIG_TOML)

    ensure_test_files()
    print(f"Installed Guardians of the Token Codex hooks in {codex_dir}")


def install_codex_global() -> Path:
    codex_dir = Path("~/.codex").expanduser()
    hooks_path = codex_dir / "hooks.json"
    config_path = codex_dir / "config.toml"
    codex_dir.mkdir(parents=True, exist_ok=True)

    existing_hooks = json.loads(hooks_path.read_text()) if hooks_path.exists() else {}
    merged_hooks = merge_hooks(existing_hooks.get("hooks", {}), HOOKS_JSON["hooks"])
    existing_hooks["hooks"] = merged_hooks
    hooks_path.write_text(json.dumps(existing_hooks, indent=2) + "\n")

    existing_config = config_path.read_text() if config_path.exists() else ""
    config_path.write_text(enable_codex_hooks_config(existing_config))
    return codex_dir


def install_codex_cli_hooks() -> Path:
    codex_dir = Path("~/.codex").expanduser()
    hooks_path = codex_dir / "hooks.json"
    codex_dir.mkdir(parents=True, exist_ok=True)
    existing_hooks = json.loads(hooks_path.read_text()) if hooks_path.exists() else {}
    merged_hooks = merge_hooks(existing_hooks.get("hooks", {}), HOOKS_JSON["hooks"])
    existing_hooks["hooks"] = merged_hooks
    hooks_path.write_text(json.dumps(existing_hooks, indent=2) + "\n")
    config_path = codex_dir / "config.toml"
    existing_config = config_path.read_text() if config_path.exists() else ""
    config_path.write_text(enable_codex_hooks_config(existing_config))
    return hooks_path


def install_codex_app_mcp() -> Path:
    codex_dir = Path("~/.codex").expanduser()
    config_path = codex_dir / "config.toml"
    codex_dir.mkdir(parents=True, exist_ok=True)
    existing_config = config_path.read_text() if config_path.exists() else ""
    config_path.write_text(ensure_codex_mcp_server(existing_config))
    return config_path


def install_claude():
    parser = workspace_parser(
        "Install Guardians of the Token hooks into a Claude Code workspace.",
        include_global=True,
        global_help="Install into ~/.claude/settings.json (merges with existing settings).",
    )
    args = parser.parse_args()

    if args.install_global:
        settings_path = install_claude_global()
        ensure_test_files()
        print(f"Installed Guardians of the Token Claude Code hooks globally in {settings_path}")
        return

    workspace = Path(args.workspace).expanduser().resolve()
    claude_dir = workspace / ".claude"
    settings_path = claude_dir / "settings.json"

    claude_dir.mkdir(parents=True, exist_ok=True)

    if settings_path.exists() and not args.force:
        raise SystemExit(
            f"{settings_path} already exists. Re-run with --force to replace it."
        )
    settings_path.write_text(json.dumps(CLAUDE_HOOKS_JSON, indent=2) + "\n")

    ensure_test_files()
    print(f"Installed Guardians of the Token Claude Code hooks in {claude_dir}")


CCSTATUSLINE_CONFIG = Path("~/.config/ccstatusline/settings.json").expanduser()
GUARDIANS_STATUS_COMMAND = "python3 -m guardians_of_the_token.status"


def claude_settings_path() -> Path:
    return Path("~/.claude/settings.json").expanduser()


def _ccstatusline_has_guardians(config: dict) -> bool:
    for line in config.get("lines", []):
        for widget in line:
            if widget.get("commandPath") == GUARDIANS_STATUS_COMMAND:
                return True
    return False


def _ccstatusline_add_guardians(config: dict) -> dict:
    lines = config.get("lines", [[]])
    first_line = list(lines[0]) if lines else []
    used_ids = {str(w.get("id", "")) for line in lines for w in line}
    sep_id = str(max((int(i) for i in used_ids if i.isdigit()), default=0) + 1)
    widget_id = str(int(sep_id) + 1)
    first_line.extend([
        {"id": sep_id, "type": "separator"},
        {"id": widget_id, "type": "custom-command", "commandPath": GUARDIANS_STATUS_COMMAND, "color": "green"},
    ])
    return {**config, "lines": [first_line, *lines[1:]]}


def install_statusline() -> str:
    """Add guardians to ccstatusline if available, otherwise set standalone statusLine."""
    if CCSTATUSLINE_CONFIG.exists():
        config = json.loads(CCSTATUSLINE_CONFIG.read_text())
        if not _ccstatusline_has_guardians(config):
            CCSTATUSLINE_CONFIG.write_text(json.dumps(_ccstatusline_add_guardians(config), indent=2) + "\n")
        return "ccstatusline"

    settings_path = claude_settings_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    existing = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    if "statusLine" in existing:
        return "standalone-exists"
    existing["statusLine"] = {
        "type": "command",
        "command": GUARDIANS_STATUS_COMMAND,
        "refreshInterval": 10,
    }
    settings_path.write_text(json.dumps(existing, indent=2) + "\n")
    return "standalone"


def install_claude_global() -> Path:
    settings_path = claude_settings_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    existing = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    merged_hooks = merge_hooks(existing.get("hooks", {}), CLAUDE_HOOKS_JSON["hooks"])
    existing["hooks"] = merged_hooks
    settings_path.write_text(json.dumps(existing, indent=2) + "\n")
    install_statusline()
    _ensure_prompt_guard_model()
    return settings_path


def _ensure_prompt_guard_model() -> None:
    """Pre-fetch the prompt-guard embedding model so first-prompt latency
    isn't dominated by a one-time download. Best-effort: surface a hint but
    don't fail the install if it can't reach HuggingFace."""
    try:
        from guardians_of_the_token.embeddings import ensure_model

        print(color("Fetching prompt-guard embedding model...", "blue", "bold"))
        path = ensure_model()
        print(color(f"OK Prompt-guard model ready in {path}", "green"))
    except Exception as exc:  # noqa: BLE001 — install must not hard-fail on network errors
        print(
            color(
                "WARN  Could not pre-fetch prompt-guard model: "
                f"{exc}\n      Run `guardians-download-models` manually before first use.",
                "yellow",
            )
        )


def claude_desktop_config_path() -> Path:
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / "Claude"
        / "claude_desktop_config.json"
    )


def install_claude_desktop_mcp() -> Path:
    config_path = claude_desktop_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    existing = json.loads(config_path.read_text()) if config_path.exists() else {}
    servers = existing.setdefault("mcpServers", {})
    servers["guardians"] = {
        "command": "guardians-mcp",
        "args": [],
    }
    config_path.write_text(json.dumps(existing, indent=2) + "\n")
    return config_path


def detect_available_integrations() -> list[str]:
    integrations = []
    home = Path.home()
    if (home / ".codex").exists():
        integrations.extend(["codex_hooks", "codex_mcp"])
    if (home / ".claude").exists():
        integrations.append("claude_code_hooks")
    if claude_desktop_config_path().parent.exists():
        integrations.append("claude_desktop_mcp")
    return integrations


def detect_available_clients() -> list[str]:
    clients = []
    home = Path.home()
    if (home / ".codex").exists():
        clients.append("codex")
    if (home / ".claude").exists():
        clients.append("claude")
    return clients


def show_install_banner() -> None:
    print(color(FIRST_INSTALL_BANNER.strip("\n"), "cyan", "bold"))
    print()


TELEMETRY_OPTION = "telemetry"


def select_clients_interactive(integrations: list[str]) -> list[str]:
    options = [*integrations, TELEMETRY_OPTION]
    if sys.stdin.isatty() and sys.stdout.isatty():
        return select_clients_tty(options, integrations)
    return select_clients_text(options, integrations)


def client_labels() -> dict[str, str]:
    return {
        "codex_hooks": "Codex CLI hooks",
        "codex_mcp": "Codex app MCP",
        "claude_code_hooks": "Claude Code hooks",
        "claude_desktop_mcp": "Claude Desktop MCP",
        TELEMETRY_OPTION: "Anonymous telemetry",
    }


def client_descriptions() -> dict[str, str]:
    return {
        "codex_hooks": "Guards Codex shell file reads, URL fetches, and large Bash output.",
        "codex_mcp": "Registers got_file_size and safe bounded tools in the Codex app.",
        "claude_code_hooks": "Guards Claude Code Read, Bash, WebFetch, oversized output, and unrelated prompts in large sessions.",
        "claude_desktop_mcp": "Registers GOT MCP tools for Claude Desktop project workflows.",
        TELEMETRY_OPTION: "Sends anonymous install/tool usage metadata only. No paths, URLs, prompts, content, commands, actions, risk, or token counts.",
    }


def select_clients_text(options: list[str], integrations: list[str]) -> list[str]:
    labels = client_labels()
    descriptions = client_descriptions()
    selected = list(options)

    while True:
        print("Choose integrations:")
        for index, option in enumerate(options, 1):
            checked = "x" if option in selected else " "
            print(f"  [{checked}] {index}) {labels[option]}")
            print(f"      {descriptions[option]}")
        print()
        raw_choice = input(
            "Toggle by number, comma-separated numbers, or press Enter to install selected: "
        ).strip().lower()

        if raw_choice == "":
            if selected:
                return selected
            print("Select at least one client before installing.")
            continue

        invalid = False
        for token in [part.strip() for part in raw_choice.split(",") if part.strip()]:
            if not token.isdigit():
                invalid = True
                break
            index = int(token) - 1
            if index < 0 or index >= len(options):
                invalid = True
                break
            option = options[index]
            if option in selected:
                selected.remove(option)
            else:
                selected.append(option)
        if not invalid:
            print()
            continue
        print("Invalid selection. Enter item numbers like 1 or 1,2.")


def read_terminal_key() -> str:
    ch = sys.stdin.read(1)
    if ch == "\x1b":
        sequence = sys.stdin.read(2)
        if sequence == "[A":
            return "up"
        if sequence == "[B":
            return "down"
        return "escape"
    if ch in {"\r", "\n"}:
        return "enter"
    if ch == " ":
        return "space"
    if ch == "\x03":
        raise KeyboardInterrupt
    return ch.lower()


def render_tty_selector(options: list[str], selected: list[str], cursor: int, message: str = "") -> None:
    labels = client_labels()
    descriptions = client_descriptions()
    print("\033[2J\033[H", end="")
    print(color(FIRST_INSTALL_BANNER.strip("\n"), "cyan", "bold"))
    print()
    print(color("Choose integrations", "bold"))
    print(color("Use Up/Down to move, Space to toggle, Enter to install.", "dim"))
    print()
    for index, option in enumerate(options):
        focused = index == cursor
        pointer = color(">", "yellow", "bold") if focused else " "
        checked = color("x", "green", "bold") if option in selected else " "
        label = color(labels[option], "bold") if focused else labels[option]
        print(f"{pointer} [{checked}] {label}")
        print(f"    {color(descriptions[option], 'dim')}")
    if message:
        print()
        print(color(message, "yellow"))


def select_clients_tty(options: list[str], integrations: list[str]) -> list[str]:
    selected = list(options)
    cursor = 0
    old_settings = termios.tcgetattr(sys.stdin.fileno())
    try:
        tty.setcbreak(sys.stdin.fileno())
        print("\033[?25l", end="")
        message = ""
        while True:
            render_tty_selector(options, selected, cursor, message)
            key = read_terminal_key()
            message = ""
            if key == "up":
                cursor = (cursor - 1) % len(options)
            elif key == "down":
                cursor = (cursor + 1) % len(options)
            elif key == "space":
                option = options[cursor]
                if option in selected:
                    selected.remove(option)
                else:
                    selected.append(option)
            elif key == "a":
                selected = list(options)
            elif key == "enter":
                if any(item in selected for item in integrations):
                    print("\033[2J\033[H", end="")
                    return selected
                message = "Select at least one integration before installing."
            elif key in {"q", "escape"}:
                raise SystemExit("Install cancelled.")
    finally:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_settings)
        print("\033[?25h", end="")


def configure_user_telemetry(enabled: bool) -> None:
    user_config = Path("~/.guardians.json").expanduser()
    existing: dict[str, Any] = {}
    if user_config.exists():
        try:
            existing = json.loads(user_config.read_text())
        except Exception:
            existing = {}
    existing["telemetry_enabled"] = enabled
    existing["telemetry_host"] = DEFAULT_CONFIG["telemetry_host"]
    existing["telemetry_api_key"] = DEFAULT_CONFIG["telemetry_api_key"]
    user_config.parent.mkdir(parents=True, exist_ok=True)
    user_config.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n")
    if enabled:
        from guardians_of_the_token.telemetry import capture_install

        capture_install(config=existing)


def install_auto():
    parser = argparse.ArgumentParser(
        description="Auto-install Guardians into the detected local LLM clients."
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Install into all detected clients without prompting.",
    )
    args = parser.parse_args()

    integrations = detect_available_integrations()
    if not integrations:
        raise SystemExit(
            "No supported client home was detected. Expected ~/.codex and/or ~/.claude."
        )
    show_install_banner()
    selected = [*integrations, TELEMETRY_OPTION] if args.yes else select_clients_interactive(integrations)
    configure_user_telemetry(TELEMETRY_OPTION in selected)

    installed = []
    if "codex_hooks" in selected:
        print(color("Installing Codex CLI hooks...", "blue", "bold"))
        install_codex_cli_hooks()
        installed.append("OK Codex CLI hooks")
    if "codex_mcp" in selected:
        print(color("Installing Codex app MCP...", "blue", "bold"))
        install_codex_app_mcp()
        installed.append("OK Codex app MCP")
    if "claude_code_hooks" in selected:
        print(color("Installing Claude Code hooks...", "blue", "bold"))
        install_claude_global()
        installed.append("OK Claude Code hooks")
    if "claude_desktop_mcp" in selected:
        print(color("Installing Claude Desktop MCP...", "blue", "bold"))
        install_claude_desktop_mcp()
        installed.append("OK Claude Desktop MCP")

    ensure_test_files()
    print(color("Installed Guardians into:", "green", "bold"))
    for item in installed:
        print(f"- {item}")


def init_project():
    parser = argparse.ArgumentParser(
        description="Initialize Guardians project storage and Claude Project policy."
    )
    parser.add_argument(
        "project",
        nargs="?",
        default=".",
        help="Project folder where .got should be created.",
    )
    args = parser.parse_args()

    from guardians_of_the_token.mcp_server import got_project_init

    result = got_project_init({"path": args.project})
    print(f"Initialized Guardians project storage in {result['got_dir']}")
    print(f"Project policy written to {result['policy_path']}")
    print(f"Project policy appended to {result['claude_path']}")


def install_statusline_cmd():
    parser = argparse.ArgumentParser(
        description="Add Guardians status to ccstatusline (if installed) or set standalone statusLine."
    )
    parser.parse_args()
    mode = install_statusline()
    if mode == "ccstatusline":
        print(f"Added Guardians widget to ccstatusline ({CCSTATUSLINE_CONFIG})")
    elif mode == "standalone":
        print("Set Guardians standalone statusLine in ~/.claude/settings.json")
    else:
        print("statusLine already configured in ~/.claude/settings.json — skipped")


def status():
    parser = argparse.ArgumentParser(description="Print one-line Guardians status (for status bars).")
    parser.add_argument("project", nargs="?", default=None, help="Project folder (default: walk up from cwd).")
    args = parser.parse_args()

    from guardians_of_the_token.status import status_line

    print(status_line(args.project))


def preflight():
    parser = argparse.ArgumentParser(description="Preflight a local file and print its GOT risk.")
    parser.add_argument("path", help="File path to check.")
    parser.add_argument("--json", action="store_true", help="Print the full metadata as JSON.")
    parser.add_argument("--context-window", type=int, default=None, help="Override context window.")
    parser.add_argument("--warn-threshold-pct", type=int, default=None, help="Override warning threshold percent.")
    args = parser.parse_args()

    from guardians_of_the_token.mcp_server import got_file_size

    payload: dict[str, Any] = {"path": args.path}
    if args.context_window is not None:
        payload["context_window"] = args.context_window
    if args.warn_threshold_pct is not None:
        payload["warn_threshold_pct"] = args.warn_threshold_pct
    result = got_file_size(payload)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return
    print(f"{result['risk']}: ~{result['estimated_tokens']:,} tokens")
    if result["risk"] != "safe":
        print(result["warning"])


def report():
    parser = argparse.ArgumentParser(description="Print local Guardians token savings report.")
    parser.add_argument(
        "project",
        nargs="?",
        default=".",
        help="Project folder containing .got/events.jsonl.",
    )
    args = parser.parse_args()

    from guardians_of_the_token.report import format_report, report_data

    print(format_report(report_data(Path(args.project).expanduser().resolve())))


def doctor():
    parser = argparse.ArgumentParser(description="Check Guardians installation status.")
    parser.add_argument(
        "project",
        nargs="?",
        default=".",
        help="Project folder to inspect for local Guardians state.",
    )
    args = parser.parse_args()
    home = Path.home()
    project = Path(args.project).expanduser().resolve()
    codex_hooks = home / ".codex" / "hooks.json"
    codex_config = home / ".codex" / "config.toml"
    claude_settings = home / ".claude" / "settings.json"
    project_config = project / ".guardians.toml"
    project_events = project / ".got" / "events.jsonl"

    print("Guardians doctor")
    print()
    print(f"Codex hooks: {'OK' if codex_hooks.exists() else 'missing'} ({codex_hooks})")
    if codex_config.exists():
        text = codex_config.read_text()
        hooks_status = "OK" if has_feature_line(text, "hooks") else "missing"
        deprecated = "yes" if "codex_hooks" in text else "no"
        mcp_status = "OK" if "[mcp_servers.guardians]" in text else "missing"
        print(f"Codex hooks feature: {hooks_status}")
        print(f"Codex deprecated codex_hooks: {deprecated}")
        print(f"Codex MCP: {mcp_status}")
    else:
        print(f"Codex config: missing ({codex_config})")
    print(f"Claude Code hooks: {'OK' if claude_settings.exists() else 'missing'} ({claude_settings})")
    print(f"Project config: {'OK' if project_config.exists() else 'missing'} ({project_config})")
    print(f"Project event log: {'OK' if project_events.exists() else 'missing'} ({project_events})")
    print(f"Bypass file: {'present' if os.path.exists('/tmp/guardians_bypass') else 'not present'}")


def main():
    parser = argparse.ArgumentParser(
        prog="guardians",
        description="Guardians of the Token command line interface.",
    )
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("install", help="Install detected integrations.")
    subcommands.add_parser("codex-install", help="Install Codex hooks.")
    subcommands.add_parser("claude-install", help="Install Claude Code hooks.")
    subcommands.add_parser("project-init", help="Initialize project policy and .guardians.toml.")
    subcommands.add_parser("install-statusline", help="Add Guardians to ccstatusline or set standalone statusLine.")
    subcommands.add_parser("status", help="Print one-line status (for status bars).")
    subcommands.add_parser("preflight", help="Preflight a local file.")
    subcommands.add_parser("report", help="Print local token savings report.")
    subcommands.add_parser("dashboard", help="Run local dashboard.")
    subcommands.add_parser("doctor", help="Check installation status.")
    args, remaining = parser.parse_known_args()

    if args.command is None:
        parser.print_help()
        return

    dispatch = {
        "install": install_auto,
        "codex-install": install_codex,
        "claude-install": install_claude,
        "project-init": init_project,
        "install-statusline": install_statusline_cmd,
        "status": status,
        "preflight": preflight,
        "report": report,
        "doctor": doctor,
    }
    if args.command == "dashboard":
        from guardians_of_the_token.dashboard import main as dashboard_main

        sys.argv = ["guardians dashboard", *remaining]
        dashboard_main()
        return
    sys.argv = [f"guardians {args.command}", *remaining]
    dispatch[args.command]()


if __name__ == "__main__":
    main()
