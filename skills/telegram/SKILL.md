---
name: telegram
description: Search, summarize and track tasks from the user's own Telegram chats with the local tgkit tool. Use when the user asks what was said or decided in a Telegram chat or group, wants a summary of recent Telegram messages, asks about tasks, to-dos or requests from Telegram, or wants a file or link someone sent there.
---

# Telegram chats (TG Kit)

TG Kit keeps a local copy of the Telegram chats the user picked, and the
`tgkit` command reads it. Search, summary and tasks work offline. Only
`tgkit sync` and `tgkit search ... --online` talk to Telegram.

The user is usually not technical. Answer in plain language, in the language
they wrote in. Do not show them commands unless they ask.

## Rules

- Never run `tgkit setup`, `tgkit add` or a bare `tgkit` yourself. They ask
  questions, and a Telegram login code that only the user may type. If TG Kit
  is not set up (an error says "tgkit setup"), tell the user to open TG Kit
  from its desktop icon, or type `tgkit setup` in a terminal window, and come
  back when it is done.
- Add `--json` to `search`, `summary` and `tasks`, and read the JSON.
- Freshness: every chat in the summary has `synced_at` and `stale`. If a chat
  is stale, or the user asks about today, run `tgkit sync` first (it only
  fetches new messages and never changes old ones), then answer.
- Decisions are quotes, not facts. A summary lists "possible decisions"; each
  carries `in_reply_to`, the message it answers. Before you say someone agreed
  to something, read that message: "em đồng ý" agrees only with what it
  replies to. If `in_reply_to` is missing from the local copy, say so.
- Quote with the date, the sender and the chat. When nothing is found, say
  that the local copy only holds the chats the user picked, and offer to ask
  Telegram itself (`--online`).
- Never open, print or copy `config.json`, `.env` or any `*.session` file in
  the data folder: they are the user's login.

## What to run

| The user asks | Run |
|---|---|
| "find / did anyone mention X" | `tgkit search "X" --json` (add `--chat "name"`, `--since 30d`) |
| "not in my chats? search Telegram" | `tgkit search "X" --online` |
| "what happened this week", "summarize" | `tgkit summary --json` (default: last 7 days) |
| "summarize chat Y last month" | `tgkit summary "Y" --since 30d --json` |
| "what are my tasks / to-dos" | `tgkit tasks mine --json`, or `tgkit tasks --json` for all open ones |
| "mark task 3 done" | `tgkit tasks done 3` |
| "add a note to task 3" | `tgkit tasks note 3 "text"` |
| "remind me to X by Friday" | `tgkit tasks add "X" --due "Friday"` |
| "that is not a task" | `tgkit tasks drop 3` |
| "show the conversation around message 123" | `tgkit show "chat" --thread 123` |
| "what files were sent in Y" | `tgkit media "Y"` |
| "which chats do you have" | `tgkit chats` |
| "get the latest messages" | `tgkit sync` |
| "what did I ask in the TG Kit app", "continue what I asked there" | `tgkit chats-log --json` |

Chat names can be typed partly and without accents: `--chat "ke toan"`
matches "Phòng Kế toán". Search also ignores accents when the words are typed
without them.

## Inside other work (reports, notes, drafts)

TG Kit is a plain command, so any task can pull from it: a weekly report, a
meeting prep note, a status update. Run the `--json` form of `summary`,
`search` or `tasks` for the period the work covers, quote messages with their
chat, sender and date, and check the `stale` flag first: when a chat is stale,
say so or run `tgkit sync` before using it. A decision in the summary is only
"possible": read its `in_reply_to` message before writing that someone agreed.

## The TG Kit app (what the user asked there)

The user may also ask questions in the TG Kit window (`tgkit app`, the
desktop icon). Each conversation there is saved in the data folder, and
`tgkit chats-log --json` prints them, newest first: every question the user
typed, the answer, which mode answered (`claude` or `offline`), the parsed
query (`intent`) and the tgkit commands Claude ran (`commands`). Add
`--last 5` for fewer, an id for one conversation, `--cards` for the result
data. Use it when the user refers to something they asked in the app ("the
search I did this morning", "finish what I started there"): read it, then
run the tgkit query again yourself, because the answer it holds may be old.
It is read-only; never edit the files under `app/` in the data folder.

## If `tgkit` is not found

TG Kit is not installed on this computer, or the terminal was opened before
it was installed. Ask the user to open a new terminal window; if that does not
help, to run the installer again from the TG Kit folder (`Install-Windows.bat`
on Windows, `Install-Mac.command` on a Mac, `bash install.sh` on Linux).
