"""0.2.9: Gemini as a second AI beside Claude (ai-status, Settings, setup buttons, version)."""

import json
import re
import sys
import threading
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tgkit import __version__, ai
from tgkit.appserver import App, serve
from tgkit.claude import ClaudeRunner
from tgkit.gemini import GeminiRunner

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "tgkit" / "web"
STUB = str(ROOT / "tests" / "fake_gemini.py")
TOKEN = "tok-029"


@pytest.fixture
def server(home):
    app = App(home, claude=ClaudeRunner(cmd=[]), gemini=GeminiRunner(cmd=[sys.executable, STUB]), token=TOKEN)
    httpd = serve(app)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    yield app, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def call(base, path, body=None, token=TOKEN):
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode("utf-8"),
                                 method="GET" if body is None else "POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-TGKit-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return res.status, json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, None


def test_version_is_029_or_later_everywhere():  # 0.2.10 and later keep every 0.2.9 test
    assert tuple(int(x) for x in __version__.split(".")) >= (0, 2, 9)
    assert tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"] == __version__
    assert json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"] == __version__


def test_ai_status_cli(home, tg, monkeypatch, tmp_path):
    code, out, _ = tg("ai-status", "--json")
    data = json.loads(out)
    assert code == 0 and data["choice"] == "auto" and data["active"] in ("claude", "off")
    assert data["gemini"]["state"] in ("no_node", "missing") and "fix" in data["gemini"]
    assert data["claude"]["state"] in ("missing", "not_logged_in", "connected", "unknown")
    code, out, _ = tg("ai-status")
    assert code == 0 and "AI answers in the window" in out and "Gemini:" in out
    code, out, _ = tg("claude-status", "--json")  # still there
    assert code == 0 and "state" in json.loads(out)
    # read-only sessions may run it: it is in both allow lists and not a write
    monkeypatch.setenv("TGKIT_READONLY", "1")
    code, out, _ = tg("ai-status", "--json")
    assert code == 0
    assert "tgkit ai-status *" in ai.READ_COMMANDS and "tgkit ai-status" in ai.READ_PREFIXES


def test_ai_status_with_gemini_signed_in(home, tg, monkeypatch, tmp_path):
    ghome = tmp_path / "g"
    (ghome / ".gemini").mkdir(parents=True)
    (ghome / ".gemini" / "settings.json").write_text('{"security":{"auth":{"selectedType":"oauth-personal"}}}',
                                                     encoding="utf-8")
    (ghome / ".gemini" / "google_accounts.json").write_text('{"active":"lan@example.com"}', encoding="utf-8")
    monkeypatch.setenv("GEMINI_CLI_HOME", str(ghome))
    monkeypatch.setenv("TGKIT_GEMINI", STUB)
    monkeypatch.setenv("TGKIT_CLAUDE", "off")
    code, out, _ = tg("ai-status", "--json")
    data = json.loads(out)
    assert data["active"] == "gemini" and data["gemini"]["state"] == "connected"
    assert data["gemini"]["account"] == "lan@example.com"
    code, out, _ = tg("ai-status")
    assert "Gemini: signed in as lan@example.com" in out


def test_api_ai_status_settings_and_setup_need_the_token(server, monkeypatch):
    app, base = server
    for path in ("/api/ai-status", "/api/gemini/install", "/api/gemini/login", "/api/gemini/node"):
        body = None if path == "/api/ai-status" else {}
        assert call(base, path, body, token=None)[0] in (401, 403)
        assert call(base, path, body, token="wrong")[0] in (401, 403)
    code, st = call(base, "/api/ai-status?refresh=1")
    assert code == 200 and st["choice"] == "auto" and set(st) >= {"active", "claude", "gemini"}
    code, s = call(base, "/api/settings", {"ai_provider": "gemini"})
    assert code == 200 and s["ai_provider"] == "gemini"
    assert call(base, "/api/state")[1]["ai"] == "gemini"
    code, err = call(base, "/api/settings", {"ai_provider": "gpt"})
    assert err.get("type") == "error" and "Claude, Gemini or Off" in err["message"]
    code, s = call(base, "/api/settings", {"ai_provider": "off"})
    assert call(base, "/api/state")[1]["ai"] is None
    code, res = call(base, "/api/gemini/login", {})
    assert code == 200 and res["ok"] and res["argv"] == [sys.executable, STUB]
    monkeypatch.setenv("TGKIT_NPM", "off")
    monkeypatch.setenv("TGKIT_NODE", "off")
    code, res = call(base, "/api/gemini/install", {})
    assert code == 200 and res["ok"] is False and res["hint"] == "Press Install Node.js first."


