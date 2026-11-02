"""A stand-in for the `claude` CLI: prints stream-json like `claude -p --output-format
stream-json` does. FAKE_CLAUDE_LOG gets one JSON line per run (argv and stdin);
FAKE_CLAUDE_MODE=fail makes it fail like a Claude that is not signed in."""

import json
import os
import sys

prompt = sys.stdin.read()
log = os.environ.get("FAKE_CLAUDE_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:  # test helper, not package code
        fh.write(json.dumps({"argv": sys.argv[1:], "stdin": prompt}, ensure_ascii=False) + "\n")

if os.environ.get("FAKE_CLAUDE_MODE") == "fail":
    print("Not logged in · Please run /login", file=sys.stderr)
    sys.exit(1)

session = "sess-1"
if "--resume" in sys.argv:
    session = sys.argv[sys.argv.index("--resume") + 1]


def out(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def delta(text):
    out({"type": "stream_event", "session_id": session,
         "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}}})


out({"type": "system", "subtype": "init", "session_id": session, "tools": ["Bash"]})
delta("Đang ")
delta("tìm...")
cmd = 'tgkit search "biên bản" --json'
out({"type": "assistant", "session_id": session, "message": {"content": [
    {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": cmd}}]}})
result = {"query": "biên bản", "total": 1, "results": [{
    "chat": "Dự án Alpha: Kế hoạch", "folder": "Du_an_Alpha", "id": 23, "date": "2026-10-30T08:00:00+00:00",
    "sender": "Bình Trần", "text": "@an_test nhờ em gửi biên bản họp trước thứ 6 nhé", "file": None,
    "reply_to": None}]}
out({"type": "user", "session_id": session, "message": {"content": [
    {"type": "tool_result", "tool_use_id": "toolu_1", "content": json.dumps(result, ensure_ascii=False),
     "is_error": False}]}})
final = "Có 1 tin nhắn nhắc đến biên bản họp."
delta(final)
out({"type": "result", "subtype": "success", "is_error": False, "result": final, "session_id": session})
