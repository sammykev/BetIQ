"""
Final scores from Flashscore for the basketball, tennis and table tennis
legs neither SportyBet nor Sportradar has a score for.

Flashscore's day feeds list every match of a sport on a day (UTC), -7 to
+7 days from today:

    https://local-global.flashscore.ninja/2/x/feed/f_{sport}_{day offset}_0_en_1
    (header x-fsign; sport 3 basketball, 2 tennis, 25 table tennis)

Records are split by "~", fields by "¬", each field "key÷value". A match is
AA÷id with AD÷kick-off (unix), AB÷3 once finished, AC÷how it ended (3
regulation, 10 after overtime, 8/9 retired/walkover), AE/AF the home/away
names (WU/WV their slugs), AG/AH the score (sets for tennis) and BA/BB,
BC/BD, BE/BF, BG/BH, BI/BJ the periods (quarters then overtime; set games).

Like sr_results: a leg the grader finds no result for is noted (want()),
the sweep looks it up in the day feeds (refresh()) and keeps the final
(KEY), which the grader reads next time (stored()). The feeds only go back
7 days, so a leg older than that is dropped.
"""

import asyncio
import json
import re
import time
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, List, Optional

BASE = "https://local-global.flashscore.ninja/2/x/feed/"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/131.0 Safari/537.36",
           "x-fsign": "SW9D1eZo", "Referer": "https://www.flashscore.com/", "Accept": "*/*"}
KEY = "betiq:fs:results"          # hash {leg key: settle-format result}
WANTED_KEY = "betiq:fs:wanted"    # hash {leg key: {"sport", "date", "time", "home", "away", "since", "tries"}}
TTL = 45 * 86400
MAX_TRIES = 40
FEED_DAYS = 7                     # how far back the day feeds go
KICKOFF_SLACK = 36 * 3600         # a listed kick-off this far from the leg's still matches
TIMEOUT = 15.0
SPORT_IDS = {"basketball": [3], "tennis": [2], "table_tennis": [25], "racket": [2, 25]}
PERIODS = [("BA", "BB"), ("BC", "BD"), ("BE", "BF"), ("BG", "BH"), ("BI", "BJ")]
REGULATION, AFTER_OT, RETIRED, WALKOVER = "3", "10", "8", "9"
# Words in club names that say nothing about which club
GENERIC = {"basket", "basketball", "baloncesto", "pallacanestro", "club", "feminin", "women", "sad", "team",
           "the", "and", "city", "united", "sport", "sports", "academy", "academie"}


def leg_key(sport: str, day: str, home: str, away: str) -> str:
    return f"{sport}|{day}|{home}|{away}"


def records(text: str) -> List[Dict[str, str]]:
    """The feed's matches, each as {field: value}."""
    out = []
    for rec in (text or "").split("~"):
        if not rec.startswith("AA÷"):
            continue
        out.append(dict(x.split("÷", 1) for x in rec.split("¬") if "÷" in x))
    return out