def test_window_strings_in_both_languages():
    js = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    html = (WEB / "index.html").read_text(encoding="utf-8")
    keys = ("secAi", "aiOff", "aiAutoPick", "aiHelp", "aiAuto", "aiNone", "geminiWhat", "gStNoNode", "gStOldNode", "gStMissing",
            "gStNotLogged", "gStConnected", "gFixNoNode", "gFixMissing", "gFixNotLogged", "installNode",
            "installGemini", "loginGemini", "installingGemini", "geminiInstalled", "nodeOpened", "nodeSite",
            "modeGemini", "geminiOn", "noAi")
    for k in keys:
        assert len(re.findall(rf"\b{k}: ", js)) == 2, k  # once in en, once in vi
    for ident in ("geminiDot", "geminiStateText", "geminiSteps", "geminiNode", "geminiInstall", "geminiLogin",
                  "geminiCheck", "aiNote"):
        assert f'id="{ident}"' in html and f'$("{ident}")' in js
    for value in ("auto", "claude", "gemini", "off"):  # auto: the way back after picking one
        assert f'name="ai" value="{value}"' in html
    assert "/api/ai-status" in js and "/api/gemini/install" in js and "/api/gemini/login" in js


def test_readme_and_no_dashes():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## Using Gemini instead of Claude" in readme and "tgkit ai-status" in readme
    for rel in ("README.md", "docs/MAINTENANCE.md", "src/tgkit/ai.py", "src/tgkit/gemini.py", "src/tgkit/playbook.md",
                "src/tgkit/appserver.py", "src/tgkit/web/static/app.js", "src/tgkit/web/index.html",
                "src/tgkit/errors.py", "src/tgkit/mcp.py", "src/tgkit/claude.py", "tests/fake_gemini.py",
                "tests/fake_claude.py", "tests/test_gemini_mode.py", "tests/test_v029.py"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert chr(0x2014) not in text and chr(0x2013) not in text, rel


# ---------------------------------------------------------------- gate 0.2.9 fix: no shell for the AI
# The gate ran "tgkit search ([IO.File]::WriteAllText('pwn.txt','x'))" through Gemini's shell tool.
# Now neither AI has a shell: tgkit's reads are tools of mcp.py, run with an argv list.

GATE_INPUTS = [
    "tgkit search ([IO.File]::WriteAllText('pwn.txt','x'))",
    "tgkit search ([IO.File]::WriteAllText('C:/x/pwn.txt','written by gemini shell')) --json",
    "tgkit search ([Diagnostics.Process]::Start('notepad.exe'))",
    "tgkit search ([Net.WebClient]::new().DownloadString('http://example.com'))",
    "tgkit search (& notepad.exe)",
    "tgkit search (. notepad.exe)",
    "tgkit tasks ($env:TGKIT_READONLY='') --json",
    "tgkit search ([scriptblock]::Create('ni pwn.txt').Invoke())",
    "tgkit search `rm a`",
]
L, R = chr(0x2018), chr(0x2019)  # curly quotes: PowerShell reads them as quotes
# (template, writes the sentinel {s} when a real shell runs it after a command)
PS_PAYLOADS = [
    ("([IO.File]::WriteAllText('{s}','x'))", True),
    ("([Diagnostics.Process]::Start('cmd.exe','/c type nul > \"{w}\"').WaitForExit())", True),
    ("([scriptblock]::Create('Set-Content -LiteralPath ''{s}'' -Value x').Invoke())", True),
    ("$(Set-Content -LiteralPath '{s}' -Value x)", True),
    ("@(Set-Content -LiteralPath '{s}' -Value x)", True),
    ("(& {{ Set-Content -LiteralPath '{s}' -Value x }})", True),
    ("\"$(Set-Content -LiteralPath '{s}' -Value x)\"", True),
    ("x; Set-Content -LiteralPath '{s}' -Value x", True),
    ("x | Set-Content -LiteralPath '{s}'", True),
    ("x > '{s}'", True),
    ("x\nSet-Content -LiteralPath '{s}' -Value x", True),
    (L + "x" + R + "; Set-Content -LiteralPath '{s}' -Value x", True),
    ("($env:TGKIT_READONLY='')", False),
    ("[IO.File]::WriteAllText('{s}','x')", False),
    ("x --% & echo x", False),
    ("{{x}}", False),
    ("'a'+'b'", False),
    ("x`; Set-Content -LiteralPath '{s}' -Value x", False),
]
BASH_PAYLOADS = [
    ("`touch {s}`", True),
    ("$(touch {s})", True),
    ("\"$(touch {s})\"", True),
    ("x; touch {s}", True),
    ("x && touch {s}", True),
    ("x | tee {s}", True),
    ("x > {s}", True),
    ("x\ntouch {s}", True),
    ("${{IFS}}x", False),
    ("<(touch {s})", False),
    ("x || touch {s}", False),
    ("\\$(touch {s})", False),
    ("x*", False),
    ("~root", False),
    ("x #y", False),
]


def _payloads(folder: Path, kind: str):
    """(payload, sentinel or None) with a fresh sentinel file per payload."""
    out = []
    for i, (tpl, writes) in enumerate(PS_PAYLOADS if kind == "ps" else BASH_PAYLOADS):
        s = folder / f"{kind}{i}.txt"
        out.append((tpl.format(s=s.as_posix(), w=str(s)), s if writes else None))
    return out


def battery(folder: Path):
    """Every read command with every payload, alone, before --json, as --chat and quoted."""
    from tgkit import mcp
    for kind in ("ps", "bash"):
        for payload, _s in _payloads(folder, kind):
            for cmd in (*ai.READ_PREFIXES, *(f"tgkit {mcp.TOOLS[t][0]}" for t in mcp.TOOLS)):
                yield f"{cmd} {payload}"
                yield f"{cmd} {payload} --json"
                yield f"{cmd} --chat {payload} --json"
                yield f"{cmd} --json {payload}"


def gemini_decision(tool, server=None):
    from tgkit import gemini
    rules = sorted(tomllib.loads(gemini.policy_text())["rule"], key=lambda r: -r.get("priority", 0))
    for r in rules:
        if "mcpName" in r:
            if server is not None and r["mcpName"] in ("*", server):
                return r["decision"]
        elif r.get("toolName") in ("*", tool):
            return r["decision"]
    return "ask_user"


def test_gate_inputs_and_the_battery_are_denied_everywhere(tmp_path):
    from test_claude_mode import permitted
    cases = [*GATE_INPUTS, *battery(tmp_path)]
    assert len(cases) > 1000
    bad = [c for c in cases if ai.allowed_command(c)]  # the in-app guard
    bad += [c for c in GATE_INPUTS + cases[::7] for shell in ("Bash", "PowerShell") if permitted(c, shell)]  # Claude
    assert gemini_decision("run_shell_command") == "deny"  # Gemini's policy: the shell tool, whatever the text
    assert not bad, bad[:5]
    for ok in ('tgkit search "biên bản" --json', 'tgkit search --regex "a|b" --json', 'tgkit search "(x)" --regex',
               "tgkit search 'O Brien'", 'tgkit tasks --chat="du an" --json', "tgkit chats-log --json"):
        assert ai.allowed_command(ok), ok


@pytest.fixture
def src_path(monkeypatch):
    """`python -m tgkit` in a child process must find this source tree."""
    monkeypatch.setenv("PYTHONPATH", str(ROOT / "src"))


def test_the_battery_through_the_tools_writes_and_starts_nothing(home, tmp_path, src_path):
    from concurrent.futures import ThreadPoolExecutor

    from tgkit import mcp
    folder = tmp_path / "sentinels"
    folder.mkdir()
    items = [*_payloads(folder, "ps"), *_payloads(folder, "bash")]
    calls = [(tool, args) for payload, _s in items
             for tool, args in (("search", {"query": payload}), ("tasks", {"chat": payload}),
                                ("search", {"query": "x", "chat": payload, "regex": True}))]
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda c: mcp.run(c[0], c[1], str(home)), calls))
    for (tool, args), (ok, text) in zip(calls, results):
        assert "Traceback" not in text, (tool, args, text[-300:])
    assert [s for _p, s in items if s]  # the payloads do name their sentinels
    assert sorted(p.name for p in folder.iterdir()) == []  # nothing at all was written there
    assert not (Path.cwd() / "pwn.txt").exists()


