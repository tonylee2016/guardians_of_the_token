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
