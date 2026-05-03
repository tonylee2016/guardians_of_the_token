import math
import os
import re
import urllib.request
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from guardians_of_the_token.test_support import TEST_URL_SIZES, get_test_file_size

DEFAULT_SAMPLE_BYTES = 16 * 1024

TEXT_EXT_RATIOS = {
    ".txt": 0.25,
    ".md": 0.24,
    ".rst": 0.24,
    ".py": 0.25,
    ".js": 0.27,
    ".ts": 0.27,
    ".tsx": 0.27,
    ".jsx": 0.27,
    ".java": 0.26,
    ".go": 0.25,
    ".rs": 0.26,
    ".rb": 0.25,
    ".c": 0.25,
    ".cc": 0.25,
    ".cpp": 0.25,
    ".h": 0.25,
    ".hpp": 0.25,
    ".html": 0.28,
    ".htm": 0.28,
    ".css": 0.27,
    ".xml": 0.29,
    ".yml": 0.27,
    ".yaml": 0.27,
    ".toml": 0.26,
    ".ini": 0.25,
    ".cfg": 0.25,
    ".conf": 0.25,
    ".csv": 0.31,
    ".tsv": 0.30,
    ".log": 0.32,
    ".json": 0.33,
    ".jsonl": 0.33,
}

BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".zip", ".gz", ".bz2",
    ".xz", ".7z", ".tar", ".mp3", ".mp4", ".mov", ".avi", ".woff", ".woff2",
    ".ttf", ".otf", ".sqlite", ".db", ".bin", ".exe", ".dylib", ".so",
}

TEXT_CONTENT_TYPES = (
    "text/",
    "application/json",
    "application/xml",
    "application/javascript",
    "application/x-javascript",
    "application/xhtml+xml",
    "application/csv",
)

BINARY_CONTENT_TYPES = (
    "image/",
    "audio/",
    "video/",
    "font/",
    "application/octet-stream",
    "application/pdf",
    "application/zip",
    "application/gzip",
)


def risk_for(tokens: int, context_window: int, warn_pct: int) -> str:
    compact_tokens = int(context_window * 90 / 100)
    warn_tokens = int(context_window * warn_pct / 100)
    if tokens >= compact_tokens:
        return "critical"
    if tokens > warn_tokens:
        return "warning"
    return "safe"


def _is_binary_extension(path: str) -> bool:
    return Path(path).suffix.lower() in BINARY_EXTENSIONS


def _base_ratio_for_extension(path: str) -> float:
    ext = path.lower() if path.startswith(".") else Path(path).suffix.lower()
    if ext in BINARY_EXTENSIONS:
        return 0.12
    return TEXT_EXT_RATIOS.get(ext, 0.25)


def _is_likely_binary_sample(sample: bytes) -> bool:
    if not sample:
        return False
    if b"\x00" in sample:
        return True
    control = sum(1 for b in sample if b < 9 or (13 < b < 32))
    return (control / len(sample)) > 0.1


def _sample_ratio(path: Path, stage1_ratio: float, sample_bytes: int) -> tuple[float, bool]:
    with path.open("rb") as f:
        sample = f.read(sample_bytes)
    if _is_likely_binary_sample(sample):
        return max(stage1_ratio, 0.12), False

    text = sample.decode("utf-8", errors="ignore")
    if not text:
        return stage1_ratio, False

    ratio = stage1_ratio
    total_chars = len(text)
    whitespace_ratio = sum(1 for ch in text if ch.isspace()) / total_chars
    non_ascii_ratio = sum(1 for ch in text if ord(ch) > 127) / total_chars
    punct_ratio = sum(1 for ch in text if ch in "{}[]()<>=:_,-;\"'`/\\|@#$%^&*+") / total_chars
    lines = text.splitlines() or [text]
    avg_line_len = sum(len(line) for line in lines) / max(1, len(lines))
    base64_like = bool(re.fullmatch(r"[A-Za-z0-9+/=\s]+", text)) and whitespace_ratio < 0.08

    if base64_like:
        ratio = max(ratio, 0.38)
    if avg_line_len > 200:
        ratio = max(ratio, 0.34)
    if punct_ratio > 0.18:
        ratio = max(ratio, 0.33)
    if non_ascii_ratio > 0.2:
        ratio = max(ratio, 0.40)
    if whitespace_ratio > 0.28 and avg_line_len < 120:
        ratio = max(0.23, min(ratio, 0.26))

    return ratio, True


