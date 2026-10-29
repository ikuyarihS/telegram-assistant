# TG Kit - maintenance rules

`README.md` explains how to **use** TG Kit. This file is for **changing** it.
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

## 1. The rules that produced this design

Each was paid for by a real defect in the tool TG Kit grew from. Do not relax
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
N with no hole above them, and later syncs start from its newest id.

### R5 - Media duplicates are recoverable; lost messages are not

`download_media(file=<directory>)` makes Telethon append ` (1)`, ` (2)` to a
repeated name. That is bounded disk waste. Always trade it away rather than
risk a message.

### R6 - Resolve an entity once, and guard the folder you will actually write

`add_chat` resolves the entity once, computes the folder name, and refuses if
that folder exists at all. It builds the chat in a hidden `.adding-<name>`
folder and renames it into place only when complete; an older half-built
staging folder is moved aside, never deleted.

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
- No command re-fetches missing attachments for rows already on disk.
- No `tgkit remove`: to stop following a chat, move its folder out of
  `chats/`.
- Task detection is keyword based (Vietnamese and English cues); it finds
  candidates, the user decides (`done`, `drop`).

## 4. Security

- Copy only chats the user picks. Sync never expands scope on its own.
- Never print `config.json`, `.env`, `TG_API_ID`, `TG_API_HASH`, or copy a
  `*.session` file.
- Nothing from a user's data folder belongs in this repository. `.gitignore`
  covers the data file names in case someone points `TGKIT_HOME` inside it.
