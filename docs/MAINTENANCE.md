# Telegram Assistant - maintenance rules

`README.md` explains how to **use** Telegram Assistant. This file is for **changing** it.
Read it before editing any code that writes data: the user's copy of their
chats cannot always be fetched again.

## 0. Where things are

| Module | What it does | Writes data? |
|---|---|---|
| `store.py` | the local copy: chats, rows, names, freshness, the one write helper | only through `atomic_write` |
| `net.py` | everything that talks to Telegram: sync, add, online search, names, login | yes, carefully (rules below) |
| `digest.py` | the offline summary | no |
| `tasks.py` | task cues and `tasks.json` | only `tasks.json` |
| `wizard.py` | setup questions and the menu | `config.json`, the pointer file |
| `cli.py` | the `tgkit` command, plain-language errors | crash log only |
| `engine.py` | reads shared by the CLI and the app (search, tasks, threads) and the app's offline answers and cards | only `tasks.json`, through `tasks.TaskList` |
| `intent.py` | the app's offline intent parser (words to a query) | no |
| `conversations.py` | the app's saved conversations, read by `tgkit chats-log` | only `app/conversations/*.json` |
| `claude.py` | the app's Claude mode: a headless `claude -p` limited to tgkit read commands | only `app/claude/tgkit-guide.md` |
| `playbook.md`, `playbook.py` | the one Claude Code playbook: `tgkit guide` prints it, `skills/telegram/SKILL.md` is its frontmatter plus this text, the app's guide is this text plus `claude.APP_RULES`. Edit `playbook.md`, copy it under the SKILL.md frontmatter; `tests/test_guide.py` fails when they differ | no |
| `crawlimport.py` | `tgkit import-crawl`: copies a tg-crawl `out/` folder into `chats/` (R18) | yes: new chats, merged rows, copied files, new names; never the tg-crawl folder |
| `appserver.py`, `app.py`, `web/` | the Telegram Assistant window: a local server on 127.0.0.1 and its HTML/CSS/JS | no chat data; sync runs `tgkit sync` |

## 1. The rules that produced this design

Each was paid for by a real defect in the tool Telegram Assistant grew from. Do not relax
one without re-earning it.

### R1 - Never open a data file in "w" mode

The original seeding script opened `messages.jsonl` in "w" mode, and once a
re-run overwrote a whole dump. Every write here goes through
`store.atomic_write`: build the full new content, write to `.tmp`, flush,
fsync, atomic replace. A crash leaves either the old file or the new one. A
probe fails the build if any other module opens a file for writing.

### R2 - A watermark computed from a partly readable file is not a watermark

`net.newest_id()` **refuses** on a damaged line; it does not skip it. Skipping
lowers the watermark, sync refetches, and the prepend writes duplicate ids on
top of an orphan every reader silently drops. `Store.rows()` takes the
opposite stance (warn and continue) because a reader should degrade loudly,
not refuse.

**Asymmetry to preserve: readers tolerate and warn; the writer refuses.**

### R3 - "Never fetched" is not "sync everything"

A chat folder with zero rows is **skipped** by sync. `min_id=0` would be an
unbounded full-history fetch of a chat nobody asked for. New chats come in
only through `tgkit add` / setup, which the user chooses explicitly.

### R4 - `iter_messages` returns NEWEST FIRST. This is the subtle one.

Telethon's default order is newest to oldest (`reverse=False`). So a fetch
that dies partway holds the **newest** N and leaves a hole above the
watermark. Committing it advances the watermark past messages that were never
fetched: **permanent, silent loss.**

`net.sync_chats` therefore discards a partial fetch whose lowest id is not
contiguous with the watermark, and holds the watermark so the next run
refetches the whole range.

Telegram message ids come from a per-user global counter, so ids within one
chat are sparse, and the contiguity test fires on nearly every partial fetch.
That is over-broad **in the safe direction**: a visible stall, not a loss; one
clean run recovers everything. **Do not "optimise" this into committing
partial fetches.** If the contiguous path ever needs to engage more often, the
correct change is to set the watermark to `min(fresh) - 1`, never to relax the
guard.

