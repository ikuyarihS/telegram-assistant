"""Turn what the user typed in the app into a TG Kit query. Rules only, no AI.

    "tuần này nhóm Beta bàn gì?"   -> summary, chat Beta, last 7 days
    "my open tasks"                -> tasks, mine
    "find bien ban hop"            -> search "bien ban hop"

Everything is matched on folded words (lower case, no accents), so typing
with or without Vietnamese accents gives the same result.
"""

import re
import unicodedata
from dataclasses import asdict, dataclass, field

from .store import fold, has_accents

KINDS = ("search", "summary", "tasks", "sync", "chats", "help")


@dataclass
class Intent:
    kind: str
    query: str | None = None
    chat: str | None = None          # folder name of the matched chat
    chat_title: str | None = None
    since: str | None = None         # text for store.parse_since: 7d, today, all, 2026-09-01
    which: str = "open"              # tasks: open, mine, all
    lang: str = "en"
    ambiguous_chats: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ------------------------------------------------------------------ word tables

def _p(*phrases: str) -> list[tuple[str, ...]]:
    return [tuple(p.split()) for p in phrases]


_TIME = [
    (_p("hom nay", "today", "hnay", "sang nay", "this morning", "chieu nay"), "today"),
    (_p("hom qua", "yesterday"), "yesterday"),
    (_p("tuan nay", "this week", "tuan qua", "past week", "trong tuan"), "7d"),
    (_p("tuan truoc", "last week", "tuan roi"), "14d"),
    (_p("thang nay", "this month", "thang qua", "past month", "trong thang"), "30d"),
    (_p("thang truoc", "last month", "thang roi"), "60d"),
    (_p("nam nay", "this year"), "365d"),
    (_p("tat ca thoi gian", "all time", "tu dau", "from the start"), "all"),
]
_UNITS = {
    "ngay": "d", "day": "d", "days": "d",
    "tuan": "w", "week": "w", "weeks": "w",
    "gio": "h", "tieng": "h", "hour": "h", "hours": "h",
    "thang": "m", "month": "m", "months": "m",
}
_TIME_LEAD = {"last", "past", "trong", "since", "tu", "from", "within"}
_TIME_TAIL = {"qua", "gan", "day", "ago", "nay", "roi"}

_SEARCH = _p("tim kiem", "tim", "kiem", "find", "search", "look for", "lookup", "ai noi", "ai nhac",
             "ai gui", "who said", "who mentioned", "who sent", "where is", "o dau", "mention", "mentioned")
_SYNC = _p("sync", "dong bo", "cap nhat", "update", "refresh", "lay tin moi", "tai tin moi",
           "get new messages", "fetch")
_SUMMARY = _p("tom tat", "tom luoc", "summary", "summarize", "summarise", "recap", "tong hop",
              "tong ket", "digest")
_TASKS = _p("viec can lam", "cong viec", "nhiem vu", "han chot", "task", "tasks", "todo", "todos",
            "to-do", "to-dos", "deadline", "deadlines", "viec")
_SUMMARY_SOFT = _p("ban gi", "noi gi", "co gi moi", "co gi", "chuyen gi", "what happened", "whats new",
                   "what's new", "news", "discuss", "discussed", "dien ra", "trao doi", "thao luan",
                   "happening", "going on")
_CHATS = _p("which chats", "my chats", "list chats", "list my chats", "danh sach nhom", "danh sach chat",
            "cac nhom", "nhung nhom nao")
_HELP = _p("help", "huong dan", "lam duoc gi", "what can you do", "how do i", "giup gi")
_MINE = _p("my", "mine", "for me", "cua toi", "cua em", "cua minh", "cho toi", "cho em", "toi phai",
           "em phai", "minh phai", "giao cho toi")
_ALL = _p("all", "tat ca", "everything", "ca da xong", "done ones")

# Words that carry no search meaning on their own.
_STOP = set("""
a an the of in on at to for from about with and or is are was were be been did do does me my mine i we
you your please pls any anything some something show tell give list messages message msg msgs chat chats
group groups file files that this these those there what which who whom where when how why can could
would should open new latest recent
tim kiem cho toi minh em anh chi giup hay nhe nha di ve cac nhung tin nhan trong nhom kenh o co
khong ko gi la cua voi nao xem thay duoc oi a ai noi nhac den gui biet know hoi la sao the vay luc
mot vai nay kia day do tep
""".split())
_CHAT_MARKERS = {"nhom", "group", "chat", "kenh", "channel", "trong", "in", "o", "cua", "team"}
_GENERIC_TITLE = {"nhom", "group", "team", "phong", "kenh", "channel", "chat", "du", "an", "the", "va",
                  "and", "of", "cua", "ban", "cong", "ty", "company", "vn", "official", "chung"}
_VI_HINTS = {"toi", "cua", "nhom", "tuan", "gi", "khong", "viec", "tim", "tom", "tat", "hom", "nay",
             "em", "anh", "chi", "minh", "nhe", "oi", "ngay", "thang", "nhung", "cac", "hay", "giup",
             "co", "duoc", "ve", "tin", "nhan", "qua", "chua"}

_WORD = re.compile(r"[^\s,;!?()\[\]{}\"“”‘’«»]+")
_QUOTED = re.compile(r"[\"“”«»]([^\"“”«»]{2,})[\"“”«»]")


