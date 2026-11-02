"""The guided setup and the simple menu: plain questions, one step at a time."""

import getpass
import os
import re
from pathlib import Path

from . import config
from .errors import UserError
from .store import Store, matcher

LINK = "https://my.telegram.org"

API_HELP = f"""
TG Kit needs two codes from Telegram, called api_id and api_hash.
You get them once, for free, in about 2 minutes:

  1. Open {LINK} in your web browser.
  2. Type your phone number. Telegram sends a code to your Telegram app;
     type that code on the web page.
  3. Click "API development tools".
  4. If it asks you to create an app: type any App title (for example My TG Kit)
     and any Short name (for example mytgkit), then click "Create application".
  5. The page now shows "App api_id" (a number) and "App api_hash" (a long code).

Keep these two codes private, like a password.
"""

DEFAULT_LIMIT = 1000


class Cancelled(Exception):
    """The user closed the input (Ctrl+Z / Ctrl+D) or nothing can be typed."""


def asker(ask=None):
    def _ask(prompt: str) -> str:
        try:
            return (ask or input)(prompt)
        except EOFError:
            raise Cancelled() from None
    return _ask


# ------------------------------------------------------------ small questions


def parse_picks(text: str, n: int) -> list[int] | None:
    """'1, 3-5 8' -> [1, 3, 4, 5, 8]. None when it is not a valid list of numbers 1..n."""
    text = (text or "").strip()
    if not text:
        return []
    if not re.fullmatch(r"[\d\s,;\-]+", text):
        return None
    out: list[int] = []
    for part in re.split(r"[\s,;]+", text):
        if not part:
            continue
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m:
            return None
        a = int(m.group(1))
        b = int(m.group(2) or a)
        if a > b:
            a, b = b, a
        if a < 1 or b > n:
            return None
        for i in range(a, b + 1):
            if i not in out:
                out.append(i)
    return out


def ask_credentials(ask, say) -> tuple[str, str]:
    say(API_HELP)
    for _ in range(5):
        api_id = ask("Type your api_id (the number): ").strip()
        if config.valid_api_id(api_id):
            break
        say("That does not look right: the api_id is a number, like 1234567. Please try again.")
    else:
        raise UserError("No valid api_id was typed.", "Run tgkit setup again when you have it.")
    for _ in range(5):
        api_hash = ask("Type your api_hash (the long code): ").strip().replace(" ", "")
        if config.valid_api_hash(api_hash):
            break
        say("That does not look right: the api_hash has 32 letters and digits. "
            "Copy it from the page and paste it here.")
    else:
        raise UserError("No valid api_hash was typed.", "Run tgkit setup again when you have it.")
    return api_id, api_hash


def choose_home(ask, say) -> Path:
    current = config.home()
    if os.environ.get("TGKIT_HOME"):
        say(f"Your data folder is {current} (set by TGKIT_HOME).")
        return current
    say("TG Kit keeps a copy of your chosen chats on this computer.")
    answer = ask(f"Press Enter to keep it in {current}\nor type another folder: ").strip().strip('"')
    chosen = Path(answer).expanduser() if answer else current
    if not chosen.is_absolute():
        chosen = Path.home() / chosen
    chosen.mkdir(parents=True, exist_ok=True)
    config.remember_home(chosen)
    say(f"OK, your data will be kept in {chosen}")
    return chosen


def ask_limit(ask, say) -> int | None:
    answer = ask(f"How many recent messages per chat? Press Enter for {DEFAULT_LIMIT}, "
                 f"or type a number, or all: ").strip().lower()
    if answer in ("all", "tat ca", "tất cả"):
        return None
    if answer.isdigit() and int(answer) > 0:
        return int(answer)
    return DEFAULT_LIMIT


def pick_dialogs(dialogs: list[dict], query: str | None, ask, say, page: int = 40) -> list[dict]:
    """Show your Telegram chats as a numbered list and return the ones picked."""
    shown = dialogs
    if query:
        test = matcher(query)
        shown = [d for d in dialogs if test(d["name"])]
    while True:
        if not shown:
            say("No chat matches that. Showing all of them again.")
            shown = dialogs
        say("")
        for i, d in enumerate(shown[:page], 1):
            added = ("   (copied only in part: pick it to copy again)" if d.get("partial")
                     else "   (already added)" if d.get("added") else "")
            say(f"  {i:>3}. {d['name']}  [{d['kind']}]{added}")
        if len(shown) > page:
            say(f"  ... and {len(shown) - page} more. Type a word to narrow the list.")
        answer = ask("\nType the numbers of the chats to add (for example 1, 3-5),\n"
                     "or a word to search the list, or just Enter to finish: ").strip()
        if not answer:
            return []
        picks = parse_picks(answer, min(page, len(shown)))
        if picks is not None:
            return [shown[i - 1] for i in picks if not shown[i - 1].get("added")]
        if re.fullmatch(r"[\d\s,;\-]+", answer):
            say(f"Please use numbers between 1 and {min(page, len(shown))}.")
            continue
        test = matcher(answer)
        shown = [d for d in dialogs if test(d["name"])]


# ------------------------------------------------------------------- setup


