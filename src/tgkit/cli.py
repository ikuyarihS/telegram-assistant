"""`tgkit`: the command people type, and the one Claude runs for them."""

import argparse
import json
import os
import sys
import traceback
from datetime import datetime

from . import __version__, applog, config, digest, engine, wizard
from . import tasks as taskmod
from .errors import UserError
from .store import Store, atomic_write, local_time, matcher, parse_date, parse_since

HELP = """Telegram Assistant - search, summarize and track tasks from your Telegram chats.

Everyday:
  tgkit today                     what do I need to do today (--lang vi, --json for Claude)
  tgkit search "words"            find messages in your chats (accents optional)
  tgkit summary                   what happened in the last 7 days
  tgkit summary "chat" --since 30d
  tgkit tasks                     your open tasks, found in your messages
  tgkit tasks done 3              mark task 3 done
  tgkit sync                      get new messages from Telegram
  tgkit app                       open the Telegram Assistant window: ask in your own words

More:
  tgkit chats-log                 what you asked in the Telegram Assistant window (--json for Claude)
  tgkit tasks note 3 "text"       add a note to task 3   (also: add, drop, undo, mine, all)
  tgkit show "chat"               read the latest messages of a chat
  tgkit chats                     your chats and when they were last updated
  tgkit add                       follow more chats (pick from a list)
  tgkit search "words" --online   ask Telegram itself (also finds older messages)
  tgkit search --regex "a|b"      search with a pattern (a regular expression, like tg-crawl's find)
  tgkit import-crawl "folder"     bring in a copy made with tg-crawl (copies it; --preview to look first)
  tgkit media "chat"              files and photos in a chat
  tgkit who                       look up the names of people
  tgkit setup                     first-time setup, or change settings
  tgkit logout                    log out of Telegram here (to stop, or to use another account)
  tgkit export search "words"     save results as a Word file (also: summary, tasks, conversation <id>,
                                  file "chat" <message id>; --format docx|xlsx|csv|md, --to folder)
  tgkit usage                     tokens used by Claude answers in the Telegram Assistant window (--json)
  tgkit claude-status             is Claude Code installed and logged in (--json)
  tgkit where                     where your data is kept
  tgkit guide                     the playbook for Claude Code: "run tgkit guide, then answer"
  tgkit                           (nothing else) opens a simple menu

Add --json to today, search, summary or tasks for data that Claude can read.
"""


class Parser(argparse.ArgumentParser):
    def error(self, message):
        if "invalid choice" in message:
            bad = message.split("invalid choice:", 1)[1].split("(", 1)[0].strip()
            message = f"{bad} is not a Telegram Assistant command."
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


def cmd_guide(args) -> int:
    """The Claude Code playbook. Reads no config and asks nothing: works before setup."""
    from .playbook import text  # noqa: PLC0415

    sys.stdout.write(text())
    sys.stdout.flush()
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
    print(f"Telegram Assistant version {__version__}")
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
        if (store.meta(d).get("from_crawl") or {}).get("account") == "not_seen":
            print("      from tg-crawl; your Telegram account does not have this chat, so sync skips it")
    if stale:
        print("\nSome chats were last updated more than 12 hours ago. Run: tgkit sync")
    return 0


def _hits(store: Store, args) -> list[tuple]:
    # One search rule for the CLI and the app (engine.find; a pattern: engine.regex_find).
    if args.regex:
        return engine.regex_find(store, args.query, args.chat, args.since, args.case)
    return engine.find(store, args.query, args.chat, args.since, args.case)


