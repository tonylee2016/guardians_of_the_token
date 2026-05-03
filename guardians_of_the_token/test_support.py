import os
from typing import Optional


TEST_FILE_SIZES = {
    "/tmp/guardians_test_small": 10_000,
    "/tmp/guardians_test_large": 200_000,
    "/tmp/guardians_test_compact": 800_000,
}

TEST_FILE_BASENAME_SIZES = {
    "guardians_test_small": 10_000,
    "guardians_test_large": 200_000,
    "guardians_test_compact": 800_000,
}

TEST_FILE_DESCRIPTIONS = {
    "/tmp/guardians_test_small": "small fixture; should pass through",
    "/tmp/guardians_test_large": "large fixture; may warn on smaller context windows",
    "/tmp/guardians_test_compact": "compact-scale fixture; should trigger guarded full reads",
}

TEST_URL_SIZES = {
    "https://guardians-test/large": 200_000,
    "https://guardians-test/compact": 800_000,
}


def get_test_file_size(path: str) -> Optional[int]:
    override = os.environ.get("GUARDIANS_TEST_SIZE")
    if override:
        try:
            return int(override)
        except ValueError:
            return None
    absolute = os.path.abspath(path)
    if absolute in TEST_FILE_SIZES:
        return TEST_FILE_SIZES[absolute]
    return TEST_FILE_BASENAME_SIZES.get(os.path.basename(path))


def ensure_test_files() -> None:
    for path, fake_size in TEST_FILE_SIZES.items():
        description = TEST_FILE_DESCRIPTIONS[path]
        content = (
            "Guardians of the Token test fixture\n"
            f"Path: {path}\n"
            f"Fake size used by hooks: {fake_size:,} bytes\n"
            f"Expected behavior: {description}\n"
            "Claude Code: use Read to test full-read blocking; bounded follow-up reads are allowed.\n"
            "Codex: use a guarded Bash command such as `cat` to test blocking.\n"
        )
        with open(path, "w") as f:
            f.write(content)


def get_test_output_tokens() -> Optional[int]:
    override = os.environ.get("GUARDIANS_TEST_TOKENS")
    if override:
        try:
            return int(override)
        except ValueError:
            return None
    return None
