"""`tgkit`: the command people type, and the one Claude runs for them."""

import argparse
import json
import os
import sys
import traceback
from datetime import datetime

from . import __version__, config, digest, engine, wizard
from . import tasks as taskmod
from .errors import UserError
from .store import Store, atomic_write, local_time, matcher, parse_date, parse_since

HELP = """TG Kit - search, summarize and track tasks from your Telegram chats.

Everyday:
  tgkit search "words"            find messages in your chats (accents optional)
  tgkit summary                   what happened in the last 7 days
  tgkit summary "chat" --since 30d
  tgkit tasks                     your open tasks, found in your messages
  tgkit tasks done 3              mark task 3 done
  tgkit sync                      get new messages from Telegram
  tgkit app                       open the TG Kit window: ask in your own words

More:
  tgkit chats-log                 what you asked in the TG Kit window (--json for Claude)
  tgkit tasks note 3 "text"       add a note to task 3   (also: add, drop, undo, mine, all)
  tgkit show "chat"               read the latest messages of a chat
  tgkit chats                     your chats and when they were last updated
  tgkit add                       follow more chats (pick from a list)
  tgkit search "words" --online   ask Telegram itself (also finds older messages)
  tgkit media "chat"              files and photos in a chat
  tgkit who                       look up the names of people
  tgkit setup                     first-time setup, or change settings
  tgkit where                     where your data is kept
  tgkit                           (nothing else) opens a simple menu

Add --json to search, summary or tasks for data that Claude can read.
"""


class Parser(argparse.ArgumentParser):
    def error(self, message):
        if "invalid choice" in message:
            bad = message.split("invalid choice:", 1)[1].split("(", 1)[0].strip()
            message = f"{bad} is not a TG Kit command."
        print(f"Sorry, I did not understand that: {message}\nType: tgkit help", file=sys.stderr)
        raise SystemExit(2)


def _console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        if not sys.stdin.isatty():
            sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass


def _store() -> Store:
    return Store(config.home())


def _ctx():
    from . import net  # noqa: PLC0415

    base = config.home()
    return net, net.Ctx(base, Store(base), config.load(base))


def _info(args, text: str) -> None:
    """Progress lines go to stderr when the output is JSON, so the JSON stays clean."""
    print(text, file=sys.stderr if getattr(args, "json", False) else sys.stdout)


# ------------------------------------------------------------------ commands


def cmd_help(args) -> int:
    print(HELP)
    return 0


def cmd_where(args) -> int:
    base = config.home()
    store = Store(base)
    cfg = config.load(base)
    print(f"Data folder: {base}")
    print(f"  ({config.home_source()})")
    print(f"Chats followed: {len(store.chat_dirs())}")
    print(f"Telegram codes saved: {'yes' if config.has_credentials(base, cfg) else 'no'}")
    print(f"Logged in on this computer: {'yes' if config.logged_in(base) else 'no'}")
    print(f"TG Kit version {__version__}")
    return 0


def cmd_chats(args) -> int:
    store = _store()
    dirs = store.match(None)
    state = store.sync_state()
    print(f"Your chats ({len(dirs)}), kept in {store.chats}:")
    stale = False
    for d in dirs:
        rs = store.rows(d)
        files = len(list((d / "media").iterdir())) if (d / "media").exists() else 0
        when = store.synced_at(d, state)
        stale = stale or store.is_stale(when)
        upd = local_time(when.isoformat()) if when else "never"
        if rs:
            span = f"{local_time(rs[0].get('date'))[:10]} to {local_time(rs[-1].get('date'))[:10]}"
            print(f"  {store.title(d)}\n      {len(rs)} messages, {span}, {files} file(s), updated {upd}")
        else:
            print(f"  {store.title(d)}\n      no messages yet")
    if stale:
        print("\nSome chats were last updated more than 12 hours ago. Run: tgkit sync")
    return 0


def _hits(store: Store, args) -> list[tuple]:
    # One search rule for the CLI and the app (engine.find).
    return engine.find(store, args.query, args.chat, args.since, args.case)


