"""A stand-in for the `claude` CLI: prints stream-json like `claude -p --output-format
stream-json` does. FAKE_CLAUDE_LOG gets one JSON line per run (argv and stdin);
FAKE_CLAUDE_MODE=fail makes it fail like a Claude that is not signed in.
FAKE_CLAUDE_CALL='{"tool": "tasks", "args": {...}}' calls that tool of the MCP server
in --mcp-config (started and called over stdio, as Claude Code does), when
--allowedTools allows mcp__tgkit__<tool> and --disallowedTools does not deny it.
FAKE_CLAUDE_SHELL=Bash|PowerShell asks for a shell command instead (FAKE_CLAUDE_CMD);
it is denied unless FAKE_CLAUDE_IGNORE_RULES=1 (a broken CLI that says it ran it)."""

import json
import os
import subprocess
import sys

# Quick commands the app runs to check Claude (no prompt, not logged as a run).
if "--help" in sys.argv:
    print(os.environ.get("FAKE_CLAUDE_HELP", "Usage: claude [options]\n  -p, --print  Print response and exit"))
    sys.exit(0)
if "--version" in sys.argv:
    print("9.9.9 (Claude Code)")
    sys.exit(0)
if sys.argv[1:3] == ["auth", "status"]:
    auth = os.environ.get("FAKE_CLAUDE_AUTH")
    if auth is None:
        print("error: unknown command 'auth'", file=sys.stderr)
        sys.exit(1)
    print(auth)
    sys.exit(0 if '"loggedIn": true' in auth else 1)

prompt = sys.stdin.read()
argv = sys.argv[1:]
log = os.environ.get("FAKE_CLAUDE_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:  # test helper, not package code
        fh.write(json.dumps({"argv": argv, "stdin": prompt}, ensure_ascii=False) + "\n")

if os.environ.get("FAKE_CLAUDE_MODE") == "fail":
    print("Not logged in · Please run /login", file=sys.stderr)
    sys.exit(1)

session = "sess-1"
if "--resume" in argv:
    session = argv[argv.index("--resume") + 1]


def out(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def delta(text):
    out({"type": "stream_event", "session_id": session,
         "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}}})


def flag_values(flag):
    if flag not in argv:
        return []
    vals = []
    for a in argv[argv.index(flag) + 1:]:
        if a.startswith("--"):
            break
        vals.append(a)
    return vals


def call_server(name, arguments):
    with open(argv[argv.index("--mcp-config") + 1], encoding="utf-8") as fh:
        server = json.load(fh)["mcpServers"]["tgkit"]
    # Like the real CLI: the server gets the config's env, not the test's PYTHONPATH.
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"} | server.get("env", {})
    proc = subprocess.Popen([server["command"], *server.get("args", [])], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, env=env)
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name, "arguments": arguments}}]
    body, _ = proc.communicate("".join(json.dumps(m) + "\n" for m in msgs).encode("utf-8"), timeout=120)
    replies = [json.loads(x) for x in body.decode("utf-8").splitlines() if x.strip()]
    result = next(r for r in replies if r.get("id") == 2)["result"]
    return "".join(c.get("text", "") for c in result["content"]), bool(result.get("isError"))


shell = os.environ.get("FAKE_CLAUDE_SHELL")
model = argv[argv.index("--model") + 1] if "--model" in argv else "claude-sonnet-test"
out({"type": "system", "subtype": "init", "session_id": session, "model": model,
     "tools": [shell] if shell else [f"mcp__tgkit__{t}" for t in ("search", "tasks")]})
delta("Đang ")
delta("tìm...")
call = json.loads(os.environ["FAKE_CLAUDE_CALL"]) if os.environ.get("FAKE_CLAUDE_CALL") else None
if shell:
    name, params = shell, {"command": os.environ.get("FAKE_CLAUDE_CMD", 'tgkit search "x" --json')}
else:
    name = f"mcp__tgkit__{call['tool'] if call else 'search'}"
    params = call["args"] if call else {"query": "biên bản"}
out({"type": "assistant", "session_id": session, "message": {"content": [
    {"type": "tool_use", "id": "toolu_1", "name": name, "input": params}]}})
allowed = name in flag_values("--allowedTools") and name not in flag_values("--disallowedTools")
if os.environ.get("FAKE_CLAUDE_IGNORE_RULES"):
    body, failed = "done", False
elif not allowed:
    body, failed = f"Permission to use {name} has been denied.", True
elif call:
    body, failed = call_server(call["tool"], call["args"])
else:
    result = {"query": "biên bản", "total": 1, "results": [{
        "chat": "Dự án Alpha: Kế hoạch", "folder": "Du_an_Alpha", "id": 23, "date": "2026-10-30T08:00:00+00:00",
        "sender": "Bình Trần", "text": "@an_test nhờ em gửi biên bản họp trước thứ 6 nhé", "file": None,
        "reply_to": None}]}
    body, failed = json.dumps(result, ensure_ascii=False), False
out({"type": "user", "session_id": session, "message": {"content": [
    {"type": "tool_result", "tool_use_id": "toolu_1", "content": body, "is_error": failed}]}})
final = os.environ.get("FAKE_CLAUDE_FINAL", "Có 1 tin nhắn nhắc đến biên bản họp.")
delta(final)
result_ev = {"type": "result", "subtype": "success", "is_error": False, "result": final, "session_id": session,
             "usage": {"input_tokens": 120, "output_tokens": 45, "cache_read_input_tokens": 3000,
                       "cache_creation_input_tokens": 200},
             "modelUsage": {model: {"inputTokens": 120}}}
if os.environ.get("FAKE_CLAUDE_COST"):
    result_ev["total_cost_usd"] = float(os.environ["FAKE_CLAUDE_COST"])
out(result_ev)
