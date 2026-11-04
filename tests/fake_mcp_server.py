"""A stand-in MCP connector (a notes app) on stdio, or on streamable HTTP with --http
(prints its port first; --sse answers as an event stream). Nothing real is reached.

FAKE_MCP_LOG    one JSON line per tools/call that reached it
FAKE_MCP_LIVE   a JSON file {tool: {annotations}} read at every tools/list (the server changed)
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOOLS = [
    {"name": "search_notes", "description": "Find notes with these words.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
     "annotations": {"readOnlyHint": True}},
    {"name": "get_note", "description": "One note by id.",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    {"name": "delete_note", "description": "Delete a note.",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}}},
    {"name": "tidyUp", "description": "Tidy the notes.", "inputSchema": {"type": "object"},
     "annotations": {"destructiveHint": True}},
    {"name": "sendEmail", "description": "Mail a note.", "inputSchema": {"type": "object"}},
]
NOTES = {"n1": "Lunch with Lan on Friday 12:00", "n2": "Buy milk"}


def tools():
    out = [dict(t) for t in TOOLS]
    live = os.environ.get("FAKE_MCP_LIVE")
    if live and os.path.exists(live):
        with open(live, encoding="utf-8") as fh:
            changes = json.load(fh)
        out = [{**t, "annotations": changes[t["name"]]} if t["name"] in changes else t for t in out]
        out = [t for t in out if changes.get(t["name"]) != "gone"]
    return out


def handle(msg):
    method, params = msg.get("method"), msg.get("params") or {}
    if "id" not in msg:
        return None
    if method == "initialize":
        result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "fake-notes", "version": "1.0"}}
    elif method == "tools/list":
        result = {"tools": tools()}
    elif method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if os.environ.get("FAKE_MCP_LOG"):
            with open(os.environ["FAKE_MCP_LOG"], "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"tool": name, "args": args}, ensure_ascii=False) + "\n")
        if name == "search_notes":
            hits = [f"{k}: {v}" for k, v in NOTES.items() if str(args.get("query", "")).lower() in v.lower()]
            result = {"content": [{"type": "text", "text": "\n".join(hits) or "no notes"}], "isError": False}
        elif name == "get_note":
            result = {"content": [{"type": "text", "text": NOTES.get(str(args.get("id")), "no such note")}],
                      "isError": False}
        else:
            result = {"content": [{"type": "text", "text": f"{name} done"}], "isError": False}
    else:
        result = {}
    return {"jsonrpc": "2.0", "id": msg["id"], "result": result}


class Http(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        rep = handle(json.loads(body))
        if rep is None:
            self.send_response(202)
            self.end_headers()
            return
        sse = "--sse" in sys.argv
        data = (f"event: message\ndata: {json.dumps(rep)}\n\n" if sse else json.dumps(rep)).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream" if sse else "application/json")
        self.send_header("Mcp-Session-Id", "fake-session-1")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if "--http" in sys.argv:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Http)
    print(httpd.server_address[1], flush=True)
    httpd.serve_forever()
else:
    for raw in sys.stdin:
        try:
            rep = handle(json.loads(raw))
        except ValueError:
            continue
        if rep is not None:
            sys.stdout.write(json.dumps(rep) + "\n")
            sys.stdout.flush()
