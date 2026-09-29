"""
Ten seasons of basketball history for the walk-forward check (GitHub
Actions, "Basketball data" → history). SportyBet keeps about 300 days of
results; older seasons come from other sources, filed under SportyBet's
own league names so they join the league's recent results:

- ESPN (NBA, WNBA, NCAA men and women): a scoreboard a day, quarter scores.
- The EuroLeague API (EuroLeague, EuroCup): a season a request.
- API-Basketball (API-Sports; the APIFOOTBALL_KEY account), when its plan
  allows old seasons: a league-season a request, every league it covers.

Games are stored in Redis like SportyBet's results ({id, t, h, a, ko, hs,
as, q, ot}), a hash field per league and season (HISTORY_KEY), with the
collection's progress (DONE_KEY) so a run picks up where the last stopped.
Team names are mapped to SportyBet's where one clearly matches (the same
club across the join between the two eras).

    python bb_history.py --minutes 100          # collect (resumes)
    python bb_history.py --sample               # a few days per source, saves nothing
"""

import argparse
import asyncio
import os
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import basketball_data as bd

HISTORY_KEY = "betiq:bb:history"          # hash: "{league}|{season}" -> encoded games
DONE_KEY = "betiq:bb:history:done"        # hash: progress marks
SEASONS = 10
ESPN = {  # slug -> (SportyBet league, months of the season, regulation periods)
    "nba": ("USA · NBA", (10, 11, 12, 1, 2, 3, 4, 5, 6), 4),
    "wnba": ("USA · WNBA", (5, 6, 7, 8, 9, 10), 4),
    "mens-college-basketball": ("USA · NCAA, Regular Season", (11, 12, 1, 2, 3, 4), 2),
    "womens-college-basketball": ("USA · NCAA Women, Regular Season", (11, 12, 1, 2, 3, 4), 4),
}
EUROLEAGUE = {"E": "International · Euroleague", "U": "International · Eurocup"}


def season_of(day: date) -> int:
    """A season by the year it starts (Aug–Jul)."""
    return day.year if day.month >= 8 else day.year - 1


# ── ESPN ──────────────────────────────────────────────────────────────────

def parse_espn(ev: Dict, league: str, reg_periods: int) -> Optional[Dict[str, Any]]:
    """A finished game from an ESPN scoreboard event."""
    try:
        comp = ev["competitions"][0]
        status = comp.get("status") or ev.get("status") or {}
        if not (status.get("type") or {}).get("completed"):
            return None
        side = {c["homeAway"]: c for c in comp["competitors"]}
        h, a = side["home"], side["away"]
        hs, as_ = int(float(h["score"])), int(float(a["score"]))
        ko = datetime.fromisoformat(ev["date"].replace("Z", "+00:00"))
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    lh = [int(float(x.get("value", 0))) for x in h.get("linescores") or []]
    la = [int(float(x.get("value", 0))) for x in a.get("linescores") or []]
    periods = int(status.get("period") or max(len(lh), reg_periods))
    q = [[lh[i], la[i]] for i in range(4)] if reg_periods == 4 and len(lh) >= 4 and len(la) >= 4 else None
    return {"id": f"espn:{ev.get('id')}", "t": league, "h": (h.get("team") or {}).get("displayName") or "",
            "a": (a.get("team") or {}).get("displayName") or "", "ko": int(ko.timestamp()), "hs": hs, "as": as_,
            "q": q, "ot": periods > reg_periods, "neutral": bool(comp.get("neutralSite"))}


def espn_days(months: Iterable[int], today: date, seasons: int = SEASONS) -> List[date]:
    """Every day in the season's months over the last `seasons` seasons, oldest first."""
    months = set(months)
    start = date(season_of(today) - seasons, 8, 1)
    out, d = [], start
    while d < today:
        if d.month in months:
            out.append(d)
        d += timedelta(days=1)
    return out


