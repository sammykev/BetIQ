"""
Player box scores for player props (GitHub Actions, "Basketball data" →
props): every player's games, into Upstash Redis for the API server.

Basketball: ESPN (NBA, WNBA) and the EuroLeague API (EuroLeague, EuroCup):
    per game [date, team, opponent, home, minutes, pts, reb, ast, 3pm, started]
Football: Understat (Premier League, LaLiga, Bundesliga, Serie A, Ligue 1):
    per game [date, team, opponent, home, minutes, goals, shots, xg, assists, started, pens]
    (pens: penalties he took, so his open-play share can be told apart)

Stored per league (PLAYERS_KEY: {name_key: {"name", "team", "games": [...]}}),
newest games last; the games already read are remembered (DONE_KEY), so
each run reads only new ones: the first runs backfill two seasons.

    python props_collect.py --minutes 80           # everything, within 80 minutes
    python props_collect.py --only bb --sample     # one source's first games, printed
"""

import argparse
import asyncio
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import basketball_data as bd
import player_props as pp

PLAYERS_KEY = "betiq:props:{sport}:{league}"
DONE_KEY = "betiq:props:done"
STATUS_KEY = "betiq:props:status"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
      "Accept": "application/json, text/html"}

# ESPN league slug -> (our league name, season windows)
ESPN_BB = {
    "nba": ("NBA", [("2024-10-22", "2025-06-22"), ("2025-10-21", "2026-06-21")]),
    "wnba": ("WNBA", [("2025-05-16", "2025-10-15"), ("2026-05-08", "2026-10-15")]),
}
EUROLEAGUE = {"E": "Euroleague", "U": "Eurocup"}
EL_SEASONS = (2024, 2025)
UNDERSTAT = {"EPL": "Premier League", "La_liga": "LaLiga", "Bundesliga": "Bundesliga",
             "Serie_A": "Serie A", "Ligue_1": "Ligue 1"}
US_SEASONS = (2024, 2025)


class Store:
    """Players per league, loaded once, saved at the end (and every so often)."""

    def __init__(self, r):
        self.r = r
        self.leagues: Dict[Tuple[str, str], Dict[str, Dict]] = {}
        self.done: set = set(x.decode() if isinstance(x, bytes) else x for x in (r.smembers(DONE_KEY) or [])) if r else set()
        self.new_done: List[str] = []
        self.autosave = False

    def league(self, sport: str, league: str) -> Dict[str, Dict]:
        k = (sport, league)
        if k not in self.leagues:
            raw = self.r.get(PLAYERS_KEY.format(sport=sport, league=league)) if self.r else None
            self.leagues[k] = json.loads(json.dumps(bd.decode(raw))) if raw else {}
            if isinstance(self.leagues[k], list):   # an empty store decodes as []
                self.leagues[k] = {}
        return self.leagues[k]

    def add(self, sport: str, league: str, name: str, team: str, row: List[Any]) -> None:
        players = self.league(sport, league)
        key = pp.name_key(name)
        p = players.setdefault(key, {"name": name, "team": team, "games": []})
        p["games"].append(row)
        p["games"].sort(key=lambda g: g[0])
        p["team"] = p["games"][-1][1]
        p["name"] = name

    def mark(self, gid: str) -> None:
        self.done.add(gid)
        self.new_done.append(gid)
        self.marked = getattr(self, "marked", 0) + 1
        if self.autosave and self.marked % 300 == 0:
            self.save()     # a run cut short keeps what it read

    def save(self) -> None:
        if not self.r:
            return
        for (sport, league), players in self.leagues.items():
            self.r.set(PLAYERS_KEY.format(sport=sport, league=league), bd.encode(players))
        if self.new_done:
            for i in range(0, len(self.new_done), 500):
                self.r.sadd(DONE_KEY, *self.new_done[i:i + 500])
            self.new_done = []


