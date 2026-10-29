---
description: Summarize what happened in your Telegram chats (default last 7 days)
argument-hint: [chat name] [since 30d]
---

Use the telegram skill. The user wants a summary of their Telegram chats:

$ARGUMENTS

Run `tgkit summary --json`, adding the chat name and `--since <period>` if given.
If any chat is `stale`, run `tgkit sync` first and summarize again. Write a short
summary per chat: what was discussed, open questions, possible decisions (check
each `in_reply_to` before calling it agreed), requests and deadlines, files and
links. Quote dates and senders.
