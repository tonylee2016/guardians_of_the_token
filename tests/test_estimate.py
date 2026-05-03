from guardians_of_the_token.estimate import estimate_file, estimate_url


def test_estimate_file_uses_sampling_for_gray_zone_minified_json(tmp_path):
    target = tmp_path / "payload.json"
    target.write_text('{"k":[' + ",".join(f'"value{i}"' for i in range(1500)) + "]}")

    result = estimate_file(str(target), context_window=40_000, warn_pct=20)

    assert result["method"] == "size+extension+sample"
    assert result["signals"]["sampled"] is True
    assert result["signals"]["ratio"] >= 0.33


def test_estimate_file_skips_sampling_when_clearly_safe(tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("hello world\n" * 10)

    result = estimate_file(str(target), context_window=200_000, warn_pct=20)

    assert result["method"] == "size+extension"
    assert result["signals"]["sampled"] is False
    assert result["risk"] == "safe"


def test_estimate_url_uses_content_type_without_body_fetch():
    result = estimate_url(
        "https://example.test/data.json",
        context_window=200_000,
        warn_pct=20,
        content_length=200_000,
        content_type="application/json",
    )

    assert result["estimated_tokens"] == 66_000
    assert result["signals"]["reason"] == "json-content-type"
    assert result["risk"] == "warning"


def test_estimate_url_missing_content_length_is_unknown():
    result = estimate_url(
        "https://example.test/page",
        context_window=200_000,
        warn_pct=20,
        content_length=None,
        content_type="text/html; charset=utf-8",
    )

    assert result["risk"] == "unknown"
    assert result["confidence"] == "low"