Seeding a NEW chat (`add_chat`) may keep a partial fetch: it holds the newest
N with no hole above them, and later syncs start from its newest id. Such a
copy is marked `"partial": true` in `channel.json` (a sync keeps the mark) and
is never reported as added: the window shows it as an error with a Retry, and
every picker (the window's Add chats, `tgkit add`) lists it as tickable with
"copied only in part", so the way back survives a restart. Picking it copies
the chat again, moving the cut-short folder aside to
`.partial-old-<name>-<stamp>`, never deleting it.

### R5 - Media duplicates are recoverable; lost messages are not

`download_media(file=<directory>)` makes Telethon append ` (1)`, ` (2)` to a
repeated name. That is bounded disk waste. Always trade it away rather than
risk a message.

### R6 - Resolve an entity once, and guard the folder you will actually write

`add_chat` resolves the entity once, computes the folder name, and refuses if
that folder exists at all. It builds the chat in a hidden `.adding-<name>`
folder and renames it into place only when complete. A staging folder is
never a chat, so the next add removes old `.adding-*` folders under the sync
lock (R17); a committed chat is never touched.

### R7 - `summary.json`'s `channel` column must be a usable ADDRESS

Never the folder name, never `channel.json`'s unsigned id. `target_for()`
rebuilds the signed form (`-100...` for a Channel, plain negative for a Chat,
bare positive for a User; `@username` when there is one).

### R8 - Fill the dialog cache before resolving numeric ids

Private chats resolve by numeric id only after `await client.get_dialogs()`.
Without it a cold session fails every `get_entity`, skips every chat and
prints "0 new": a false clean, worse than an error.

### R9 - Never prompt for a login outside setup

`net.connected()` refuses with "run tgkit setup" when the session is not
authorized, instead of letting Telethon ask for a phone number. Claude runs
`tgkit` for users and must never be the one typing a login code.

### R10 - Non-ASCII names must work

Chat titles, folder names and file names are often Vietnamese. Output is
reconfigured to UTF-8 even when piped on a Windows console; tests run a
non-ASCII folder and file name.

### R11 - One sync at a time per data folder

Two syncs started together read the same watermark and both prepend the same
new messages: duplicate ids. `net.sync_chats` holds `store.SyncLock` from the
watermark read to the last write, and `net.add_chats` holds it while it adds
chats (it rewrites `sync-state.json` too). The lock is the operating system's
file lock (`msvcrt.locking` on Windows, `flock` elsewhere) on `sync.lock` in
the data folder; the file is never moved or deleted, so every process locks
the same file. A second sync refuses in plain words. The system frees the lock
when its process ends, so there is no stale-lock takeover: a takeover by pid
or age let two syncs hold the lock at once (a check, then a move, raced by a
second sync), and let a sync running past the age limit lose its lock. A dry
run writes nothing and takes no lock.

The lock file holds the holder's pid, start, what it does and a heartbeat
(`SyncLock.beat`: every chat, every message fetched and every chunk of a file
being downloaded, at most once a second, so a long download is never taken
for a hang). A `tgkit sync` started by the window also carries the window's
token (`TGKIT_LOCK_OWNER`, stored as `owner`): a venv launcher puts a process
between the window and the holder, so the pid alone cannot say whose it is,
and Stop ends only processes it can prove are its own: the holder whose token
matches, then the launcher it holds a handle to. Never a process-tree walk
(`taskkill /T`): Windows keeps stale parent ids after a pid is reused, so a
walk can end an unrelated program.
`store.lock_status` reads it without touching the lock: a lock held with no
heartbeat for `STUCK_AFTER` (180) seconds, or under a pid that is no longer
running, is stuck (a login waits for a person: 15 minutes). The window shows
it, and for its own task (its `tgkit sync` child, its copy of ticked chats,
its login) offers Stop and retry: the child is ended or the task cancelled,
the system frees the lock, and the action runs again. It never ends another
program and never deletes a file; an interrupted write leaves the old file or
the new one (R1), an interrupted add leaves a `.adding-*` folder that the next
add removes (R17).

### R12 - The app never writes a chat

The Telegram Assistant window (`appserver.py`) reads through `engine`, the same code the
CLI runs. Its sync button starts `tgkit sync` as a child process, so R1 to
R11 apply unchanged. Its only writes are `tasks.json` (the task buttons, via
`TaskList`) and its own files under `app/`. Its API listens on 127.0.0.1 and
needs the random token written into the page; chat text is put on the page
with `textContent`, never parsed as HTML. Claude mode may run only tgkit read
commands (`claude.ALLOWED`); anything else is denied without a prompt. Every
`tgkit tasks` form is allowed, and each changing word (`done`, `undo`,
`reopen`, `drop`, `note`, `add`) is denied first, between options or last
(`claude.DENIED`; a deny rule wins). A wildcard pattern is not enough on its
own (`tasks --json*` once let `tasks --json done 1` through), so the session
also gets `TGKIT_READONLY=1` and `cli.main` refuses every changing command in
that mode.

Gemini mode (`gemini.py`, 0.2.9) follows the same rules from the same lists
(`ai.READ_COMMANDS`, `ai.DENIED_COMMANDS`, `ai.app_rules`; one source, so the
two CLIs cannot drift). Each run writes `app/gemini/tgkit-policy.toml` and
passes it as `--admin-policy` and `--policy` (Gemini CLI policy engine): only
`run_shell_command` with a tgkit read prefix is allowed, the changing tgkit
commands are denied first, and every other tool (files, web, MCP, skills,
agents) is denied, so the model never sees it. The user's `~/.gemini` settings
are only read, never written. The run sets `TGKIT_READONLY=1` and
`GEMINI_CLI_NO_RELAUNCH=true` (one node process, so Stop ends the process the
app started), and runs node with the CLI's script rather than npm's
`gemini.cmd`. If a tool result shows that a command outside the allow list
ran anyway, the runner stops the answer (`blocked`). Signed in = an auth type
in `~/.gemini/settings.json` plus the Google account in
`google_accounts.json` (or `GEMINI_API_KEY`), until an answer fails with exit
41; the token itself is in the system keychain. `ai.pick` decides who answers:
Settings' choice, else Claude first, then Gemini.

### R13 - One change of tasks.json at a time

Every write of `tasks.json` goes through `TaskList.change()`: it takes
`store.TasksLock` (the same operating-system lock as R11, on `tasks.lock`),
reads the file again, applies the change and writes it with `atomic_write`.
A scan reads the messages outside the lock and only merges what is new under
it. Without this a Done click in the window and a `tgkit tasks` scan in a
terminal each wrote back their own copy, and the later one lost the click. A
second writer waits up to 15 seconds, then refuses in plain words.

### R14 - One Telegram client at a time per data folder

Telethon keeps the login in `tgkit.session`, an SQLite file. In 0.2.3 the
window listed or added chats while another client had it open, and SQLite said
`OperationalError: database is locked`. Every client is now made under
`store.SessionLock` (`session.lock`, same operating-system lock as R11):
`net.connected`, `net.login`, `net.logout` and the login in the window. A
second client waits up to 30 seconds, then refuses in plain words. Everything
that takes both takes `SyncLock` first, then `SessionLock`: `sync_chats`,
`add_chats` (twice: listing, then copying; the user picks in between with no
lock held), `adopt_session`, the window's `dialogs` and its login. A login in
the window frees both locks on every way out, failures included: a lock left
held blocks every later sync.

### R15 - The chats shown belong to one Telegram account

`account.json` says whose chats are in the data folder. After a login (window
or `tgkit setup`), and whenever setup or add asks Telegram who the login
file belongs to, `account.adopt` saves who it is. For another account it moves
the shown account's data (`account.ACCOUNT_DATA`) to `accounts/<its id>/` and
brings back `accounts/<new id>/` if there is one. It never overwrites and never
deletes; a move that fails halfway puts back what moved and says so. A sync
never copies another account's messages into the chats shown: it switches
first (under its own sync lock), then gets the new account's chats. The window
asks Telegram who is logged in when it opens (`net.adopt_session`, 20 seconds
at most, skipped offline) and switches the same way, so it never shows the
config account's chats for a login made elsewhere. Log out with "keep chats"
writes `account.json` from config `me` first (a folder from 0.2.3 has none), so
the next login to another account moves those chats aside. After any switch
the window starts a new conversation and reloads every list.

### R16 - The first copy of ticked chats reports every chat

In the window, Add runs `net.add_chats` in the background (`setupflow.add`);
`/api/setup/add-progress` has one row per ticked chat: waiting, copying N,
done, or a plain error with Retry for that chat alone. One chat failing never
stops the others, and the window keeps every failed chat's error on screen.

### R17 - Text first, files after, a few chats at once, Stop at any time

`net.add_chats` and `net.sync_chats` read text only (`net.read_text`) and
write each chat's rows as soon as its text is in, so it is searchable at once.
Files go to `net.MediaPool` (`FILES_AT_ONCE` workers) and land through
`net.apply_media`, which rewrites only the media keys of a row (`media_path`,
`media_pending`, `media_skipped`, `media_error`, `media_tries`) with
`atomic_write`, under the same lock. A row says `media_pending` until its file
is in; a failed file keeps `media_error` and is queued again by the next sync
(`queue_retries`, at most `RETRY_FILES_MAX` per chat, `MEDIA_TRIES` per file).
The Files choice (`config.media_mode`: `small` up to `max_media_mb`, `none`,
`all`) decides what is queued; a file left out is marked `media_skipped`, and
a later sync with a wider choice fetches it.

Up to `CHATS_AT_ONCE` (3) chats run at once, on the ONE client of R14
(`asyncio.Semaphore`, never a second client). A FloodWait is waited out
(`net._flood_wait`, up to `FLOOD_MAX_WAIT` seconds, a few times, beating the
lock so it is never taken for a hang) and the read resumes from the lowest id
it holds (`offset_id`); a longer wait ends that chat in plain words. The R4
guard is unchanged: a sync that reads a chat only in part commits nothing for
it.

Stop is on screen the whole time a copy or a sync runs (`/api/stop`). A copy
checks `ctx.stop` between pages: a chat with rows becomes the flagged partial
seed of R4 (row "Stopped", Retry copies the rest), the others stay untouched.
A sync started by the window is asked through `sync.stop` (holding the
window's token, `store.stop_check`) and ends cleanly; after `SYNC_STOP_WAIT`
seconds the token-proved end of R11 applies. Each add first removes old
`chats/.adding-*` staging folders (`net.clean_staging`, under the sync lock:
real folders only, never a link, never a committed chat).

### R18 - A tg-crawl copy is read, never changed, and merged by id

`crawlimport.run_import` opens the tg-crawl folder for reading only: its rows
are parsed, its files are copied with `shutil.copy2`, nothing there is written,
moved or deleted (`test_v027` hashes every source file before and after). It
takes `SyncLock` (R11), cleans old staging folders, then checks the account
under `SessionLock` (`check_account`, through `net.connected`, R14). Each
chat goes through `chats/.adding-<folder>` and lands at once: a new chat by
one rename, an existing chat by moving its new files in and then one
`atomic_write` of `messages.jsonl` (R1). Rows merge by id, newest first, with
no duplicate ids; on the same id the Telegram Assistant row wins (tg-crawl
records no edits), only a missing file is filled in. So `net.newest_id` stays
the true watermark (R2). A chat whose tg-crawl copy starts after the end of
Telegram Assistant's copy is not merged: the messages between would never be
fetched (R4). A source with a line that cannot be read is not imported (R2).
After each chat the ids and count are checked against the source; a missing
id is a warning row. A chat the logged-in account does not have is kept with
`channel.json` `from_crawl.account = "not_seen"`; sync skips it in plain words
and does not count it as failed. Names from `senders.json` are added only for
ids not known yet. The window runs `tgkit import-crawl --events` as a child
(R12 holds: the app itself never writes a chat), with the same Stop as R17.
Headless Claude never runs it (`claude.DENIED`, `TGKIT_READONLY`).

`tgkit search --regex` (and Pattern (advanced) in the window) runs Python `re`
in a child process (`engine.regex_find`) with a time limit (`REGEX_TIMEOUT`):
`re` cannot be interrupted, so a pattern that backtracks forever is ended with
its process and the user gets a plain sentence.

Every caught error that is not a `UserError` goes to
`<data>/app/logs/tgkit.log` (rotated) with its traceback, never message text,
codes, phone numbers or passwords (`applog`). Settings has Copy diagnostics.

## 2. How to change this code

1. Run the tests first: `python -m pytest`. They use a synthetic data folder
   and a fake Telegram client; nothing touches the network.
2. Back up the data folder before testing any write path against real data,
   or point `TGKIT_HOME` at a copy.
3. Run `tgkit sync --dry-run` before `tgkit sync`.
4. Prove a change with an executed test, not an argument. The gap-loss bug in
   R4 was introduced by a "fix" that read correctly and was wrong; it was
   caught by simulating a partial fetch and counting what the next run would
   never see. When in doubt about Telethon's behaviour, read its installed
   source, not its docs.
5. After any change to sync, check a real data folder: every
   `messages.jsonl` must have unique ids in strictly descending order.

## 3. Known gaps

- `thread_of()` is O(n^2) in its fixpoint loop; fine for chats of a few
  thousand messages, index children if one grows far beyond that.
- Sync reads each `messages.jsonl` twice (watermark, then prepend).
- Plain `tgkit search` still treats a query that is a valid regex as a
  pattern (`store.matcher`) without the R18 time limit; only `--regex` is
  bounded.
- An import into an existing chat that fails after its new files were moved
  in leaves those files (unreferenced) in its `media/`; the rows are as they
  were (R5: a spare file is recoverable, a lost message is not).
- No `tgkit remove`: to stop following a chat, move its folder out of
  `chats/`.
- Task detection is keyword based (Vietnamese and English cues); it finds
  candidates, the user decides (`done`, `drop`).
- Change `config.json` only through `config.update` / `config.put`: they hold
  `config.lock` (threads and processes) and write atomically, so a Settings
  change and a login finishing at the same moment both stay.
- `/api/open-link` opens only the link forms of `store.telegram_link`, and only
  for a chat that is in Telegram Assistant; the link opened is rebuilt from that chat's data.
  It refuses `tg://user` and `tg://openmessage`: Telegram Desktop has no handler
  for them (tdesktop `core/local_url_handlers.cpp` knows only `resolve` and
  `privatepost`), so Telegram Assistant never makes them. A private chat or basic group
  without a username gets no link; the window and the exports say "Open
  Telegram and find <chat name>" (the window adds a Copy name button).

## 4. The icon

One source picture makes every icon: `assets/icon-source.png` (square, 512 px
or more). To change it, replace that file and run

```
python scripts/make-icons.py
```

It writes `src/tgkit/web/static/tgkit.ico` (16 to 256 px: the shortcuts, the
window and the taskbar), `icon-256.png` and `favicon.png` (the page icon).
Commit the four files. `python scripts/make-icons.py --draw` draws the
built-in placeholder picture into `assets/icon-source.png` again (that
placeholder is also kept as a vector in `assets/icon.svg` and
`web/static/icon.svg`). The shipped picture is a raster: the page logo and
favicon use `icon-256.png` and `favicon.png`, so they always match the
shortcuts.

## 5. Security

- Copy only chats the user picks. Sync never expands scope on its own.
- Never print `config.json`, `.env`, `TG_API_ID`, `TG_API_HASH`, or copy a
  `*.session` file.
- Nothing from a user's data folder belongs in this repository. `.gitignore`
  covers the data file names in case someone points `TGKIT_HOME` inside it.
