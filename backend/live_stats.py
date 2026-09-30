"""
Match statistics for basketball, tennis, table tennis and football, from
Sportradar's stats service. SportyBet's event ids are Sportradar's
("sr:match:N"), so each match is read by its own id, no name matching.

    https://stats.fn.sportradar.com/common/en/Etc:UTC/gismo/match_detailsextended/N

answers {"doc": [{"data": {"teams", "index": [stat ids in order], "values":
{id: {"name", "value": {"home", "away"}}}}}]}. Values are counts, or
"a/b/c" strings (made/missed/attempted, won/lost/total), or "a/b" (won/of).
Each sport's SHOWN list picks and orders what the site shows; rows() turns
a doc into [{key, label, h, a, hs?, as?}] (h/a: the numbers the bars use,
hs/as: how they read when not plain numbers).

Kept per day in Redis (KEY) apart from the match days, so the live-score
jobs and this one never write over each other.
"""

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

URL = "https://stats.fn.sportradar.com/common/en/Etc:UTC/gismo/match_detailsextended/{num}"
KEY = "betiq:{sport}:stats:{date}"
TTL = 4 * 86400
EVERY = 90              # seconds between reads of one match
AT_ONCE = 8             # reads at a time
TIMEOUT = 8.0
MAX_PER_TICK = 80       # per sport
MAX_TRIES = 3           # reads of a finished match that fail before it's given up

# (Sportradar's name, our key, label, kind): kinds
#   n: a count · pct: a percentage · made: "made/missed/attempted" · won: "won/of"
#   serve: "in/out/total" (shown as in of total, %) · time: seconds
SHOWN: Dict[str, List[tuple]] = {
    "basketball": [
        ("Ball possession", "possession", "Possession", "pct"),
        ("Two pointers scored", "twos", "2-pointers", "made"),
        ("Three pointers scored", "threes", "3-pointers", "made"),
        ("Free throws scored", "free_throws", "Free throws", "made"),
        ("Rebounds", "rebounds", "Rebounds", "n"),
        ("Total Fouls", "fouls", "Fouls", "n"),
        ("Timeouts", "timeouts", "Timeouts", "n"),
        ("Biggest lead", "biggest_lead", "Biggest lead", "n"),
        ("Time spent in lead", "time_in_lead", "Time in the lead", "time"),
        ("Max Points in a Row", "best_run", "Best run (points)", "n"),
    ],
    "tennis": [
        ("Aces", "aces", "Aces", "n"),
        ("Double Faults", "double_faults", "Double faults", "n"),
        ("1st Serve Successful", "first_serve_in", "1st serve in", "serve"),
        ("1st Serve Pts. Won", "first_serve_won", "Won on 1st serve", "serve"),
        ("2nd Serve Pts. Won", "second_serve_won", "Won on 2nd serve", "serve"),
        ("Break Points Won", "break_points", "Break points won", "won"),
        ("Service Games Won", "service_games", "Service games won", "serve"),
        ("Service Points Won", "service_points", "Service points won", "serve"),
        ("Receiver Points Won", "return_points", "Return points won", "serve"),
        ("Points won", "points", "Total points won", "n"),
        ("Max Points in a Row", "best_run", "Most points in a row", "n"),
        ("Tiebreaks Won", "tiebreaks", "Tiebreaks won", "n"),
    ],
    # Football: the site's football stats keys (results_feed.LIVE_STATS), for
    # matches ESPN gives none for (smaller leagues, friendlies)
    "football": [
        ("Ball possession", "possession", "Possession", "pct"),
        ("Goal attempts", "shots", "Shots", "n"),
        ("Shots on target", "sot", "Shots on target", "n"),
        ("Corner kicks", "corners", "Corners", "n"),
        ("Fouls", "fouls", "Fouls", "n"),
        ("Offsides", "offsides", "Offsides", "n"),
        ("Saves", "saves", "Saves", "n"),
        ("Yellow cards", "yellow", "Yellow cards", "n"),
        ("Red cards", "red", "Red cards", "n"),
        ("Dangerous Attack", "dangerous", "Dangerous attacks", "n"),
    ],
    "table_tennis": [
        ("Points won", "points", "Total points won", "n"),
        ("Service Points Won", "service_points", "Service points won", "serve"),
        ("Receiver Points Won", "return_points", "Return points won", "serve"),
        ("Service errors", "service_errors", "Service errors", "n"),
        ("Biggest lead", "biggest_lead", "Biggest lead", "n"),
        ("Max Points in a Row", "best_run", "Most points in a row", "n"),
        ("Timeouts", "timeouts", "Timeouts", "n"),
        ("Yellow cards", "yellow", "Yellow cards", "n"),
    ],
}

