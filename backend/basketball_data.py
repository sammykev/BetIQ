"""
Basketball history and fixtures from SportyBet (probe_basketball.py found
what it offers):

- Results: /factsCenter/eventResultList, one day per request (a longer
  window is refused), going back about RESULT_DAYS. Each game has its final
  score (overtime included), the quarters and its competition — every
  league SportyBet covers, under the same names as its fixtures, so no name
  matching is needed between history and the matches we price.
- Fixtures: the upcoming listing with every line of our markets
  (basketball_markets.LISTING_MARKETS).

Results are kept in Redis per day (RESULTS_KEY, a hash: date -> the day's
games, compressed), collected a day at a time: yesterday and today on each
run, and older days (a backfill) a few at a time until RESULT_DAYS are in.
"""

import asyncio
import base64
import json
import re
import zlib
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import basketball_model as bm

BASKETBALL = "sr:sport:2"
RESULTS_KEY = "betiq:bb:results"        # hash: YYYY-MM-DD -> encoded games
RESULT_DAYS = 300                      # how far back SportyBet keeps results
BACKFILL_PER_RUN = 40                  # older days fetched per collection run (all ~300 in a few hours)
MIN_LEAGUE_GAMES = 20                  # a competition needs this many to be rated
# Mixed teams, no league table: not rated
_NOT_A_LEAGUE = re.compile(r"friendl|all[- ]star|exhibition|3x3|bskt cup|summer league", re.I)

FLAGS = {"USA": "🇺🇸", "Spain": "🇪🇸", "Italy": "🇮🇹", "France": "🇫🇷", "Germany": "🇩🇪", "Greece": "🇬🇷",
         "Turkiye": "🇹🇷", "Turkey": "🇹🇷", "Australia": "🇦🇺", "China": "🇨🇳", "Japan": "🇯🇵", "Israel": "🇮🇱",
         "Lithuania": "🇱🇹", "Serbia": "🇷🇸", "Argentina": "🇦🇷", "Brazil": "🇧🇷", "Mexico": "🇲🇽", "Poland": "🇵🇱",
         "Czechia": "🇨🇿", "Slovakia": "🇸🇰", "Sweden": "🇸🇪", "Finland": "🇫🇮", "Norway": "🇳🇴", "Denmark": "🇩🇰",
         "Switzerland": "🇨🇭", "Hungary": "🇭🇺", "Slovenia": "🇸🇮", "Croatia": "🇭🇷", "Russia": "🇷🇺",
         "Ukraine": "🇺🇦", "Latvia": "🇱🇻", "Estonia": "🇪🇪", "Portugal": "🇵🇹", "Belgium": "🇧🇪",
         "Netherlands": "🇳🇱", "Romania": "🇷🇴", "Bulgaria": "🇧🇬", "Philippines": "🇵🇭",
         "Republic of Korea": "🇰🇷", "Chile": "🇨🇱", "Iceland": "🇮🇸", "Puerto Rico": "🇵🇷", "Canada": "🇨🇦",
         "Austria": "🇦🇹", "Cyprus": "🇨🇾", "Montenegro": "🇲🇪", "Bosnia & Herzegovina": "🇧🇦",
         "New Zealand": "🇳🇿", "Uruguay": "🇺🇾", "Venezuela": "🇻🇪", "Colombia": "🇨🇴", "Nigeria": "🇳🇬"}


def split_tournament(t: str) -> Tuple[str, str]:
    """("Spain", "Liga ACB") from SportyBet's "Spain · Liga ACB"."""
    country, _, name = (t or "").partition(" · ")
    return (country, name) if name else ("", country)


def flag(t: str) -> str:
    return FLAGS.get(split_tournament(t)[0], "🏀")


