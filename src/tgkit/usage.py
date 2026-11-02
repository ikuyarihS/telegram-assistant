"""Token use of Claude mode answers, kept in the data folder.

    <data>/usage.jsonl   one line per Claude answer: when, which conversation,
                         the model, input / output / cache tokens, and the cost
                         only when Claude Code reported one (its own estimate)

Only Claude mode answers use tokens. Search, summary, tasks and sync run on
this computer and use none, so they are never written here.
"""

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

FILE = "usage.jsonl"
KINDS = ("input", "output", "cache_read", "cache_write")
_LOCK = threading.Lock()


def path(base: Path) -> Path:
    return Path(base) / FILE


def _int(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def from_result(msg: dict) -> dict | None:
    """The usage of one answer, from Claude Code's stream-json `result` event."""
    u = msg.get("usage") if isinstance(msg, dict) else None
    if not isinstance(u, dict) or not u:
        return None
    out = {
        "input": _int(u.get("input_tokens")),
        "output": _int(u.get("output_tokens")),
        "cache_read": _int(u.get("cache_read_input_tokens")),
        "cache_write": _int(u.get("cache_creation_input_tokens")),
    }
    cost = msg.get("total_cost_usd")
    if isinstance(cost, (int, float)) and not isinstance(cost, bool):
        out["cost_usd"] = round(float(cost), 6)
    models = msg.get("modelUsage")
    if isinstance(models, dict) and models:
        out["models"] = [str(k) for k in models]
    return out


def tokens(u: dict) -> int:
    """Every token kind together: what the daily limit counts."""
    return sum(_int(u.get(k)) for k in KINDS)


def record(base: Path, u: dict, conversation: str | None = None, model: str | None = None) -> dict:
    """Append one answer's usage. One write of one line, so a reader never sees half."""
    entry = {"at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
             "conversation": conversation, "model": model}
    entry.update({k: _int(u.get(k)) for k in KINDS})
    if "cost_usd" in u:
        entry["cost_usd"] = u["cost_usd"]
    line = (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8")
    p = path(base)
    p.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
    return entry


def entries(base: Path) -> list[dict]:
    """Every saved line. A damaged line is skipped: usage is information, not data."""
    p = path(base)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict) and e.get("at"):
            out.append(e)
    return out


def _local(iso: str) -> datetime | None:
    try:
        return datetime.fromisoformat(iso).astimezone()
    except (TypeError, ValueError):
        return None


def _empty() -> dict:
    return {"answers": 0, "tokens": 0, **{k: 0 for k in KINDS}}


def _add(acc: dict, e: dict) -> None:
    acc["answers"] += 1
    for k in KINDS:
        acc[k] += _int(e.get(k))
    acc["tokens"] = sum(acc[k] for k in KINDS)
    if isinstance(e.get("cost_usd"), (int, float)):
        acc["cost_usd"] = round(acc.get("cost_usd", 0.0) + float(e["cost_usd"]), 6)


def totals(base: Path, conversation: str | None = None, now: datetime | None = None) -> dict:
    """Today, this month (local time), every day of this month, and one conversation."""
    now = (now or datetime.now()).astimezone()
    today, month = now.date(), (now.year, now.month)
    out = {"today": _empty(), "month": _empty(), "all": _empty(), "days": {},
           "conversation": _empty() if conversation else None}
    for e in entries(base):
        when = _local(e.get("at"))
        if when is None:
            continue
        _add(out["all"], e)
        if (when.year, when.month) == month:
            _add(out["month"], e)
            day = when.date().isoformat()
            _add(out["days"].setdefault(day, _empty()), e)
        if when.date() == today:
            _add(out["today"], e)
        if conversation and e.get("conversation") == conversation:
            _add(out["conversation"], e)
    out["cost_note"] = "cost_usd is Claude Code's own estimate; on a Claude plan you are not billed per answer."
    out["what_uses_tokens"] = ("Only answers written by Claude use tokens. Search, summary, tasks and "
                               "getting new messages run on this computer and use none.")
    return out


def limit_state(base: Path, cfg: dict) -> dict:
    """The daily limit the user set (0 = none), today's use, and whether it is reached."""
    limit = _int(cfg.get("usage_daily_limit"))
    used = totals(base)["today"]["tokens"] if limit else 0
    return {"limit": limit, "used": used, "reached": bool(limit) and used >= limit,
            "hard": bool(cfg.get("usage_hard_stop")) and bool(limit)}
