import io
import json

import pytest

from guardians_of_the_token import pause


@pytest.fixture(autouse=True)
def _isolated_pause_file(tmp_path, monkeypatch):
    monkeypatch.setenv("GUARDIANS_PAUSE_FILE", str(tmp_path / "pause_until"))
    yield


def test_parse_duration_forms():
    assert pause.parse_duration("90") == 90 * 60      # bare int = minutes
    assert pause.parse_duration("1h") == 3600
    assert pause.parse_duration("30m") == 1800
    assert pause.parse_duration("1h30m") == 5400
    assert pause.parse_duration("45s") == 45
    assert pause.parse_duration("2d") == 2 * 86400


def test_parse_duration_caps_and_rejects():
    assert pause.parse_duration("100d") == 7 * 86400  # capped at 7 days
    for bad in ("", "abc", "1h banana", "0", "-5m"):
        with pytest.raises(ValueError):
            pause.parse_duration(bad)


def test_set_is_and_clear_pause():
    assert pause.is_paused() is False
    pause.set_pause(3600)
    assert pause.is_paused() is True
    remaining = pause.pause_remaining()
    assert remaining is not None and 3500 < remaining <= 3600
    assert pause.clear_pause() is True
    assert pause.is_paused() is False
    assert pause.clear_pause() is False


def test_expired_pause_auto_clears():
    pause.set_pause(1)
    # Write an already-expired timestamp directly.
    from datetime import datetime, timedelta, timezone

    pause.pause_file().write_text(
        (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    )
    assert pause.is_paused() is False
    assert pause.pause_file().exists() is False  # cleaned up on read


def test_corrupt_pause_file_fails_open():
    pause.pause_file().parent.mkdir(parents=True, exist_ok=True)
    pause.pause_file().write_text("not a timestamp")
    assert pause.is_paused() is False


def test_format_duration():
    assert pause.format_duration(3600) == "1h"
    assert pause.format_duration(5400) == "1h 30m"
    assert pause.format_duration(45) == "45s"


def test_guards_exit_silently_when_paused(monkeypatch, capsys):
    from guardians_of_the_token.claude import prompt_guard

    pause.set_pause(3600)
    monkeypatch.setattr(
        "sys.stdin", io.StringIO(json.dumps({"prompt": "anything", "cwd": "/x"}))
    )
    with pytest.raises(SystemExit) as exc:
        prompt_guard.main()
    assert exc.value.code == 0
    assert capsys.readouterr().out == ""
