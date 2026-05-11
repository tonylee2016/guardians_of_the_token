import json
import os
import subprocess
import sys

from guardians_of_the_token.config import estimate_cost, load_config, policy_decision
from guardians_of_the_token.events import log_event
from guardians_of_the_token.report import format_report, report_data
from guardians_of_the_token.dashboard import dashboard_html


def test_project_toml_config_supports_whitelist_and_cost(tmp_path, monkeypatch):
    config_path = tmp_path / ".guardians.toml"
    config_path.write_text(
        "warn_threshold_pct = 10\n"
        "default_input_price_per_million = 2.5\n"
        "whitelist = [\"docs/**\"]\n"
        "ignore = [\"dist/**\"]\n"
    )
    target = tmp_path / "docs" / "guide.md"
    target.parent.mkdir()
    target.write_text("hello\n")
    monkeypatch.delenv("GUARDIANS_CONFIG", raising=False)

    config = load_config(target)

    assert config["warn_threshold_pct"] == 10
    assert estimate_cost(1_000_000, config) == 2.5
    assert policy_decision(str(target), config) == "whitelisted"


def test_codex_pre_hook_allows_whitelisted_large_file(tmp_path):
    (tmp_path / ".guardians.toml").write_text('whitelist = ["large.txt"]\n')
    target = tmp_path / "large.txt"
    target.write_text("small")
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "cat large.txt"},
        "cwd": str(tmp_path),
        "model": "gpt-5.5",
        "transcript_path": "",
    }
    env = os.environ.copy()
    env["GUARDIANS_TEST_SIZE"] = "800000"

    result = subprocess.run(
        [sys.executable, "-m", "guardians_of_the_token.codex.pre_tool_guard"],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == ""


def test_report_summarizes_logged_savings(tmp_path):
    log_event(
        {
            "client": "codex",
            "kind": "file",
            "target": "logs/build.log",
            "action": "blocked",
            "estimated_tokens": 200_000,
            "estimated_cost": 0.6,
        },
        base_dir=tmp_path,
    )
    log_event(
        {
            "client": "claude",
            "kind": "output",
            "target": "Bash output",
            "action": "suppressed",
            "estimated_tokens": 100_000,
            "estimated_cost": 0.3,
        },
        base_dir=tmp_path,
    )

    data = report_data(tmp_path)
    text = format_report(data)

    assert data["tokens_saved"] == 300_000
    assert data["cost_saved"] == 0.9
    assert "Estimated cost avoided: $0.9000" in text
    assert "logs/build.log" in text


def test_dashboard_renders_report_metrics():
    html = dashboard_html(
        {
            "blocked_operations": 2,
            "tokens_saved": 300_000,
            "cost_saved": 0.9,
            "by_client": {"codex": 1, "claude": 1},
            "by_kind": {"file": 1},
            "top_targets": [("logs/build.log", 200_000)],
            "events": [],
        }
    )

    assert "Guardians Dashboard" in html
    assert "300,000" in html
    assert "$0.9000" in html
