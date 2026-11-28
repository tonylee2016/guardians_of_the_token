"""In-process tests for cap_output's edge cases."""

import io
import os

import pytest

from guardians_of_the_token.claude import cap_output


def _main(monkeypatch, data: bytes):
    out = io.BytesIO()
    monkeypatch.setattr(cap_output.sys, "stdin", type("S", (), {"buffer": io.BytesIO(data)})())
    monkeypatch.setattr(cap_output.sys, "stdout", type("S", (), {"buffer": out})())
    with pytest.raises(SystemExit) as exc:
        cap_output.main()
    return exc.value.code, out.getvalue()


def test_unwritable_output_dir_still_summarizes(monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setenv("GUARDIANS_OUTPUT_DIR", str(blocker / "sub"))
    code, out = _main(monkeypatch, b"x\n" * 20_000)
    assert code == 0
    assert b"Full output was not saved" in out


def test_bad_exit_marker_and_write_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("GUARDIANS_OUTPUT_DIR", str(tmp_path))

    class Broken(io.BytesIO):
        def write(self, _):
            raise OSError("disk full")

    monkeypatch.setattr(cap_output, "_open_full_log", lambda: (Broken(), str(tmp_path / "x.log")))
    code, out = _main(monkeypatch, b"y\n" * 20_000 + b"\x1eGOT_EXIT=oops\n")
    assert code == 0
    assert b"Full output was not saved" in out


def test_small_output_survives_failed_cleanup(monkeypatch, tmp_path):
    monkeypatch.setenv("GUARDIANS_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(cap_output.os, "remove", lambda _: (_ for _ in ()).throw(OSError()))
    assert _main(monkeypatch, b"hi\n") == (0, b"hi\n")


def test_digest_blank_lines_overflow_many_problems_and_trim(monkeypatch):
    monkeypatch.setattr(cap_output, "MAX_PATTERNS", 2)
    digest = cap_output.Digest()
    digest.add(b"   \n")
    for word in "ghijklmnopqrs":
        digest.add(f"ERROR {word * 300}\n".encode())
    assert digest.overflow
    text = digest.render(10**6, 1, None)
    assert "More problems:" in text
    assert text.endswith("… (summary trimmed)\n")
    assert len(text) <= cap_output.SUMMARY_CAP
