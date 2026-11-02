#!/usr/bin/env bash
# TG Kit installer for macOS and Linux.  Run:  bash install.sh
# (On a Mac you can double-click Install-Mac.command instead.)
# It creates a private Python environment in this folder (.venv), installs TG Kit into it,
# links the tgkit command into ~/.local/bin, registers the Claude Code plugin if Claude is
# installed, and starts the first-time setup.
# Set TGKIT_NO_SYSTEM_CHANGES=1 to skip the link, the PATH line and Claude.
set -euo pipefail

KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$KIT/.venv"

say() { printf '%s\n' "$*"; }
fail() { printf '\n%s\n' "$*" >&2; exit 1; }

say "Installing TG Kit..."
say ""

PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3 python; do
  if command -v "$c" >/dev/null 2>&1 \
     && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    PY="$c"
    break
  fi
done
if [ -z "$PY" ]; then
  if [ "$(uname)" = "Darwin" ]; then
    fail "TG Kit needs Python 3.10 or newer (free). Download it from https://www.python.org/downloads/ (the big yellow button), install it, then run this again."
  fi
  fail "TG Kit needs Python 3.10 or newer. Install it, for example: sudo apt install python3 python3-venv   then run this again."
fi

say "Step 1 of 3: preparing a private Python for TG Kit (in $VENV)..."
if [ ! -x "$VENV/bin/python" ]; then
  "$PY" -m venv "$VENV" \
    || fail "Could not create $VENV. On Ubuntu or Debian run: sudo apt install python3-venv   then run this again."
fi

say "Step 2 of 3: installing TG Kit (needs internet, about 1 minute)..."
"$VENV/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade pip || true
# "[app]" adds the TG Kit window (pywebview); without it the window opens in the web browser.
if ! "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade "$KIT[app]"; then
  say "(The window part could not be installed; TG Kit will open in your web browser instead.)"
  "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade "$KIT" \
    || fail "Installing failed. Check your internet connection and run this again."
fi
VERSION="$("$VENV/bin/python" -c 'import tgkit; print(tgkit.__version__)')"

if [ -z "${TGKIT_NO_SYSTEM_CHANGES:-}" ]; then
  say "Step 3 of 3: making the tgkit command available..."
  mkdir -p "$HOME/.local/bin"
  ln -sf "$VENV/bin/tgkit" "$HOME/.local/bin/tgkit"
  case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *)
      if [ "$(uname)" = "Darwin" ]; then rcs="$HOME/.zshrc $HOME/.bash_profile"; else rcs="$HOME/.bashrc $HOME/.profile"; fi
      for rc in $rcs; do
        grep -qs '.local/bin' "$rc" || printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$rc"
      done
      export PATH="$HOME/.local/bin:$PATH"
      ;;
  esac
  if command -v claude >/dev/null 2>&1; then
    say "Adding TG Kit to Claude Code..."
    { claude plugin marketplace add "$KIT" && claude plugin install tgkit@tgkit; } >/dev/null 2>&1 \
      || say "Could not add it to Claude automatically. In Claude Code type:  /plugin marketplace add $KIT   and then:  /plugin install tgkit@tgkit"
  else
    say "(Claude Code is not installed: skipped. See README, part \"Use with Claude Code\".)"
  fi
fi

say ""
if [ "$("$VENV/bin/python" -c 'from tgkit import config; print(int(config.logged_in(config.home())))' 2>/dev/null)" = "1" ]; then
  # Running this again is how you update: your data is kept and the setup is skipped.
  say "TG Kit $VERSION is installed. You are already logged in, so the setup is skipped."
else
  say "Installed TG Kit $VERSION! The first-time setup starts now."
  say ""
  "$VENV/bin/tgkit" setup || true
fi
say ""
say "Next time, open a new terminal window and type: tgkit app   (the TG Kit window)"
say "or just: tgkit   (a simple menu)"
