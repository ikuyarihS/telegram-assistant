# Telegram chats (Telegram Assistant)

Telegram Assistant keeps a local copy of the Telegram chats the user picked, and the
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
  may type. If Telegram Assistant is not set up (an error says "tgkit setup"), tell the
  user to open Telegram Assistant from its desktop icon, or type `tgkit setup` in a
  terminal window, and come back when it is done.
- Never run `tgkit logout` yourself. To log out or use another Telegram
  account, the user opens Settings in the Telegram Assistant window (or types
  `tgkit logout` in a terminal) and then connects again.
- Never run `tgkit import-crawl` yourself. It copies a whole tg-crawl folder
  into Telegram Assistant; the user does it in Settings ("Coming from
  tg-crawl") or types it in a terminal.
- Add `--json` to `today`, `search`, `summary`, `tasks`, `chats-log`, `usage`,
  `claude-status` and `export`, and read the JSON.
- "What do I need to do today" (Vietnamese: "hôm nay tôi cần làm gì"): run
  `tgkit today --json` and answer with a short list, most urgent first: tasks
  due today, then people waiting for the user's answer, then the rest. Each
  with who, which chat and when; add the `link` so the user can open it.
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
| "what do I need to do today", "hôm nay tôi cần làm gì" | `tgkit today --json` (add `--lang vi` for Vietnamese group titles) |
| "find / did anyone mention X" | `tgkit search "X" --json` (add `--chat "name"`, `--since 30d`) |
| "find this pattern / regex", "like tg-crawl's find" | `tgkit search --regex "pattern" --json` (accents must match; `find` is the same command) |
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
| "what did I ask in the Telegram Assistant app", "continue what I asked there" | `tgkit chats-log --json` |
| "save / send me that as a file" | `tgkit export search "X" --json` (also `summary`, `tasks`, `conversation <id>`) |
| "the tasks as an Excel sheet" | `tgkit export tasks --format xlsx --json` |
| "save the file X sent in chat Y" | `tgkit export file "Y" <message id> --json` |
| "how many tokens did the Telegram Assistant app use" | `tgkit usage --json` |
| "is Claude connected in the Telegram Assistant app" | `tgkit claude-status --json` |

Chat names can be typed partly and without accents: `--chat "ke toan"`
matches "Phòng Kế toán". Search also ignores accents when the words are typed
without them. `--since` takes `today`, `7d`, `2w`, `30d` or a date like
`2026-09-01`.

## What the JSON holds

- `search --json`: `{"query", "total", "results": [...]}`; each result has
  `chat`, `folder`, `id`, `date`, `sender`, `text`, `file`, `reply_to` (the
  id of the message it answers), `link` and `link_kind`. `link_kind`
  "message": the link (t.me) opens that message in Telegram; "chat": the chat
  is a private chat or a small group, Telegram has no links to its messages,
  and the link (tg://) opens only the chat; "none": no link at all (a private
  chat or small group without a username), tell the user to open Telegram and
  find the chat by its name. Say which when you give a link.
  `summary --json` rows carry the same `link` and `link_kind`.
  `total` counts every hit; `results` holds the newest 30 unless `--limit`
  says otherwise (`--limit 0` = all).
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
  `source` (`message` or `manual`). `--chat` keeps only tasks from those
  chats, `--since` only tasks from that period. Listing tasks also saves
  newly found ones.
- `today --json`: `{"kind": "today", "date", "me", "known_me", "total",
  "counts", "stale", "groups": [...]}`. The groups, in this order: `tasks`
  (open tasks for the user, due today first; `due_today`, `task` is the task
  number), `asks` (questions and requests to the user from the last 24 hours
  with no later message from the user in that chat), `today` (messages that
  name today, today's date, or "tomorrow" written yesterday). Each group has
  `title`, `total`, `items` (at most 8; each `chat`, `sender`, `time`, `text`,
  `link`, `link_kind`) and `more` (how many were left out). It only reads:
  it saves nothing, not even newly found tasks. If `known_me` is false, the
  user's account is not known yet, so `asks` is empty: say so.
- `chats-log --json`: `{"conversations": [...]}`, newest first; each has
  `id`, `title`, `created`, `updated` and `turns` (see below).
- `export ... --json`: `{"path", "name", "folder"}` of the file it saved
  (Word `.docx` unless `--format xlsx|csv|md`; never overwrites a file).
- `usage --json`: tokens used by Claude answers in the Telegram Assistant window:
  `today`, `month`, `days`, `all` (each `answers`, `tokens`, `input`,
  `output`, `cache_read`, `cache_write`, and `cost_usd` only when Claude Code
  reported it, as its own estimate) and the daily `limit` the user set.
  Today, search, summary, tasks and sync use no tokens.
- `claude-status --json`: `state` (`connected`, `not_logged_in`, `missing`,
  `unknown`), `version`, `account`, `plan`, the `model` and `effort` chosen
  for the app, and `fix` (steps in plain words).
- `chats`, `show` and `media` print plain text: chat names with their message
  count and last update; messages as `[date] sender: text`; files with their
  path on disk.

## Inside other work (reports, notes, drafts)

Telegram Assistant is a plain command, so any task can pull from it: a weekly report, a
meeting prep note, a status update. Run the `--json` form of `summary`,
`search` or `tasks` for the period the work covers, quote messages with their
chat, sender and date, and check the `stale` flag first: when a chat is stale,
say so or run `tgkit sync` before using it. A decision in the summary is only
"possible": read its `in_reply_to` message before writing that someone agreed.
Add the result's `link` when you quote a message, so the reader can open it in
Telegram. When the user wants the result as a file to share, write it as a
Word document (`.docx`), never Markdown: `tgkit export` makes one from a
search, a summary or the task list, and tells you where it saved it.

## The Telegram Assistant app (what the user asked there)

The user may also ask questions in the Telegram Assistant window (`tgkit app`, the
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

Telegram Assistant (the `tgkit` command; called TG Kit up to version 0.2.5)
is not installed on this computer, or the terminal was opened before it was
installed. Ask the user to open a new terminal window; if that does not
help, to run the installer again from the Telegram Assistant folder (`Install-Windows.bat`
on Windows, `Install-Mac.command` on a Mac, `bash install.sh` on Linux).
