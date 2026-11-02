# TG Kit

TG Kit is a small helper for your own Telegram chats. It:

- **searches** your chats, even when you type without Vietnamese accents
  ("bien ban" finds "Biên bản");
- **summarizes** what happened, for example in the last 7 days: who wrote,
  the busiest conversations, open questions, possible decisions, requests,
  links and files;
- **tracks your tasks**: it finds messages that ask someone to do something
  ("nhờ em...", "please...", "TODO", deadlines) and keeps a to-do list you
  can tick off.

It keeps a copy of **only the chats you pick** on your own computer. Search,
summary and tasks work without internet and without any AI. If you use Claude,
you can simply ask Claude about your chats in your own words.

---

## What you need

- A computer with Windows, macOS or Linux, and an internet connection for the
  installation.
- Your phone with Telegram, to log in once.
- About 10 minutes.

---

## Install on Windows

1. Download TG Kit and unzip it into a folder you will keep, for example
   `Documents\TG Kit`. (Do not run it from inside the zip file.)
2. Open that folder and double-click **`Install-Windows.bat`**.
3. If Windows shows a blue "Windows protected your PC" window, click
   **More info**, then **Run anyway**.
4. If TG Kit says Python is missing, type `Y` and press Enter to install it
   (or install it from https://www.python.org/downloads/ and tick
   **Add python.exe to PATH** on the first screen, then double-click
   `Install-Windows.bat` again).