def _bash() -> str | None:
    import shutil
    found = shutil.which("bash")
    if found and "system32" not in found.lower() and "windowsapps" not in found.lower():
        return found
    git = shutil.which("git")
    for cand in ([Path(git).parents[1] / "usr" / "bin" / "bash.exe", Path(git).parents[1] / "bin" / "bash.exe"]
                 if git else []):
        if cand.exists():
            return str(cand)
    return None


def test_positive_control_real_shells_do_run_the_payloads(tmp_path):
    """The detector works: the same payloads after a plain command DO write their sentinel in a
    real powershell.exe and bash, so the empty folder above is a real absence."""
    import shutil
    import subprocess
    ran = 0
    ps = shutil.which("powershell") or shutil.which("pwsh")
    if ps:
        items = [(p, s) for p, s in _payloads(tmp_path, "ps") if s]
        script = "\n".join(f"Write-Output {p}" for p, _ in items)
        subprocess.run([ps, "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, timeout=120)
        missing = [p for p, s in items if not s.exists()]
        assert not missing, missing
        ran += 1
    bash = _bash()
    if bash:
        items = [(p, s) for p, s in _payloads(tmp_path, "bash") if s]
        script = "\n".join(f"echo {p}" for p, _ in items)
        subprocess.run([bash, "-c", script], capture_output=True, timeout=120)
        missing = [p for p, s in items if not s.exists()]
        assert not missing, missing
        ran += 1
    if not ran:
        pytest.skip("no PowerShell or bash here")


def test_legit_reads_through_the_tools(home, src_path):
    from tgkit import mcp
    for args in ({"query": "biên bản"}, {"query": "bien ban"}, {"query": "O'Brien"}, {"query": 'say "hi"'},
                 {"query": "a|b", "regex": True}, {"query": "(x)", "regex": True}, {"query": "biên|bản", "regex": True},
                 {"query": "-x --online"}, {"query": "biên bản", "chat": "Alpha", "since": "3650d", "limit": 5}):
        ok, text = mcp.run("search", args, str(home))
        assert ok, (args, text[-300:])
        assert isinstance(json.loads(text)["total"], int)
    assert json.loads(mcp.run("search", {"query": "biên bản"}, str(home))[1])["total"] >= 1
    assert json.loads(mcp.run("search", {"query": "biên|bản", "regex": True}, str(home))[1])["total"] >= 1
    assert mcp.argv("search", {"query": "-x --online"}) == ["search", "--json", "--", "-x --online"]
    ok, text = mcp.run("tasks", {"view": "all", "since": "3650d"}, str(home))
    assert ok and isinstance(json.loads(text)["tasks"], list)
    for bad in ({"query": "x", "online": True}, {"query": 3}, {"query": "x", "limit": "5; rm"}, {"query": "a\x00b"},
                {"query": "x", "regex": "yes"}):
        assert mcp.run("search", bad, str(home))[0] is False, bad
    assert mcp.run("tasks", {"view": "done"}, str(home)) == (False, mcp.run("tasks", {"view": "Drop"}, str(home))[1])
    assert mcp.run("sync", {}, str(home))[0] is False


def test_the_server_speaks_mcp_on_stdio(home, src_path):
    import subprocess
    from tgkit import mcp
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "search", "arguments": {"query": "$(touch pwn) biên bản"}}},
            {"jsonrpc": "2.0", "id": 4, "method": "ping"}]
    cmd = mcp.server_command(home)
    done = subprocess.run([cmd["command"], *cmd["args"]], input="".join(json.dumps(m) + "\n" for m in msgs).encode(),
                          capture_output=True, timeout=60)
    replies = {r["id"]: r for r in map(json.loads, done.stdout.decode("utf-8").splitlines())}
    assert replies[1]["result"]["serverInfo"]["name"] == "tgkit" and replies[1]["result"]["protocolVersion"] == "2025-06-18"
    assert [t["name"] for t in replies[2]["result"]["tools"]] == list(mcp.TOOLS)
    assert replies[3]["result"]["isError"] is False and json.loads(replies[3]["result"]["content"][0]["text"])["total"] == 0
    assert replies[4]["result"] == {} and set(replies) == {1, 2, 3, 4}


