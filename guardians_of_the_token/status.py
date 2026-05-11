from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from guardians_of_the_token.report import report_data


def _find_got_dir(start: Path) -> Path | None:
    for directory in (start, *start.parents):
        if (directory / ".got" / "events.jsonl").exists():
            return directory
    return None


def _cwd_from_stdin() -> str | None:
    """Read cwd from ccstatusline's JSON stdin, if available."""
    if sys.stdin.isatty():
        return None
    try:
        payload = json.loads(sys.stdin.read())
        return payload.get("cwd")
    except Exception:
        return None


def status_line(project: str | Path | None = None) -> str:
    if project is not None:
        base_dir = Path(project).expanduser().resolve()
    else:
        found = _find_got_dir(Path(os.getcwd()).resolve())
        base_dir = found if found is not None else Path(os.getcwd()).resolve()

    data = report_data(base_dir)
    blocks = data["blocked_operations"]

    if blocks == 0:
        return "🛡️ on"

    tok = data["tokens_saved"]
    cost = data["cost_saved"]
    tok_str = f"{tok / 1_000_000:.1f}M" if tok >= 1_000_000 else f"{tok / 1_000:.0f}k" if tok >= 1_000 else str(tok)
    return f"🛡️ {blocks}× · {tok_str} tok · ${cost:.2f}"


def main():
    parser = argparse.ArgumentParser(description="Print a one-line Guardians status for the status bar.")
    parser.add_argument("project", nargs="?", default=None, help="Project folder (default: walk up from cwd).")
    args = parser.parse_args()

    project = args.project
    if project is None:
        project = _cwd_from_stdin()

    print(status_line(project), end="")


if __name__ == "__main__":
    main()