5. Wait about a minute while it installs. The first-time setup then starts by
   itself: go on with [First-time setup](#first-time-setup) below.

When it is done you have a **TG Kit** icon on your desktop and in the Start
menu. It opens the TG Kit window (see [The TG Kit window](#the-tg-kit-window)).

## Install on a Mac

1. Download TG Kit and unzip it into a folder you will keep, for example
   `Documents/TG Kit`.
2. If you do not have Python yet: open https://www.python.org/downloads/ ,
   click the big yellow download button and install it like any other app.
3. In the TG Kit folder, **right-click** `Install-Mac.command` and choose
   **Open** (the first time a Mac asks you to confirm files from the internet;
   click **Open** again).
   If the Mac says the file cannot be opened, open **Terminal**, type `bash `
   (with a space), drag `install.sh` from the TG Kit folder into the window,
   and press Enter.
4. Wait about a minute while it installs. The first-time setup then starts by
   itself: go on with [First-time setup](#first-time-setup) below.

## Install on Linux

1. Download TG Kit and unzip it into a folder you will keep.
2. Make sure Python 3.10 or newer is installed (on Ubuntu or Debian:
   `sudo apt install python3 python3-venv`).
3. Open a terminal in the TG Kit folder and type: `bash install.sh`
4. The first-time setup then starts by itself.

---

## First-time setup

The setup has four plain steps. You can stop at any time and start again
later with `tgkit setup` (or option 7 in the menu).

1. **Where to keep your data.** Press Enter to use the suggested folder, or
   type another folder (for example on a bigger drive).
2. **Connect TG Kit to Telegram.** Telegram gives every user two free codes,
   called `api_id` and `api_hash`. To get yours:
   1. Open https://my.telegram.org in your web browser.
   2. Type your phone number. Telegram sends a code to your Telegram app;
      type it on the web page.
   3. Click **API development tools**.
   4. If it asks you to create an app, type any App title (for example
      `My TG Kit`) and any Short name (for example `mytgkit`), then click
      **Create application**.
   5. Copy **App api_id** (a number) and **App api_hash** (a long code) into
      TG Kit when it asks.

   Keep these two codes private, like a password.
3. **Log in.** Type your phone number with the country code (for example
   `+84 912 345 678`). Telegram sends a login code to your Telegram app; type
   it in TG Kit. If your account has a two-step password, TG Kit asks for it
   (what you type stays hidden).
4. **Pick your chats.** TG Kit shows your Telegram chats as a numbered list.
   Type the numbers you want, for example `1, 3-5`, or type a word to search
   the list. Only these chats are copied. You can add more later.

---

## The TG Kit window

Double-click the **TG Kit** icon (Windows), or type `tgkit app` in a terminal
(Mac, Linux). A window opens with one box at the bottom: type a question in
Vietnamese or English, with or without accents, and press **Enter**.

- "Tuần này các nhóm bàn gì?", "What happened this week?" gives a summary;
- "tìm biên bản họp", "find the invoice" finds messages; **Show conversation**
  under a result shows the messages around it;
- "Việc của tôi", "my open tasks" lists your tasks, each with a **Mark done**
  (and **Undo**) button;
- **Get new messages** (bottom left) fetches what is new from Telegram, with
  a progress bar. In the list of chats, a green dot means the copy is fresh;
  an amber dot means it was last updated more than 12 hours ago.

On the left are your past conversations and your chats; the box above them
filters both. Click a chat to get its summary for this week.
Keys: **Enter** sends, **Shift+Enter** starts a new line, **Ctrl+K** starts a
new conversation, **Esc** stops an answer.

**Who answers.** If Claude Code is installed and signed in on the computer,
Claude answers ("Claude" at the top right): it decides what to search, runs
TG Kit, and writes the answer; click "Ran 2 TG Kit commands" to see what it
ran. Claude may only run TG Kit's read commands (search, summary, task
lists, chats); its other tools are switched off, so it cannot change files.
The TG Kit that Claude starts is read-only: it refuses to mark, reopen, drop,
note or add a task, to get new messages or to set up, however the command is
written. Listing tasks still saves newly found ones, as the window itself
does. Without Claude, TG Kit answers by itself
with simple rules ("Offline"). You can choose **TG Kit only** at the bottom
left to never use Claude.

The window follows the light or dark theme of your computer. Your questions
are kept in your data folder; Claude Code can read them with
`tgkit chats-log --json` (see [Use it with Claude](#use-it-with-claude)). If
the window part could not be installed, TG Kit opens the same screen in your
web browser.

## Everyday use in a terminal

Open a terminal and type `tgkit`. A menu opens:

```
  1  Search messages
  2  Summary of the last 7 days
  3  My tasks
  4  Get new messages from Telegram
  5  Add more chats
  6  Show my chats
  7  Setup (first time, or change settings)
  0  Quit
```

Type a number and press Enter.

If you prefer typing commands, these need no extra options:

| What you want | Type |
|---|---|
| Find messages | `tgkit search "biên bản"` |
| What happened in the last 7 days | `tgkit summary` |
| The same for one chat and a longer period | `tgkit summary "Team" --since 30d` |
| Your open tasks | `tgkit tasks` |
| Only tasks meant for you | `tgkit tasks mine` |
| Mark task 3 done | `tgkit tasks done 3` |
| Add a note to task 3 | `tgkit tasks note 3 "sent on Monday"` |
| Add your own task | `tgkit tasks add "call the bank" --due "Friday"` |
| Get new messages from Telegram | `tgkit sync` |
| Read the latest messages of a chat | `tgkit show "Team"` |
| Files and photos in a chat | `tgkit media "Team"` |
| Search Telegram itself (older messages too) | `tgkit search "contract" --online` |
| Follow more chats | `tgkit add` |
| Where is my data | `tgkit where` |
| All commands | `tgkit help` |

Tips:

- Run `tgkit sync` (menu option 4) about once a day so your copy is fresh.
  The summary tells you when a chat has not been updated for more than 12
  hours.
- Chat names can be typed partly and without accents: `"ke toan"` finds
  "Phòng Kế toán".
- Tasks are found automatically in the last 30 days of messages. A task you
  mark done never comes back.

---

## Use it with Claude

If you use Claude Code, the installer adds TG Kit to Claude for you. Then
just ask, in English or Vietnamese, for example:

- "What did the team decide this week in Telegram?"
- "Tóm tắt nhóm Kế toán tuần này"
- "What are my tasks from Telegram? Which are due this week?"
- "Find the file with the meeting minutes someone sent"
- "Mark task 3 as done"

Claude runs TG Kit for you and answers with dates, names and quotes. Claude
never logs in for you: if TG Kit is not set up yet, it asks you to run the
setup yourself.

If the installer could not add it (or you installed Claude later), type this
in Claude Code, replacing the path with your TG Kit folder:

```
/plugin marketplace add C:\path\to\TG Kit
/plugin install tgkit@tgkit
```

There are also short commands in Claude: `/tg-search`, `/tg-summary`,
`/tg-tasks` and `/tg-sync`.

Claude Code can also see what you asked in the TG Kit window: ask it, for
example, "continue the search I did in the TG Kit app". It reads your saved
conversations with `tgkit chats-log --json` and runs the search again.

---

## Where your data lives, and privacy

- Everything stays on your computer, in your data folder (`tgkit where`
  shows it). Nothing is sent anywhere except to Telegram itself.
- Inside it: `chats/` (one folder per chat, messages and files),
  `tasks.json` (your task list), `config.json` (your api codes) and
  `tgkit.session` (your Telegram login).
- **Never share `config.json` or `tgkit.session`**: together they give access
  to your Telegram account. If you think someone got them, open Telegram,
  go to Settings, Devices, and end that session.
- TG Kit only copies the chats you picked. It never changes or deletes
  anything in Telegram.

## Updating and removing

- **Update:** unzip the new TG Kit over the old folder (or into a new one) and
  double-click **`Update-Windows.bat`** (on a Mac, `Install-Mac.command`
  again). Your chats, tasks and login are kept, and the setup is skipped
  because you are already logged in. The icons and the `tgkit` command move
  to the new version by themselves.
- **Remove:** delete the TG Kit folder and the TG Kit icons (desktop and Start
  menu). Your data folder stays until you delete it yourself.

## Share TG Kit with someone

TG Kit is shared as a zip file. In the TG Kit folder (a git copy), run:

```
powershell -ExecutionPolicy Bypass -File scripts\make-share-zip.ps1
```

It makes `dist\TGKit-<version>.zip` from the committed files only: never your
data folder, your login, `.venv` or anything in `.gitignore`. Send that zip.
The other person unzips it and double-clicks `Install-Windows.bat` (on a Mac,
`Install-Mac.command`); each person logs in with their own Telegram account.
For a new version, raise `version` in `pyproject.toml`, commit, make a new
zip and send it; they unzip it over the old folder and double-click
`Update-Windows.bat`.

## Troubleshooting

| What you see | What to do |
|---|---|
| "TG Kit is not connected to Telegram yet" | Run `tgkit setup` (menu option 7). |
| "You are not logged in to Telegram on this computer yet" | Run `tgkit setup`. |
| "Could not reach Telegram" | Check your internet connection and try again. |
| "Telegram asked TG Kit to slow down" | Wait a few minutes and try again. |
| `tgkit` is not recognized | Open a **new** terminal window. If it still fails, run the installer again. |
| "Sorry, something went wrong" | The message says where the details were saved. Send that file to whoever gave you TG Kit. |
| A chat is missing from the summary | It had no messages in that period, or it is not one of your picked chats (`tgkit chats`). |

---

## For developers

- Python package in `src/tgkit` (entry point `tgkit = tgkit.cli:main`).
  Telethon is only imported by commands that talk to Telegram.
- Tests use a synthetic data folder (fake chats and people, Vietnamese text,
  newest-first files) and never touch the network:
  `python -m pip install -e .[test]` then `python -m pytest`.
- Read [docs/MAINTENANCE.md](docs/MAINTENANCE.md) before changing anything
  that writes data: sync has rules that each cost a real data loss once.
- The window (`tgkit app`, `src/tgkit/app.py`) is the system's own web view
  through pywebview (optional extra `tgkit[app]`), showing a local HTML/CSS/JS
  page (`src/tgkit/web/`) served on 127.0.0.1 by `appserver.py`. The same
  package and the same installers work on Windows, macOS and Linux; no
  separate build or signing per system. The offline answers come from
  `intent.py` and `engine.py`; Claude mode is `claude.py`.
- The UI is checked with the impeccable detector, static and rendered:
  `impeccable detect src/tgkit/web` and `impeccable detect http://127.0.0.1:<port>/`
  (start a server with `tgkit app --no-window --port <port>`).