def test_tool_table_matches_the_cli_and_prints_the_rest():
    """Every field is a real option of that tgkit command; every option no tool offers is named
    in mcp.NOT_OFFERED (the third bucket, printed first)."""
    from tgkit import cli, mcp
    ap = cli.build_parser()
    subs = next(a for a in ap._actions if a.__class__.__name__ == "_SubParsersAction").choices
    rest = {}
    for name, (cmd, add_json, _t, pos, opts) in mcp.TOOLS.items():
        p = subs[cmd]
        dests = {a.dest: a for a in p._actions if a.dest not in ("help",)}
        fields = {"view": "action"}.get
        for key in [*pos, *opts]:
            assert fields(key, key) in dests, (name, key)
        for key in opts:
            assert f"--{key}" in dests[key].option_strings, (name, key)
        assert add_json == ("json" in dests), name
        left = sorted(set(dests) - {fields(k, k) for k in [*pos, *opts]} - {"json"})
        if left:
            rest[cmd] = left
    print("options no tool offers:", rest)
    assert rest == {k: sorted(v) for k, v in mcp.NOT_OFFERED.items()}
    assert not any(cmd in ("sync", "setup", "add", "export", "logout", "import-crawl", "who", "app", "menu")
                   for cmd, *_ in mcp.TOOLS.values())