def cmd_search(args) -> int:
    store = _store()
    if args.regex and args.online:
        raise UserError("Telegram itself cannot search with a pattern.", "Leave out --online or --regex.")
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
    if not hits and args.regex:
        print(f"No messages match the pattern '{args.query}'.")
        print("Tip: in a pattern, accents must match; without --regex, accents are optional.")
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
        with tl.change():
            # Check every number first: one bad number changes nothing and claims nothing.
            for ident in rest:
                tl.get(ident)
            changed = [tl.set_status(ident, status) for ident in rest]
        for t in changed:
            print(f"Task {t['id']} is now {status}.")
        return 0
    if action == "note":
        if len(rest) < 2:
            raise UserError("Add the task number and the note.", 'For example: tgkit tasks note 3 "sent on Monday"')
        with tl.change():
            t = tl.note(rest[0], " ".join(rest[1:]))
        print(f"Note added to task {t['id']}.")
        return 0
    if action == "add":
        if not rest:
            raise UserError("What is the task?", 'For example: tgkit tasks add "call the bank"')
        with tl.change():
            t = tl.add(" ".join(rest), args.due)
        print(f"Added task {t['id']}.")
        return 0

    tl, found = engine.scan_tasks(base, store, args.chat, args.since)
    if action == "scan":
        _info(args, f"Found {found} new task(s).")
        if not args.json:
            return 0
    which = {"list": "open", "open": "open", "scan": "open", "mine": "mine", "all": "all"}[action]
    rows = engine.select_tasks(tl, store, which, args.chat, args.since)
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


def cmd_today(args) -> int:
    """What do I need to do today. Read-only (never writes tasks.json), so Claude may run it."""
    store = _store()
    lang = "vi" if str(args.lang or "").lower().startswith("vi") else "en"
    data = engine.today(store.home, store, lang)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    print(engine.today_text(data, lang))
    for g in data["groups"]:
        if not g["total"]:
            continue
        print(f"\n{g['title']} ({g['total']})")
        for i in g["items"]:
            who = " ".join(x for x in [i.get("sender"), ("trong" if lang == "vi" else "in"), i.get("chat")]
                           if x) if i.get("chat") else (i.get("sender") or "")
            due = f"   ({'hạn' if lang == 'vi' else 'due'}: {i['due']})" if i.get("due") else ""
            num = f"#{i['task']} " if i.get("task") else ""
            print(f"  - {num}{i.get('time') or ''}  {who}: {i.get('text')}{due}".replace("  :", ":"))
            if i.get("link"):
                print(f"      {i['link']}")
        if g["more"]:
            print(f"  ... {'và' if lang == 'vi' else 'and'} {g['more']} "
                  f"{'mục nữa' if lang == 'vi' else 'more'}")
    return 0


def cmd_sync(args) -> int:
    from .store import OWNER_ENV, stop_check  # noqa: PLC0415

    net, ctx = _ctx()
    # Started by the window: its Stop button asks this sync to end cleanly (store.STOP_FILE).
    ctx.stop = stop_check(ctx.store.home, os.environ.get(OWNER_ENV))
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


def cmd_import_crawl(args) -> int:
    """Copy a tg-crawl copy in: messages, files and names. The tg-crawl folder is only read."""
    from . import crawlimport  # noqa: PLC0415
    from .store import OWNER_ENV, stop_check  # noqa: PLC0415

    base = config.home()
    if args.events:  # the window: one JSON event per line
        def say(ev):
            print(json.dumps(ev, ensure_ascii=False), flush=True)
        try:
            res = crawlimport.run_import(base, args.folder, check=not args.offline,
                                         stop=stop_check(base, os.environ.get(OWNER_ENV)), report=say)
        except UserError as exc:
            say({"type": "error", "message": exc.message, "hint": exc.hint or ""})
            return 1
        say({"type": "done", **res})
        return 0 if not (res["counts"]["warning"] or res["counts"]["failed"]) else 1
    plan = crawlimport.preview(base, args.folder)
    if args.preview:
        if args.json:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
        else:
            print(crawlimport.render_preview(plan))
            print("\nNothing was copied. To import, run the same command without --preview.")
        return 0
    if not args.json:
        print(crawlimport.render_preview(plan))
        print("\nImporting (the tg-crawl folder is only read) ...", flush=True)

    def progress(ev):
        if not args.json and ev.get("type") == "chat" and ev.get("state") == "copying":
            print(f"  {ev['chat']} ...", flush=True)
    res = crawlimport.run_import(base, args.folder, check=not args.offline, report=progress)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(crawlimport.render_report(res))
    return 0 if not (res["counts"]["warning"] or res["counts"]["failed"]) else 1


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
        print("No conversations in the Telegram Assistant app yet. Open it with: tgkit app")
        return 0
    if args.id:
        c = items[0]
        print(f"{c.get('title')}  ({local_time(c.get('created'))})")
        for t in c.get("turns") or []:
            who = "You" if t.get("role") == "user" else f"Telegram Assistant ({t.get('mode') or 'offline'})"
            print(f"\n[{local_time(t.get('at'))}] {who}:\n{t.get('text') or ''}")
        return 0
    print(f"Conversations in the Telegram Assistant app ({len(items)}), newest first:")
    for c in items:
        print(f"  {c['id']}  {local_time(c.get('updated'))}  {len(c.get('turns') or [])} turn(s)  "
              f"{c.get('title')}")
    print("\nRead one: tgkit chats-log <id>   |   for Claude: tgkit chats-log --json")
    return 0


