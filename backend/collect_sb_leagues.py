"""
League history for the European clubs our other sources don't cover, from
SportyBet's results list (free, every league it offers, a year and more
back): Bulgaria, Croatia, Serbia, Hungary, the Baltics, the Caucasus,
Kazakhstan and the rest (COUNTRIES — the countries football-data.co.uk and
ESPN's small leagues don't give us).

Day by day, newest first, every finished senior league match in those
countries is kept (cups, youth, women and simulated games left out), and
every club seen in SportyBet's Champions, Europa or Conference League
results is noted. selected() then keeps, per country, the league with the
most of those clubs — its top flight, the one tied to the European
competitions — and the Europe model (europe_model.inputs) learns those
clubs' ratings and form from it, as it does from the extra leagues.

Progress lives in Redis (KEY): later runs read only new days (and the last
RECHECK_DAYS again, for late results).

    python collect_sb_leagues.py --minutes 50     (GitHub Actions: "Europe model", and nightly)
"""

import argparse
import asyncio
import gzip
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

KEY = "betiq:sb_leagues:v1"
SINCE = date(2024, 7, 1)          # two seasons and a bit
RECHECK_DAYS = 3
MAX_PAGES = 60

# SportyBet category names (lower case) → our code. Countries whose leagues
# we already have from football-data.co.uk or ESPN are left out: one club,
# one history.
COUNTRIES: Dict[str, str] = {
    "albania": "ALB", "andorra": "AND", "armenia": "ARM", "azerbaijan": "AZE", "belarus": "BLR",
    "bosnia & herzegovina": "BIH", "bosnia and herzegovina": "BIH", "bosnia": "BIH", "bulgaria": "BUL",
    "croatia": "CRO", "estonia": "EST", "faroe islands": "FRO", "georgia": "GEO", "gibraltar": "GIB",
    "hungary": "HUN", "iceland": "ISL", "kazakhstan": "KAZ", "kosovo": "KOS", "latvia": "LVA",
    "lithuania": "LTU", "luxembourg": "LUX", "north macedonia": "MKD", "macedonia": "MKD",
    "moldova": "MDA", "montenegro": "MNE", "san marino": "SMR", "serbia": "SRB", "slovakia": "SVK",
    "slovenia": "SVN", "ukraine": "UKR", "liechtenstein": "LIE",
}
UEFA = re.compile(r"uefa (champions league|europa league|europa conference league|conference league)", re.I)
NOT_LEAGUE = re.compile(r"cup|pokal|kupa|\bkup\b|coppa|copa|coupe|beker|taca|puchar|kupok|super ?cup|"
                        r"kauss|pohar|kupi|kubok|winter|tournament|turnir|"
                        r"play-?off|qualif|friendl|relegation|promotion", re.I)


def empty() -> Dict[str, Any]:
    return {"rows": {}, "days": [], "uefa": {}, "runs": []}


def load(r) -> Dict[str, Any]:
    raw = r.get(KEY) if r is not None else None
    data = empty()
    if raw:
        data.update(json.loads(gzip.decompress(raw)))
    return data


def save(r, data: Dict[str, Any]) -> int:
    blob = gzip.compress(json.dumps(data, separators=(",", ":")).encode())
    r.set(KEY, blob)
    return len(blob)


def split_tournament(ev: Dict) -> tuple:
    """("category", "tournament") from SportyBet's "_tournament" tag."""
    t = str(ev.get("_tournament") or "")
    cat, _, name = t.partition(" · ")
    return cat.strip(), name.strip()


def take(events: List[Dict], data: Dict[str, Any]) -> int:
    """Keep one day's events: league rows for COUNTRIES, and the clubs seen in
    UEFA competitions. Returns rows added."""
    import results_feed
    added = 0
    for ev in events:
        cat, name = split_tournament(ev)
        if results_feed.not_senior(ev):
            continue
        res = results_feed.parse_sportybet(ev)
        if not res or res.get("status") != "finished" or res.get("hg") is None:
            continue
        if cat.lower().startswith("international") and UEFA.search(name):
            for team in (res["home"], res["away"]):
                data["uefa"][team] = max(data["uefa"].get(team, ""), res["date"])
            continue
        code = COUNTRIES.get(cat.lower())
        if not code or NOT_LEAGUE.search(name):
            continue
        hg, ag = int(res["hg"]), int(res["ag"])
        eid = str(ev.get("eventId") or f"{res['date']}|{res['home']}|{res['away']}")
        if eid not in data["rows"]:
            added += 1
        data["rows"][eid] = {"Date": res["date"], "HomeTeam": res["home"], "AwayTeam": res["away"],
                             "FTHG": hg, "FTAG": ag, "Result": "H" if hg > ag else "A" if hg < ag else "D",
                             "league": f"SB-{code}", "tournament": name}
    return added