def run_setup(ask=None, say=print, secret=None) -> int:
    from . import net  # noqa: PLC0415  (only setup and network commands need Telegram)

    ask = asker(ask)
    secret = secret or getpass.getpass
    say("Welcome to TG Kit! This takes about 5 minutes, one step at a time.")
    say("You can stop any time with Ctrl+C and start again with: tgkit setup\n")

    say("Step 1 of 4: where to keep your data")
    base = choose_home(ask, say)
    base.mkdir(parents=True, exist_ok=True)
    cfg = config.load(base)

    say("\nStep 2 of 4: connect TG Kit to Telegram")
    if config.has_credentials(base, cfg):
        say("Your api_id and api_hash are saved. They belong to TG Kit, not to one account: they\n"
            "work with any Telegram account. To use another account, keep them and choose\n"
            "switch in the next step.")
        again = ask("Press Enter to keep them, or type new to enter new ones: ").strip().lower()
        if again == "new":
            api_id, api_hash = ask_credentials(ask, say)
            cfg = config.put(base, api_id=api_id, api_hash=api_hash, me=None)
    else:
        api_id, api_hash = ask_credentials(ask, say)
        cfg = config.put(base, api_id=api_id, api_hash=api_hash, me=None)
    say("Saved.")

    say("\nStep 3 of 4: log in to Telegram")
    if config.logged_in(base):
        from . import account  # noqa: PLC0415

        name = account.who(base) or "your account"
        choice = ask(f"You are logged in as {name}. Press Enter to keep this account,\n"
                     "or type switch to log out and log in with another one: ").strip().lower()
        if choice in ("switch", "s", "doi", "đổi"):
            data = "keep"
            n = account.data_summary(base)["chats"]
            if n:
                say(f"TG Kit has a copy of {n} chat(s) from {name}.\n"
                    "  1  Keep it (still searchable; it updates only when you log in to that account again)\n"
                    "  2  Move it to a backup folder inside your data folder (nothing is deleted)")
                data = "backup" if ask("Type 1 or 2 (Enter = 1): ").strip() == "2" else "keep"
            res = account.logout(base, data)
            say(f"Logged out of {name}." + (f" Your chats were moved to {res['backup']}" if res["backup"] else ""))
            if res["telegram_note"]:
                say(res["telegram_note"] + " The login file was removed anyway.")
            cfg = config.load(base)
    ctx = net.Ctx(base, Store(base), cfg)

    def ask_phone():
        return ask("Your phone number with country code, for example +84 912 345 678: ").strip()

    def ask_code():
        return ask("Telegram just sent you a login code (look in your Telegram app).\n"
                   "Type the code here: ").strip()

    def ask_password():
        try:
            return secret("Your account has a two-step password. Type it (it stays hidden): ")
        except EOFError:
            raise Cancelled() from None

    me = net.run(net.login(ctx, ask_phone, ask_code, ask_password))
    from . import account  # noqa: PLC0415

    for note in account.switch_notes(account.adopt(base, me)):  # only what really moved
        say(note)
    cfg = config.load(base)
    say(f"Logged in as {me.get('name') or me.get('username') or 'you'}.")

    say("\nStep 4 of 4: pick the chats TG Kit should follow")
    say("Only the chats you pick are copied. You can add more later with: tgkit add")
    limit = ask_limit(ask, say)
    net.run(net.add_chats(ctx, None, limit, lambda dialogs, q: pick_dialogs(dialogs, q, ask, say)))

    say("\nAll set! Things to try:")
    say('  tgkit search "meeting"     find messages')
    say("  tgkit summary              what happened in the last 7 days")
    say("  tgkit tasks                your open tasks")
    say("  tgkit sync                 get new messages (run it every day or so)")
    say("Or just type tgkit to open the menu. In Claude, simply ask about your Telegram chats.")
    return 0


# -------------------------------------------------------------------- menu


MENU = """
TG Kit - your Telegram helper
  1  Search messages
  2  Summary of the last 7 days
  3  My tasks
  4  Get new messages from Telegram
  5  Add more chats
  6  Show my chats
  7  Setup (first time, or change settings)
  8  Log out / switch Telegram account
  0  Quit
"""


def run_menu(main, ask=None, say=print) -> int:
    """`main(argv)` runs one tgkit command; the menu only builds the argv."""
    ask = asker(ask)
    try:
        while True:
            say(MENU)
            choice = ask("Type a number and press Enter: ").strip()
            if choice in ("0", "q", "quit", "exit"):
                return 0
            if choice == "1":
                words = ask("What are you looking for? ").strip()
                if words:
                    main(["search", words])
            elif choice == "2":
                main(["summary"])
            elif choice == "3":
                main(["tasks"])
                act = ask("Type a task number to mark it done, or press Enter: ").strip()
                if act.isdigit():
                    main(["tasks", "done", act])
            elif choice == "4":
                main(["sync"])
            elif choice == "5":
                main(["add"])
            elif choice == "6":
                main(["chats"])
            elif choice == "7":
                main(["setup"])
            elif choice == "8":
                main(["logout"])
            else:
                say("Please type one of the numbers shown.")
                continue
            ask("\nPress Enter to go back to the menu...")
    except Cancelled:
        say("Bye.")
        return 0
