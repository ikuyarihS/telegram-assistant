"""A stand-in for the Gemini CLI: prints stream-json like `gemini -p ... -o stream-json`
(Gemini CLI 0.66.0 events: init, message, tool_use, tool_result, error, result).

It applies the policy file the app passes (--admin-policy) the way the real CLI's
policy engine decides a tool: highest priority first, toolName "*" for any tool,
mcpName for the tools of one MCP server (named mcp_<server>_<tool>), no match =
ask_user = denied when headless. An allowed tgkit tool really runs: the fake
starts the MCP server written in the work folder's .gemini/settings.json and calls
it over stdio, the way the real CLI does.

FAKE_GEMINI_LOG          one JSON line per run (argv, stdin, the env it got)
FAKE_GEMINI_MODE         auth: not signed in (exit 41); quota: limit reached
FAKE_GEMINI_CALL='{"tool": "search", "args": {...}}'  call this tgkit tool (really run)
FAKE_GEMINI_CMD="<cmd>"  call run_shell_command with this command (never run)
FAKE_GEMINI_TOOL=<name>  call another tool (write_file, web_fetch, ...)
FAKE_GEMINI_IGNORE_POLICY=1  a broken CLI that says it ran whatever was asked
FAKE_GEMINI_SLEEP=<s>    wait before the answer (Stop)
"""

import json
import os
import subprocess
import sys
import time

if "--version" in sys.argv or "-v" in sys.argv:
    print("0.66.0")
    sys.exit(0)

prompt = sys.stdin.read()
args = sys.argv[1:]
log = os.environ.get("FAKE_GEMINI_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:  # test helper, not package code
        fh.write(json.dumps({"argv": args, "stdin": prompt, "readonly": os.environ.get("TGKIT_READONLY"),
                             "no_relaunch": os.environ.get("GEMINI_CLI_NO_RELAUNCH"), "cwd": os.getcwd(),
                             "trust": os.environ.get("GEMINI_CLI_TRUST_WORKSPACE")},
                            ensure_ascii=False) + "\n")

mode = os.environ.get("FAKE_GEMINI_MODE")
if mode == "auth":
    print("Please set an Auth method in your C:\\Users\\x\\.gemini\\settings.json or specify one of the "
          "following environment variables before running: GEMINI_API_KEY", file=sys.stderr)
    sys.exit(41)


def out(obj):
    sys.stdout.write(json.dumps({"timestamp": "2026-11-03T08:00:00.000Z", **obj}, ensure_ascii=False) + "\n")
    sys.stdout.flush()


session = args[args.index("--resume") + 1] if "--resume" in args else "gem-sess-1"
model = args[args.index("-m") + 1] if "-m" in args else "gemini-2.5-pro"
out({"type": "init", "session_id": session, "model": model})
out({"type": "message", "role": "user", "content": prompt})

if mode == "quota":
    out({"type": "error", "severity": "error", "message": "RESOURCE_EXHAUSTED: You have exhausted your daily quota."})
    out({"type": "result", "status": "error", "stats": {"total_tokens": 0, "input_tokens": 0, "output_tokens": 0,
                                                       "cached": 0, "input": 0, "duration_ms": 5, "tool_calls": 0,
                                                       "models": {}}})
    sys.exit(1)


def load_policy():
    if "--admin-policy" not in args:
        return []
    import tomllib  # noqa: PLC0415

    with open(args[args.index("--admin-policy") + 1], "rb") as fh:
        rules = tomllib.load(fh).get("rule", [])
    return sorted(rules, key=lambda r: -int(r.get("priority", 0)))


def matches(rule, tool, server):
    if "commandPrefix" in rule or "commandRegex" in rule or "argsPattern" in rule:
        return False  # the app's policy has none any more; never guess an allow
    if "mcpName" in rule:
        if server is None or rule["mcpName"] not in ("*", server):
            return False
        simple = tool.split("_", 2)[2] if tool.startswith("mcp_") else tool
        return rule.get("toolName", "*") in ("*", simple)
    return rule.get("toolName") in ("*", tool)


def decide(rules, tool, server):
    rule = next((r for r in rules if matches(r, tool, server)), None)
    decision = rule["decision"] if rule else "ask_user"
    if decision != "allow":  # ask_user is deny when headless
        return False, (rule or {}).get("denyMessage") or "Tool execution denied by policy."
    return True, ""


def call_server(name, arguments):
    """Start the MCP server of the work folder's settings and call one tool over stdio."""
    with open(os.path.join(".gemini", "settings.json"), encoding="utf-8") as fh:
        servers = json.load(fh)["mcpServers"]
    allowed = args[args.index("--allowed-mcp-server-names") + 1].split(",")
    server = servers[allowed[0]]
    proc = subprocess.Popen([server["command"], *server.get("args", [])], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, env=dict(os.environ))
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name, "arguments": arguments}}]
    body, _ = proc.communicate("".join(json.dumps(m) + "\n" for m in msgs).encode("utf-8"), timeout=120)
    replies = [json.loads(x) for x in body.decode("utf-8").splitlines() if x.strip()]
    result = next(r for r in replies if r.get("id") == 2)["result"]
    return not result.get("isError"), "".join(c.get("text", "") for c in result["content"])