def _bundle() -> Path | None:
    import os
    for cand in (os.environ.get("TGKIT_GEMINI_BUNDLE"),
                 "D:/ClaudeData/zerg/z20261103-071319-haiz/scratch/gcli/package/bundle"):
        if cand and (Path(cand) / "gemini.js").exists():
            return Path(cand)
    return None


ENGINE_JS = r"""
import fs from "node:fs"; import path from "node:path"; import { pathToFileURL } from "node:url";
const [bundle, policy, casesFile] = process.argv.slice(2);
let m = null;
for (const f of fs.readdirSync(bundle).filter(f => /^chunk-.*\.js$/.test(f))) {
  if (fs.statSync(path.join(bundle, f)).size < 5e6) continue;
  if (!fs.readFileSync(path.join(bundle, f), "utf8").includes("createPolicyEngineConfig")) continue;
  try { const x = await import(pathToFileURL(path.join(bundle, f)).href);
        if (x.createPolicyEngineConfig && x.PolicyEngine) { m = x; break; } } catch {}
}
const cfg = await m.createPolicyEngineConfig({ policyPaths: [policy], adminPolicyPaths: [policy] },
  m.ApprovalMode.DEFAULT, path.join(bundle, "policies"), false);
const eng = new m.PolicyEngine({ ...cfg, nonInteractive: true, approvalMode: m.ApprovalMode.DEFAULT });
const out = [];
for (const [name, args, server] of JSON.parse(fs.readFileSync(casesFile, "utf8")))
  out.push((await eng.check({ name, args }, server ?? undefined)).decision);
console.log(JSON.stringify(out));
"""


def test_the_real_gemini_policy_engine_agrees(tmp_path):
    """Gemini CLI 0.66.0's own policy engine on the policy the app passes (skipped without a copy)."""
    import shutil
    import subprocess
    from tgkit import gemini
    bundle, node = _bundle(), shutil.which("node")
    if not bundle or not node:
        pytest.skip("no Gemini CLI bundle or node here (set TGKIT_GEMINI_BUNDLE)")
    policy = tmp_path / "tgkit-policy.toml"
    policy.write_text(gemini.policy_text(), encoding="utf-8")
    shell = [*GATE_INPUTS, *list(battery(tmp_path))[::5]]
    cases = ([["run_shell_command", {"command": c}, None] for c in shell]
             + [[t, {"file_path": "x", "content": "x"}, None] for t in
                ("write_file", "replace", "read_file", "web_fetch", "google_web_search", "glob", "list_directory",
                 "save_memory", "activate_skill", "write_todos")]
             + [["mcp_evil_search", {"query": "x"}, "evil"], ["mcp_tgkit2_search", {"query": "x"}, "tgkit2"]]
             + [[f"mcp_tgkit_{t}", {"query": "([IO.File]::WriteAllText('pwn.txt','x'))"}, "tgkit"] for t in
                ("search", "tasks", "summary", "chats_log")])
    cf = tmp_path / "cases.json"
    cf.write_text(json.dumps(cases), encoding="utf-8")
    js = tmp_path / "engine.mjs"
    js.write_text(ENGINE_JS, encoding="utf-8")
    done = subprocess.run([node, str(js), str(bundle), str(policy), str(cf)], capture_output=True, timeout=300)
    got = json.loads(done.stdout.decode("utf-8").strip().splitlines()[-1])
    want = ["deny"] * (len(cases) - 4) + ["allow"] * 4
    bad = [(c[0], c[1], g) for c, g, w in zip(cases, got, want) if g != w]
    assert len(got) == len(cases) and not bad, bad[:5]