async def espn(client, done: Dict[str, str], deadline: float, sample: bool) -> Tuple[Dict[Tuple[str, int], List], List[str]]:
    """{(league, season): games}, and the progress marks made."""
    out: Dict[Tuple[str, int], List] = {}
    marks: List[str] = []
    today = date.today()
    for slug, (league, months, reg) in ESPN.items():
        days = espn_days(months, today)
        if sample:
            days = days[-400::100]
        todo = [d for d in days if f"espn:{slug}:{d.isoformat()}" not in done]
        print(f"[ESPN] {slug}: {len(todo)} of {len(days)} days to fetch", flush=True)
        sem = asyncio.Semaphore(6)

        async def one(d: date) -> Optional[List[Dict]]:
            async with sem:
                for attempt in range(3):
                    try:
                        r = await client.get(f"https://site.api.espn.com/apis/site/v2/sports/basketball/{slug}/scoreboard",
                                             params={"dates": d.strftime("%Y%m%d"), "limit": 1000,
                                                     **({"groups": 50} if "college" in slug else {})})
                        if r.status_code == 200:
                            return [g for g in (parse_espn(e, league, reg) for e in r.json().get("events") or []) if g]
                        if r.status_code == 404:
                            return []
                    except Exception:
                        pass
                    await asyncio.sleep(1 + attempt * 2)
                return None
        for i in range(0, len(todo), 60):
            if time.time() > deadline:
                print(f"[ESPN] {slug}: out of time", flush=True)
                return out, marks
            chunk = todo[i:i + 60]
            got = await asyncio.gather(*(one(d) for d in chunk))
            for d, games in zip(chunk, got):
                if games is None:
                    continue
                for g in games:
                    out.setdefault((league, season_of(d)), []).append(g)
                # Today's and yesterday's games may not be final yet
                if d < today - timedelta(days=2):
                    marks.append(f"espn:{slug}:{d.isoformat()}")
        n = sum(len(v) for (lg, _), v in out.items() if lg == league)
        print(f"[ESPN] {slug}: {n} games", flush=True)
    return out, marks


# ── EuroLeague / EuroCup ───────────────────────────────────────────────────

def parse_euroleague(g: Dict, league: str) -> Optional[Dict[str, Any]]:
    if not g.get("played"):
        return None
    try:
        loc, road = g["local"], g["road"]
        hs, as_ = int(loc["score"]), int(road["score"])
        ko = datetime.fromisoformat(str(g["date"]).replace("Z", "+00:00"))
        if ko.tzinfo is None:
            ko = ko.replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None
    pl, pr = loc.get("partials") or {}, road.get("partials") or {}
    q = None
    try:
        q = [[int(pl[f"partials{i}"]), int(pr[f"partials{i}"])] for i in range(1, 5)]
    except (KeyError, TypeError, ValueError):
        pass
    ot = bool(pl.get("extraPeriods")) or (q is not None and sum(x[0] for x in q) == sum(x[1] for x in q))
    return {"id": f"el:{g.get('gameCode') or g.get('id')}:{ko.date()}", "t": league,
            "h": (loc.get("club") or {}).get("name") or "", "a": (road.get("club") or {}).get("name") or "",
            "ko": int(ko.timestamp()), "hs": hs, "as": as_, "q": q, "ot": ot, "neutral": bool(g.get("isNeutralVenue"))}


async def euroleague(client, done: Dict[str, str], deadline: float, sample: bool):
    out: Dict[Tuple[str, int], List] = {}
    marks: List[str] = []
    last = season_of(date.today())
    for comp, league in EUROLEAGUE.items():
        for year in range(last - SEASONS, last):          # finished seasons only
            mark = f"el:{comp}{year}"
            if mark in done or time.time() > deadline:
                continue
            games = None
            for url in (f"https://api-live.euroleague.net/v2/competitions/{comp}/seasons/{comp}{year}/games",
                        f"https://feeds.incrowdsports.com/provider/euroleague-feeds/v2/competitions/{comp}/seasons/{comp}{year}/games"):
                try:
                    r = await client.get(url, headers={"Accept": "application/json"})
                    if r.status_code == 200 and (r.text or "").lstrip().startswith("{"):
                        games = r.json().get("data") or []
                        break
                except Exception:
                    pass
                await asyncio.sleep(2)
            if games is None:
                print(f"[EuroLeague] {comp}{year}: no answer", flush=True)
                if sample:
                    break
                continue
            parsed = [x for x in (parse_euroleague(g, league) for g in games) if x]
            out[(league, year)] = parsed
            marks.append(mark)
            print(f"[EuroLeague] {comp}{year}: {len(parsed)} games", flush=True)
            await asyncio.sleep(2)
            if sample:
                break
    return out, marks