def _yes(answer: str) -> bool:
    return answer.strip().lower() in ("y", "yes", "co", "có", "ok")


def cmd_logout(args) -> int:
    from . import account  # noqa: PLC0415

    base = config.home()
    name = account.who(base)
    if not account.has_session(base) and not args.backup_data:
        print("This computer is not logged in to Telegram, so there is nothing to log out.")
        print("To log in (with any account): tgkit setup")
        return 0
    ask = wizard.asker(input)
    print(f"Logged in to Telegram as {name}." if name else "This computer has a Telegram login.")
    print("Logging out ends Telegram Assistant's login here, on this computer only. Your Telegram app and your\n"
          "messages on Telegram are not touched. Your api_id and api_hash stay saved: they work\n"
          "with any account.")
    if not args.yes and not _yes(ask("Log out now? Type yes, or press Enter to cancel: ")):
        print("Nothing was changed.")
        return 0
    data = "backup" if args.backup_data else "keep"
    if not (args.keep_data or args.backup_data or args.yes):
        n = account.data_summary(base)["chats"]
        if n:
            print(f"\nTelegram Assistant has a copy of {n} chat(s) from this account. What should happen to it?\n"
                  "  1  Keep it (you can still search it; it gets new messages again only\n"
                  "     when you log in to this same account)\n"
                  "  2  Move it to a backup folder inside your data folder, so Telegram Assistant starts\n"
                  "     empty for another account. Nothing is deleted.")
            data = "backup" if ask("Type 1 or 2 (Enter = 1): ").strip() == "2" else "keep"
    res = account.logout(base, data)
    print("\nLogged out." if res["telegram"] else "\nLogged out on this computer.")
    if res["telegram_note"]:
        print(f"{res['telegram_note']} The login file was removed anyway. To be sure, open Telegram >\n"
              "Settings > Devices and end the Telegram Assistant session there.")
    if res["backup"]:
        print(f"Your chats, tasks and app conversations were moved to:\n  {res['backup']}")
    print("To log in again, with this or another account: tgkit setup")
    return 0


def cmd_claude_status(args) -> int:
    from . import claude  # noqa: PLC0415

    st = claude.status(home=config.home())
    if args.json:
        print(json.dumps(st, ensure_ascii=False, indent=2))
        return 0
    words = {"missing": "Claude Code is not installed on this computer.",
             "not_logged_in": "Claude Code is installed but not logged in.",
             "connected": "Claude Code is installed and logged in.",
             "unknown": "Claude Code is installed. Its login could not be checked."}[st["state"]]
    print(words)
    if st["version"]:
        print(f"  version {st['version']}")
    if st["account"]:
        plan = f" ({st['plan']} plan)" if st["plan"] else ""
        print(f"  account {st['account']}{plan}")
    print(f"  model for Telegram Assistant answers: {st['model'] or 'Claude Code default'}"
          + (f", effort {st['effort']}" if st["effort"] else ""))
    for i, step in enumerate(st.get("fix") or [], 1):
        print(f"  {i}. {step}")
    return 0


def _n(value) -> str:
    return f"{int(value or 0):,}"


def cmd_usage(args) -> int:
    from . import usage  # noqa: PLC0415

    base = config.home()
    tot = usage.totals(base, args.conversation)
    cfg = config.load(base) if config.config_path(base).exists() else {}
    tot["limit"] = usage.limit_state(base, cfg)
    if args.json:
        print(json.dumps(tot, ensure_ascii=False, indent=2))
        return 0
    print(tot["what_uses_tokens"])
    for key, label in (("today", "Today"), ("month", "This month"), ("conversation", "This conversation")):
        t = tot.get(key)
        if not t:
            continue
        cost = f", about ${t['cost_usd']:.2f} (Claude Code's estimate)" if "cost_usd" in t else ""
        print(f"  {label}: {_n(t['tokens'])} tokens in {t['answers']} answer(s): {_n(t['input'])} in, "
              f"{_n(t['output'])} out, {_n(t['cache_read'])} read from cache, "
              f"{_n(t['cache_write'])} written to cache{cost}")
    lim = tot["limit"]
    if lim["limit"]:
        print(f"  Daily limit you set: {_n(lim['limit'])} tokens"
              + (" (reached)" if lim["reached"] else "")
              + (", then Telegram Assistant answers without Claude" if lim["hard"] else ", a warning only"))
    return 0


