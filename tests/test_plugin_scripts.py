"""Tests for the Claude Code plugin's launcher and bootstrap scripts."""

import importlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


@pytest.fixture
def scripts(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    monkeypatch.setenv("GUARDIANS_PLUGIN_VENV", str(tmp_path / "venv"))
    for name in ("_env", "bootstrap", "run"):
        sys.modules.pop(name, None)
    env = importlib.import_module("_env")
    bootstrap = importlib.import_module("bootstrap")
    run = importlib.import_module("run")
    return env, bootstrap, run


def _fake_venv(tmp_path, version="1.4.0", windows=False):
    venv = tmp_path / "venv"
    if windows:
        site = venv / "Lib" / "site-packages"
    else:
        site = venv / "lib" / "python3.13" / "site-packages"
    (site / f"guardians_of_the_token-{version}.dist-info").mkdir(parents=True)
    return venv


# ---------- _env ----------


def test_venv_dir_default_and_override(scripts, monkeypatch, tmp_path):
    env, _, _ = scripts
    assert env.venv_dir() == tmp_path / "venv"
    monkeypatch.delenv("GUARDIANS_PLUGIN_VENV")
    assert env.venv_dir() == Path.home() / ".guardians" / "plugin-venv"


def test_venv_python_posix_and_windows(scripts, monkeypatch, tmp_path):
    env, _, _ = scripts
    assert env.venv_python() == tmp_path / "venv" / "bin" / "python"
    monkeypatch.setattr(env.sys, "platform", "win32")
    assert env.venv_python() == tmp_path / "venv" / "Scripts" / "python.exe"


def test_can_import(scripts):
    env, _, _ = scripts
    assert env.can_import(sys.executable) is True
    assert env.can_import("/nonexistent/python") is False


def test_can_import_reports_missing_package(scripts, monkeypatch):
    env, _, _ = scripts
    monkeypatch.setattr(env, "IMPORT_NAME", "definitely_not_a_real_module_xyz")
    assert env.can_import(sys.executable) is False


def test_find_python_prefers_current_interpreter(scripts):
    env, _, _ = scripts
    assert env.find_python() == sys.executable


def test_find_python_falls_back_to_venv(scripts, monkeypatch, tmp_path):
    env, _, _ = scripts
    monkeypatch.setattr(env, "IMPORT_NAME", "definitely_not_a_real_module_xyz")
    assert env.find_python() is None

    vpy = tmp_path / "venv" / "bin" / "python"
    vpy.parent.mkdir(parents=True)
    vpy.write_text("")
    monkeypatch.setattr(env, "can_import", lambda python: python == str(vpy))
    assert env.find_python() == str(vpy)


def test_find_python_survives_find_spec_errors(scripts, monkeypatch):
    env, _, _ = scripts
    import importlib.util

    def boom(name):
        raise ValueError("broken")

    monkeypatch.setattr(importlib.util, "find_spec", boom)
    assert env.find_python() is None


# ---------- run.py ----------


def test_run_without_module_prints_usage(scripts, monkeypatch, capsys):
    _, _, run = scripts
    monkeypatch.setattr(sys, "argv", ["run.py"])
    assert run.main() == 0
    assert "usage" in capsys.readouterr().err


def test_run_fails_open_when_package_missing(scripts, monkeypatch):
    _, _, run = scripts
    monkeypatch.setattr(sys, "argv", ["run.py", "some.module"])
    monkeypatch.setattr(run, "find_python", lambda: None)
    assert run.main() == 0


def test_run_execs_module_with_found_python(scripts, monkeypatch):
    _, _, run = scripts
    calls = []
    monkeypatch.setattr(sys, "argv", ["run.py", "pkg.mod", "--flag"])
    monkeypatch.setattr(run, "find_python", lambda: "/py")
    monkeypatch.setattr(run.os, "execv", lambda path, args: calls.append((path, args)))
    assert run.main() == 0
    assert calls == [("/py", ["/py", "-m", "pkg.mod", "--flag"])]


def test_run_uses_subprocess_on_windows(scripts, monkeypatch):
    _, _, run = scripts
    monkeypatch.setattr(sys, "argv", ["run.py", "pkg.mod"])
    monkeypatch.setattr(run, "find_python", lambda: "/py")
    monkeypatch.setattr(run.sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "call", lambda args: 7)
    assert run.main() == 7


def test_run_end_to_end_runs_a_hook():
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "run.py"), "guardians_of_the_token.claude.pre_tool_guard"],
        input="{}",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0


# ---------- bootstrap.py ----------


def test_python_supported_matches_interpreter(scripts):
    _, bootstrap, _ = scripts
    assert bootstrap._python_supported() is (sys.version_info >= (3, 10))