_NUMS = re.compile(r"-?\d+(?:\.\d+)?")


def key(sport: str, date: str) -> str:
    return KEY.format(sport=sport, date=date)


def sr_number(event_id: str) -> Optional[str]:
    """Sportradar's match number in a SportyBet id ("sr:match:75103556"), or
    None for ids that aren't Sportradar's (some SportyBet-only events)."""
    tail = str(event_id or "").rsplit(":", 1)[-1]
    return tail if tail.isdigit() and len(tail) <= 10 else None


def _nums(v: Any) -> List[float]:
    if isinstance(v, bool) or v is None:
        return []
    if isinstance(v, (int, float)):
        return [float(v)]
    return [float(x) for x in _NUMS.findall(str(v))]


def _fmt(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else f"{x:g}"


def _side(v: Any, kind: str) -> Optional[tuple]:
    """(the bar's number, how it reads or None) for one side."""
    n = _nums(v)
    if not n:
        return None
    if kind == "n":
        return n[0], None
    if kind == "pct":
        return n[0], f"{_fmt(n[0])}%"
    if kind == "time":
        s = int(n[0])
        return float(s), f"{s // 60}:{s % 60:02d}"
    if kind in ("made", "serve"):
        # made/missed/attempted (serve: in/out/total, won/lost/total)
        if len(n) >= 3:
            got, total = n[0], n[2]
        elif len(n) == 2:
            got, total = n[0], n[0] + n[1]
        else:
            return n[0], None
        text = f"{_fmt(got)}/{_fmt(total)}" + (f" ({round(100 * got / total)}%)" if total else "")
        return got, text
    if kind == "won":
        if len(n) >= 2:
            return n[0], f"{_fmt(n[0])}/{_fmt(n[1])}"
        return n[0], None
    return n[0], None


def rows(data: Any, sport: str) -> List[Dict[str, Any]]:
    """The stats the site shows for one match, in SHOWN's order."""
    if not isinstance(data, dict):
        return []
    by_name = {}
    for v in (data.get("values") or {}).values():
        if isinstance(v, dict) and v.get("name") and isinstance(v.get("value"), dict):
            by_name[str(v["name"]).strip().lower()] = v["value"]
    out = []
    for name, k, label, kind in SHOWN.get(sport, []):
        val = by_name.get(name.lower())
        if not val:
            continue
        h, a = _side(val.get("home"), kind), _side(val.get("away"), kind)
        if h is None or a is None:
            continue
        row: Dict[str, Any] = {"key": k, "label": label, "h": h[0], "a": a[0]}
        if h[1] is not None or a[1] is not None:
            row["hs"], row["as"] = h[1] or _fmt(h[0]), a[1] or _fmt(a[0])
        out.append(row)
    return out


def from_score(periods: Any, sport: str) -> List[Dict[str, Any]]:
    """For matches Sportradar has no stats for (SportyBet's own events, like
    the Setka Cup): what the game-by-game score shows. Table tennis: points
    and games won, the biggest game win; tennis: games and sets won."""
    try:
        ps = [(int(h), int(a)) for h, a in periods or []]
    except (TypeError, ValueError):
        return []
    if not ps:
        return []
    th, ta = sum(h for h, _ in ps), sum(a for _, a in ps)
    wh, wa = sum(1 for h, a in ps if h > a), sum(1 for h, a in ps if a > h)
    if sport == "table_tennis":
        mh = max((h - a for h, a in ps if h > a), default=0)
        ma = max((a - h for h, a in ps if a > h), default=0)
        total = th + ta
        return [{"key": "points", "label": "Total points won", "h": th, "a": ta,
                 "hs": f"{th} ({round(100 * th / total)}%)" if total else str(th),
                 "as": f"{ta} ({round(100 * ta / total)}%)" if total else str(ta)},
                {"key": "games", "label": "Games won", "h": wh, "a": wa},
                {"key": "best_game", "label": "Biggest game win (points)", "h": mh, "a": ma}]
    if sport == "tennis":
        return [{"key": "games", "label": "Total games won", "h": th, "a": ta},
                {"key": "sets", "label": "Sets won", "h": wh, "a": wa}]
    return []


def football_stats(rows: List[Dict[str, Any]]) -> Optional[Dict[str, List[float]]]:
    """Football rows in the shape ESPN's stats have on the site ({key: [home, away]})."""
    return {r["key"]: [r["h"], r["a"]] for r in rows} or None if rows else None


def doc_data(body: Any) -> Any:
    try:
        return (body.get("doc") or [{}])[0].get("data")
    except (AttributeError, IndexError):
        return None


_client: List[Any] = []


def _http():
    """One kept-alive client for every read (no new TLS handshake each time)."""
    if not _client:
        import httpx
        _client.append(httpx.AsyncClient(timeout=TIMEOUT, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/131.0 Safari/537.36",
            "Accept": "application/json", "Referer": "https://www.sportybet.com/"}))
    return _client[0]