def delta(text):
    out({"type": "message", "role": "assistant", "content": text, "delta": True})


delta("Đang ")
delta("tìm...")
call = json.loads(os.environ["FAKE_GEMINI_CALL"]) if os.environ.get("FAKE_GEMINI_CALL") else None
shell_cmd = os.environ.get("FAKE_GEMINI_CMD")
if shell_cmd:
    tool, server, params = "run_shell_command", None, {"command": shell_cmd}
elif os.environ.get("FAKE_GEMINI_TOOL"):
    tool, server, params = os.environ["FAKE_GEMINI_TOOL"], None, {"file_path": "notes.txt", "content": "x"}
else:
    simple = call["tool"] if call else "search"
    tool, server, params = f"mcp_tgkit_{simple}", "tgkit", (call["args"] if call else {"query": "biên bản"})
out({"type": "tool_use", "tool_name": tool, "tool_id": "call_1", "parameters": params})
allowed, why = (True, "") if os.environ.get("FAKE_GEMINI_IGNORE_POLICY") else decide(load_policy(), tool, server)
if not allowed:
    out({"type": "tool_result", "tool_id": "call_1", "status": "error", "output": why,
         "error": {"type": "POLICY_DENIED", "message": why}})
elif server and call:
    ok, text = call_server(call["tool"], call["args"])
    out({"type": "tool_result", "tool_id": "call_1", "status": "success" if ok else "error", "output": text,
         "error": None if ok else {"type": "MCP_TOOL_ERROR", "message": text}})
elif server:
    result = {"query": "biên bản", "total": 1, "results": [{
        "chat": "Dự án Alpha: Kế hoạch", "folder": "Du_an_Alpha", "id": 23, "date": "2026-10-30T08:00:00+00:00",
        "sender": "Bình Trần", "text": "@an_test nhờ em gửi biên bản họp trước thứ 6 nhé", "file": None,
        "reply_to": None}]}
    out({"type": "tool_result", "tool_id": "call_1", "status": "success",
         "output": json.dumps(result, ensure_ascii=False)})
else:  # a broken CLI that let something else through
    out({"type": "tool_result", "tool_id": "call_1", "status": "success", "output": "done"})

time.sleep(float(os.environ.get("FAKE_GEMINI_SLEEP") or 0))
final = os.environ.get("FAKE_GEMINI_FINAL", "Có 1 tin nhắn nhắc đến biên bản họp.")
delta(final)
out({"type": "result", "status": "success",
     "stats": {"total_tokens": 3365, "input_tokens": 3200, "output_tokens": 45, "cached": 3000, "input": 200,
               "duration_ms": 1200, "tool_calls": 1, "models": {model: {"total_tokens": 3365}}}})
