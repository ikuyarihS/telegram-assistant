"""A stand-in for `npm install -g @google/gemini-cli`: lays out what npm leaves in its
global folder (FAKE_NPM_PREFIX): the gemini command and the package with its script.
FAKE_NPM_MODE=fail exits 1; FAKE_NPM_MODE=nothing exits 0 but installs nothing."""

import json
import os
import sys
from pathlib import Path

mode = os.environ.get("FAKE_NPM_MODE")
if mode == "fail":
    print("npm ERR! network request failed", file=sys.stderr)
    sys.exit(1)
if sys.argv[1:] != ["install", "-g", "@google/gemini-cli"]:
    print("unexpected arguments: " + " ".join(sys.argv[1:]), file=sys.stderr)
    sys.exit(2)
if mode != "nothing":
    prefix = Path(os.environ["FAKE_NPM_PREFIX"])
    pkg = prefix / "node_modules" / "@google" / "gemini-cli"
    (pkg / "bundle").mkdir(parents=True, exist_ok=True)
    (pkg / "package.json").write_text(json.dumps({"name": "@google/gemini-cli", "bin": {"gemini": "bundle/gemini.js"}}),
                                      encoding="utf-8")
    (pkg / "bundle" / "gemini.js").write_text("// gemini\n", encoding="utf-8")
    (prefix / ("gemini.cmd" if os.name == "nt" else "gemini")).write_text("@echo off\n", encoding="utf-8")
print("added 1 package")