async def read(num: str, sport: str, get=None) -> Optional[List[Dict[str, Any]]]:
    """One match's rows; None when it couldn't be read."""
    try:
        if get is None:
            resp = await asyncio.wait_for(_http().get(URL.format(num=num)), TIMEOUT)
            if resp.status_code != 200:
                return None
            body = resp.json()
        else:
            body = await get(URL.format(num=num))
    except Exception:
        return None
    data = doc_data(body)
    return rows(data, sport) if isinstance(data, dict) else []


async def fetch(event_ids: Iterable[str], sport: str, get=None) -> Dict[str, Optional[List[Dict]]]:
    """{event id: rows (None: unreadable)} for Sportradar-numbered events, AT_ONCE at a time."""
    sem = asyncio.Semaphore(AT_ONCE)
    ids = [e for e in event_ids if sr_number(e)]

    async def one(eid: str):
        async with sem:
            return eid, await read(sr_number(eid), sport, get)
    return dict(await asyncio.gather(*(one(e) for e in ids)))


def due(entries: Iterable[Dict], stored: Dict[str, Dict], now: datetime, live: str = "live",
        finished: str = "finished") -> List[str]:
    """The ids to read now, live ones first: live matches not read in the
    last EVERY seconds, then finished ones without their full-match numbers
    (newest first; each tried at most MAX_TRIES times)."""
    playing, done = [], []
    for e in entries:
        eid = e.get("id")
        res = e.get("result") or {}
        st = res.get("status")
        if not eid or not sr_number(eid) or st not in (live, finished):
            continue
        have = stored.get(eid) or {}
        if st == finished:
            if have.get("final") or int(have.get("tries") or 0) >= MAX_TRIES:
                continue
            done.append((res.get("at") or "", eid))
            continue
        try:
            last = datetime.fromisoformat(have["at"]) if have.get("at") else None
        except ValueError:
            last = None
        if last and (now - last).total_seconds() < EVERY:
            continue
        playing.append(eid)
    done.sort(reverse=True)
    return (playing + [eid for _, eid in done])[:MAX_PER_TICK]


def load(r, sport: str, date: str) -> Dict[str, Dict]:
    try:
        raw = r.hgetall(key(sport, date)) if r else {}
    except Exception:
        return {}
    out = {}
    for k, v in (raw or {}).items():
        try:
            out[k.decode() if isinstance(k, bytes) else k] = json.loads(v)
        except Exception:
            continue
    return out


def save(r, sport: str, date: str, got: Dict[str, Dict]) -> None:
    if not r or not got:
        return
    k = key(sport, date)
    pipe = r.pipeline(transaction=False)
    pipe.hset(k, mapping={eid: json.dumps(v, separators=(",", ":")) for eid, v in got.items()})
    pipe.expire(k, TTL)
    pipe.execute()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
