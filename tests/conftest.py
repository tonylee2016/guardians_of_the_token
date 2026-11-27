"""Shared test fixtures.

The install path calls ``_ensure_prompt_guard_model()`` which can hit
HuggingFace to fetch the bundled MiniLM ONNX. We stub it out for all
tests so the suite stays offline-clean and fast regardless of whether
the local dev box has the model files already cached.
"""

import pytest


@pytest.fixture(autouse=True)
def _stub_prompt_guard_model_fetch(monkeypatch):
    monkeypatch.setattr(
        "guardians_of_the_token.cli._ensure_prompt_guard_model",
        lambda: None,
    )


@pytest.fixture(autouse=True)
def _isolate_user_state(monkeypatch, tmp_path_factory):
    """Keep every test away from the real home dir, pause file, and telemetry ID."""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GUARDIANS_PAUSE_FILE", str(home / ".got" / "pause_until"))
    monkeypatch.setattr("guardians_of_the_token.config.USER_CONFIG", home / ".guardians.json")
    monkeypatch.setattr("guardians_of_the_token.telemetry.TELEMETRY_ID", home / ".guardians" / "telemetry_id")
    return home


@pytest.fixture(autouse=True)
def _no_pypi_network(monkeypatch):
    """Update checks must never reach pypi.org from the test suite."""

    def offline(timeout: float = 1.5) -> str:
        raise OSError("network disabled in tests")

    monkeypatch.setattr("guardians_of_the_token.update.latest_pypi_version", offline)


@pytest.fixture(autouse=True)
def _no_claude_cli(monkeypatch):
    """Installer detection must not depend on whether this machine has the claude CLI."""
    monkeypatch.setattr("guardians_of_the_token.cli.claude_cli", lambda: None)
