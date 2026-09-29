"""
What basketball data we can get, before building on it (run in GitHub
Actions: our servers and the dev sandbox can't reach some of these).

1. SportyBet: every basketball event it lists, by competition, and every
   market (id, name, specifier, outcomes) on a spread of match pages —
   what the model must price and booking must map.
2. SportyBet results: whether its results listing answers (for grading).
3. ESPN: every basketball league it has, and which have past scores with
   quarter scores and team stats (history for the model).
4. SofaScore and the EuroLeague API: whether they answer from here.
5. Logos: where team badges come from.

    python probe_basketball.py            # everything, a summary per part
    python probe_basketball.py --pages 8  # match pages to read

Writes the full detail to basketball_probe.json (the workflow keeps it).
"""

import argparse
import asyncio
import json
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List

import sportybet

BASKETBALL = "sr:sport:2"
OUT: Dict[str, Any] = {}


def line(*parts) -> None:
    print(*parts, flush=True)


async def sportybet_listing(pages: int) -> None:
    line("\n=== 1. SportyBet basketball listing ===")
    session = sportybet.shared_session()
    merged: Dict[str, Dict] = {}
    for today in ("true", "false"):
        try:
            events, n, total = await sportybet._paged(session, "/factsCenter/pcUpcomingEvents", {
                "sportId": BASKETBALL, "marketId": "1,219,223,225", "pageSize": 100, "todayGames": today}, 20)
            line(f"pcUpcomingEvents todayGames={today}: {len(events)} events in {n} pages (SportyBet says {total})")
            for e in events:
                merged.setdefault(e["eventId"], e)
        except Exception as e:
            line(f"pcUpcomingEvents todayGames={today}: {e}")
    events = list(merged.values())
    by_t = Counter(e.get("_tournament") or "?" for e in events)
    line(f"{len(events)} events in {len(by_t)} competitions:")
    for t, n in by_t.most_common():
        line(f"  {n:4d}  {t}")
    OUT["listing"] = {"events": len(events), "competitions": dict(by_t.most_common())}
    if events:
        e = events[0]
        line("event keys:", sorted(e.keys()))
        line("listing markets on one event:", json.dumps([
            {"id": m.get("id"), "desc": m.get("desc"), "specifier": m.get("specifier"),
             "outcomes": [(o.get("id"), o.get("desc"), o.get("odds")) for o in m.get("outcomes") or []]}
            for m in e.get("markets") or []])[:1500])

    # Match pages: a spread over the biggest competitions
    picked, seen = [], set()
    for t, _ in by_t.most_common():
        for e in events:
            if e.get("_tournament") == t and t not in seen:
                picked.append(e)
                seen.add(t)
                break
        if len(picked) >= pages:
            break
    markets: Dict[str, Dict] = {}
    for e in picked:
        try:
            page = await sportybet.event_page(e["eventId"], session)
        except Exception as ex:
            line(f"event page {e['eventId']}: {ex}")
            continue
        ms = (page or {}).get("markets") or []
        line(f"\n{e.get('_tournament')}: {e['homeTeamName']} v {e['awayTeamName']} — {len(ms)} markets")
        for m in ms:
            mid = str(m.get("id"))
            k = markets.setdefault(mid, {"desc": m.get("desc") or m.get("name"), "group": m.get("group"),
                                         "specifiers": set(), "outcomes": {}, "seen": 0})
            k["seen"] += 1
            if m.get("specifier"):
                k["specifiers"].add(m["specifier"])
            for o in m.get("outcomes") or []:
                k["outcomes"].setdefault(str(o.get("id")), o.get("desc"))
        if page:
            OUT.setdefault("page_keys", sorted(page.keys()))
            OUT.setdefault("sample_market", (ms or [None])[0])
    line(f"\nEvery market seen on {len(picked)} match pages ({len(markets)}):")
    for mid, k in sorted(markets.items(), key=lambda kv: (-kv[1]["seen"], int(kv[0]) if kv[0].isdigit() else 0)):
        specs = sorted(k["specifiers"])
        line(f"  {mid:>7} x{k['seen']}  {k['desc']}  | spec: {'; '.join(specs[:4])}{' …' if len(specs) > 4 else ''}"
             f"  | out: {json.dumps(k['outcomes'])[:200]}")
    OUT["markets"] = {mid: {**k, "specifiers": sorted(k["specifiers"])} for mid, k in markets.items()}