def _tokens(text: str) -> list[tuple[str, str]]:
    """(original word, folded word) pairs; trailing . : ' trimmed from both."""
    out = []
    for m in _WORD.finditer(text):
        orig = m.group(0).strip(".:'")
        if orig:
            out.append((orig, fold(orig)))
    return out


def _find(ft: list[str], phrase: tuple[str, ...], used: set) -> int:
    n = len(phrase)
    for i in range(len(ft) - n + 1):
        if tuple(ft[i:i + n]) == phrase and not any(j in used for j in range(i, i + n)):
            return i
    return -1


def _take(ft: list[str], phrases, used: set) -> bool:
    """Mark the first phrase found as used; True when one was found."""
    for ph in sorted(phrases, key=len, reverse=True):
        i = _find(ft, ph, used)
        if i >= 0:
            used.update(range(i, i + len(ph)))
            return True
    return False


def _has(ft: list[str], phrases, used: set) -> bool:
    return any(_find(ft, ph, used) >= 0 for ph in phrases)


# ------------------------------------------------------------------ parts


def _time(ft: list[str], used: set) -> str | None:
    for i, w in enumerate(ft):
        if i in used:
            continue
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", w):
            used.add(i)
            if i > 0 and ft[i - 1] in _TIME_LEAD:
                used.add(i - 1)
            return w
        m = re.fullmatch(r"(\d{1,3})([dwh])", w)
        num, unit = (m.group(1), m.group(2)) if m else (None, None)
        if num is None and w.isdigit() and i + 1 < len(ft) and ft[i + 1] in _UNITS:
            num, unit = w, _UNITS[ft[i + 1]]
            used.add(i + 1)
        if num is None:
            continue
        used.add(i)
        if i > 0 and ft[i - 1] in _TIME_LEAD:
            used.add(i - 1)
        j = i + (2 if not m else 1)
        while j < len(ft) and ft[j] in _TIME_TAIL:
            used.add(j)
            j += 1
        n = max(1, min(int(num), 999))
        return f"{n * 30}d" if unit == "m" else f"{n}{unit}"
    for phrases, value in _TIME:
        if _take(ft, phrases, used):
            return value
    return None


def _chat(ft: list[str], used: set, chats: list[tuple[str, str]]):
    """Best matching chat as (folder, title, tied) or None. Scores whole folded words."""
    scored = []
    for folder, title in chats:
        tt = [f for _, f in _tokens(title or folder.replace("_", " "))]
        if not tt:
            continue
        hit, score = set(), 0
        for i, w in enumerate(ft):
            if i in used or w in _GENERIC_TITLE:
                continue
            if w in tt and len(w) >= 3:
                hit.add(i)
                score += len(w)
        for a, b in zip(tt, tt[1:]):
            for i in range(len(ft) - 1):
                if ft[i] == a and ft[i + 1] == b and i not in used and i + 1 not in used:
                    hit.update((i, i + 1))
                    score += len(a) + len(b) + 1
        if score:
            scored.append((score, folder, title, hit))
    if not scored:
        return None
    scored.sort(key=lambda s: -s[0])
    best = scored[0]
    tied = [s for s in scored if s[0] == best[0]]
    if len(tied) > 1:
        for s in tied:
            used.update(s[3])
        return None, None, [s[2] for s in tied]
    used.update(best[3])
    first = min(best[3])
    if first > 0 and ft[first - 1] in _CHAT_MARKERS:
        used.add(first - 1)
    return best[1], best[2], []


def _lang(text: str, ft: list[str]) -> str:
    if has_accents(text) or any(w in _VI_HINTS for w in ft):
        return "vi"
    return "en"


def _query(toks: list[tuple[str, str]], used: set) -> str:
    words = [o for i, (o, f) in enumerate(toks) if i not in used and f not in _STOP]
    return " ".join(words).strip()


# ------------------------------------------------------------------ parse


def parse(text: str, chats: list[tuple[str, str]] | None = None) -> Intent:
    """`chats` is [(folder, title)] of the chats on disk, for name matching."""
    text = unicodedata.normalize("NFC", (text or "").strip())
    toks = _tokens(text)
    ft = [f for _, f in toks]
    used: set = set()
    lang = _lang(text, ft)
    since = _time(ft, used)

    quoted = _QUOTED.search(text)
    found = _chat(ft, used, chats or [])
    chat, title, tied = (found if found else (None, None, []))

    def make(kind: str, **kw) -> Intent:
        return Intent(kind=kind, chat=chat, chat_title=title, since=since, lang=lang,
                      ambiguous_chats=tied, **kw)

    if quoted:
        return make("search", query=quoted.group(1).strip())
    if _take(ft, _SEARCH, used):
        return make("search", query=_query(toks, used) or None)
    if _take(ft, _SYNC, used):
        return make("sync")
    if _take(ft, _SUMMARY, used):
        return make("summary")
    if _has(ft, _TASKS, used):
        _take(ft, _TASKS, used)
        which = "open"
        if _take(ft, _MINE, used):
            which = "mine"
        elif _take(ft, _ALL, used):
            which = "all"
        return make("tasks", which=which)
    if _take(ft, _SUMMARY_SOFT, used):
        return make("summary")
    if _take(ft, _CHATS, used):
        return make("chats")
    if _take(ft, _HELP, used):
        return make("help")
    rest = _query(toks, used)
    if rest:
        return make("search", query=rest)
    if chat or since:
        return make("summary")
    return make("help")
