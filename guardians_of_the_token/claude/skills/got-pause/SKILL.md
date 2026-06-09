---
name: got-pause
description: Pause (or resume) the Guardians-of-the-Token guards for a while.
---

# /got-pause

Invoked when the user wants Guardians of the Token to stop interrupting for a
while — e.g. they're about to do something the guards would flag (read a large
file, paste a big log, run a noisy command) and don't want to be blocked each
time. The pause is global: it quiets every guard for this CLI and any other
(Codex included) until it expires.

## What you should do

1. Work out the requested duration from what the user said. Accept natural
   forms like `1h`, `30m`, `1h30m`, `90s`, or a bare number of minutes. If they
   didn't say, default to `1h`. If they want to re-enable now, treat it as
   `off`.
2. Run the pause command for the current project:

   ```bash
   guardians pause <duration>     # e.g. guardians pause 1h
   guardians pause off            # resume immediately (or: guardians resume)
   ```

3. Tell the user it worked and when the guards come back — for example
   "Guards paused for 1h; run `/got-pause off` to re-enable sooner."

## Notes

- While paused, all guards exit silently — no context-cost blocks, no prompt
  guard, no output trimming, no project_state snapshots. Nothing else changes.
- The pause auto-expires, so a forgotten pause re-enables itself (capped at 7
  days). Running `guardians resume` clears it early.
- `/got-pause` is recognized by the prompt guard as a control command, so it is
  never blocked even in a large session.