async def sportybet_results() -> None:
    line("\n=== 2. SportyBet results ===")
    session = sportybet.shared_session()
    end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=1)
    ms = lambda d: int(d.timestamp() * 1000)
    tries = [
        ("GET", "/factsCenter/eventResultList", {"params": {
            "pageNum": 1, "pageSize": 100, "sportId": BASKETBALL, "startTime": ms(start), "endTime": ms(end)}}),
        ("GET", "/factsCenter/eventResultList", {"params": {
            "pageNum": 1, "pageSize": 100, "sportId": BASKETBALL, "categoryId": "", "tournamentId": "",
            "startTime": ms(start), "endTime": ms(end)}}),
        ("GET", "/factsCenter/wapResultList", {"params": {"sportId": BASKETBALL, "startTime": ms(start), "endTime": ms(end)}}),
    ]
    for method, path, kw in tries:
        try:
            data = await sportybet._request(session, method, path, **kw)
            found: List[Dict] = []
            sportybet._collect_events(data.get("data"), found)
            line(f"{path}: OK, {len(found)} events")
            if found:
                e = found[0]
                line("  keys:", sorted(e.keys()))
                line("  sample:", json.dumps({k: e.get(k) for k in (
                    "homeTeamName", "awayTeamName", "setScore", "gameScore", "status", "matchStatus",
                    "estimateStartTime", "_tournament")})[:600])
                OUT["results"] = {"path": path, "events": len(found), "sample": e}
                return
            line("  data:", json.dumps(data.get("data"))[:400])
        except Exception as e:
            line(f"{path}: {e}")


async def espn() -> None:
    line("\n=== 3. ESPN basketball ===")
    from curl_cffi.requests import AsyncSession
    async with AsyncSession(timeout=20) as s:
        r = await s.get("https://sports.core.api.espn.com/v2/sports/basketball/leagues", params={"limit": 300})
        leagues = []
        if r.status_code == 200:
            for item in r.json().get("items") or []:
                try:
                    lr = await s.get(item["$ref"].replace("http://", "https://"))
                    d = lr.json()
                    leagues.append((d.get("slug"), d.get("name") or d.get("displayName")))
                except Exception:
                    continue
        line(f"core API: HTTP {r.status_code}, {len(leagues)} leagues")
        slugs = [sl for sl, _ in leagues if sl] or ["nba", "wnba", "mens-college-basketball",
                                                     "womens-college-basketball", "nba-development", "fiba"]
        names = dict(leagues)
        OUT["espn"] = {}
        windows = ["20250301", "20250115", "20241201", "20250520", "20250715"]
        for slug in slugs:
            done, sample = 0, None
            for day in windows:
                try:
                    sr = await s.get(f"https://site.api.espn.com/apis/site/v2/sports/basketball/{slug}/scoreboard",
                                     params={"dates": day, "limit": 300})
                    evs = sr.json().get("events") or [] if sr.status_code == 200 else []
                except Exception:
                    evs = []
                fin = [e for e in evs if ((e.get("competitions") or [{}])[0].get("status") or {}).get("type", {}).get("completed")]
                done += len(fin)
                if fin and not sample:
                    c = fin[0]["competitions"][0]
                    comp = c["competitors"][0]
                    sample = {"event": fin[0].get("id"), "name": fin[0].get("name"),
                              "linescores": bool(comp.get("linescores")),
                              "statistics": [st.get("name") for st in comp.get("statistics") or []][:12],
                              "logo": (comp.get("team") or {}).get("logo")}
            OUT["espn"][slug] = {"name": names.get(slug), "finished_in_sample_days": done, "sample": sample}
            line(f"  {slug:32s} {str(names.get(slug))[:40]:40s} finished on 5 sample days: {done:4d}"
                 + (f"  quarters={sample['linescores']} stats={len(sample['statistics'])} logo={'yes' if sample['logo'] else 'no'}"
                    if sample else ""))


