#!/usr/bin/env python3
"""
Reads stdin, passes through up to CHAR_CAP bytes, appends a truncation
notice if cut. Drains all stdin so the upstream process never deadlocks.
"""
import sys
import io

CHAR_CAP = 32_000  # ~8K tokens
TOKEN_CAP = 8_000

buf = io.BytesIO()
total = 0

while True:
    chunk = sys.stdin.buffer.read(4096)
    if not chunk:
        break
    if total < CHAR_CAP:
        space = CHAR_CAP - total
        buf.write(chunk[:space])
    total += len(chunk)

sys.stdout.buffer.write(buf.getvalue())

if total > CHAR_CAP:
    estimated = total // 4
    sys.stdout.write(
        f"\n\n[Guardians of the Token: output capped at {TOKEN_CAP:,} tokens "
        f"({estimated:,} estimated total). Say 'give me full output' to bypass the guard.]"
    )