def _num(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _made(x: Any) -> float:
    """ "3-7" -> 3."""
    return _num(str(x or "0").split("-")[0])


def _minutes(x: Any) -> float:
    """ "24" / "24:30" / "PT24M30S" / seconds as a number -> minutes."""
    s = str(x or "0")
    m = re.match(r"^PT(?:(\d+)M)?(?:(\d+)S)?$", s)
    if m:
        return int(m.group(1) or 0) + int(m.group(2) or 0) / 60
    if ":" in s:
        a, b = s.split(":")[:2]
        return _num(a) + _num(b) / 60
    return _num(s)


# ── Basketball: ESPN ────────────────────────────────────────────────────────

def parse_espn_box(summary: Dict, day: str) -> List[Tuple[str, str, List]]:
    """(name, team, row) for each player who played in an ESPN box score."""
    out = []
    teams = (summary.get("boxscore") or {}).get("players") or []
    names = [((t.get("team") or {}).get("displayName") or "") for t in teams]
    homes = {}
    for c in ((summary.get("header") or {}).get("competitions") or [{}])[0].get("competitors") or []:
        homes[(c.get("team") or {}).get("displayName")] = c.get("homeAway") == "home"
    for i, t in enumerate(teams):
        team, opp = names[i], names[1 - i] if len(names) == 2 else ""
        st = (t.get("statistics") or [{}])[0]
        keys = st.get("keys") or []
        idx = {k: j for j, k in enumerate(keys)}
        for a in st.get("athletes") or []:
            s = a.get("stats") or []
            if a.get("didNotPlay") or not s or "minutes" not in idx:
                continue
            mins = _minutes(s[idx["minutes"]])
            if mins <= 0:
                continue
            get = lambda k: s[idx[k]] if k in idx and idx[k] < len(s) else 0
            row = [day, team, opp, homes.get(team, False), round(mins, 1), _num(get("points")), _num(get("rebounds")),
                   _num(get("assists")), _made(get("threePointFieldGoalsMade-threePointFieldGoalsAttempted")),
                   bool(a.get("starter"))]
            out.append(((a.get("athlete") or {}).get("displayName") or "", team, row))
    return out


async def espn_bb(store: Store, client, deadline: float, sample: bool) -> int:
    import httpx
    n = 0
    # ESPN answers a plain client (browser-like headers get something else)
    client = httpx.AsyncClient(timeout=30)
    today = date.today()
    for slug, (league, windows) in ESPN_BB.items():
        for start, end in windows:
            d, last = date.fromisoformat(start), min(date.fromisoformat(end), today - timedelta(days=1))
            while d <= last and time.time() < deadline:
                day = d.isoformat()
                d += timedelta(days=1)
                if f"espn-day:{slug}:{day}" in store.done:
                    continue
                try:
                    resp = await client.get(f"https://site.api.espn.com/apis/site/v2/sports/basketball/{slug}/scoreboard",
                                            params={"dates": day.replace("-", "")})
                    sb = resp.json()
                except Exception as e:
                    print(f"  {slug} {day}: HTTP {getattr(locals().get('resp'), 'status_code', '?')} {e}")
                    continue
                events = sb.get("events") or []
                finished = [e for e in events if ((e.get("competitions") or [{}])[0].get("status") or {})
                            .get("type", {}).get("completed")]
                for ev in finished:
                    gid = f"espn:{slug}:{ev['id']}"
                    if gid in store.done:
                        continue
                    try:
                        summ = (await client.get(f"https://site.api.espn.com/apis/site/v2/sports/basketball/{slug}/summary",
                                                 params={"event": ev["id"]})).json()
                    except Exception as e:
                        print(f"  {slug} {ev['id']}: {e}")
                        continue
                    rows = parse_espn_box(summ, day)
                    for name, team, row in rows:
                        store.add("bb", league, name, team, row)
                    if sample and n < 1:
                        print(f"  sample {slug} {ev.get('name')}: {rows[:2]}")
                    store.mark(gid)
                    n += 1
                # A day is done once every game on it has finished
                if len(finished) == len(events):
                    store.mark(f"espn-day:{slug}:{day}")
                if sample and n >= 2:
                    return n
    return n


# ── Basketball: EuroLeague API ───────────────────────────────────────────────

EL_KEYS = {"min": ("timePlayed", "minutes", "min"), "pts": ("points",),
           "reb": ("totalRebounds", "rebounds"), "ast": ("assistances", "assists"),
           "tpm": ("fieldGoalsMade3", "threePointersMade"), "started": ("startFive", "isStarter")}


def _pick(stats: Dict, names) -> Any:
    for k in names:
        if k in stats:
            return stats[k]
    return None


def parse_el_stats(data: Dict, day: str, local: str, road: str) -> List[Tuple[str, str, List]]:
    out = []
    for side, team, opp, home in (("local", local, road, True), ("road", road, local, False)):
        for p in (data.get(side) or {}).get("players") or []:
            s = p.get("stats") or {}
            mins = _minutes(_pick(s, EL_KEYS["min"]))
            if isinstance(_pick(s, EL_KEYS["min"]), (int, float)) and mins > 60:   # seconds
                mins /= 60
            if mins <= 0:
                continue
            name = ((p.get("player") or {}).get("person") or {}).get("name") or ""
            row = [day, team, opp, home, round(mins, 1), _num(_pick(s, EL_KEYS["pts"])), _num(_pick(s, EL_KEYS["reb"])),
                   _num(_pick(s, EL_KEYS["ast"])), _num(_pick(s, EL_KEYS["tpm"])), bool(_pick(s, EL_KEYS["started"]))]
            out.append((name, team, row))
    return out


async def euroleague(store: Store, client, deadline: float, sample: bool) -> int:
    n = 0
    base = "https://api-live.euroleague.net/v2/competitions"
    for comp, league in EUROLEAGUE.items():
        for season in EL_SEASONS:
            code = f"{comp}{season}"
            try:
                games = (await client.get(f"{base}/{comp}/seasons/{code}/games")).json().get("data") or []
            except Exception as e:
                print(f"  {code}: {e}")
                continue
            for g in games:
                if time.time() > deadline:
                    return n
                gid = f"el:{code}:{g.get('gameCode')}"
                if not g.get("played") or gid in store.done:
                    continue
                try:
                    stats = (await client.get(f"{base}/{comp}/seasons/{code}/games/{g['gameCode']}/stats")).json()
                except Exception as e:
                    print(f"  {gid}: {e}")
                    continue
                day = str(g.get("date") or "")[:10]
                local = ((g.get("local") or {}).get("club") or {}).get("name") or ""
                road = ((g.get("road") or {}).get("club") or {}).get("name") or ""
                if sample and n < 1:
                    p0 = ((stats.get("local") or {}).get("players") or [{}])[0]
                    print(f"  sample {code} stat keys: {sorted((p0.get('stats') or {}).keys())}")
                rows = parse_el_stats(stats, day, local, road)
                for name, team, row in rows:
                    store.add("bb", league, name, team, row)
                if sample and n < 1:
                    print(f"  sample {code} {local} v {road}: {rows[:2]}")
                store.mark(gid)
                n += 1
                if sample and n >= 2:
                    return n
    return n


# ── Football: Understat ─────────────────────────────────────────────────────

def _embedded(html: str, var: str) -> Any:
    m = re.search(rf"var {var}\s*=\s*JSON\.parse\('(.+?)'\)", html)
    if not m:
        return None
    return json.loads(m.group(1).encode("utf-8").decode("unicode_escape"))


async def _understat_json(client, page: str, api: str, var: str) -> Any:
    """Understat's data: its JSON endpoint (newer site), else the page's embedded var."""
    try:
        r = await client.get(api, headers={**UA, "X-Requested-With": "XMLHttpRequest", "Referer": page})
        if r.status_code == 200 and r.text.lstrip().startswith(("{", "[")):
            return r.json()
    except Exception:
        pass
    r = await client.get(page, headers=UA)
    return _embedded(r.text, var)


def parse_understat_match(data: Dict, day: str, home: str, away: str) -> List[Tuple[str, str, List]]:
    """Rows from a match's rosters (and its shots, for penalties)."""
    rosters = data.get("rosters") or data.get("rostersData") or {}
    shots = data.get("shots") or data.get("shotsData") or {}
    pens: Dict[str, int] = {}
    for side in ("h", "a"):
        for s in shots.get(side) or []:
            if s.get("situation") == "Penalty":
                pens[str(s.get("player_id"))] = pens.get(str(s.get("player_id")), 0) + 1
    out = []
    for side, team, opp, is_home in (("h", home, away, True), ("a", away, home, False)):
        for p in (rosters.get(side) or {}).values():
            mins = _num(p.get("time"))
            if mins <= 0:
                continue
            row = [day, team, opp, is_home, mins, _num(p.get("goals")), _num(p.get("shots")), round(_num(p.get("xG")), 3),
                   _num(p.get("assists")), p.get("position") != "Sub", pens.get(str(p.get("player_id")), 0)]
            out.append((p.get("player") or "", team, row))
    return out


async def understat(store: Store, client, deadline: float, sample: bool) -> int:
    n = 0
    for slug, league in UNDERSTAT.items():
        for season in US_SEASONS:
            page = f"https://understat.com/league/{slug}/{season}"
            try:
                data = await _understat_json(client, page, f"https://understat.com/getLeagueData/{slug}/{season}", "datesData")
            except Exception as e:
                print(f"  understat {slug} {season}: {e}")
                continue
            dates = data.get("dates") if isinstance(data, dict) else data
            if not dates:
                print(f"  understat {slug} {season}: no matches (keys {list(data)[:8] if isinstance(data, dict) else type(data)})")
                continue
            for m in dates:
                if time.time() > deadline:
                    return n
                gid = f"us:{m.get('id')}"
                if not m.get("isResult") or gid in store.done:
                    continue
                mpage = f"https://understat.com/match/{m['id']}"
                try:
                    md = await _understat_json(client, mpage, f"https://understat.com/getMatchData/{m['id']}", "rostersData")
                    if md is not None and "rosters" not in md and "rostersData" not in md and "h" in md:
                        # The page's embedded rosters only: its shots are another var
                        html = (await client.get(mpage, headers=UA)).text
                        md = {"rosters": md, "shots": _embedded(html, "shotsData") or {}}
                except Exception as e:
                    print(f"  {gid}: {e}")
                    continue
                if not md:
                    print(f"  {gid}: no data")
                    continue
                day = str(m.get("datetime") or "")[:10]
                rows = parse_understat_match(md, day, (m.get("h") or {}).get("title") or "", (m.get("a") or {}).get("title") or "")
                for name, team, row in rows:
                    store.add("fb", league, name, team, row)
                if sample and n < 1:
                    print(f"  sample understat {slug}: {rows[:2]}")
                store.mark(gid)
                n += 1
                await asyncio.sleep(0.4)
                if sample and n >= 2:
                    return n
    return n


async def run(minutes: float, only: str, sample: bool) -> Dict:
    import httpx
    import model_store
    r = model_store._client()
    store = Store(r)
    store.autosave = not sample
    deadline = time.time() + minutes * 60
    got: Dict[str, int] = {}
    async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers=UA) as client:
        for name, fn in (("espn_bb", espn_bb), ("euroleague", euroleague), ("understat", understat)):
            if only and not name.startswith(only) and not (only == "bb" and name in ("espn_bb", "euroleague")) \
                    and not (only == "fb" and name == "understat"):
                continue
            share = time.time() + (deadline - time.time()) / 2 if name != "understat" else deadline
            try:
                got[name] = await fn(store, client, min(deadline, share) if not sample else deadline, sample)
            except Exception as e:
                print(f"{name} failed: {e}")
            print(f"{name}: {got.get(name, 0)} new games")
            if not sample:
                store.save()
    counts = {f"{s}:{l}": len(p) for (s, l), p in store.leagues.items()}
    status = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "new_games": got, "players": counts}
    if r and not sample:
        r.set(STATUS_KEY, json.dumps(status))
    print(json.dumps(status))
    return status


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=80)
    ap.add_argument("--only", default="", help="bb | fb | espn_bb | euroleague | understat")
    ap.add_argument("--sample", action="store_true", help="read two games per source, print, save nothing")
    a = ap.parse_args()
    asyncio.run(run(a.minutes, a.only, a.sample))