def cmd_search(args) -> int:
    store = _store()
    if args.online:
        net, ctx = _ctx()
        dirs = store.match(args.chat)
        return net.run(net.server_search(ctx, args.query, dirs, args.limit or 50))
    hits = _hits(store, args)
    shown = hits[: args.limit] if args.limit else hits
    if args.json:
        print(json.dumps({
            "query": args.query,
            "total": len(hits),
            "results": [engine.hit_dict(store, r, d) for r, d, _, _ in shown],
        }, ensure_ascii=False, indent=2))
        return 0
    if not hits:
        print(f"No messages found for '{args.query}'.")
        print(f'Tip: try fewer words, or ask Telegram itself: tgkit search "{args.query}" --online')
        return 0
    for r, d, i, rs in shown:
        if args.context:
            print(f"--- {store.title(d)} ---")
            for j in range(max(0, i - args.context), min(len(rs), i + args.context + 1)):
                print((">> " if j == i else "   ") + store.fmt(rs[j]))
            print()
        else:
            print(store.fmt(r, chat=store.title(d)))
    more = f", showing the newest {len(shown)}. Add --limit 0 to see all" if len(shown) < len(hits) else ""
    print(f"\n{len(hits)} message(s) found{more}.")
    return 0


def cmd_show(args) -> int:
    store = _store()
    since = parse_since(args.since) if args.since else None
    until = parse_since(args.until) if args.until else None
    from .store import thread_of  # noqa: PLC0415

    for d in store.match(args.chat):
        rs = store.rows(d)
        if args.id is not None:
            rs = [r for r in rs if r.get("id") == args.id]
        if since:
            rs = [r for r in rs if (parse_date(r.get("date")) or since) >= since]
        if until:
            rs = [r for r in rs if (parse_date(r.get("date")) or until) <= until]
        if args.thread is not None:
            rs = thread_of(rs, args.thread)
        if args.sender:
            want = str(args.sender)
            test = matcher(want)
            rs = [r for r in rs if str(r.get("sender_id")) == want
                  or test(store.sender_name(r.get("sender_id")))]
        if args.last and not args.all:
            rs = rs[-args.last:]
        print(f"=== {store.title(d)}  ({len(rs)} shown) ===")
        for r in rs:
            print(store.fmt(r, width=args.width))
        print()
    return 0


def cmd_media(args) -> int:
    store = _store()
    for d in store.match(args.chat):
        lines = []
        for r in store.rows(d):
            m = r.get("media") or {}
            if not m:
                continue
            name = m.get("file_name") or ("photo" if m.get("kind") == "photo" else m.get("kind"))
            path = r.get("media_path")
            where = str(store.chats / path) if path else "(not downloaded)"
            lines.append(f"  [{local_time(r.get('date'))}] {store.sender_name(r.get('sender_id'))}: "
                         f"{name}\n      {where}")
        if lines:
            print(f"=== {store.title(d)} ===")
            print("\n".join(lines))
            print()
    return 0


def cmd_summary(args) -> int:
    store = _store()
    dg = digest.build(store, store.match(args.chat), args.since)
    if args.json:
        print(json.dumps(dg, ensure_ascii=False, indent=2))
    else:
        print(digest.render(dg))
    return 0


TASK_ACTIONS = ("list", "open", "mine", "all", "scan", "done", "undo", "reopen", "drop", "note", "add")


def cmd_tasks(args) -> int:
    action = (args.action or "list").lower()
    rest = args.rest or []
    if action not in TASK_ACTIONS:
        raise UserError(f"'{action}' is not something tasks can do.",
                        "Try: tgkit tasks, tgkit tasks done 3, tgkit tasks note 3 \"text\", "
                        "tgkit tasks add \"text\", tgkit tasks all")
    base = config.home()
    store = Store(base)
    tl = taskmod.TaskList(base)

    if action in ("done", "undo", "reopen", "drop"):
        if not rest:
            raise UserError("Which task? Add its number.", f"For example: tgkit tasks {action} 3")
        status = {"done": "done", "undo": "open", "reopen": "open", "drop": "dropped"}[action]
        # Check every number first: one bad number changes nothing and claims nothing.
        for ident in rest:
            tl.get(ident)
        changed = [tl.set_status(ident, status) for ident in rest]
        tl.save()
        for t in changed:
            print(f"Task {t['id']} is now {status}.")
        return 0
    if action == "note":
        if len(rest) < 2:
            raise UserError("Add the task number and the note.", 'For example: tgkit tasks note 3 "sent on Monday"')
        t = tl.note(rest[0], " ".join(rest[1:]))
        tl.save()
        print(f"Note added to task {t['id']}.")
        return 0
    if action == "add":
        if not rest:
            raise UserError("What is the task?", 'For example: tgkit tasks add "call the bank"')
        t = tl.add(" ".join(rest), args.due)
        tl.save()
        print(f"Added task {t['id']}.")
        return 0

    tl, found = engine.scan_tasks(base, store, args.chat, args.since)
    if action == "scan":
        _info(args, f"Found {found} new task(s).")
        if not args.json:
            return 0
    which = {"list": "open", "open": "open", "scan": "open", "mine": "mine", "all": "all"}[action]
    rows = tl.select(which)
    if args.json:
        print(json.dumps({"tasks": rows}, ensure_ascii=False, indent=2))
        return 0
    if found:
        print(f"Found {found} new task(s) in your messages.")
    title = {"open": "Open tasks", "mine": "Your open tasks", "all": "All tasks"}[which]
    if not rows:
        print(f"{title}: none. Nice!")
        return 0
    print(f"{title} ({len(rows)}):")
    for t in rows:
        print(taskmod.render(t))
    print("\nMark one done: tgkit tasks done <number>   |   add a note: tgkit tasks note <number> \"text\"")
    return 0