async def others() -> None:
    line("\n=== 4. SofaScore, EuroLeague ===")
    from curl_cffi.requests import AsyncSession
    tries = [
        ("sofascore api", "https://api.sofascore.com/api/v1/sport/basketball/scheduled-events/2025-03-01"),
        ("sofascore www", "https://www.sofascore.com/api/v1/sport/basketball/scheduled-events/2025-03-01"),
        ("euroleague v2", "https://api-live.euroleague.net/v2/competitions/E/seasons/E2024/games"),
        ("euroleague v1", "https://api-live.euroleague.net/v1/results?seasonCode=E2024&gameNumber=1"),
        ("eurocup v2", "https://api-live.euroleague.net/v2/competitions/U/seasons/U2024/games"),
    ]
    async with AsyncSession(impersonate="chrome131", timeout=20) as s:
        for name, url in tries:
            try:
                r = await s.get(url, headers={"Accept": "application/json"})
                body = r.text or ""
                info = ""
                if r.status_code == 200 and body.lstrip().startswith("{"):
                    d = r.json()
                    if "events" in d:
                        ts = Counter((e.get("tournament") or {}).get("name") for e in d["events"])
                        info = f"{len(d['events'])} events, {len(ts)} tournaments: " + ", ".join(
                            f"{t} ({n})" for t, n in ts.most_common(25))
                    elif isinstance(d.get("data"), list):
                        info = f"{len(d['data'])} games; keys {sorted(d['data'][0].keys())[:20] if d['data'] else []}"
                    else:
                        info = f"keys {sorted(d.keys())[:20]}"
                line(f"  {name}: HTTP {r.status_code} {len(body)} bytes {info[:900]}")
                OUT.setdefault("others", {})[name] = {"status": r.status_code, "info": info[:2000]}
            except Exception as e:
                line(f"  {name}: {e}")


async def logos() -> None:
    line("\n=== 5. Logos ===")
    from curl_cffi.requests import AsyncSession
    async with AsyncSession(timeout=20) as s:
        for team in ("Real Madrid", "Fenerbahce", "Los Angeles Lakers", "Anadolu Efes", "Sydney Kings"):
            try:
                r = await s.get("https://www.thesportsdb.com/api/v1/json/3/searchteams.php", params={"t": team})
                ts = [t for t in (r.json().get("teams") or []) if t.get("strSport") == "Basketball"]
                line(f"  thesportsdb {team}: {len(ts)} basketball; badge={'yes' if ts and ts[0].get('strBadge') else 'no'}")
            except Exception as e:
                line(f"  thesportsdb {team}: {e}")


async def results_depth() -> None:
    """How far back SportyBet's results go, and how a long window pages."""
    line("\n=== 6. SportyBet results: history depth ===")
    session = sportybet.shared_session()
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    ms = lambda d: int(d.timestamp() * 1000)
    for back, span in ((2, 1), (8, 1), (30, 1), (60, 1), (120, 1), (200, 1), (300, 1), (400, 1), (550, 1), (730, 1),
                       (8, 7), (40, 30)):
        start = today - timedelta(days=back)
        try:
            events, pages, total = await sportybet._paged(session, "/factsCenter/eventResultList", {
                "pageSize": 100, "sportId": BASKETBALL, "startTime": ms(start),
                "endTime": ms(start + timedelta(days=span))}, 30)
            comps = Counter(e.get("_tournament") for e in events)
            quarters = sum(1 for e in events if e.get("gameScore"))
            line(f"  {start.date()} +{span}d: {len(events)} results in {pages} pages (totalNum {total}), "
                 f"{quarters} with quarters, {len(comps)} competitions: "
                 + ", ".join(f"{t} {n}" for t, n in comps.most_common(8)))
            OUT.setdefault("results_depth", {})[f"{back}+{span}"] = {"n": len(events), "total": total}
            if events and back == 8 and span == 1:
                odd = [e for e in events if e.get("regularTimeScore") and e.get("regularTimeScore") != e.get("setScore")]
                line("  overtime sample:", json.dumps([{k: e.get(k) for k in (
                    "homeTeamName", "awayTeamName", "setScore", "regularTimeScore", "gameScore")} for e in odd[:2]]))
        except Exception as e:
            line(f"  {start.date()} +{span}d: {e}")


async def espn_debug() -> None:
    line("\n=== 7. ESPN (why nothing?) ===")
    import httpx
    async with httpx.AsyncClient(timeout=20) as c:
        for slug, day in (("nba", "20250301"), ("nba", "20260301"), ("wnba", "20250701"), ("nbl", "20251201")):
            r = await c.get(f"https://site.api.espn.com/apis/site/v2/sports/basketball/{slug}/scoreboard",
                            params={"dates": day})
            body = r.text
            n = len(r.json().get("events") or []) if r.status_code == 200 else None
            line(f"  {slug} {day}: HTTP {r.status_code}, {len(body)} bytes, events={n} {body[:160]!r}")