def estimate_file(path: str, *, context_window: int, warn_pct: int, sample_bytes: int = DEFAULT_SAMPLE_BYTES) -> dict:
    resolved = Path(path)
    real_size = resolved.stat().st_size
    fake_size = get_test_file_size(str(resolved))
    size = fake_size if fake_size is not None else real_size
    stage1_ratio = _base_ratio_for_extension(str(resolved))
    stage1_tokens = math.ceil(size * stage1_ratio)
    warn_tokens = int(context_window * warn_pct / 100)
    gray_low = int(warn_tokens * 0.5)
    gray_high = int(warn_tokens * 1.25)

    sampled = False
    ratio = stage1_ratio
    method = "size+extension"
    confidence = "medium"

    if not _is_binary_extension(str(resolved)) and gray_low <= stage1_tokens <= gray_high:
        ratio, sampled = _sample_ratio(resolved, stage1_ratio, sample_bytes)
        if sampled:
            method = "size+extension+sample"
            confidence = "high"

    estimated_tokens = math.ceil(size * ratio)
    return {
        "bytes": size,
        "estimated_tokens": estimated_tokens,
        "risk": risk_for(estimated_tokens, context_window, warn_pct),
        "confidence": confidence,
        "method": method,
        "signals": {
            "ratio": ratio,
            "sampled": sampled,
            "sample_bytes": sample_bytes if sampled else 0,
        },
    }


def url_head_metadata(url: str) -> dict:
    fake_size = TEST_URL_SIZES.get(url)
    if fake_size is not None:
        return {"content_length": fake_size, "content_type": "text/plain; charset=utf-8"}

    req = urllib.request.Request(url, method="HEAD")
    req.add_header("User-Agent", "GuardiansOfTheToken/1.0")
    with urllib.request.urlopen(req, timeout=5) as resp:
        value = resp.headers.get("Content-Length")
        return {
            "content_length": int(value) if value else None,
            "content_type": resp.headers.get("Content-Type"),
        }


def _base_ratio_for_url(url: str, content_type: Optional[str]) -> tuple[float, str]:
    parsed = urlparse(url)
    ext = Path(parsed.path).suffix.lower()
    if content_type:
        lowered = content_type.lower()
        if lowered.startswith(BINARY_CONTENT_TYPES):
            return 0.12, "binary-content-type"
        if lowered.startswith(TEXT_CONTENT_TYPES):
            if "json" in lowered:
                return 0.33, "json-content-type"
            if "csv" in lowered:
                return 0.31, "csv-content-type"
            if "html" in lowered or "xml" in lowered:
                return 0.29, "markup-content-type"
            return 0.25, "text-content-type"
    if ext:
        return _base_ratio_for_extension(ext), "url-extension"
    return 0.25, "default"


def estimate_url(url: str, *, context_window: int, warn_pct: int, content_length: Optional[int], content_type: Optional[str]) -> dict:
    ratio, reason = _base_ratio_for_url(url, content_type)
    if content_length is None:
        # TODO: For MCP preflight, add an opt-in bounded preview fetch:
        # GET with Range: bytes=0-16383 and Accept-Encoding: identity. If the
        # response includes Content-Range with a total size, combine that total
        # with sample token density to infer full-page token risk. If total size
        # remains unknown, keep risk="unknown" and report sample-only signals.
        return {
            "bytes": None,
            "estimated_tokens": 0,
            "risk": "unknown",
            "confidence": "low",
            "method": "head-metadata",
            "signals": {
                "ratio": ratio,
                "content_type": content_type,
                "content_length_present": False,
                "reason": reason,
            },
        }

    estimated_tokens = math.ceil(content_length * ratio)
    risk = risk_for(estimated_tokens, context_window, warn_pct)
    return {
        "bytes": content_length,
        "estimated_tokens": estimated_tokens,
        "risk": risk,
        "confidence": "medium",
        "method": "head-metadata",
        "signals": {
            "ratio": ratio,
            "content_type": content_type,
            "content_length_present": True,
            "reason": reason,
        },
    }