# ── Names and storage ─────────────────────────────────────────────────────

def map_teams(games: List[Dict], sportybet_teams: Iterable[str], threshold: float = 0.8) -> int:
    """Rename each team to its SportyBet name where one clearly matches."""
    import sportybet
    names = list(set(sportybet_teams))
    cache: Dict[str, str] = {}

    def best(name: str) -> str:
        if name not in cache:
            scored = max(((sportybet.team_similarity(name, n), n) for n in names), default=(0.0, name))
            cache[name] = scored[1] if scored[0] >= threshold else name
        return cache[name]
    changed = 0
    for g in games:
        for k in ("h", "a"):
            new = best(g[k])
            changed += new != g[k]
            g[k] = new
    return changed


def merge_store(r, found: Dict[Tuple[str, int], List], sportybet_teams: Dict[str, set]) -> int:
    """Add games to their league-season fields (by id, no duplicates)."""
    saved = 0
    for (league, season), games in found.items():
        if not games:
            continue
        map_teams(games, sportybet_teams.get(league, ()))
        field = f"{league}|{season}"
        have = {g["id"]: g for g in bd.decode(r.hget(HISTORY_KEY, field))}
        before = len(have)
        for g in games:
            have[g["id"]] = g
        r.hset(HISTORY_KEY, field, bd.encode(sorted(have.values(), key=lambda g: g["ko"])))
        saved += len(have) - before
    return saved


def load(r) -> List[Dict]:
    """Every stored history game."""
    out: List[Dict] = []
    for blob in (r.hgetall(HISTORY_KEY) or {}).values():
        try:
            out += bd.decode(blob)
        except Exception:
            continue
    return out


def combine(sportybet: List[Dict], history: List[Dict]) -> List[Dict]:
    """SportyBet's results plus the history from before them, per league (so
    the era SportyBet covers isn't counted twice)."""
    first: Dict[str, int] = {}
    for g in sportybet:
        if g.get("t"):
            first[g["t"]] = min(first.get(g["t"], g["ko"]), g["ko"])
    older = [g for g in history if g["ko"] < first.get(g["t"], 1 << 62)]
    return sportybet + older


async def main(minutes: float, sample: bool) -> None:
    import httpx
    import model_store
    r = None if sample else model_store._client()
    if r is None and not sample:
        raise SystemExit("UPSTASH_REDIS_URL isn't set")
    done = {} if sample else {(k.decode() if isinstance(k, bytes) else k): "1" for k in (r.hkeys(DONE_KEY) or [])}
    # SportyBet's team names per league (the names the server rates)
    teams: Dict[str, set] = {}
    if r is not None:
        for blob in (r.hgetall(bd.RESULTS_KEY) or {}).values():
            for g in bd.decode(blob):
                teams.setdefault(g.get("t") or "", set()).update((g["h"], g["a"]))
    deadline = time.time() + minutes * 60
    total = 0
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for name, source in (("euroleague", euroleague), ("espn", espn)):
            found, marks = await source(client, done, deadline, sample)
            n = sum(len(v) for v in found.values())
            if sample:
                for (league, season), games in list(found.items())[:4]:
                    print(f"  {league} {season}: {len(games)} games, e.g. {games[:1]}")
                continue
            saved = merge_store(r, found, teams)
            for m in marks:
                r.hset(DONE_KEY, m, "1")
            total += saved
            print(f"[{name}] {n} games read, {saved} new stored", flush=True)
    if r is not None:
        per: Dict[str, int] = {}
        for g in load(r):
            per[g["t"]] = per.get(g["t"], 0) + 1
        print("Stored history:", ", ".join(f"{k}: {v}" for k, v in sorted(per.items(), key=lambda kv: -kv[1])))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=100)
    ap.add_argument("--sample", action="store_true")
    a = ap.parse_args()
    asyncio.run(main(a.minutes, a.sample))
