"""Tests for dashboard, embeddings, events, pause, telemetry, and the test fixtures."""

import io
import json
import sys
import threading
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest

from guardians_of_the_token import dashboard, embeddings, events, pause, telemetry, test_server, test_support


# ---------- dashboard ----------


def _serve(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return f"http://127.0.0.1:{server.server_address[1]}"


def test_dashboard_handler_serves_index_and_404(tmp_path):
    from http.server import ThreadingHTTPServer

    server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.make_handler(tmp_path))
    base = _serve(server)
    try:
        body = urllib.request.urlopen(base + "/").read().decode()
        assert "No events yet." in body
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(base + "/missing")
        assert err.value.code == 404
    finally:
        server.shutdown()
        server.server_close()


def test_dashboard_main_stops_on_ctrl_c(tmp_path, monkeypatch, capsys):
    class FakeServer:
        closed = False

        def __init__(self, address, handler):
            self.address = address

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            FakeServer.closed = True

    monkeypatch.setattr(dashboard, "ThreadingHTTPServer", FakeServer)
    monkeypatch.setattr(sys, "argv", ["guardians-dashboard", "--port", "9999", "--project", str(tmp_path)])
    dashboard.main()
    assert FakeServer.closed
    assert "http://127.0.0.1:9999" in capsys.readouterr().out


# ---------- embeddings ----------


def test_ensure_model_downloads_missing_files(tmp_path, monkeypatch):
    monkeypatch.setattr(embeddings, "_MODELS_DIR", tmp_path / "models")
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "config.json").write_text("{}")  # already present: skipped
    fetched = []

    class FakeResp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(url):
        fetched.append(url)
        return FakeResp(b"data")

    monkeypatch.setattr(embeddings.urllib.request, "urlopen", fake_urlopen)
    assert embeddings.ensure_model() == tmp_path / "models"
    assert len(fetched) == len(embeddings._MODEL_FILES) - 1
    assert (tmp_path / "models" / "model.onnx").read_bytes() == b"data"


def test_load_reports_missing_model(tmp_path, monkeypatch):
    monkeypatch.setattr(embeddings, "_session", None)
    monkeypatch.setattr(embeddings, "_tokenizer", None)
    monkeypatch.setattr(embeddings, "_MODELS_DIR", tmp_path)
    with pytest.raises(embeddings.EmbeddingUnavailable, match="not found"):
        embeddings._load()


def test_load_reports_missing_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(embeddings, "_session", None)
    monkeypatch.setattr(embeddings, "_tokenizer", None)
    monkeypatch.setattr(embeddings, "_MODELS_DIR", tmp_path)
    (tmp_path / "model.onnx").write_text("x")
    (tmp_path / "tokenizer.json").write_text("x")
    monkeypatch.setitem(sys.modules, "onnxruntime", None)  # makes the import fail
    with pytest.raises(embeddings.EmbeddingUnavailable, match="onnxruntime"):
        embeddings._load()


def test_load_returns_cached_session(monkeypatch):
    monkeypatch.setattr(embeddings, "_session", "sess")
    monkeypatch.setattr(embeddings, "_tokenizer", "tok")
    assert embeddings._load() == ("sess", "tok")


def test_default_similarity_threshold():
    assert embeddings.default_similarity_threshold(None) == 0.12
    assert embeddings.default_similarity_threshold("bge-small-en-v1.5") == 0.25
    assert embeddings.default_similarity_threshold("unknown") == 0.12


