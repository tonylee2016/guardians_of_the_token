import argparse
import json
import sys
import termios
import tty
from pathlib import Path
from typing import Any

from guardians_of_the_token.test_support import ensure_test_files


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
    }
}


CONFIG_TOML = """[features]
codex_hooks = true
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
    if "codex_hooks" in existing:
        return ensure_codex_mcp_server(existing)
    if "[features]" not in existing:
        separator = "" if not existing or existing.endswith("\n") else "\n"
        return ensure_codex_mcp_server(f"{existing}{separator}{CONFIG_TOML}")

    lines = existing.splitlines()
    for index, line in enumerate(lines):
        if line.strip() == "[features]":
            lines.insert(index + 1, "codex_hooks = true")
            break
    result = "\n".join(lines)
    result = result + ("\n" if existing.endswith("\n") else "")
    return ensure_codex_mcp_server(result)


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
        if "codex_hooks" not in existing:
            raise SystemExit(
                f"{config_path} already exists and does not mention codex_hooks. "
                "Enable codex hooks manually or re-run with --force."
            )
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


def install_claude_global() -> Path:
    settings_path = Path("~/.claude/settings.json").expanduser()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    existing = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    merged_hooks = merge_hooks(existing.get("hooks", {}), CLAUDE_HOOKS_JSON["hooks"])
    existing["hooks"] = merged_hooks
    settings_path.write_text(json.dumps(existing, indent=2) + "\n")
    return settings_path


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


def select_clients_interactive(integrations: list[str]) -> list[str]:
    if len(integrations) <= 1:
        return integrations
    if sys.stdin.isatty() and sys.stdout.isatty():
        return select_clients_tty(integrations)
    return select_clients_text(integrations)


def client_labels() -> dict[str, str]:
    return {
        "codex_hooks": "Codex CLI hooks",
        "codex_mcp": "Codex app MCP",
        "claude_code_hooks": "Claude Code hooks",
        "claude_desktop_mcp": "Claude Desktop MCP",
    }


def client_descriptions() -> dict[str, str]:
    return {
        "codex_hooks": "Guards Codex shell file reads, URL fetches, and large Bash output.",
        "codex_mcp": "Registers got_file_size and safe bounded tools in the Codex app.",
        "claude_code_hooks": "Guards Claude Code Read, Bash, WebFetch, and oversized output.",
        "claude_desktop_mcp": "Registers GOT MCP tools for Claude Desktop project workflows.",
    }


def select_clients_text(integrations: list[str]) -> list[str]:
    labels = client_labels()
    descriptions = client_descriptions()
    selected = list(integrations)

    while True:
        print("Detected installable integrations:")
        for index, integration in enumerate(integrations, 1):
            checked = "x" if integration in selected else " "
            print(f"  [{checked}] {index}) {labels[integration]}")
            print(f"      {descriptions[integration]}")
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
            if index < 0 or index >= len(integrations):
                invalid = True
                break
            integration = integrations[index]
            if integration in selected:
                selected.remove(integration)
            else:
                selected.append(integration)
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


def render_tty_selector(integrations: list[str], selected: list[str], cursor: int, message: str = "") -> None:
    labels = client_labels()
    descriptions = client_descriptions()
    print("\033[2J\033[H", end="")
    print(color(FIRST_INSTALL_BANNER.strip("\n"), "cyan", "bold"))
    print()
    print(color("Choose integrations", "bold"))
    print(color("Use Up/Down to move, Space to toggle, Enter to install.", "dim"))
    print()
    for index, integration in enumerate(integrations):
        focused = index == cursor
        pointer = color(">", "yellow", "bold") if focused else " "
        checked = color("x", "green", "bold") if integration in selected else " "
        label = color(labels[integration], "bold") if focused else labels[integration]
        print(f"{pointer} [{checked}] {label}")
        print(f"    {color(descriptions[integration], 'dim')}")
    if message:
        print()
        print(color(message, "yellow"))


def select_clients_tty(integrations: list[str]) -> list[str]:
    selected = list(integrations)
    cursor = 0
    old_settings = termios.tcgetattr(sys.stdin.fileno())
    try:
        tty.setcbreak(sys.stdin.fileno())
        print("\033[?25l", end="")
        message = ""
        while True:
            render_tty_selector(integrations, selected, cursor, message)
            key = read_terminal_key()
            message = ""
            if key == "up":
                cursor = (cursor - 1) % len(integrations)
            elif key == "down":
                cursor = (cursor + 1) % len(integrations)
            elif key == "space":
                integration = integrations[cursor]
                if integration in selected:
                    selected.remove(integration)
                else:
                    selected.append(integration)
            elif key == "a":
                selected = list(integrations)
            elif key == "enter":
                if selected:
                    print("\033[2J\033[H", end="")
                    return selected
                message = "Select at least one integration before installing."
            elif key in {"q", "escape"}:
                raise SystemExit("Install cancelled.")
    finally:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_settings)
        print("\033[?25h", end="")


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
    selected = integrations if args.yes else select_clients_interactive(integrations)

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


if __name__ == "__main__":
    install_codex()
