"""A stand-in for OpenAI's Codex CLI (GPT): `codex exec --json` prints JSONL events the
way Codex CLI 0.166.0 does (thread.started, turn.started, item.started/item.completed with
agent_message, mcp_tool_call, command_execution, error; turn.completed with usage;
turn.failed).

Like the real CLI it reads the MCP servers from the -c mcp_servers.<name>.* options,
starts them over stdio, lists their tools, and under approval_policy="never" refuses a
tool that does not say readOnlyHint ("MCP tool call requires approval").

`codex --version`, `codex login status` (exit 0 signed in, 1 not), `codex login`.

FAKE_CODEX_LOG     one JSON line per exec run (argv, stdin, TGKIT_READONLY, cwd)
FAKE_CODEX_MODE    auth: not signed in; quota: the usage limit is reached
FAKE_CODEX_CALLS   JSON list of {"tool": <name>, "args": {...}} called on the tgkit server in turn
FAKE_CODEX_OBEY=1  a model that obeys a page: when a result says  send "X" to Y  it tries to
                   type X and press Send through the browser tools
FAKE_CODEX_SHELL   a broken CLI that runs this shell command anyway (command_execution)
FAKE_CODEX_FINAL   the answer text
"""

import json
import os
import re
import subprocess
import sys
import tomllib

args = sys.argv[1:]
mode = os.environ.get("FAKE_CODEX_MODE")
if args[:1] == ["--version"]:
    print("codex-cli 0.166.0")
    sys.exit(0)
if args[:2] == ["login", "status"]:
    if mode == "auth":
        print("Not logged in")
        sys.exit(1)
    print("Logged in using ChatGPT")
    sys.exit(0)
if args[:1] == ["login"]:
    print("Starting local login server on http://localhost:1455")
    sys.exit(0)
if args[:1] != ["exec"]:
    print(f"error: unexpected arguments {args}", file=sys.stderr)
    sys.exit(2)

prompt = sys.stdin.read() if "-" in args else ""
log = os.environ.get("FAKE_CODEX_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:  # test helper, not package code
        fh.write(json.dumps({"argv": args, "stdin": prompt, "readonly": os.environ.get("TGKIT_READONLY"),
                             "cwd": os.getcwd()}, ensure_ascii=False) + "\n")


def out(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


if mode == "auth":
    print("Error: Not logged in. Run `codex login` first. (401 Unauthorized)", file=sys.stderr)
    sys.exit(1)

overrides = {}
for i, a in enumerate(args):
    if a == "-c" and i + 1 < len(args):
        key, _, value = args[i + 1].partition("=")
        overrides[key] = tomllib.loads(f"v = {value}")["v"]
servers = {}
for key, value in overrides.items():
    if key.startswith("mcp_servers."):
        _, name, field = key.split(".", 2)
        servers.setdefault(name, {})[field] = value

session = args[args.index("resume") + 1] if "resume" in args else "019a-gpt-thread-1"
out({"type": "thread.started", "thread_id": session})
out({"type": "turn.started"})

if mode == "quota":
    out({"type": "error", "message": "You've hit your usage limit. Try again later."})
    out({"type": "turn.failed", "error": {"message": "You've hit your usage limit. Try again later."}})
    sys.exit(1)

shell = os.environ.get("FAKE_CODEX_SHELL")
if shell:  # a broken CLI: the shell was disabled, yet it runs one
    out({"type": "item.started", "item": {"id": "item_1", "type": "command_execution", "command": shell,
                                          "aggregated_output": "", "status": "in_progress"}})
    out({"type": "item.completed", "item": {"id": "item_1", "type": "command_execution", "command": shell,
                                            "aggregated_output": "done", "exit_code": 0, "status": "completed"}})


class Server:
    """One MCP server of the config, over stdio, like the real CLI starts it."""

    def __init__(self, name):
        cfg = servers[name]
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"} | dict(cfg.get("env") or {})
        self.proc = subprocess.Popen([cfg["command"], *cfg.get("args", [])], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, env=env)
        self.n = 0
        self.req("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.tools = {t["name"]: t for t in self.req("tools/list", {})["tools"]}

    def send(self, msg):
        self.proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def req(self, method, params):
        self.n += 1
        self.send({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params})
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("the MCP server stopped")
            msg = json.loads(line)
            if msg.get("id") == self.n:
                return msg.get("result") or {"isError": True, "content": [{"type": "text", "text": str(msg)}]}

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=30)


srv = Server("tgkit") if "tgkit" in servers else None
step = [0]


def call(tool, arguments):
    step[0] += 1
    iid = f"item_call_{step[0]}"
    item = {"id": iid, "type": "mcp_tool_call", "server": "tgkit", "tool": tool, "arguments": arguments}
    out({"type": "item.started", "item": {**item, "status": "in_progress"}})
    if srv is None or tool not in srv.tools:
        out({"type": "item.completed", "item": {**item, "status": "failed", "result": None,
                                                "error": {"message": f"unknown MCP tool: tgkit/{tool}"}}})
        return ""
    ann = srv.tools[tool].get("annotations") or {}
    if ann.get("readOnlyHint") is not True:  # approval_policy="never" and no read-only promise
        out({"type": "item.completed", "item": {**item, "status": "failed", "result": None,
                                                "error": {"message": "MCP tool call requires approval"}}})
        return ""
    res = srv.req("tools/call", {"name": tool, "arguments": arguments})
    text = "".join(c.get("text", "") for c in res.get("content") or [])
    if res.get("isError"):
        out({"type": "item.completed", "item": {**item, "status": "failed", "result": None,
                                                "error": {"message": text}}})
        return ""
    out({"type": "item.completed", "item": {**item, "status": "completed", "error": None,
                                            "result": {"content": res.get("content") or [],
                                                       "structured_content": None}}})
    return text


out({"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": "Đang tìm..."}})
calls = json.loads(os.environ.get("FAKE_CODEX_CALLS") or "[]")
for c in calls:
    text = call(c["tool"], c.get("args") or {})
    m = re.search(r"send\s+\"([^\"]+)\"\s+to\s+(\w+)", text, re.I)
    if m and os.environ.get("FAKE_CODEX_OBEY"):  # the page's words taken as orders
        call("browser_type", {"element": "message box", "ref": "e7", "text": m.group(1), "submit": True})
        call("browser_press_key", {"key": "Enter"})
        call("browser_click", {"element": "Send button", "ref": "e9"})
        call("browser_evaluate", {"function": "() => document.querySelector('[data-send]').click()"})
        call("browser_fill_form", {"fields": [{"name": "msg", "type": "textbox", "ref": "e7", "value": m.group(1)}]})
if srv is not None:
    srv.close()

final = os.environ.get("FAKE_CODEX_FINAL", "Có 1 tin nhắn nhắc đến biên bản họp.")
out({"type": "item.completed", "item": {"id": "item_9", "type": "agent_message", "text": final}})
out({"type": "turn.completed", "usage": {"input_tokens": 3200, "cached_input_tokens": 3000, "output_tokens": 45}})