def test_embeddings_main_accepts_download_flag(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(embeddings, "ensure_model", lambda: tmp_path)
    monkeypatch.setattr(sys, "argv", ["guardians-download-models", "--download"])
    embeddings.main()
    assert str(tmp_path) in capsys.readouterr().out


# ---------- events ----------


def test_event_base_dir_prefers_project_root(tmp_path):
    assert events.event_base_dir({"project_root": str(tmp_path)}) == tmp_path


def test_event_base_dir_uses_parent_of_a_file(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    assert events.event_base_dir(None, f) == tmp_path.resolve()


def test_log_event_swallows_errors(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("GUARDIANS_INBOX", str(blocker / "sub"))  # parent is a file: mkdir fails
    events.log_event({"action": "blocked"})


def test_read_events_skips_blank_and_bad_lines(tmp_path):
    got = tmp_path / ".got"
    got.mkdir()
    (got / "events.jsonl").write_text('{"a": 1}\n\nnot json\n{"b": 2}\n')
    assert events.read_events(tmp_path) == [{"a": 1}, {"b": 2}]


# ---------- pause ----------


@pytest.fixture
def pause_path(tmp_path, monkeypatch):
    path = tmp_path / "pause_until"
    monkeypatch.setenv("GUARDIANS_PAUSE_FILE", str(path))
    return path


def test_format_duration_zero():
    assert pause.format_duration(0) == "0s"


def test_clear_pause_handles_os_errors(pause_path, monkeypatch):
    pause_path.write_text("x")

    def boom(self):
        raise OSError("busy")

    monkeypatch.setattr(type(pause_path), "unlink", boom)
    assert pause.clear_pause() is False


def test_naive_timestamp_is_treated_as_utc(pause_path):
    until = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(tzinfo=None)
    pause_path.write_text(until.isoformat())
    assert 3500 < pause.pause_remaining() <= 3600


def test_is_paused_fails_open(monkeypatch):
    def boom():
        raise RuntimeError("bad")

    monkeypatch.setattr(pause, "pause_remaining", boom)
    assert pause.is_paused() is False


# ---------- telemetry ----------


def test_anonymous_id_created_once(tmp_path, monkeypatch):
    monkeypatch.delenv("GUARDIANS_TELEMETRY_ID", raising=False)
    monkeypatch.setattr(telemetry, "TELEMETRY_ID", tmp_path / "g" / "telemetry_id")
    first = telemetry.anonymous_id()
    assert telemetry.anonymous_id() == first


def test_anonymous_id_falls_back_when_unwritable(tmp_path, monkeypatch):
    monkeypatch.delenv("GUARDIANS_TELEMETRY_ID", raising=False)
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setattr(telemetry, "TELEMETRY_ID", blocker / "sub" / "telemetry_id")
    assert len(telemetry.anonymous_id()) == 36


def test_capture_install_skips_without_opt_in_or_key(monkeypatch):
    sent = []
    monkeypatch.setattr(telemetry.urllib.request, "urlopen", lambda *a, **k: sent.append(a))
    monkeypatch.setenv("GUARDIANS_TELEMETRY", "0")
    telemetry.capture_install()
    monkeypatch.setenv("GUARDIANS_TELEMETRY", "1")
    monkeypatch.delenv("GUARDIANS_TELEMETRY_API_KEY", raising=False)
    telemetry.capture_install(config={})
    assert sent == []


def test_capture_install_ignores_network_errors(monkeypatch):
    monkeypatch.setenv("GUARDIANS_TELEMETRY", "1")
    monkeypatch.setenv("GUARDIANS_TELEMETRY_API_KEY", "phc_test")
    monkeypatch.setenv("GUARDIANS_TELEMETRY_ID", "id")

    def boom(*a, **k):
        raise OSError("offline")

    monkeypatch.setattr(telemetry.urllib.request, "urlopen", boom)
    telemetry.capture_install()


# ---------- test_support ----------


def test_get_test_file_size_variants(monkeypatch):
    monkeypatch.setenv("GUARDIANS_TEST_SIZE", "abc")
    assert test_support.get_test_file_size("/x") is None
    monkeypatch.delenv("GUARDIANS_TEST_SIZE")
    assert test_support.get_test_file_size("/tmp/guardians_test_large") == 200_000
    assert test_support.get_test_file_size("elsewhere/guardians_test_small") == 10_000


def test_get_test_output_tokens_invalid(monkeypatch):
    monkeypatch.setenv("GUARDIANS_TEST_TOKENS", "lots")
    assert test_support.get_test_output_tokens() is None
    monkeypatch.delenv("GUARDIANS_TEST_TOKENS")
    assert test_support.get_test_output_tokens() is None


def test_ensure_test_files_writes_fixtures(tmp_path, monkeypatch):
    target = str(tmp_path / "fixture")
    monkeypatch.setattr(test_support, "TEST_FILE_SIZES", {target: 123})
    monkeypatch.setattr(test_support, "TEST_FILE_DESCRIPTIONS", {target: "demo"})
    test_support.ensure_test_files()
    text = open(target).read()
    assert "Fake size used by hooks: 123 bytes" in text


# ---------- test_server ----------


def test_test_server_get_and_404():
    server = test_server.make_server("127.0.0.1", 0)
    base = _serve(server)
    try:
        body = urllib.request.urlopen(base + "/large").read().decode()
        assert "Path: /large" in body
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(base + "/nope")
        assert err.value.code == 404
        assert b"Available paths" in err.value.read()
        head = urllib.request.Request(base + "/nope", method="HEAD")
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(head)
    finally:
        server.shutdown()
        server.server_close()


def test_local_ipv4_candidates(monkeypatch):
    infos = [(0, 0, 0, "", ("127.0.0.1", 0)), (0, 0, 0, "", ("192.168.1.5", 0)), (0, 0, 0, "", ("192.168.1.5", 0))]
    monkeypatch.setattr(test_server.socket, "getaddrinfo", lambda *a: infos)
    assert test_server.local_ipv4_candidates() == ["192.168.1.5"]

    def boom(*a):
        raise OSError("no dns")

    monkeypatch.setattr(test_server.socket, "getaddrinfo", boom)
    assert test_server.local_ipv4_candidates() == []


@pytest.mark.parametrize(
    "host, candidates, expected",
    [
        ("127.0.0.1", [], "Paths: /small"),
        ("0.0.0.0", ["10.0.0.2"], "http://10.0.0.2:8765"),
        ("0.0.0.0", [], "unable to detect"),
    ],
)
def test_test_server_main(monkeypatch, capsys, host, candidates, expected):
    class FakeServer:
        def serve_forever(self):
            return None

    monkeypatch.setattr(test_server, "make_server", lambda h, p: FakeServer())
    monkeypatch.setattr(test_server, "local_ipv4_candidates", lambda: candidates)
    monkeypatch.setattr(sys, "argv", ["guardians-test-server", "--host", host])
    test_server.main()
    assert expected in capsys.readouterr().out


def test_embed_cached_reuses_vectors_and_fails_open(monkeypatch, tmp_path):
    import numpy as np

    calls = []

    def fake_embed(texts, model_name):
        calls.append(list(texts))
        return np.array([[float(len(t)), 1.0] for t in texts])

    monkeypatch.setattr(embeddings, "embed", fake_embed)
    first = embeddings.embed_cached(["aa", "bbb", "aa"], model_name="m/x")
    assert first.tolist() == [[2.0, 1.0], [3.0, 1.0], [2.0, 1.0]]
    assert sorted(calls[0]) == ["aa", "bbb"]
    # Second call embeds only the new text.
    embeddings.embed_cached(["bbb", "cccc"], model_name="m/x")
    assert calls[1] == ["cccc"]
    # All cached: no model call at all.
    embeddings.embed_cached(["aa", "cccc"], model_name="m/x")
    assert len(calls) == 2
    # Oldest entries are evicted past the cap.
    monkeypatch.setattr(embeddings, "_CACHE_MAX", 2)
    embeddings.embed_cached(["ddddd"], model_name="m/x")
    with np.load(embeddings._cache_path("m/x")) as data:
        assert len(data["keys"]) == 2
    # A corrupt cache file is ignored.
    embeddings._cache_path("m/x").write_bytes(b"not a cache")
    assert embeddings.embed_cached(["aa"], model_name="m/x").tolist() == [[2.0, 1.0]]
    # An unwritable cache dir still returns vectors.
    monkeypatch.setattr(embeddings, "_cache_path", lambda name: tmp_path / "file" / "sub" / "c.npz")
    (tmp_path / "file").write_text("")
    assert embeddings.embed_cached(["eeeeee"], model_name="m/x").tolist() == [[6.0, 1.0]]
