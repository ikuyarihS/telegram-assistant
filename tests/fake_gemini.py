"""A stand-in for the Gemini CLI: prints stream-json like `gemini -p ... -o stream-json`
(Gemini CLI 0.66.0 events: init, message, tool_use, tool_result, error, result).

It applies the policy file the app passes (--admin-policy) the way the real CLI's
policy engine does for run_shell_command: highest priority first, commandRegex and
commandPrefix matched against the JSON of the arguments, a chained command
checked part by part, no match = ask_user = denied when headless.

FAKE_GEMINI_LOG          one JSON line per run (argv, stdin, the env it got)
FAKE_GEMINI_MODE         auth: not signed in (exit 41); quota: limit reached
FAKE_GEMINI_RUN="<args>" the tool call is `tgkit <args>`, really run when the policy allows it
FAKE_GEMINI_CMD="<cmd>"  the tool call is this whole shell command (never run unless it is tgkit)
FAKE_GEMINI_TOOL=<name>  call another tool (write_file, web_fetch, ...)
FAKE_GEMINI_IGNORE_POLICY=1  a broken CLI that runs whatever is asked
FAKE_GEMINI_SLEEP=<s>    wait before the answer (Stop)
"""

import json
import os
import re
import shlex
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
                             "no_relaunch": os.environ.get("GEMINI_CLI_NO_RELAUNCH"), "cwd": os.getcwd()},
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


def matches(rule, tool, command):
    if rule.get("mcpName"):
        return False  # no MCP tools here
    if rule.get("toolName") not in ("*", tool):
        return False
    if tool != "run_shell_command":
        return "commandPrefix" not in rule and "commandRegex" not in rule
    text = json.dumps({"command": command}, ensure_ascii=False, separators=(",", ":"))
    if "commandRegex" in rule:
        return re.search('"command":"' + rule["commandRegex"], text) is not None
    if "commandPrefix" in rule:
        prefixes = rule["commandPrefix"] if isinstance(rule["commandPrefix"], list) else [rule["commandPrefix"]]
        for p in prefixes:
            head = json.dumps(p, ensure_ascii=False)[:-1]
            if re.search(re.escape('"command":' + head) + r'(?:[\s"]|\\")', text):
                return True
        return False
    return True


def decide(rules, tool, command):
    parts = [c.strip() for c in re.split(r"&&|\|\||;|\|", command)] if tool == "run_shell_command" else [command]
    for part in parts:
        rule = next((r for r in rules if matches(r, tool, part)), None)
        decision = rule["decision"] if rule else "ask_user"
        if decision != "allow":  # ask_user is deny when headless
            return False, (rule or {}).get("denyMessage") or "Tool execution denied by policy."
    return True, ""


def delta(text):
    out({"type": "message", "role": "assistant", "content": text, "delta": True})


delta("Đang ")
delta("tìm...")
tool = os.environ.get("FAKE_GEMINI_TOOL", "run_shell_command")
run = os.environ.get("FAKE_GEMINI_RUN")
command = os.environ.get("FAKE_GEMINI_CMD") or (f"tgkit {run}" if run else 'tgkit search "biên bản" --json')
params = {"command": command} if tool == "run_shell_command" else {"file_path": "notes.txt", "content": "x"}
out({"type": "tool_use", "tool_name": tool, "tool_id": "call_1", "parameters": params})
allowed, why = (True, "") if os.environ.get("FAKE_GEMINI_IGNORE_POLICY") else decide(load_policy(), tool, command)
if not allowed:
    out({"type": "tool_result", "tool_id": "call_1", "status": "error", "output": why,
         "error": {"type": "POLICY_DENIED", "message": why}})
elif tool == "run_shell_command" and command.startswith("tgkit ") and (run or os.environ.get("FAKE_GEMINI_CMD")):
    done = subprocess.run([sys.executable, "-m", "tgkit", *shlex.split(command)[1:]], capture_output=True,
                          text=True, encoding="utf-8")
    ok = done.returncode == 0
    out({"type": "tool_result", "tool_id": "call_1", "status": "success" if ok else "error",
         "output": done.stdout + done.stderr,
         "error": None if ok else {"type": "TOOL_EXECUTION_ERROR", "message": done.stdout + done.stderr}})
elif tool == "run_shell_command" and not os.environ.get("FAKE_GEMINI_CMD"):
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