def cmd_export(args) -> int:
    from . import export  # noqa: PLC0415

    base = config.home()
    what = (args.what or "").lower()
    rest = list(args.rest or [])
    if what == "file":
        if len(rest) < 2:
            raise UserError("Which file?", 'For example: tgkit export file "Team" 1234   (chat, then message id)')
        dirs = Store(base).match(rest[0])
        if len(dirs) != 1:
            raise UserError(f"'{rest[0]}' matches {len(dirs)} chats.", "Type more of the chat name.")
        path = export.save_attachment(base, dirs[0].name, rest[1], args.to)
    else:
        if what not in ("search", "summary", "tasks", "conversation"):
            raise UserError(f"Telegram Assistant cannot export '{what}'.",
                            'Try: tgkit export search "words", tgkit export summary, tgkit export tasks, '
                            "tgkit export conversation <id>")
        kw = {"chat": args.chat, "since": args.since, "lang": args.lang}
        if what == "search":
            kw["query"] = " ".join(rest)
        elif what == "summary" and rest and not args.chat:
            kw["chat"] = rest[0]
        elif what == "tasks":
            kw["which"] = rest[0] if rest else "open"
        elif what == "conversation":
            if not rest:
                raise UserError("Which conversation?", "See the ids with: tgkit chats-log")
            kw["conversation"] = rest[0]
        doc = export.build(base, what, **kw)
        path = export.save(base, doc, args.format, args.to)
    if args.json:
        print(json.dumps({"path": str(path), "name": path.name, "folder": str(path.parent)}, ensure_ascii=False))
        return 0
    print(f"Saved: {path}")
    if not args.no_reveal and sys.stdout.isatty():
        export.reveal(path)
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
    sub.add_parser("guide", aliases=["playbook"]).set_defaults(func=cmd_guide)
    sub.add_parser("where", aliases=["info"]).set_defaults(func=cmd_where)
    sub.add_parser("setup", aliases=["login", "init"]).set_defaults(func=cmd_setup)
    sub.add_parser("menu").set_defaults(func=cmd_menu)
    sub.add_parser("chats", aliases=["list"]).set_defaults(func=cmd_chats)

    p = sub.add_parser("search", aliases=["find"])
    p.add_argument("query")
    p.add_argument("--chat", help="only chats whose name contains this")
    p.add_argument("--online", "--server", action="store_true", help="ask Telegram itself")
    p.add_argument("--regex", "--pattern", action="store_true",
                   help="the query is a regular expression (like tg-crawl's find); accents must match")
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
    p.add_argument("--since", default=None,
                   help="only tasks since e.g. 7d; also how far back to look for new ones (default 30d)")
    p.add_argument("--chat", help="only tasks from chats whose name contains this")
    p.add_argument("--due", help="due date for tasks add")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_tasks)

    p = sub.add_parser("today", aliases=["hom-nay"])
    p.add_argument("--lang", default="en", help="en or vi")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_today)

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

    p = sub.add_parser("import-crawl", aliases=["import"])
    p.add_argument("folder", help="the tg-crawl folder (or its out folder)")
    p.add_argument("--preview", action="store_true", help="only say what would be imported")
    p.add_argument("--offline", action="store_true", help="do not ask Telegram which chats your account has")
    p.add_argument("--json", action="store_true")
    p.add_argument("--events", action="store_true", help=argparse.SUPPRESS)  # the window
    p.set_defaults(func=cmd_import_crawl)

    p = sub.add_parser("app", aliases=["window", "ui"], add_help=False)
    p.add_argument("app_args", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_app)

    p = sub.add_parser("chats-log", aliases=["history"])
    p.add_argument("id", nargs="?", help="one conversation")
    p.add_argument("--last", type=int, default=20, help="the newest N conversations (0 = all)")
    p.add_argument("--cards", action="store_true", help="include the result cards")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_chats_log)

    p = sub.add_parser("logout", aliases=["log-out", "switch-account"])
    p.add_argument("--yes", action="store_true", help="do not ask (keeps the chats unless --backup-data)")
    p.add_argument("--keep-data", action="store_true", help="keep the copied chats")
    p.add_argument("--backup-data", action="store_true", help="move the chats to a backup folder")
    p.set_defaults(func=cmd_logout)

    p = sub.add_parser("claude-status", aliases=["claude"])
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_claude_status)

    p = sub.add_parser("usage", aliases=["tokens"])
    p.add_argument("--conversation", help="also the total of one conversation (its id)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_usage)

    p = sub.add_parser("export", aliases=["save"])
    p.add_argument("what", nargs="?", default="", help="search, summary, tasks, conversation or file")
    p.add_argument("rest", nargs="*")
    p.add_argument("--chat")
    p.add_argument("--since")
    p.add_argument("--format", default="docx", help="docx (default), xlsx, csv or md")
    p.add_argument("--to", help="the folder to save in (default: the one set in the app, or Documents/Telegram Assistant)")
    p.add_argument("--lang", default="en", help="en or vi: the language of the headings")
    p.add_argument("--no-reveal", action="store_true", help="do not open the folder afterwards")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_export)
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
    "FloodWaitError": ("Telegram asked Telegram Assistant to slow down.", "Please wait a few minutes and try again."),
    "AuthKeyUnregisteredError": ("Your Telegram login on this computer has ended.", "Run: tgkit setup"),
    "SessionRevokedError": ("Your Telegram login on this computer was closed.", "Run: tgkit setup"),
    "AuthKeyDuplicatedError": ("Your Telegram login file was used on two computers at once.", "Run: tgkit setup"),
    "ApiIdInvalidError": ("Telegram did not accept your api_id / api_hash.", "Run: tgkit setup and type them again."),
    "PhoneNumberInvalidError": ("Telegram did not accept that phone number.",
                                "Run tgkit setup again and type it with the country code, like +84 912 345 678."),
    "PhoneCodeInvalidError": ("That login code was not right.", "Run tgkit setup again."),
    "PhoneCodeExpiredError": ("That login code has expired.", "Run tgkit setup again to get a new one."),
    "PasswordHashInvalidError": ("That two-step password was not right.", "Run tgkit setup again."),
    # sqlite3 "database is locked": another Telegram Assistant client had the login file open
    "OperationalError": ("Telegram Assistant is still busy with Telegram (getting new messages or copying chats).",
                         "Wait a moment and try again."),
}


