#!/usr/bin/env python3
"""
Summarize a command's output for the context window.

GOT's Bash hook runs commands as

    { (cmd) 2>&1; printf '\\036GOT_EXIT=%s\\n' "$?"; } | python3 -m guardians_of_the_token.claude.cap_output

so this module sees the output and the command's exit code, and exits with
that code itself (the pipeline would otherwise always report success).

Short output passes through unchanged. Long output is replaced by a summary
built with plain rules, no model:

  1. problem lines (error, fail, exception, warning, ...), grouped so that a
     line repeated 3,000 times shows once with a count, most severe first;
  2. repeated lines collapsed into patterns ("4,000x test_case_<N> PASSED");
  3. the last and first lines.

The header, exit code, top problems and last lines come first, inside the
first ~2KB, because Claude Code's own preview of a large result shows only
its first 2KB. The full output is saved to a file named in the header.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import time
from collections import deque

PASS_THROUGH_CAP = 16_000  # chars; below Claude Code's own ~30K save-to-file limit
SUMMARY_CAP = 6_000        # chars; the whole summary stays well under PASS_THROUGH_CAP
LINE_CAP = 220             # chars shown per line in the summary
SCAN_LINE_CAP = 2_000      # chars of each line scanned by the rules
MAX_PATTERNS = 20_000
TOKEN_CAP = 8_000          # kept for the notice wording
EXIT_MARK = b"\x1eGOT_EXIT="

SEVERITY = [
    ("fatal", re.compile(r"\b(fatal|panic|segfault|segmentation fault|core dumped|killed)\b", re.I)),
    ("error", re.compile(r"\b(errors?|exception|traceback|err!|uncaught|unhandled)\b|^E\s", re.I)),
    ("fail", re.compile(r"\b(fail(ed|ure|ures|s)?|assert(ion)?error|not ok)\b", re.I)),
    ("warning", re.compile(r"\b(warn(ing|ings)?|deprecat(ed|ion))\b", re.I)),
]
RANK = {name: i for i, (name, _) in enumerate(SEVERITY)}
# Summary lines that are worth keeping even when they match nothing above.
SUMMARY_LINE = re.compile(
    r"(\b\d+ (passed|failed|errors?|warnings?|skipped)\b|^=+ .* =+$|\bBUILD (SUCCEEDED|FAILED|SUCCESSFUL)\b|"
    r"\b(Tests?|Ran) \d+|\btook \d|\bin \d+(\.\d+)?s\b)",
    re.I,
)
# "0 errors", "no warnings" are good news, not problems.
GOOD_NEWS = re.compile(r"\b(0|no|zero) (errors?|failures?|failed|warnings?)\b", re.I)
VARIABLE = re.compile(r"0x[0-9a-fA-F]+|\b[0-9a-fA-F]{8,}\b|\d+")


def _output_dir() -> str:
    return os.environ.get("GUARDIANS_OUTPUT_DIR") or os.path.join(tempfile.gettempdir(), "guardians-output")


def _open_full_log():
    try:
        directory = _output_dir()
        os.makedirs(directory, exist_ok=True)
        fd, path = tempfile.mkstemp(prefix=time.strftime("output-%Y%m%d-%H%M%S-"), suffix=".log", dir=directory)
        return os.fdopen(fd, "wb"), path
    except OSError:
        return None, None


def _short(line: str, cap: int = LINE_CAP) -> str:
    line = line.rstrip("\r\n")
    return line if len(line) <= cap else line[: cap - 1] + "…"


def _short_end(line: str, cap: int = LINE_CAP) -> str:
    """Like _short, but keeps the end of a long line (where its ending is)."""
    line = line.rstrip("\r\n")
    return line if len(line) <= cap else "…" + line[-(cap - 1):]


def _severity(line: str):
    if GOOD_NEWS.search(line):
        return None
    for name, rx in SEVERITY:
        if rx.search(line):
            return name
    return None


class Digest:
    """One pass over the output: patterns, problems, head and tail."""

    def __init__(self):
        self.lines = 0
        self.head: list[str] = []
        self.tail: deque[str] = deque(maxlen=20)  # long lines keep only their ends
        self.patterns: dict[str, list] = {}  # pattern -> [count, first_line_no, example]
        self.problems: dict[str, list] = {}  # pattern -> [rank, count, first_line_no, example]
        self.summaries: deque[tuple[int, str]] = deque(maxlen=8)
        self.overflow = False

    def add(self, raw: bytes):
        self.lines += 1
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        scan = line[:SCAN_LINE_CAP]
        if len(self.head) < 10:
            self.head.append(line)
        self.tail.append(line if len(line) <= SCAN_LINE_CAP else line[-SCAN_LINE_CAP:])
        pattern = VARIABLE.sub("<N>", scan).strip()
        if not pattern:
            return
        entry = self.patterns.get(pattern)
        if entry:
            entry[0] += 1
        elif len(self.patterns) < MAX_PATTERNS:
            self.patterns[pattern] = [1, self.lines, line]
        else:
            self.overflow = True
        sev = _severity(scan)
        if sev:
            prob = self.problems.get(pattern)
            if prob:
                prob[1] += 1
            else:
                self.problems[pattern] = [RANK[sev], 1, self.lines, line]
        elif SUMMARY_LINE.search(scan):
            self.summaries.append((self.lines, line))

    def render(self, total_bytes: int, exit_code, full_path) -> str:
        out: list[str] = []
        where = f"Full output: {full_path} (grep it or read slices)" if full_path else "Full output was not saved; re-run with a filter (grep/tail)"
        out.append(
            f"[Guardians of the Token: output capped at {TOKEN_CAP:,} tokens ({total_bytes // 4:,} estimated total, "
            f"{self.lines:,} lines). Summary below. {where}. Say 'give me full output' to bypass the guard.]"
        )
        if exit_code is not None:
            out.append(f"Exit code: {exit_code}")

        # Most severe first, then rarest first: a FATAL seen once beats a WARN seen 3,000 times.
        problems = sorted(self.problems.values(), key=lambda p: (p[0], p[1], p[2]))
        if problems:
            out.append(f"Problems ({sum(p[1] for p in problems):,} lines, {len(problems):,} kinds):")
            for rank, count, line_no, example in problems[:6]:
                times = f" ({count:,}x)" if count > 1 else ""
                out.append(f"  L{line_no}{times} {_short(example)}")
        else:
            out.append("Problems: none matched (error/fail/exception/warning)")

        out.append("Last lines:")
        out.extend(f"  {_short_end(line)}" for line in list(self.tail)[-5:])

        # ----- the first ~2KB ends about here; the rest is detail -----
        if len(problems) > 6:
            out.append("More problems:")
            for rank, count, line_no, example in problems[6:20]:
                times = f" ({count:,}x)" if count > 1 else ""
                out.append(f"  L{line_no}{times} {_short(example)}")
        if self.summaries:
            out.append("Summary lines:")
            out.extend(f"  L{n} {_short(line)}" for n, line in self.summaries)
        repeated = sorted((p for p in self.patterns.items() if p[1][0] > 1), key=lambda p: -p[1][0])[:8]
        if repeated:
            out.append("Repeated lines:")
            for pattern, (count, line_no, example) in repeated:
                out.append(f"  {count:,}x {_short(pattern, 160)}")
        if self.lines > 5 or len(self.head[0] if self.head else "") > LINE_CAP:
            out.append("First lines:")
            out.extend(f"  {_short(line)}" for line in self.head)
        if self.lines > 5:
            out.append("Last 20 lines:")
            out.extend(f"  {_short_end(line)}" for line in self.tail)

        text = "\n".join(out) + "\n"
        if len(text) > SUMMARY_CAP:
            text = text[: SUMMARY_CAP - 40].rsplit("\n", 1)[0] + "\n  … (summary trimmed)\n"
        return text


def main() -> None:
    stdin = sys.stdin.buffer
    out = sys.stdout.buffer
    full, full_path = _open_full_log()
    digest = Digest()
    passthrough = bytearray()
    total = 0
    exit_code = None

    for raw in stdin:
        mark = raw.find(EXIT_MARK)
        if mark >= 0:
            try:
                exit_code = int(raw[mark + len(EXIT_MARK):].strip() or 0)
            except ValueError:
                exit_code = None
            raw = raw[:mark]
            if not raw:
                continue
        total += len(raw)
        if full is not None:
            try:
                full.write(raw)
            except OSError:
                full.close()
                full, full_path = None, None
        if len(passthrough) <= PASS_THROUGH_CAP:
            passthrough += raw
        digest.add(raw)
    if full is not None:
        full.close()

    if total <= PASS_THROUGH_CAP:
        out.write(bytes(passthrough))
        if full_path:
            try:
                os.remove(full_path)
            except OSError:
                pass
    else:
        out.write(digest.render(total, exit_code, full_path).encode("utf-8"))
    out.flush()
    sys.exit(exit_code or 0)


if __name__ == "__main__":
    main()