def cmd_sync(args) -> int:
    net, ctx = _ctx()
    dirs = ctx.store.match(args.chat)
    return net.run(net.sync_chats(ctx, dirs, args.quiet, args.dry_run))


def cmd_add(args) -> int:
    net, ctx = _ctx()
    limit = None if args.all else args.limit
    ask = wizard.asker(input)

    def pick(dialogs, query):
        return wizard.pick_dialogs(dialogs, query, ask, print)

    try:
        return net.run(net.add_chats(ctx, args.chat, limit, pick))
    except wizard.Cancelled:
        print("Nothing was added.")
        return 0


def cmd_who(args) -> int:
    net, ctx = _ctx()
    return net.run(net.resolve_names(ctx, ctx.store.match(args.chat)))


def cmd_setup(args) -> int:
    try:
        return wizard.run_setup()
    except wizard.Cancelled:
        print("\nSetup stopped. Start it again any time with: tgkit setup")
        return 0


def cmd_app(args) -> int:
    from . import app  # noqa: PLC0415

    rest = list(args.app_args or [])
    if rest[:1] == ["--"]:
        rest = rest[1:]
    return app.run(rest)


def cmd_chats_log(args) -> int:
    from .conversations import Conversations, log_view  # noqa: PLC0415

    convs = Conversations(config.home())
    if args.id:
        items = [convs.get(args.id)]
    else:
        items = convs.all()[: args.last] if args.last else convs.all()
    if args.json:
        print(json.dumps({"conversations": [log_view(c, args.cards) for c in items]},
                         ensure_ascii=False, indent=2))
        return 0
    if not items:
        print("No conversations in the TG Kit app yet. Open it with: tgkit app")
        return 0
    if args.id:
        c = items[0]
        print(f"{c.get('title')}  ({local_time(c.get('created'))})")
        for t in c.get("turns") or []:
            who = "You" if t.get("role") == "user" else f"TG Kit ({t.get('mode') or 'offline'})"
            print(f"\n[{local_time(t.get('at'))}] {who}:\n{t.get('text') or ''}")
        return 0
    print(f"Conversations in the TG Kit app ({len(items)}), newest first:")
    for c in items:
        print(f"  {c['id']}  {local_time(c.get('updated'))}  {len(c.get('turns') or [])} turn(s)  "
              f"{c.get('title')}")
    print("\nRead one: tgkit chats-log <id>   |   for Claude: tgkit chats-log --json")
    return 0


def cmd_menu(args) -> int:
    base = config.home()
    if not Store(base).chat_dirs() and not config.logged_in(base):
        return cmd_setup(args)
    return wizard.run_menu(main)


