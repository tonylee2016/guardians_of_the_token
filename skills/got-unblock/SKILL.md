---
name: got-unblock
description: Bypass the Guardians-of-the-Token prompt guard for the current prompt.
---

# /got-unblock

Invoked when the user wants to send a prompt that the Guardians prompt
guard would otherwise block as off-topic for the current large session.

## How it's used

The user types `/got-unblock <their actual prompt>`. Claude Code routes
the full input through the `UserPromptSubmit` hook; the hook recognizes
the `/got-unblock` prefix as a control command and lets it through
without running the topic-drift check.

## What you should do

Treat the text after `/got-unblock` as the user's real question and
answer it directly. Do not explain that you "saw the unblock command"
or comment on the bypass — that just adds noise. If the input is just
`/got-unblock` with nothing after it, ask the user what they wanted to
ask.

## Examples

User input: `/got-unblock plan a three-day trip to Tokyo`
You: (answer the Tokyo trip question normally)

User input: `/got-unblock what is the chemical formula for caffeine?`
You: (answer the caffeine question normally)

User input: `/got-unblock`
You: "I'm ready — what did you want to ask?"
