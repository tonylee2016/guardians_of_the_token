import io
import json
from datetime import datetime, timedelta, timezone

from guardians_of_the_token import state
from guardians_of_the_token.claude import session_start
from guardians_of_the_token.update import UpdateResult


def _write_transcript(tmp_path):
    lines = [
        {
            "type": "user",
            "sessionId": "sess-1",
            "message": {"role": "user", "content": "Implement the project_state feature"},
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "Edit", "input": {"file_path": "src/foo.py"}},
                    {"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q   tests/"}},
                ],
                "usage": {"input_tokens": 100, "cache_read_input_tokens": 150_000},
            },
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": "I edited the file and ran the tests; here is a longer explanation of what changed so far in the session.",
                    }
                ],
            },
        },
        {
            "type": "system",
            "subtype": "away_summary",
            "content": "Session recap: building project_state snapshots for cold start.",
        },
    ]
    path = tmp_path / "transcript.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    return str(path)


def test_build_state_extracts_signals(tmp_path):
    transcript = _write_transcript(tmp_path)
    st = state.build_state(
        transcript,
        trigger="precompact-auto",
        base_dir=tmp_path,
        live_tokens=150_100,
        context_pct=0.75,
        settings={},
    )

    assert st.summary_source == "recap"
    assert "project_state snapshots" in st.summary
    assert st.goals == ["Implement the project_state feature"]
    assert st.files == ["src/foo.py"]
    assert st.commands == ["pytest -q tests/"]  # whitespace collapsed
    assert "edited the file" in st.last_activity
    assert st.session_id == "sess-1"
    assert st.context_pct == 0.75


def test_capture_writes_md_and_json(tmp_path):
    transcript = _write_transcript(tmp_path)
    md = state.capture(transcript, base_dir=tmp_path, trigger="precompact-auto", config={})
    assert md is not None and md.exists()

    text = md.read_text()
    assert "# GOT project_state" in text
    assert "src/foo.py" in text

    data = state.load_state(tmp_path)
    assert data["files"] == ["src/foo.py"]
    assert data["trigger"] == "precompact-auto"


def test_capture_disabled(tmp_path):
    transcript = _write_transcript(tmp_path)
    md = state.capture(
        transcript, base_dir=tmp_path, trigger="x", config={"project_state": {"enabled": False}}
    )
    assert md is None
    assert state.load_state(tmp_path) is None


def test_maybe_capture_on_pressure_respects_threshold_and_throttle(tmp_path):
    transcript = _write_transcript(tmp_path)
    config = {"project_state": {"save_context_pct": 0.70, "min_save_interval_seconds": 300}}

    # below threshold -> no write
    assert (
        state.maybe_capture_on_pressure(
            transcript, base_dir=tmp_path, live_tokens=10, context_pct=0.5, config=config
        )
        is None
    )
    assert state.load_state(tmp_path) is None

    # above threshold -> writes
    assert state.maybe_capture_on_pressure(
        transcript, base_dir=tmp_path, live_tokens=150_100, context_pct=0.8, config=config
    ) is not None

    # immediate re-call is throttled
    assert (
        state.maybe_capture_on_pressure(
            transcript, base_dir=tmp_path, live_tokens=150_100, context_pct=0.9, config=config
        )
        is None
    )


def test_resume_offer_fresh_and_matching(tmp_path):
    transcript = _write_transcript(tmp_path)
    state.capture(transcript, base_dir=tmp_path, trigger="precompact-auto", config={})

    offer = state.resume_offer(tmp_path, config={})
    assert offer and "project_state" in offer and ".got/project_state.md" in offer


def test_resume_offer_stale_returns_none(tmp_path):
    transcript = _write_transcript(tmp_path)
    state.capture(transcript, base_dir=tmp_path, trigger="precompact-auto", config={})

    _, json_path = state.state_paths(tmp_path)
    data = json.loads(json_path.read_text())
    data["saved_at"] = (datetime.now(timezone.utc) - timedelta(hours=400)).isoformat()
    json_path.write_text(json.dumps(data))

    assert state.resume_offer(tmp_path, config={"project_state": {"max_age_hours": 168}}) is None


def test_resume_offer_other_project_returns_none(tmp_path):
    transcript = _write_transcript(tmp_path)
    state.capture(transcript, base_dir=tmp_path, trigger="precompact-auto", config={})

    _, json_path = state.state_paths(tmp_path)
    data = json.loads(json_path.read_text())
    data["project"] = "/some/other/project"
    json_path.write_text(json.dumps(data))

    assert state.resume_offer(tmp_path, config={}) is None


def _no_update(*_a, **_k):
    return UpdateResult("current", "1.2.0", "1.2.0", "")


def test_session_start_emits_resume_on_cold_start(monkeypatch, capsys):
    monkeypatch.setattr(session_start, "startup_auto_update", _no_update)
    monkeypatch.setattr(state, "resume_offer", lambda *a, **k: "RESUME_NUDGE")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"source": "startup", "cwd": "/x"})))

    session_start.main()

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "RESUME_NUDGE" in payload["hookSpecificOutput"]["additionalContext"]


def test_session_start_silent_on_resume_source(monkeypatch, capsys):
    monkeypatch.setattr(session_start, "startup_auto_update", _no_update)
    monkeypatch.setattr(state, "resume_offer", lambda *a, **k: "RESUME_NUDGE")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"source": "resume", "cwd": "/x"})))

    session_start.main()

    assert capsys.readouterr().out == ""