# Set by claude.py for the headless Claude session: every command that changes
# something is refused, whatever words or option order reach argparse.
READONLY_ENV = "TGKIT_READONLY"
TASK_WRITES = ("done", "undo", "reopen", "drop", "note", "add")


def _writes(func, args) -> bool:
    if func is cmd_tasks:
        return (args.action or "").lower() in TASK_WRITES
    return func in (cmd_setup, cmd_add, cmd_sync, cmd_who, cmd_menu, cmd_app, cmd_logout, cmd_export,
                    cmd_import_crawl)


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
        if os.environ.get(READONLY_ENV) and _writes(func, args):
            raise UserError(
                "Telegram Assistant is read-only here: it was started by Claude in the Telegram Assistant window, "
                "so it does not change tasks, sync, add chats, set up, log out or save files.",
                "Use the buttons in the Telegram Assistant window for that.",
            )
        return func(args) or 0
    except UserError as exc:
        print(exc.render(), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001
        applog.caught(f"tgkit {getattr(func, '__name__', 'command').removeprefix('cmd_')}", exc)
        friendly = _FRIENDLY.get(type(exc).__name__)
        if debug:
            traceback.print_exc()
        if friendly:
            print("\n".join(friendly), file=sys.stderr)
            return 1
        log = _crash_log(exc)  # the kind of error and its detail go to the file, never the screen
        print("Sorry, something went wrong.", file=sys.stderr)
        if log:
            print(f"The details were saved in {log}. Send that file to whoever gave you Telegram Assistant.",
                  file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
