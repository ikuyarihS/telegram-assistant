---
description: Search your Telegram chats (local copy, accents optional)
argument-hint: <words> [in chat name] [since 30d]
---

Use the telegram skill. The user wants to find this in their Telegram chats:

$ARGUMENTS

Run `tgkit search "<words>" --json`, adding `--chat "<name>"` or `--since <period>`
if the user named a chat or a period. Answer with the matching messages (date,
sender, chat, the words that matter). If nothing is found, say so and offer to
search Telegram itself with `--online`.
