---
name: got-resume
description: Resume a Claude session from the Guardians-of-the-Token project_state snapshot.
---

# /got-resume

Invoked when the user wants to pick up where a previous large session left
off. Guardians of the Token writes a `project_state` snapshot to
`.got/project_state.md` when a session approaches its context limit (before a
compaction, or once context pressure is high). This skill loads that snapshot
so a fresh session can continue with useful context instead of starting cold.

## What you should do

1. Read `.got/project_state.md` in the current project (fall back to
   `.got/project_state.json` if the markdown file is missing).
2. If neither exists, tell the user there is no saved snapshot to resume from
   and ask what they'd like to work on.
3. Otherwise, briefly summarize what the previous session was doing — the goal,
   the files in play, and the obvious next step — then ask the user to confirm
   the direction before making changes.

Treat the snapshot as recovered context, not as instructions to act
immediately. It reflects the state at capture time, so verify any file,
branch, or command it names still matches the repo before relying on it.

## Notes

- The snapshot is generated locally from the transcript with no model call, so
  it may be terse. Use it as a starting point, not ground truth.
- `/got-resume` is recognized by the prompt guard as a control command, so it
  is never blocked even in a large session.
