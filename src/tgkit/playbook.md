# Telegram chats (TG Kit)

TG Kit keeps a local copy of the Telegram chats the user picked, and the
`tgkit` command reads it. Use it whenever the user asks what was said or
decided in a Telegram chat or group, wants a summary of recent Telegram
messages, asks about tasks, to-dos or requests from Telegram, or wants a file
or link someone sent there. Search, summary and tasks work offline. Only
`tgkit sync` and `tgkit search ... --online` talk to Telegram.

The user is usually not technical. Answer in plain words, in the language
they wrote in (Vietnamese or English). Do not show them commands unless they
ask. Do not use em dashes or en dashes.

This text is what `tgkit guide` prints, so any Claude Code session can read
it first: "run tgkit guide, then answer".

## Rules

- Never run `tgkit setup`, `tgkit login`, `tgkit add` or a bare `tgkit`
  yourself. They ask questions, and a Telegram login code that only the user
  may type. If TG Kit is not set up (an error says "tgkit setup"), tell the
  user to open TG Kit from its desktop icon, or type `tgkit setup` in a
  terminal window, and come back when it is done.
- Add `--json` to `search`, `summary`, `tasks` and `chats-log`, and read the
  JSON.
- Freshness: every chat in the summary has `synced_at` and `stale`. If a chat
  is stale, or the user asks about today, run `tgkit sync` first (it only
  fetches new messages and never changes old ones), then answer.
- Change tasks (`done`, `undo`, `reopen`, `drop`, `note`, `add`) only when the
  user explicitly asks for that change. Never change a task on your own, for
  example while writing a report or listing tasks.
- Decisions are quotes, not facts. A summary lists "possible decisions"; each
  carries `in_reply_to`, the message it answers. Before you say someone agreed
  to something, read that message: "em đồng ý" agrees only with what it
  replies to. If `in_reply_to` says "not in the local copy", say so.
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
| "tasks from chat Y this week" | `tgkit tasks --chat "Y" --since 7d --json` |
| "every task, also done ones" | `tgkit tasks all --json` |
| "mark task 3 done" | `tgkit tasks done 3` |
| "add a note to task 3" | `tgkit tasks note 3 "text"` |
| "remind me to X by Friday" | `tgkit tasks add "X" --due "Friday"` |
| "that is not a task" | `tgkit tasks drop 3` |
| "show the conversation around message 123" | `tgkit show "chat" --thread 123` |
| "the latest messages in Y" | `tgkit show "Y" --last 20` |
| "what files were sent in Y" | `tgkit media "Y"` |
| "which chats do you have" | `tgkit chats` |
| "get the latest messages" | `tgkit sync` |
| "what did I ask in the TG Kit app", "continue what I asked there" | `tgkit chats-log --json` |

Chat names can be typed partly and without accents: `--chat "ke toan"`
matches "Phòng Kế toán". Search also ignores accents when the words are typed
without them. `--since` takes `today`, `7d`, `2w`, `30d` or a date like
`2026-09-01`.

## What the JSON holds

- `search --json`: `{"query", "total", "results": [...]}`; each result has
  `chat`, `folder`, `id`, `date`, `sender`, `text`, `file`, `reply_to` (the
  id of the message it answers). `total` counts every hit; `results` holds
  the newest 30 unless `--limit` says otherwise (`--limit 0` = all).
- `summary --json`: `{"window": {"since", "label"}, "total_messages",
  "chats": [...], "quiet_chats": [...], "note"}`. Each chat has `title`,
  `messages`, `first`, `last`, `synced_at`, `stale`, `people` (name and
  message count), `threads` (`root`, `messages`, `people`), and lists of
  `questions`, `decisions`, `tasks` (each `id`, `date`, `sender`, `text`,
  `in_reply_to`), `links` and `files`. `quiet_chats` had no messages in the
  period.
- `tasks --json`: `{"tasks": [...]}`; each task has `id` (the number for
  `done`, `note`, `drop`), `text`, `chat`, `msg_id`, `sender`, `date`, `due`,
  `assignees`, `for_me`, `status` (`open`, `done`, `dropped`), `notes` and
  `source` (`message` or `manual`). Listing tasks also saves newly found ones.
- `chats-log --json`: `{"conversations": [...]}`, newest first; each has
  `id`, `title`, `created`, `updated` and `turns` (see below).
- `chats`, `show` and `media` print plain text: chat names with their message
  count and last update; messages as `[date] sender: text`; files with their
  path on disk.

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
