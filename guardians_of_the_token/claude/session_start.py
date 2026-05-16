#!/usr/bin/env python3
"""Claude Code SessionStart hook for non-interactive update checks."""

from __future__ import annotations

from guardians_of_the_token.update import startup_auto_update


def main() -> None:
    result = startup_auto_update()
    if result.status == "updated" and result.message:
        print(result.message)


if __name__ == "__main__":
    main()