# ---------------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    ap = Parser(prog="tgkit", description="Search, summarize and track tasks from your Telegram chats.",
                add_help=False)
    ap.add_argument("-h", "--help", action="store_true")
    ap.add_argument("--version", action="version", version=f"tgkit {__version__}")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("help").set_defaults(func=cmd_help)
    sub.add_parser("where", aliases=["info"]).set_defaults(func=cmd_where)
    sub.add_parser("setup", aliases=["login", "init"]).set_defaults(func=cmd_setup)
    sub.add_parser("menu").set_defaults(func=cmd_menu)
    sub.add_parser("chats", aliases=["list"]).set_defaults(func=cmd_chats)

    p = sub.add_parser("search", aliases=["find"])
    p.add_argument("query")
    p.add_argument("--chat", help="only chats whose name contains this")
    p.add_argument("--online", "--server", action="store_true", help="ask Telegram itself")
    p.add_argument("--since", help="only messages since e.g. 7d, 2w, 2026-09-01")
    p.add_argument("--context", type=int, default=0, help="also show N messages around each hit")
    p.add_argument("--limit", type=int, default=30, help="show at most N (0 = all)")
    p.add_argument("--case", action="store_true", help="match upper/lower case exactly")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("show")
    p.add_argument("chat", nargs="?")
    p.add_argument("--last", type=int, default=40)
    p.add_argument("--all", action="store_true")
    p.add_argument("--id", type=int)
    p.add_argument("--since")
    p.add_argument("--until")
    p.add_argument("--sender")
    p.add_argument("--thread", type=int, metavar="ID")
    p.add_argument("--width", type=int, default=240)
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("media", aliases=["files"])
    p.add_argument("chat", nargs="?")
    p.set_defaults(func=cmd_media)

    p = sub.add_parser("summary", aliases=["digest"])
    p.add_argument("chat", nargs="?")
    p.add_argument("--since", default=None, help="period, default 7d")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_summary)

    p = sub.add_parser("tasks", aliases=["task", "todo"])
    p.add_argument("action", nargs="?")
    p.add_argument("rest", nargs="*")
    p.add_argument("--since", default=None, help="how far back to look for tasks, default 30d")
    p.add_argument("--chat")
    p.add_argument("--due", help="due date for tasks add")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_tasks)

    p = sub.add_parser("sync", aliases=["update"])
    p.add_argument("chat", nargs="?")
    p.add_argument("--chat", dest="chat_opt")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--dry-run", action="store_true", help="only report what would be fetched")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("add")
    p.add_argument("chat", nargs="?", help="a word to filter the list, or @username")
    p.add_argument("--limit", type=int, default=wizard.DEFAULT_LIMIT)
    p.add_argument("--all", action="store_true", help="the whole history")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("who")
    p.add_argument("--chat")
    p.set_defaults(func=cmd_who)

    p = sub.add_parser("app", aliases=["window", "ui"], add_help=False)
    p.add_argument("app_args", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_app)

    p = sub.add_parser("chats-log", aliases=["history"])
    p.add_argument("id", nargs="?", help="one conversation")
    p.add_argument("--last", type=int, default=20, help="the newest N conversations (0 = all)")
    p.add_argument("--cards", action="store_true", help="include the result cards")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_chats_log)
    return ap


def _crash_log(exc: BaseException) -> str | None:
    try:
        base = config.home()
        path = base / "logs" / "last-error.txt"
        atomic_write(path, f"{datetime.now().isoformat()}  tgkit {__version__}\n"
                     + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
        return str(path)
    except Exception:  # noqa: BLE001
        return None


_FRIENDLY = {
    "FloodWaitError": ("Telegram asked TG Kit to slow down.", "Please wait a few minutes and try again."),
    "AuthKeyUnregisteredError": ("Your Telegram login on this computer has ended.", "Run: tgkit setup"),
    "SessionRevokedError": ("Your Telegram login on this computer was closed.", "Run: tgkit setup"),
    "AuthKeyDuplicatedError": ("Your Telegram login file was used on two computers at once.", "Run: tgkit setup"),
    "ApiIdInvalidError": ("Telegram did not accept your api_id / api_hash.", "Run: tgkit setup and type them again."),
    "PhoneNumberInvalidError": ("Telegram did not accept that phone number.",
                                "Run tgkit setup again and type it with the country code, like +84 912 345 678."),
    "PhoneCodeInvalidError": ("That login code was not right.", "Run tgkit setup again."),
    "PhoneCodeExpiredError": ("That login code has expired.", "Run tgkit setup again to get a new one."),
    "PasswordHashInvalidError": ("That two-step password was not right.", "Run tgkit setup again."),
}


def main(argv=None) -> int:
    _console()
    argv = list(sys.argv[1:] if argv is None else argv)
    debug = bool(os.environ.get("TGKIT_DEBUG"))
    if "--debug" in argv:
        argv.remove("--debug")
        debug = True
    if argv and argv[0] in ("app", "window", "ui"):
        # The app has its own options (--browser, --port); argparse would claim them here.
        argv = [argv[0], "--", *argv[1:]] if len(argv) > 1 and argv[1] != "--" else argv
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    if getattr(args, "help", False):
        return cmd_help(args)
    if getattr(args, "chat_opt", None) and not getattr(args, "chat", None):
        args.chat = args.chat_opt
    func = getattr(args, "func", None) or cmd_menu
    try:
        return func(args) or 0
    except UserError as exc:
        print(exc.render(), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001
        friendly = _FRIENDLY.get(type(exc).__name__)
        if debug:
            traceback.print_exc()
        if friendly:
            print("\n".join(friendly), file=sys.stderr)
            return 1
        log = _crash_log(exc)
        print(f"Sorry, something went wrong: {type(exc).__name__}: {exc}", file=sys.stderr)
        if log:
            print(f"The details were saved in {log}. Send that file to whoever gave you TG Kit.",
                  file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