def test_plugin_version_reads_manifest(scripts, monkeypatch, tmp_path):
    _, bootstrap, _ = scripts
    manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert bootstrap.plugin_version() == manifest["version"]
    monkeypatch.setattr(bootstrap, "PLUGIN_ROOT", tmp_path / "missing")
    assert bootstrap.plugin_version() is None
    assert bootstrap._requirement() == "guardians-of-the-token"


def test_requirement_pins_plugin_version(scripts, monkeypatch):
    _, bootstrap, _ = scripts
    monkeypatch.setattr(bootstrap, "plugin_version", lambda: "9.9.9")
    assert bootstrap._requirement() == "guardians-of-the-token==9.9.9"


def test_venv_package_version(scripts, tmp_path):
    _, bootstrap, _ = scripts
    assert bootstrap.venv_package_version() is None
    _fake_venv(tmp_path, "1.2.3")
    assert bootstrap.venv_package_version() == "1.2.3"


def test_venv_package_version_windows_layout(scripts, tmp_path):
    _, bootstrap, _ = scripts
    _fake_venv(tmp_path, "2.0.0", windows=True)
    assert bootstrap.venv_package_version() == "2.0.0"


def test_status_roundtrip_and_errors(scripts, monkeypatch, tmp_path):
    _, bootstrap, _ = scripts
    assert bootstrap._read_status() == {}
    tmp_path.mkdir(exist_ok=True)
    bootstrap._write_status("ok")
    assert bootstrap._read_status()["status"] == "ok"
    # Unwritable state dir: writing must not raise.
    monkeypatch.setattr(bootstrap, "_status_file", lambda: tmp_path / "nope" / "x" / "s.json")
    bootstrap._write_status("failed")


def _stub_main(bootstrap, monkeypatch, *, python, wanted="1.4.0", venv_version=None):
    monkeypatch.setattr(bootstrap, "_python_supported", lambda: True)
    monkeypatch.setattr(bootstrap, "find_python", lambda: python)
    monkeypatch.setattr(bootstrap, "plugin_version", lambda: wanted)
    monkeypatch.setattr(bootstrap, "venv_package_version", lambda: venv_version)


def _message(capsys):
    out = capsys.readouterr().out
    return json.loads(out)["systemMessage"] if out else ""


def test_main_silent_when_user_has_own_install(scripts, monkeypatch, capsys):
    _, bootstrap, _ = scripts
    _stub_main(bootstrap, monkeypatch, python=sys.executable)
    assert bootstrap.main() == 0
    assert _message(capsys) == ""


def test_main_silent_when_venv_matches_plugin(scripts, monkeypatch, capsys):
    env, bootstrap, _ = scripts
    _stub_main(bootstrap, monkeypatch, python=str(env.venv_python()), venv_version="1.4.0")
    assert bootstrap.main() == 0
    assert _message(capsys) == ""


def test_main_updates_mismatched_venv_silently(scripts, monkeypatch, capsys):
    env, bootstrap, _ = scripts
    _stub_main(bootstrap, monkeypatch, python=str(env.venv_python()), venv_version="1.3.0")
    started = []
    monkeypatch.setattr(bootstrap, "_start_worker", lambda: started.append(1) or True)
    assert bootstrap.main() == 0
    assert started == [1]
    assert _message(capsys) == ""


def test_main_starts_install_and_notifies(scripts, monkeypatch, capsys):
    _, bootstrap, _ = scripts
    _stub_main(bootstrap, monkeypatch, python=None)
    monkeypatch.setattr(bootstrap, "_start_worker", lambda: True)
    assert bootstrap.main() == 0
    message = _message(capsys)
    assert "installing guardians-of-the-token==1.4.0 in the background" in message


def test_main_reports_when_worker_cannot_start(scripts, monkeypatch, capsys):
    _, bootstrap, _ = scripts
    _stub_main(bootstrap, monkeypatch, python=None)
    monkeypatch.setattr(bootstrap, "_start_worker", lambda: False)
    bootstrap.main()
    assert "could not start the automatic install" in _message(capsys)


def test_main_silent_when_update_worker_cannot_start(scripts, monkeypatch, capsys):
    env, bootstrap, _ = scripts
    _stub_main(bootstrap, monkeypatch, python=str(env.venv_python()), venv_version="1.0.0")
    monkeypatch.setattr(bootstrap, "_start_worker", lambda: False)
    bootstrap.main()
    assert _message(capsys) == ""