def selected(data: Dict[str, Any]) -> Dict[str, str]:
    """{league code: tournament} — per country, the league with the most clubs
    that played in a UEFA competition (its top flight)."""
    uefa = set(data.get("uefa") or {})
    teams: Dict[tuple, set] = {}
    for r in (data.get("rows") or {}).values():
        k = (r["league"], r.get("tournament") or "")
        if NOT_LEAGUE.search(k[1]):
            continue                      # a cup stored before the filter knew its name
        teams.setdefault(k, set()).update((r["HomeTeam"], r["AwayTeam"]))
    best: Dict[str, tuple] = {}
    for (code, name), ts in teams.items():
        score = (len(ts & uefa), len(ts))
        if score[0] and (code not in best or score > best[code][0]):
            best[code] = (score, name)
    return {code: name for code, (_, name) in best.items()}


def rows_frame(data: Dict[str, Any]):
    """The selected leagues' matches, in the league CSVs' shape."""
    import pandas as pd
    keep = selected(data)
    rows = [r for r in (data.get("rows") or {}).values() if keep.get(r["league"]) == r.get("tournament")]
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).drop(columns=["tournament"])
    df["Date"] = pd.to_datetime(df["Date"])
    return df.sort_values("Date").reset_index(drop=True)


async def collect(data: Dict[str, Any], deadline: float, today: Optional[date] = None, log=print) -> Dict[str, Any]:
    """Read days newest first (the last RECHECK_DAYS always, then days not
    read yet) until the deadline."""
    import sportybet
    today = today or datetime.now(timezone.utc).date()
    done = set(data.get("days") or [])
    days = [today - timedelta(days=i) for i in range(1, (today - SINCE).days + 1)]
    todo = [d for d in days if (today - d).days <= RECHECK_DAYS or d.isoformat() not in done]
    session = sportybet.shared_session()
    report = {"read": 0, "added": 0, "errors": 0}
    for d in todo:
        if time.time() > deadline:
            break
        start = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        try:
            events, _, _ = await sportybet._paged(session, "/factsCenter/eventResultList", {
                "pageSize": 100, "sportId": sportybet.FOOTBALL, "startTime": int(start.timestamp() * 1000),
                "endTime": int((start + timedelta(days=1)).timestamp() * 1000)}, MAX_PAGES)
        except Exception as e:
            report["errors"] += 1
            log(f"  {d}: {type(e).__name__}: {e}")
            await asyncio.sleep(2)
            continue
        report["added"] += take(events, data)
        report["read"] += 1
        done.add(d.isoformat())
        if report["read"] % 25 == 0:
            log(f"  … {report['read']} days read, {report['added']} matches added (at {d})")
    data["days"] = sorted(done)
    report["left"] = sum(1 for d in days if d.isoformat() not in done)
    return report


def summary(data: Dict[str, Any]) -> str:
    keep = selected(data)
    counts: Dict[str, int] = {}
    for r in (data.get("rows") or {}).values():
        if keep.get(r["league"]) == r.get("tournament"):
            counts[r["league"]] = counts.get(r["league"], 0) + 1
    return (f"{len(data.get('rows') or {})} matches kept, {len(data.get('uefa') or {})} clubs seen in UEFA "
            f"competitions; top flights: " + ", ".join(f"{c} {keep[c]} ({counts.get(c, 0)})" for c in sorted(keep)))


def main() -> None:
    import model_store
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=50)
    args = ap.parse_args()
    r = model_store._client()
    if r is None:
        raise SystemExit("UPSTASH_REDIS_URL isn't set")
    data = load(r)
    deadline = time.time() + args.minutes * 60
    rep = asyncio.run(collect(data, deadline))
    data["runs"] = (data.get("runs") or [])[-20:] + [{"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **rep}]
    size = save(r, data)
    print(f"SportyBet leagues: {rep} · {size / 1024:.0f} KB stored\n{summary(data)}")


if __name__ == "__main__":
    main()