def crest(competitor_id: Any, size: str = "medium") -> Optional[str]:
    """A team's badge from Sportradar's image CDN (SportyBet's data comes
    from Sportradar: its competitor ids are Sportradar's)."""
    n = str(competitor_id or "").split(":")[-1]
    return f"https://img.sportradar.com/ls/crest/{size}/{n}.png" if n.isdigit() else None


def kickoff(ev: Dict) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(int(ev["estimateStartTime"]) / 1000, timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None


def _pair(s: Any) -> Optional[Tuple[int, int]]:
    m = re.match(r"^\s*(\d+)\s*:\s*(\d+)\s*$", str(s or ""))
    return (int(m.group(1)), int(m.group(2))) if m else None


def parse_result(ev: Dict) -> Optional[Dict[str, Any]]:
    """A finished game from SportyBet's results: {id, t, h, a, ko, hs, as, q, ot}.
    q: the four quarters (regulation), ot: whether there was overtime."""
    if str(ev.get("matchStatus") or "").lower() not in ("ended", "aet", "after overtime") and ev.get("status") != 4:
        return None
    final = _pair(ev.get("setScore"))
    k = kickoff(ev)
    if not final or not k or not ev.get("homeTeamName") or not ev.get("awayTeamName"):
        return None
    periods = [p for p in (_pair(x) for x in ev.get("gameScore") or []) if p]
    reg = [p for p in (_pair(x) for x in ev.get("regularTimeScore") or []) if p] or periods[:4]
    q = [list(p) for p in reg[:4]] if len(reg) >= 4 else None
    ot = len(periods) > 4 or (q is not None and (sum(p[0] for p in q), sum(p[1] for p in q)) != final)
    return {"id": str(ev.get("eventId") or ""), "t": ev.get("_tournament") or "", "h": ev["homeTeamName"],
            "a": ev["awayTeamName"], "ko": int(k.timestamp()), "hs": final[0], "as": final[1], "q": q, "ot": ot}


_ENDED = re.compile(r"ended|finished|^ft$|after overtime|aet|cancel|abandon|postpon|interrupt", re.I)
_NOT_STARTED = re.compile(r"not started|^$", re.I)


def parse_live(ev: Dict) -> Optional[Dict[str, Any]]:
    """An in-play game from a SportyBet live event: {id, score, periods,
    minute}; None when it isn't in play (not started, or over)."""
    status = str(ev.get("matchStatus") or "").strip()
    score = _pair(ev.get("setScore"))
    # SportyBet event status: 0 not started, 1 in play, 4 over
    if not score or _ENDED.search(status) or _NOT_STARTED.search(status) or ev.get("status") in (0, 4):
        return None
    periods = [list(p) for p in (_pair(x) for x in ev.get("gameScore") or []) if p]
    clock = str(ev.get("remainingTimeInPeriod") or "").strip()
    minute = f"{status} {clock}".strip() if clock and clock not in ("00:00", "0:00") else status
    return {"id": str(ev.get("eventId") or ""), "score": list(score), "periods": periods or None,
            "minute": _short_period(minute)}


def _short_period(s: str) -> str:
    """ "1st quarter 04:12" -> "Q1 04:12"; "Halftime" -> "HT"; "Overtime" -> "OT"."""
    s = re.sub(r"(\d)(?:st|nd|rd|th)\s+quarter", r"Q\1", s, flags=re.I)
    s = re.sub(r"(\d)(?:st|nd|rd|th)\s+half", r"H\1", s, flags=re.I)
    s = re.sub(r"half\s*time|halftime|pause", "HT", s, flags=re.I)
    return re.sub(r"overtime", "OT", s, flags=re.I)


LIVE_LISTS = (("/factsCenter/liveOrPrematchEvents", {"sportId": BASKETBALL}),
              ("/factsCenter/wapConfigurableIndexLiveEvents", {"sportId": BASKETBALL}))


async def fetch_live(started_ids: List[str], session=None, per_event_max: int = 40, sport_id: str = BASKETBALL,
                     parse=None, final=None, finals: Optional[Dict[str, Dict]] = None,
                     timeout: float = 12.0, from_pages: Optional[set] = None) -> Tuple[Dict[str, Dict], str, set]:
    """In-play scores for the games we priced that have started: ({event id:
    live}, how they were read, the ids actually checked). SportyBet's live
    listing first; the games it doesn't include (it can leave some out) are
    read from their own pages (productId 1: live), up to per_event_max.
    With `final` (a results parser) and a `finals` dict, a page that shows
    the game over puts its final score in `finals` (the results list can
    miss games). Every request is given up after `timeout` seconds. The ids
    whose live score came from their own page go in `from_pages`: a page
    can keep showing the last in-play score of a game that has ended."""
    import sportybet
    session = session or sportybet.shared_session()
    parse = parse or parse_live
    wanted = set(started_ids)
    live: Dict[str, Dict] = {}
    checked: set = set()
    how = []
    for path, params in LIVE_LISTS:
        try:
            data = await asyncio.wait_for(sportybet._request(
                session, "GET", path, params={**params, "sportId": sport_id, "_t": sportybet._now_ms()}), timeout)
        except Exception:
            continue
        found: List[Dict] = []
        sportybet._collect_events(data.get("data"), found)
        if not found:
            continue
        seen = {str(e.get("eventId")) for e in found}
        for x in (parse(e) for e in found):
            if x and x["id"] in wanted:
                live[x["id"]] = x
        checked |= wanted & seen
        how.append(f"{path}: {len(found)} events, {len(wanted & seen)} of ours")
        break
    # Ours the listing didn't have: their own pages (in play, or over)
    rest = [eid for eid in wanted if eid not in checked][:per_event_max]
    errors = 0
    for eid in rest:
        try:
            data = await asyncio.wait_for(sportybet._request(
                session, "GET", "/factsCenter/event", params={"eventId": eid, "productId": 1}), timeout)
        except Exception:
            errors += 1
            continue
        checked.add(eid)
        ev = data.get("data") or {}
        x = parse(ev)
        if x:
            live[eid] = x
            if from_pages is not None:
                from_pages.add(eid)
        elif final is not None and finals is not None:
            f = final(ev)
            if f:
                finals[eid] = f
    if rest:
        how.append(f"event pages: {len(rest) - errors} read" + (f", {errors} failed" if errors else ""))
    return live, " · ".join(how) or "nothing read", checked


def encode(games: List[Dict]) -> str:
    return base64.b64encode(zlib.compress(json.dumps(games, separators=(",", ":")).encode(), 9)).decode()


def decode(blob: Any) -> List[Dict]:
    if not blob:
        return []
    if isinstance(blob, bytes):
        blob = blob.decode()
    return json.loads(zlib.decompress(base64.b64decode(blob)))


async def fetch_results_day(day: str, session=None) -> List[Dict]:
    """Every finished basketball game SportyBet has for one UTC day."""
    import sportybet
    session = session or sportybet.shared_session()
    start = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    events, _, _ = await sportybet._paged(session, "/factsCenter/eventResultList", {
        "pageSize": 100, "sportId": BASKETBALL, "startTime": int(start.timestamp() * 1000),
        "endTime": int((start + timedelta(days=1)).timestamp() * 1000)}, 40)
    return [g for g in (parse_result(e) for e in events) if g]


def days_to_collect(have: Iterable[str], today: date) -> List[str]:
    """Yesterday and today (always: late results), then the most recent
    missing older days, up to BACKFILL_PER_RUN."""
    have = set(have)
    recent = [(today - timedelta(days=i)).isoformat() for i in (1, 0)]
    older = [(today - timedelta(days=i)).isoformat() for i in range(2, RESULT_DAYS + 1)]
    return recent + [d for d in older if d not in have][:BACKFILL_PER_RUN]


async def fetch_upcoming(session=None, max_pages: int = 20) -> Tuple[List[Dict], List[str]]:
    """Every basketball match SportyBet lists (today's and later), with every
    line of our markets, and a report line per feed."""
    import basketball_markets
    import sportybet
    session = session or sportybet.shared_session()
    merged: Dict[str, Dict] = {}
    report: List[str] = []
    for today in ("true", "false"):
        try:
            events, pages, total = await sportybet._paged(session, "/factsCenter/pcUpcomingEvents", {
                "sportId": BASKETBALL, "marketId": basketball_markets.LISTING_MARKETS, "pageSize": 100,
                "todayGames": today}, max_pages)
        except Exception as e:
            report.append(f"{'today' if today == 'true' else 'upcoming'}: {e}")
            continue
        for e in events:
            merged.setdefault(str(e["eventId"]), e)
        report.append(f"{'today' if today == 'true' else 'upcoming'}: {len(events)} events")
    return list(merged.values()), report


# ── From results to the model ─────────────────────────────────────────────

def games_by_league(results: Iterable[Dict]) -> Dict[str, List[bm.Game]]:
    """Each competition's games (friendlies and cups of mixed teams left out)."""
    out: Dict[str, List[bm.Game]] = {}
    seen = set()
    for g in results:
        if not g.get("t") or _NOT_A_LEAGUE.search(g["t"]) or g.get("id") in seen:
            continue
        seen.add(g.get("id"))
        day = datetime.fromtimestamp(g["ko"], timezone.utc).date().isoformat()
        out.setdefault(g["t"], []).append(bm.Game(day, g["h"], g["a"], g["hs"], g["as"],
                                                  [tuple(p) for p in g["q"]] if g.get("q") else None,
                                                  ot=bool(g.get("ot"))))
    return out


def apply_calibration(lg: bm.League, backtest: Optional[Dict]) -> bm.League:
    """Widen (or narrow) a league's spreads by how far results really landed
    from our expectations in the walk-forward check (backtest_basketball.py)."""
    scale = ((backtest or {}).get("leagues") or {}).get(lg.name, {}).get("sigma_scale") or {}
    m = min(1.35, max(0.9, float(scale.get("margin") or 1.0)))
    t = min(1.35, max(0.9, float(scale.get("total") or 1.0)))
    for k in list(lg.sigma):
        lg.sigma[k] *= t if "total" in k or k == "team" else m
    # League constants measured out of sample over the long history (years of
    # games, where the league's own recent results are one season at most)
    c = ((backtest or {}).get("leagues") or {}).get(lg.name, {}).get("constants") or {}
    if c.get("n", 0) >= 200:
        lg.margin_shares = tuple(c["margin_shares"])
        lg.q_shares = tuple(c["q_shares"])
        lg.h1_share = float(c["h1_share"])
        lg.tie_factor = float(c["tie_factor"])
    return lg


def fit_all(results: Iterable[Dict], as_of: Optional[date] = None,
            backtest: Optional[Dict] = None) -> Dict[str, bm.League]:
    """A rated league for every competition with MIN_LEAGUE_GAMES games,
    its spreads calibrated by the last walk-forward check where there is one."""
    out = {}
    for t, games in games_by_league(results).items():
        if len(games) < MIN_LEAGUE_GAMES:
            continue
        try:
            lg = bm.fit(t, games, as_of)
        except Exception as e:
            print(f"[Basketball] couldn't rate {t}: {e}")
            continue
        if lg:
            out[t] = apply_calibration(lg, backtest)
    return out


def result_for(games_by_id: Dict[str, Dict], leg: Dict) -> Optional[Dict]:
    """A leg's result for settling (basketball_markets.settle): by the
    SportyBet event it was booked on."""
    g = games_by_id.get(str(leg.get("event_id") or ""))
    if not g:
        return None
    return {"status": "finished", "final": [g["hs"], g["as"]], "periods": g.get("q"), "ot": g.get("ot")}
