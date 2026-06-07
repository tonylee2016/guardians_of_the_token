"""Project-state snapshots for cold-start continuity.

When a Claude Code session approaches its context limit, GOT captures a
compact, LLM-free snapshot of what the session was doing and writes it to
``.got/project_state.md`` (human + agent readable) plus a ``.json`` sidecar
holding the same data and metadata. A fresh session can then be *prompted*
to resume from that snapshot instead of starting cold.

The snapshot is built entirely from the transcript JSONL — no model call —
so it stays cheap enough to run from hooks. It reuses any summary Claude has
already written (an away-recap or a compaction summary) and augments it with
recent goals (user prompts), files touched (Edit/Write tool calls), and
recent shell commands (Bash tool calls).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from guardians_of_the_token.events import got_dir
from guardians_of_the_token.transcript import (
    _ASSISTANT_MIN_CHARS,
    _HEAD_BYTES,
    _entry_text,
    _is_compact_summary,
    _is_recap,
    _is_synthetic_user_entry,
    _is_user_prompt,
    _iter_entries,
    _read_segment,
    _truncate,
    effective_context_window,
)

# Read a wider tail than the prompt guard does: we want enough recent
# tool calls to reconstruct what files/commands the session was working on.
_STATE_TAIL_BYTES = 400_000

# Tools whose ``file_path`` argument indicates a file the session edited.
_EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

_SUMMARY_MAX_CHARS = 4000
_GOAL_MAX_CHARS = 300
_COMMAND_MAX_CHARS = 200
_ACTIVITY_MAX_CHARS = 800


@dataclass
class ProjectState:
    saved_at: str
    trigger: str
    project: str
    git_branch: str
    session_id: str
    live_tokens: Optional[int]
    context_pct: Optional[float]
    summary: str
    summary_source: str            # "recap" | "compact" | "none"
    goals: list = field(default_factory=list)        # recent user prompts
    files: list = field(default_factory=list)        # files touched, recent first
    commands: list = field(default_factory=list)     # recent shell commands
    last_activity: str = ""        # last substantive assistant turn


# --- transcript extraction ---------------------------------------------------


def _tool_uses(entry: dict):
    """Yield ``(name, input_dict)`` for each assistant tool_use block."""
    if entry.get("type") != "assistant":
        return
    msg = entry.get("message")
    if not isinstance(msg, dict):
        return
    content = msg.get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            inp = block.get("input")
            if isinstance(inp, dict):
                yield str(block.get("name") or ""), inp


def _dedupe_recent_first(items: list[str], limit: int) -> list[str]:
    """Keep the last occurrence of each item, most-recent first, capped."""
    seen: set[str] = set()
    ordered: list[str] = []
    for item in reversed(items):
        if item and item not in seen:
            seen.add(item)
            ordered.append(item)
        if len(ordered) >= limit:
            break
    return ordered


def _git_branch(base_dir: Path) -> str:
    head = base_dir / ".git" / "HEAD"
    try:
        text = head.read_text().strip()
    except OSError:
        return ""
    if text.startswith("ref:"):
        return text.split("/", 2)[-1] if "/" in text else text[4:].strip()
    return text[:12]  # detached HEAD: short sha


def _read_transcript(transcript_path: str) -> str:
    if not transcript_path or not os.path.exists(transcript_path):
        return ""
    size = os.path.getsize(transcript_path)
    head = _read_segment(transcript_path, offset=0, size=_HEAD_BYTES)
    if size <= _HEAD_BYTES:
        return head
    tail = _read_segment(transcript_path, offset=max(0, size - _STATE_TAIL_BYTES), size=_STATE_TAIL_BYTES)
    return head + "\n" + tail


def build_state(
    transcript_path: str,
    *,
    trigger: str,
    base_dir: Path,
    live_tokens: Optional[int],
    context_pct: Optional[float],
    settings: dict[str, Any],
) -> ProjectState:
    max_goals = int(settings.get("max_user_prompts") or 6)
    max_files = int(settings.get("max_files") or 20)
    max_commands = int(settings.get("max_commands") or 12)

    text = _read_transcript(transcript_path)

    recap = ""
    compact = ""
    session_id = ""
    prompts: list[str] = []
    assistant_texts: list[str] = []
    files: list[str] = []
    commands: list[str] = []

    for entry in _iter_entries(text):
        sid = entry.get("sessionId")
        if isinstance(sid, str) and sid:
            session_id = sid
        if _is_recap(entry):
            recap = _entry_text(entry) or recap
        elif _is_compact_summary(entry):
            compact = _entry_text(entry) or compact
        elif _is_user_prompt(entry):
            if _is_synthetic_user_entry(entry):
                continue
            t = _entry_text(entry).strip()
            if t:
                prompts.append(t)
        elif entry.get("type") == "assistant":
            t = _entry_text(entry).strip()
            if len(t) >= _ASSISTANT_MIN_CHARS:
                assistant_texts.append(t)
            for name, inp in _tool_uses(entry):
                if name in _EDIT_TOOLS:
                    fp = inp.get("file_path") or inp.get("notebook_path")
                    if fp:
                        files.append(str(fp).strip())
                elif name == "Bash":
                    cmd = inp.get("command")
                    if cmd:
                        commands.append(" ".join(str(cmd).split())[:_COMMAND_MAX_CHARS])

    if recap:
        summary, summary_source = recap, "recap"
    elif compact:
        summary, summary_source = compact, "compact"
    else:
        summary, summary_source = "", "none"

    goals = [_truncate(p, _GOAL_MAX_CHARS) for p in _dedupe_recent_first(prompts, max_goals)]

    return ProjectState(
        saved_at=datetime.now(timezone.utc).isoformat(),
        trigger=trigger,
        project=str(base_dir),
        git_branch=_git_branch(base_dir),
        session_id=session_id,
        live_tokens=live_tokens,
        context_pct=round(context_pct, 4) if isinstance(context_pct, (int, float)) else None,
        summary=_truncate(summary, _SUMMARY_MAX_CHARS),
        summary_source=summary_source,
        goals=goals,
        files=_dedupe_recent_first(files, max_files),
        commands=_dedupe_recent_first(commands, max_commands),
        last_activity=_truncate(assistant_texts[-1], _ACTIVITY_MAX_CHARS) if assistant_texts else "",
    )


# --- rendering & persistence -------------------------------------------------


def render_markdown(state: ProjectState) -> str:
    lines = ["# GOT project_state", ""]
    lines.append(f"- Saved: {state.saved_at} (trigger: {state.trigger})")
    lines.append(f"- Project: {state.project}")
    if state.git_branch:
        lines.append(f"- Git branch: {state.git_branch}")
    if state.context_pct is not None:
        lines.append(f"- Context at capture: ~{state.context_pct * 100:.0f}%")
    lines.append("")

    lines.append("## Summary")
    lines.append(state.summary if state.summary else "_(no summary was available in the transcript)_")
    lines.append("")

    if state.goals:
        lines.append("## Recent goals (your last prompts)")
        lines.extend(f"- {g}" for g in state.goals)
        lines.append("")

    if state.files:
        lines.append("## Files touched")
        lines.extend(f"- `{f}`" for f in state.files)
        lines.append("")

    if state.commands:
        lines.append("## Recent commands")
        lines.extend(f"- `{c}`" for c in state.commands)
        lines.append("")

    if state.last_activity:
        lines.append("## Last activity")
        lines.append(state.last_activity)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def state_paths(base_dir: str | Path) -> tuple[Path, Path]:
    directory = got_dir(base_dir)
    return directory / "project_state.md", directory / "project_state.json"


def save_state(state: ProjectState, *, base_dir: str | Path) -> Optional[Path]:
    md_path, json_path = state_paths(base_dir)
    try:
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(render_markdown(state))
        json_path.write_text(json.dumps(asdict(state), indent=2) + "\n")
    except OSError:
        return None
    return md_path


def load_state(base_dir: str | Path) -> Optional[dict[str, Any]]:
    _, json_path = state_paths(base_dir)
    try:
        return json.loads(json_path.read_text())
    except Exception:
        return None


# --- capture entry points ----------------------------------------------------


def _parse_iso(raw: object) -> Optional[datetime]:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _settings(config: Optional[dict[str, Any]]) -> dict[str, Any]:
    return dict((config or {}).get("project_state") or {})


def capture(
    transcript_path: str,
    *,
    base_dir: str | Path,
    trigger: str,
    live_tokens: Optional[int] = None,
    context_pct: Optional[float] = None,
    config: Optional[dict[str, Any]] = None,
) -> Optional[Path]:
    settings = _settings(config)
    if not settings.get("enabled", True):
        return None
    base = Path(base_dir)
    state = build_state(
        transcript_path,
        trigger=trigger,
        base_dir=base,
        live_tokens=live_tokens,
        context_pct=context_pct,
        settings=settings,
    )
    return save_state(state, base_dir=base)


def _should_refresh(base_dir: str | Path, *, min_interval_seconds: int) -> bool:
    data = load_state(base_dir)
    if not data:
        return True
    saved = _parse_iso(data.get("saved_at"))
    if saved is None:
        return True
    return (datetime.now(timezone.utc) - saved).total_seconds() >= min_interval_seconds


def maybe_capture_on_pressure(
    transcript_path: str,
    *,
    base_dir: str | Path,
    live_tokens: Optional[int],
    context_pct: Optional[float],
    config: Optional[dict[str, Any]] = None,
) -> Optional[Path]:
    """Refresh the snapshot when context pressure crosses the save threshold.

    Throttled by ``min_save_interval_seconds`` so we don't rewrite the file on
    every prompt once a session is large. Fail-open: any error returns None.
    """
    settings = _settings(config)
    if not settings.get("enabled", True):
        return None
    if context_pct is None or live_tokens is None:
        return None
    if context_pct < float(settings.get("save_context_pct") or 0.70):
        return None
    if not _should_refresh(base_dir, min_interval_seconds=int(settings.get("min_save_interval_seconds") or 300)):
        return None
    try:
        return capture(
            transcript_path,
            base_dir=base_dir,
            trigger="pressure",
            live_tokens=live_tokens,
            context_pct=context_pct,
            config=config,
        )
    except Exception:
        return None


def resume_offer(base_dir: str | Path, *, config: Optional[dict[str, Any]] = None) -> Optional[str]:
    """Return an additionalContext nudge if a fresh snapshot exists, else None.

    Used by the SessionStart hook to *prompt* the user to resume — it never
    injects the snapshot itself, only a short instruction to offer it.
    """
    settings = _settings(config)
    if not settings.get("enabled", True):
        return None
    data = load_state(base_dir)
    if not data:
        return None
    if str(data.get("project") or "") != str(Path(base_dir)):
        return None
    saved = _parse_iso(data.get("saved_at"))
    if saved is None:
        return None
    age_hours = (datetime.now(timezone.utc) - saved).total_seconds() / 3600.0
    if age_hours > float(settings.get("max_age_hours") or 168):
        return None

    when = _humanize_age(age_hours)
    pct = data.get("context_pct")
    pct_note = f", ~{float(pct) * 100:.0f}% context at the time" if isinstance(pct, (int, float)) else ""
    return (
        "🛡️ Guardians of the Token saved a project_state snapshot from a previous "
        f"large session in this project ({when}{pct_note}). The user has NOT asked "
        "to resume yet. Briefly let them know a saved snapshot exists and ask whether "
        "they want to resume from it. If they say yes, read `.got/project_state.md` and "
        "continue from there. Do not dump the snapshot contents unprompted."
    )


def _humanize_age(age_hours: float) -> str:
    if age_hours < 1:
        minutes = max(1, int(age_hours * 60))
        return f"saved {minutes} min ago"
    if age_hours < 48:
        return f"saved {int(round(age_hours))}h ago"
    return f"saved {int(age_hours // 24)} days ago"