def _int(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _pairs(f: Dict[str, str]) -> List[List[int]]:
    out = []
    for h, a in PERIODS:
        ph, pa = _int(f.get(h)), _int(f.get(a))
        if ph is None or pa is None:
            break
        out.append([ph, pa])
    return out


def parse(f: Dict[str, str], sport: str) -> Optional[Dict]:
    """The match's final in the grader's format for its sport; None when it
    isn't over, was called off, or ended some way not read here."""
    if f.get("AB") != "3":
        return None
    final = (_int(f.get("AG")), _int(f.get("AH")))
    if None in final:
        return None
    ended = f.get("AC")
    ps = _pairs(f)
    if sport == "basketball":
        if ended not in (REGULATION, AFTER_OT):
            return None
        q = ps[:4]
        reg = (sum(h for h, _ in q), sum(a for _, a in q)) if len(q) == 4 else None
        ot = ended == AFTER_OT or len(ps) > 4 or (reg is not None and reg != final)
        return {"status": "finished", "final": list(final), "periods": q, "ot": bool(ot)}
    if ended not in (REGULATION, RETIRED, WALKOVER):
        return None
    ret = ended != REGULATION
    games = ps
    if not ret and games and len(games) != final[0] + final[1]:
        games = []
    return {"status": "finished", "sets": list(final), "games": games or None, "ret": ret}


def _tokens(*names: str) -> set:
    words = re.sub(r"[^a-z0-9]+", " ", " ".join(n or "" for n in names).lower()).split()
    return {w for w in words if len(w) >= 3 and w not in GENERIC}


def find(recs: List[Dict[str, str]], home: str, away: str, kickoff: float) -> Optional[Dict[str, str]]:
    """The listed match with both sides' names (a word in common each) kicking
    off nearest the leg's time; None when there's none or two fit equally."""
    th, ta = _tokens(home), _tokens(away)
    if not th or not ta:
        return None
    fits = []
    for f in recs:
        ko = _int(f.get("AD"))
        if ko is None or abs(ko - kickoff) > KICKOFF_SLACK:
            continue
        if th & _tokens(f.get("AE"), f.get("CX"), f.get("WU", "").replace("-", " ")) and \
                ta & _tokens(f.get("AF"), f.get("FK"), f.get("WV", "").replace("-", " ")):
            fits.append((abs(ko - kickoff), f))
    fits.sort(key=lambda x: x[0])
    if not fits or (len(fits) > 1 and fits[1][0] == fits[0][0] and fits[1][1].get("AA") != fits[0][1].get("AA")):
        return None
    return fits[0][1]


def _kickoff(day: str, hhmm: Optional[str]) -> Optional[float]:
    try:
        return datetime.fromisoformat(f"{day}T{(hhmm or '12:00')[:5]}:00+00:00").timestamp()
    except (TypeError, ValueError):
        return None


def _json(raw) -> Optional[Dict]:
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def stored(r, key: str) -> Optional[Dict]:
    if not r:
        return None
    try:
        return _json(r.hget(KEY, key))
    except Exception:
        return None


def want(r, leg: Dict, sport: str) -> None:
    """Note a leg whose result the grader couldn't find (once)."""
    if not r or sport not in SPORT_IDS or not leg.get("home") or not leg.get("away") or not leg.get("date"):
        return
    try:
        r.hsetnx(WANTED_KEY, leg_key(sport, leg["date"], leg["home"], leg["away"]), json.dumps({
            "sport": sport, "date": leg["date"], "time": leg.get("time"), "home": leg["home"],
            "away": leg["away"], "since": int(time.time()), "tries": 0}))
    except Exception:
        pass


async def _read(sport_id: int, offset: int, get: Optional[Callable] = None) -> Optional[str]:
    url = f"{BASE}f_{sport_id}_{offset}_0_en_1"
    try:
        if get is not None:
            return await get(url)
        import httpx
        async with httpx.AsyncClient(timeout=TIMEOUT, headers=HEADERS) as c:
            resp = await c.get(url)
        return resp.text if resp.status_code == 200 else None
    except Exception:
        return None


async def refresh(r, get: Optional[Callable] = None, now: Optional[float] = None) -> Dict[str, int]:
    """Look the noted legs up in the day feeds: finals kept, the rest tried
    again next time (dropped after MAX_TRIES or once their day has left the feeds)."""
    now = time.time() if now is None else now
    today = datetime.fromtimestamp(now, timezone.utc).date()
    wanted = {}
    for k, v in (r.hgetall(WANTED_KEY) or {}).items():
        k = k.decode() if isinstance(k, bytes) else k
        w = _json(v) or {}
        try:
            offset = (date.fromisoformat(w.get("date") or "") - today).days
        except ValueError:
            offset = None
        if offset is None or offset < -FEED_DAYS or w.get("tries", 0) >= MAX_TRIES:
            r.hdel(WANTED_KEY, k)
            continue
        wanted[k] = (w, offset)
    feeds: Dict[tuple, Optional[str]] = {}
    for w, offset in wanted.values():
        for sid in SPORT_IDS[w["sport"]]:
            for o in (offset - 1, offset, offset + 1):
                if -FEED_DAYS <= o <= 0:
                    feeds.setdefault((sid, o), None)
    texts = await asyncio.gather(*(_read(sid, o, get) for sid, o in feeds))
    recs = {k: records(t) for k, t in zip(feeds, texts) if t}
    out = {"asked": len(wanted), "found": 0, "feeds": len(feeds), "unread": len(feeds) - len(recs)}
    for k, (w, offset) in wanted.items():
        listed = [f for sid in SPORT_IDS[w["sport"]] for o in (offset - 1, offset, offset + 1)
                  for f in recs.get((sid, o), [])]
        ko = _kickoff(w["date"], w.get("time"))
        f = find(listed, w["home"], w["away"], ko) if ko is not None else None
        sport = w["sport"] if w["sport"] == "basketball" else "racket"
        res = parse(f, sport) if f else None
        if res:
            r.hset(KEY, k, json.dumps(res, separators=(",", ":")))
            r.hdel(WANTED_KEY, k)
            out["found"] += 1
        else:
            r.hset(WANTED_KEY, k, json.dumps({**w, "tries": w.get("tries", 0) + 1}))
    if out["found"]:
        r.expire(KEY, TTL)
    return out