async def crests() -> None:
    line("\n=== 8. Team crests (Sportradar image CDN) ===")
    from curl_cffi.requests import AsyncSession
    session = sportybet.shared_session()
    events, _, _ = await sportybet._paged(session, "/factsCenter/pcUpcomingEvents", {
        "sportId": BASKETBALL, "marketId": "219", "pageSize": 100, "todayGames": "false"}, 2)
    async with AsyncSession(impersonate="chrome131", timeout=15) as s:
        for e in events[:8]:
            cid = str(e.get("homeTeamId") or "").split(":")[-1]
            for url in (f"https://img.sportradar.com/ls/crest/big/{cid}.png",
                        f"https://img.sportradar.com/ls/crest/medium/{cid}.png"):
                try:
                    r = await s.get(url)
                    line(f"  {e.get('homeTeamName')} ({e.get('homeTeamId')}): {url.split('/crest/')[1]} "
                         f"HTTP {r.status_code} {r.headers.get('content-type')} {len(r.content)} bytes")
                except Exception as ex:
                    line(f"  {url}: {ex}")


async def euroleague_sample() -> None:
    line("\n=== 9. EuroLeague game sample ===")
    from curl_cffi.requests import AsyncSession
    async with AsyncSession(impersonate="chrome131", timeout=20) as s:
        r = await s.get("https://api-live.euroleague.net/v2/competitions/E/seasons/E2024/games")
        games = r.json().get("data") or []
        played = [g for g in games if g.get("played")]
        line(f"  E2024: {len(games)} games, {len(played)} played")
        if played:
            g = played[0]
            line("  local:", json.dumps(g.get("local"))[:700])
            line("  date:", g.get("date"), "neutral:", g.get("isNeutralVenue"))


async def pipeline() -> None:
    """The server's own code against live SportyBet: yesterday's results,
    the listing with every market, and the predictions built from it."""
    line("\n=== 10. The server's pipeline, live ===")
    from collections import Counter as C
    import basketball_data as bd
    import basketball_predictions as bp
    day = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    games = await bd.fetch_results_day(day)
    line(f"  results {day}: {len(games)} games, {sum(1 for g in games if g['q'])} with quarters, "
         f"{sum(1 for g in games if g['ot'])} overtime; sample {json.dumps(games[:1])[:300]}")
    events, report = await bd.fetch_upcoming()
    line(f"  listing: {len(events)} events · {' · '.join(report)}")
    per = [len(e.get("markets") or []) for e in events]
    ids = C(str(m.get("id")) for e in events for m in e.get("markets") or [])
    line(f"  markets per event: min {min(per or [0])}, median {sorted(per)[len(per) // 2] if per else 0}, max {max(per or [0])}")
    line(f"  market ids in the listing: {dict(ids.most_common())}")
    preds = bp.build(events, {})
    lines = [len(p['bb_markets']) for p in preds]
    fam = C(x["family"] for p in preds for x in p["bb_markets"])
    line(f"  predictions: {len(preds)} (market-only, no ratings here), lines kept per match: "
         f"median {sorted(lines)[len(lines) // 2] if lines else 0}; by family {dict(fam)}")
    for p in preds[:3]:
        top = sorted(p["bb_markets"], key=lambda x: -x["prob"])[:4]
        line(f"  {p['league']}: {p['home']} v {p['away']} exp {p['exp_home_pts']}-{p['exp_away_pts']} "
             f"p_home {p['p_home']} · " + "; ".join(f"{x['label']} {x['prob']:.0%} @{x['odds']}" for x in top))
    # The first match's lines booked for real (a share code, no bet placed)
    import sportybet
    if preds:
        p = preds[0]
        picks = [x for x in p["bb_markets"] if x["family"] in ("bb_total", "bb_handicap")][:1]
        try:
            share = await sportybet.share_selections([x["sb"] for x in picks])
            line(f"  booking a {picks[0]['label']} line: code {share.get('code')}")
        except Exception as e:
            line(f"  booking test: {e}")


async def main(pages: int, parts: str) -> None:
    every = {"listing": lambda: sportybet_listing(pages), "results": sportybet_results, "espn": espn,
             "others": others, "logos": logos, "depth": results_depth, "espn_debug": espn_debug,
             "crests": crests, "euroleague": euroleague_sample, "pipeline": pipeline}
    chosen = [every[p] for p in parts.split(",")] if parts else list(every.values())
    for part in chosen:
        try:
            await part()
        except Exception as e:
            line(f"part failed: {e}")
    with open("basketball_probe.json", "w") as f:
        json.dump(OUT, f, indent=1, default=str)
    line("\nFull detail: basketball_probe.json")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=10)
    ap.add_argument("--parts", default="", help="comma-separated: " + "listing,results,espn,others,logos,depth,espn_debug,crests,euroleague")
    a = ap.parse_args()
    asyncio.run(main(a.pages, a.parts))
