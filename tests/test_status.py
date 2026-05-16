import json

import pytest

from guardians_of_the_token.cli import (
    GUARDIANS_STATUS_COMMAND,
    _ccstatusline_add_guardians,
    _ccstatusline_has_guardians,
    install_statusline,
)
from guardians_of_the_token.status import status_line


# --- status_line ---

def test_status_line_no_events(tmp_path):
    assert status_line(tmp_path) == "🛡️ on"


def test_status_line_with_events(tmp_path):
    got = tmp_path / ".got"
    got.mkdir()
    (got / "events.jsonl").write_text(
        json.dumps({
            "action": "blocked",
            "client": "claude",
            "estimated_tokens": 50_000,
            "estimated_cost": 0.15,
            "kind": "file",
            "risk": "warning",
            "target": "/tmp/big.log",
            "time": "2026-01-01T00:00:00+00:00",
        }) + "\n"
    )
    result = status_line(tmp_path)
    assert result.startswith("🛡️")
    assert "1×" in result
    assert "50k" in result
    assert "$0.15" in result


def test_status_line_formats_millions(tmp_path):
    got = tmp_path / ".got"
    got.mkdir()
    (got / "events.jsonl").write_text(
        json.dumps({
            "action": "blocked",
            "client": "claude",
            "estimated_tokens": 1_500_000,
            "estimated_cost": 4.50,
            "kind": "file",
            "risk": "critical",
            "target": "/tmp/huge.log",
            "time": "2026-01-01T00:00:00+00:00",
        }) + "\n"
    )
    result = status_line(tmp_path)
    assert "1.5M" in result


# --- ccstatusline helpers ---

CLEAN_CONFIG = {
    "version": 3,
    "lines": [
        [
            {"id": "1", "type": "model", "color": "cyan"},
            {"id": "2", "type": "separator"},
            {"id": "3", "type": "git-branch", "color": "magenta"},
        ],
        [],
        [],
    ],
}


def test_ccstatusline_has_guardians_false():
    assert not _ccstatusline_has_guardians(CLEAN_CONFIG)


def test_ccstatusline_has_guardians_true():
    config = _ccstatusline_add_guardians(CLEAN_CONFIG)
    assert _ccstatusline_has_guardians(config)


def test_ccstatusline_add_guardians_appends_separator_and_widget():
    config = _ccstatusline_add_guardians(CLEAN_CONFIG)
    last_two = config["lines"][0][-2:]
    assert last_two[0]["type"] == "separator"
    assert last_two[1]["type"] == "custom-command"
    assert last_two[1]["commandPath"] == GUARDIANS_STATUS_COMMAND
    assert last_two[1]["color"] == "green"


def test_ccstatusline_add_guardians_uses_unique_ids():
    config = _ccstatusline_add_guardians(CLEAN_CONFIG)
    ids = [w["id"] for line in config["lines"] for w in line]
    assert len(ids) == len(set(ids))


def test_ccstatusline_add_guardians_preserves_other_lines():
    config = _ccstatusline_add_guardians(CLEAN_CONFIG)
    assert config["lines"][1] == []
    assert config["lines"][2] == []


# --- install_statusline ---

def test_install_statusline_standalone(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    ccstatus = tmp_path / "nonexistent.json"
    monkeypatch.setattr("guardians_of_the_token.cli.claude_settings_path", lambda: settings)
    monkeypatch.setattr("guardians_of_the_token.cli.CCSTATUSLINE_CONFIG", ccstatus)

    result = install_statusline()
    assert result == "standalone"
    data = json.loads(settings.read_text())
    assert data["statusLine"]["command"] == GUARDIANS_STATUS_COMMAND


def test_install_statusline_standalone_exists_skips(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"statusLine": {"command": "other-tool"}}))
    ccstatus = tmp_path / "nonexistent.json"
    monkeypatch.setattr("guardians_of_the_token.cli.claude_settings_path", lambda: settings)
    monkeypatch.setattr("guardians_of_the_token.cli.CCSTATUSLINE_CONFIG", ccstatus)

    result = install_statusline()
    assert result == "standalone-exists"
    assert json.loads(settings.read_text())["statusLine"]["command"] == "other-tool"


def test_install_statusline_ccstatusline(tmp_path, monkeypatch):
    import guardians_of_the_token.cli as cli_mod
    ccstatus = tmp_path / "ccstatusline.json"
    ccstatus.write_text(json.dumps(CLEAN_CONFIG))
    monkeypatch.setattr(cli_mod, "CCSTATUSLINE_CONFIG", ccstatus)

    result = install_statusline()
    assert result == "ccstatusline"
    config = json.loads(ccstatus.read_text())
    assert _ccstatusline_has_guardians(config)


# --- install_claude_skills_global ---


def test_install_claude_skills_copies_bundled_skill(tmp_path, monkeypatch):
    from guardians_of_the_token.cli import install_claude_skills_global

    dest = tmp_path / "skills"
    monkeypatch.setattr("guardians_of_the_token.cli.CLAUDE_SKILLS_DIR", dest)

    installed = install_claude_skills_global()
    assert installed, "expected at least one skill to be installed"
    unblock = dest / "got-unblock" / "SKILL.md"
    assert unblock in installed
    text = unblock.read_text()
    assert "got-unblock" in text
    assert "/got-unblock" in text


def test_install_claude_skills_overwrites_existing(tmp_path, monkeypatch):
    from guardians_of_the_token.cli import install_claude_skills_global

    dest = tmp_path / "skills"
    (dest / "got-unblock").mkdir(parents=True)
    (dest / "got-unblock" / "SKILL.md").write_text("stale content")
    monkeypatch.setattr("guardians_of_the_token.cli.CLAUDE_SKILLS_DIR", dest)

    install_claude_skills_global()
    assert "stale content" not in (dest / "got-unblock" / "SKILL.md").read_text()


def test_install_statusline_ccstatusline_idempotent(tmp_path, monkeypatch):
    import guardians_of_the_token.cli as cli_mod
    ccstatus = tmp_path / "ccstatusline.json"
    ccstatus.write_text(json.dumps(CLEAN_CONFIG))
    monkeypatch.setattr(cli_mod, "CCSTATUSLINE_CONFIG", ccstatus)

    install_statusline()
    config_after_first = json.loads(ccstatus.read_text())
    install_statusline()
    config_after_second = json.loads(ccstatus.read_text())
    assert config_after_first == config_after_second
