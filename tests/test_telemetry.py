import json

from guardians_of_the_token import telemetry


def test_telemetry_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("GUARDIANS_TELEMETRY", raising=False)

    assert telemetry.telemetry_enabled({}) is False


def test_install_properties_only_include_runtime_metadata_and_disable_geoip():
    props = telemetry.install_properties()

    assert props["$geoip_disable"] is True
    assert props["$ip"] is None
    assert "got_version" in props
    assert "python_version" in props
    assert "platform" in props
    assert "client" not in props
    assert "kind" not in props
    assert "action" not in props
    assert "risk" not in props
    assert "estimated_tokens_bucket" not in props
    assert "target" not in props
    assert "url" not in props


def test_capture_install_sends_single_anonymous_install_event(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(
            {
                "url": request.full_url,
                "timeout": timeout,
                "body": json.loads(request.data.decode("utf-8")),
            }
        )

        class Response:
            def close(self):
                pass

        return Response()

    monkeypatch.setenv("GUARDIANS_TELEMETRY", "1")
    monkeypatch.setenv("GUARDIANS_TELEMETRY_API_KEY", "phc_test")
    monkeypatch.setenv("GUARDIANS_TELEMETRY_ID", "anon-test")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    telemetry.capture_install()

    assert len(calls) == 1
    assert calls[0]["url"] == "https://us.i.posthog.com/capture/"
    body = calls[0]["body"]
    assert body["api_key"] == "phc_test"
    assert body["event"] == "got_install"
    assert body["distinct_id"] == "anon-test"
    assert body["properties"]["$geoip_disable"] is True
    assert body["properties"]["$ip"] is None
    assert "got_version" in body["properties"]
    assert "python_version" in body["properties"]
    assert "platform" in body["properties"]
    assert "client" not in body["properties"]
    assert "kind" not in body["properties"]
    assert "risk" not in body["properties"]
    assert "estimated_tokens_bucket" not in body["properties"]
    assert "target" not in body["properties"]