def test_main_old_python(scripts, monkeypatch, capsys):
    env, bootstrap, _ = scripts
    _stub_main(bootstrap, monkeypatch, python=None)
    monkeypatch.setattr(bootstrap, "_python_supported", lambda: False)
    bootstrap.main()
    assert "needs Python 3.10+" in _message(capsys)
    # With a working (older) venv on an old python, stay quiet.
    _stub_main(bootstrap, monkeypatch, python=str(env.venv_python()), venv_version="1.0.0")
    monkeypatch.setattr(bootstrap, "_python_supported", lambda: False)
    bootstrap.main()
    assert _message(capsys) == ""


def test_main_reports_install_in_progress(scripts, monkeypatch, capsys, tmp_path):
    _, bootstrap, _ = scripts
    _stub_main(bootstrap, monkeypatch, python=None)
    tmp_path.mkdir(exist_ok=True)
    bootstrap._lock_file().write_text("x")
    bootstrap.main()
    assert "still installing" in _message(capsys)


def test_stale_lock_is_ignored(scripts, tmp_path):
    _, bootstrap, _ = scripts
    lock = bootstrap._lock_file()
    lock.write_text("x")
    old = time.time() - bootstrap.LOCK_STALE_SECONDS - 5
    os.utime(lock, (old, old))
    assert bootstrap._install_running() is False


def test_main_reports_recent_failure_without_retrying(scripts, monkeypatch, capsys):
    _, bootstrap, _ = scripts
    _stub_main(bootstrap, monkeypatch, python=None)
    monkeypatch.setattr(bootstrap, "_requirement", lambda: "guardians-of-the-token==1.4.0")
    bootstrap._status_file().write_text(
        json.dumps({"status": "failed", "at": time.time(), "requirement": "guardians-of-the-token==1.4.0"})
    )
    started = []
    monkeypatch.setattr(bootstrap, "_start_worker", lambda: started.append(1) or True)
    bootstrap.main()
    assert "automatic install failed" in _message(capsys)
    assert started == []


def test_main_retries_after_old_failure(scripts, monkeypatch, capsys):
    _, bootstrap, _ = scripts
    _stub_main(bootstrap, monkeypatch, python=None)
    bootstrap._status_file().write_text(json.dumps({"status": "failed", "at": 0, "requirement": "x"}))
    monkeypatch.setattr(bootstrap, "_start_worker", lambda: True)
    bootstrap.main()
    assert "in the background" in _message(capsys)


def test_start_worker_spawns_detached_process(scripts, monkeypatch):
    _, bootstrap, _ = scripts
    calls = []

    class FakePopen:
        def __init__(self, args, **kwargs):
            calls.append((args, kwargs))

    monkeypatch.setattr(bootstrap.subprocess, "Popen", FakePopen)
    assert bootstrap._start_worker() is True
    args, kwargs = calls[0]
    assert args[-1] == "--worker"
    assert kwargs["start_new_session"] is True
    assert bootstrap._lock_file().exists()


def test_start_worker_handles_errors(scripts, monkeypatch):
    _, bootstrap, _ = scripts

    def boom(*a, **k):
        raise OSError("no fork")

    monkeypatch.setattr(bootstrap.subprocess, "Popen", boom)
    assert bootstrap._start_worker() is False


def test_worker_success_creates_venv_installs_and_fetches_model(scripts, monkeypatch):
    env, bootstrap, _ = scripts
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[1:3] == ["-m", "venv"]:
            env.venv_python().parent.mkdir(parents=True)
            env.venv_python().write_text("")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(bootstrap.subprocess, "run", fake_run)
    monkeypatch.setattr(bootstrap, "_requirement", lambda: "guardians-of-the-token==1.4.0")
    bootstrap._lock_file().parent.mkdir(parents=True, exist_ok=True)
    bootstrap._lock_file().write_text("x")

    assert bootstrap.worker() == 0
    assert calls[0][1:3] == ["-m", "venv"]
    assert "guardians-of-the-token==1.4.0" in calls[1]
    assert calls[2][-1] == "guardians_of_the_token.embeddings"
    assert bootstrap._read_status()["status"] == "ok"
    assert not bootstrap._lock_file().exists()


def test_worker_failure_is_recorded(scripts, monkeypatch, capsys):
    env, bootstrap, _ = scripts
    env.venv_python().parent.mkdir(parents=True)
    env.venv_python().write_text("")

    def fake_run(args, **kwargs):
        raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(bootstrap.subprocess, "run", fake_run)
    assert bootstrap.worker() == 0  # no lock file: unlink error is swallowed
    assert bootstrap._read_status()["status"] == "failed"
    assert "install failed" in capsys.readouterr().err


def test_bootstrap_script_entry_points(tmp_path):
    env = {**os.environ, "GUARDIANS_PLUGIN_VENV": str(tmp_path / "venv")}
    # The package is importable in the test environment, so main() is silent.
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "bootstrap.py")], capture_output=True, text=True, env=env, check=False
    )
    assert result.returncode == 0
    assert result.stdout == ""
