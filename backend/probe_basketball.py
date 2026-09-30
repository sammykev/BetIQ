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


_PROP = re.compile(r"player|scorer|score|shot|assist|card|booked|rebound|point|3-point|block|steal|foul|tackle|save|header", re.I)


async def _prop_markets(sport_id: str, events: List[Dict], label: str, pages: int = 6) -> None:
    """Player markets on a spread of big matches' pages: id, name, specifier, outcomes."""
    session = sportybet.shared_session()
    seen: Dict[str, Dict] = {}
    done = 0
    for e in events:
        if done >= pages:
            break
        try:
            page = await sportybet.event_page(e["eventId"], session)
        except Exception as ex:
            line(f"  page {e['eventId']}: {ex}")
            continue
        ms = (page or {}).get("markets") or []
        props = [m for m in ms if "player" in (m.get("specifier") or "") or "player" in str(m.get("desc")).lower()
                 or "scorer" in str(m.get("desc")).lower() or "playerprops" in json.dumps(m.get("outcomes") or [])[:400]]
        line(f"  {label} {e.get('_tournament')}: {e['homeTeamName']} v {e['awayTeamName']}: {len(ms)} markets, {len(props)} player")
        done += 1
        for m in props:
            k = seen.setdefault(str(m.get("id")), {"desc": m.get("desc"), "n": 0, "specs": [], "outs": []})
            k["n"] += 1
            if len(k["specs"]) < 3:
                k["specs"].append(m.get("specifier"))
            if len(k["outs"]) < 4:
                k["outs"] += [(o.get("id"), o.get("desc"), o.get("odds")) for o in (m.get("outcomes") or [])[:2]]
    for mid, k in sorted(seen.items(), key=lambda kv: -kv[1]["n"]):
        line(f"    {mid:>6} x{k['n']} {k['desc']} | specs {k['specs']} | outs {json.dumps(k['outs'])[:300]}")
    OUT.setdefault("props", {})[label] = seen


async def props() -> None:
    """Player props: SportyBet's markets (football and basketball), and where
    per-player match stats come from (ESPN box scores, EuroLeague, Understat)."""
    line("\n=== 11. Player props ===")
    session = sportybet.shared_session()
    fb, _ = await sportybet.fetch_catalog(session)
    big = re.compile(r"premier league|laliga|serie a|bundesliga|ligue 1|champions league", re.I)
    fb = [e for e in fb if big.search(e.get("_tournament") or "") and "women" not in (e.get("_tournament") or "").lower()]
    spread, ts = [], set()
    for e in fb:
        if e.get("_tournament") not in ts:
            spread.append(e)
            ts.add(e.get("_tournament"))
    await _prop_markets(sportybet.FOOTBALL, spread + fb, "football", 6)
    bb, _, _ = await sportybet._paged(session, "/factsCenter/pcUpcomingEvents", {
        "sportId": BASKETBALL, "marketId": "219", "pageSize": 100, "todayGames": "false"}, 3)
    top = re.compile(r"nba|euroleague|eurocup|wnba|acb|nbl", re.I)
    bb = sorted([e for e in bb if top.search(e.get("_tournament") or "")], key=lambda e: -(e.get("totalMarketSize") or 0))
    await _prop_markets(BASKETBALL, bb, "basketball", 6)

    import httpx
    async with httpx.AsyncClient(timeout=25) as c:
        # ESPN box scores: NBA and a football league
        for sport, slug, day in (("basketball", "nba", "20260301"), ("soccer", "eng.1", "20260301"),
                                 ("soccer", "esp.1", "20260301"), ("basketball", "wnba", "20250715")):
            try:
                sb = (await c.get(f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{slug}/scoreboard",
                                  params={"dates": day})).json()
                ev = (sb.get("events") or [None])[0]
                if not ev:
                    line(f"  espn {slug} {day}: no events")
                    continue
                d = (await c.get(f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{slug}/summary",
                                 params={"event": ev["id"]})).json()
                if sport == "basketball":
                    teams = (d.get("boxscore") or {}).get("players") or []
                    st = (teams[0].get("statistics") or [{}])[0] if teams else {}
                    ath = (st.get("athletes") or [{}])[0]
                    line(f"  espn {slug}: {ev.get('name')}: stat keys {st.get('keys') or st.get('labels')}; "
                         f"first {(ath.get('athlete') or {}).get('displayName')} {ath.get('stats')}; players "
                         f"{sum(len((t.get('statistics') or [{}])[0].get('athletes') or []) for t in teams)}")
                else:
                    rosters = d.get("rosters") or []
                    r0 = (rosters[0].get("roster") or [{}]) if rosters else [{}]
                    p0 = r0[0]
                    line(f"  espn {slug}: {ev.get('name')}: {len(rosters)} rosters, {sum(len(r.get('roster') or []) for r in rosters)} players; "
                         f"first {(p0.get('athlete') or {}).get('displayName')} starter={p0.get('starter')} "
                         f"stats {[(x.get('name'), x.get('value')) for x in p0.get('stats') or []][:20]}")
            except Exception as ex:
                line(f"  espn {slug}: {ex}")
        for name, url in (("euroleague boxscore v1", "https://live.euroleague.net/api/Boxscore?gamecode=1&seasoncode=E2024"),
                          ("euroleague stats v2", "https://api-live.euroleague.net/v2/competitions/E/seasons/E2024/games/1/stats"),
                          ("understat EPL", "https://understat.com/league/EPL/2025"),
                          ("understat match", "https://understat.com/match/28000")):
            try:
                r = await c.get(url, headers={"User-Agent": "Mozilla/5.0"})
                body = r.text
                line(f"  {name}: HTTP {r.status_code} {len(body)} bytes {body[:220]!r}")
            except Exception as ex:
                line(f"  {name}: {ex}")


async def live() -> None:
    """Live basketball scores: which listing answers, and its fields."""
    line("\n=== Live basketball on SportyBet ===")
    session = sportybet.shared_session()
    keep = ("eventId", "homeTeamName", "awayTeamName", "estimateStartTime", "status", "matchStatus", "setScore",
            "gameScore", "playedSeconds", "remainingTimeInPeriod", "period", "pointScore", "ballPossession")
    OUT["live"] = {}
    found_any: List[Dict] = []
    for path, params in (("/factsCenter/liveOrPrematchEvents", {"sportId": BASKETBALL}),
                         ("/factsCenter/liveOrPrematchEvents", {"sportId": BASKETBALL, "productId": 1}),
                         ("/factsCenter/wapConfigurableIndexLiveEvents", {"sportId": BASKETBALL}),
                         ("/factsCenter/pcLiveEvents", {"sportId": BASKETBALL}),
                         ("/factsCenter/wapConfigurableLiveEvents", {"sportId": BASKETBALL})):
        try:
            data = await sportybet._request(session, "GET", path, params={**params, "_t": sportybet._now_ms()})
        except Exception as e:
            line(f"  {path} {params}: {e}")
            continue
        found: List[Dict] = []
        sportybet._collect_events(data.get("data"), found)
        line(f"  {path} {params}: bizCode {data.get('bizCode')} · {len(found)} events")
        for e in found[:3]:
            line("    " + json.dumps({k: e.get(k) for k in keep if k in e}, default=str)[:600])
        OUT["live"][f"{path} {params}"] = {"n": len(found), "sample": [{k: e.get(k) for k in keep if k in e} for e in found[:10]],
                                          "keys": sorted(found[0].keys()) if found else [],
                                          "raw": json.dumps(data, default=str)[:3000] if not found else None}
        found_any = found_any or found
    # One live event's own page (productId 1: live)
    for e in found_any[:2]:
        try:
            data = await sportybet._request(session, "GET", "/factsCenter/event",
                                            params={"eventId": e["eventId"], "productId": 1})
            ev = data.get("data") or {}
            line(f"  event page {e['eventId']}: " + json.dumps({k: ev.get(k) for k in keep if k in ev}, default=str)[:600])
        except Exception as ex:
            line(f"  event page: {ex}")


async def history() -> None:
    """Deep history (10 seasons) for the leagues we price: which sources
    answer from here, how far back, and whether games carry quarter scores."""
    import os
    line("\n=== Deep history sources ===")
    from curl_cffi.requests import AsyncSession
    OUT["history"] = {}
    # The leagues we price now (the server's rated leagues), busiest first
    ours: List[Any] = []
    if os.environ.get("UPSTASH_REDIS_URL"):
        try:
            import redis
            import basketball_data as bd
            r = redis.from_url(os.environ["UPSTASH_REDIS_URL"])
            ours = sorted(((d["name"], d.get("n", 0)) for d in bd.decode(r.get("betiq:bb:model"))), key=lambda t: -t[1])
        except Exception as e:
            line(f"  our leagues: {e}")
    line(f"  our rated leagues: {len(ours)}")
    for name, n in ours:
        line(f"    {name}: {n} games")
    OUT["history"]["ours"] = ours

    # ESPN: every basketball league it has, one mid-season day per year back to 2015
    async with AsyncSession(timeout=25) as s:     # a plain client (ESPN answers it)
        r = await s.get("https://sports.core.api.espn.com/v2/sports/basketball/leagues", params={"limit": 300})
        slugs = []
        for item in (r.json().get("items") or []) if r.status_code == 200 else []:
            try:
                d = (await s.get(item["$ref"].replace("http://", "https://"))).json()
                slugs.append((d.get("slug"), d.get("name")))
            except Exception:
                continue
        line(f"  ESPN: {len(slugs)} leagues")
        OUT["history"]["espn"] = {}
        for slug, name in slugs:
            per_year = {}
            for year in range(2015, 2026):
                got = 0
                for md in ("0115", "0315", "0715", "1115"):
                    try:
                        sr = await s.get(f"https://site.api.espn.com/apis/site/v2/sports/basketball/{slug}/scoreboard",
                                         params={"dates": f"{year}{md}", "limit": 500, "groups": 50})
                        evs = (sr.json().get("events") or []) if sr.status_code == 200 else []
                    except Exception:
                        evs = []
                    got += sum(1 for e in evs if ((e.get("competitions") or [{}])[0].get("status") or {})
                               .get("type", {}).get("completed"))
                per_year[year] = got
            if any(per_year.values()):
                line(f"    {slug:28s} {str(name)[:34]:34s} " + " ".join(f"{y % 100:02d}:{n}" for y, n in per_year.items()))
            OUT["history"]["espn"][slug] = {"name": name, "per_year": per_year}

    async with AsyncSession(impersonate="chrome131", timeout=25) as s:
        # EuroLeague / EuroCup: a season per request
        OUT["history"]["euroleague"] = {}
        for comp in ("E", "U"):
            for year in (2015, 2019, 2024):
                try:
                    r = await s.get(f"https://api-live.euroleague.net/v2/competitions/{comp}/seasons/{comp}{year}/games")
                    games = [g for g in (r.json().get("data") or []) if g.get("played")]
                    loc = (games[0].get("local") or {}) if games else {}
                    line(f"    euroleague {comp}{year}: HTTP {r.status_code}, {len(games)} played; local keys {sorted(loc.keys())}")
                    OUT["history"]["euroleague"][f"{comp}{year}"] = {"played": len(games), "local": loc}
                except Exception as e:
                    line(f"    euroleague {comp}{year}: {e}")

        # SofaScore: the other leagues (Spain, Italy, Germany, France, Greece, Turkey, ABA, Australia, China, ...)
        OUT["history"]["sofascore"] = {}
        base = "https://api.sofascore.com/api/v1"
        tournaments: Dict[int, str] = {}
        for day in ("2025-01-18", "2025-03-01", "2025-11-15"):
            try:
                r = await s.get(f"{base}/sport/basketball/scheduled-events/{day}")
                line(f"    sofascore {day}: HTTP {r.status_code}")
                for e in (r.json().get("events") or []) if r.status_code == 200 else []:
                    ut = (e.get("tournament") or {}).get("uniqueTournament") or {}
                    if ut.get("id"):
                        cat = ((e.get("tournament") or {}).get("category") or {}).get("name")
                        tournaments[ut["id"]] = f"{cat} · {ut.get('name')}"
            except Exception as ex:
                line(f"    sofascore {day}: {ex}")
        line(f"    sofascore: {len(tournaments)} tournaments seen")
        for tid, tname in list(tournaments.items())[:120]:
            try:
                r = await s.get(f"{base}/unique-tournament/{tid}/seasons")
                seasons = (r.json().get("seasons") or []) if r.status_code == 200 else []
                sample = None
                if len(seasons) > 8:
                    old = seasons[min(9, len(seasons) - 1)]
                    er = await s.get(f"{base}/unique-tournament/{tid}/season/{old['id']}/events/last/0")
                    evs = (er.json().get("events") or []) if er.status_code == 200 else []
                    if evs:
                        hs = evs[0].get("homeScore") or {}
                        sample = {"season": old.get("year"), "events": len(evs), "quarters": "period4" in hs}
                line(f"      {tid:6d} {tname[:48]:48s} seasons {len(seasons):3d} "
                     f"{seasons[-1].get('year') if seasons else ''}..{seasons[0].get('year') if seasons else ''} {sample or ''}")
                OUT["history"]["sofascore"][tid] = {"name": tname, "seasons": len(seasons), "sample": sample,
                                                   "years": [x.get("year") for x in seasons[:12]]}
            except Exception as ex:
                line(f"      {tid} {tname}: {ex}")


async def history2() -> None:
    """Second look at deep-history sources, each with the client it answers."""
    import os
    import httpx
    line("\n=== Deep history, second look ===")
    OUT["history2"] = {}
    async with httpx.AsyncClient(timeout=25, follow_redirects=True) as c:
        # ESPN (plain client): its leagues, finished games on one mid-season day per year
        r = await c.get("https://sports.core.api.espn.com/v2/sports/basketball/leagues", params={"limit": 300})
        slugs = []
        for item in (r.json().get("items") or []) if r.status_code == 200 else []:
            try:
                d = (await c.get(item["$ref"].replace("http://", "https://"))).json()
                slugs.append((d.get("slug"), d.get("name")))
            except Exception:
                continue
        line(f"  ESPN leagues: {slugs}")
        for slug, name in slugs:
            per = {}
            for year in (2015, 2018, 2021, 2024):
                got = 0
                for md in ("0120", "0310", "0715", "1120"):
                    try:
                        sr = await c.get(f"https://site.api.espn.com/apis/site/v2/sports/basketball/{slug}/scoreboard",
                                         params={"dates": f"{year}{md}", "limit": 500})
                        evs = (sr.json().get("events") or []) if sr.status_code == 200 else []
                    except Exception:
                        evs = []
                    got += len(evs)
                per[year] = got
            # A week in one request?
            try:
                wr = await c.get(f"https://site.api.espn.com/apis/site/v2/sports/basketball/{slug}/scoreboard",
                                 params={"dates": "20240115-20240121", "limit": 1000})
                week = len(wr.json().get("events") or []) if wr.status_code == 200 else f"HTTP {wr.status_code}"
            except Exception as e:
                week = str(e)[:60]
            line(f"    {slug:28s} {str(name)[:30]:30s} {per} week-range: {week}")
            OUT["history2"].setdefault("espn", {})[slug] = {"name": name, "per_year": per, "week": week}

        # EuroLeague / EuroCup: other feeds, paced
        for url in ("https://api-live.euroleague.net/v2/competitions/E/seasons/E2016/games",
                    "https://feeds.incrowdsports.com/provider/euroleague-feeds/v2/competitions/E/seasons/E2016/games",
                    "https://api-live.euroleague.net/v1/results?seasonCode=E2016&gameNumber=1",
                    "https://api-live.euroleague.net/v2/competitions/U/seasons/U2016/games"):
            await asyncio.sleep(2)
            try:
                rr = await c.get(url, headers={"Accept": "application/json"})
                body = rr.text or ""
                n = ""
                if body.lstrip().startswith("{"):
                    n = f"{len(rr.json().get('data') or [])} games"
                line(f"    {url[:95]}: HTTP {rr.status_code} {len(body)}b {n} {body[:120]!r}")
            except Exception as e:
                line(f"    {url[:95]}: {e}")

        # API-Basketball (API-Sports: the same account key as API-Football)
        key = os.environ.get("APIFOOTBALL_KEY", "")
        if key:
            h = {"x-apisports-key": key}
            base = "https://v1.basketball.api-sports.io"
            try:
                st = (await c.get(f"{base}/status", headers=h)).json()
                resp = st.get("response") or {}
                line(f"    api-basketball status: plan {(resp.get('subscription') or {}).get('plan')}, "
                     f"requests {(resp.get('requests') or {})}, errors {st.get('errors')}")
                lg = (await c.get(f"{base}/leagues", headers=h)).json()
                leagues = lg.get("response") or []
                line(f"    api-basketball leagues: {len(leagues)}; errors {lg.get('errors')}")
                OUT["history2"]["apibb_leagues"] = [
                    {"id": x.get("id"), "name": x.get("name"), "country": (x.get("country") or {}).get("name"),
                     "seasons": [s.get("season") for s in x.get("seasons") or []]} for x in leagues]
                for lid, season in ((12, "2015-2016"), (12, "2023-2024"), (117, "2016-2017")):
                    g = (await c.get(f"{base}/games", headers=h, params={"league": lid, "season": season})).json()
                    games = g.get("response") or []
                    sample = games[0].get("scores") if games else None
                    line(f"    api-basketball games league {lid} {season}: {len(games)}; errors {g.get('errors')}; "
                         f"sample scores {json.dumps(sample)[:300] if sample else ''}")
            except Exception as e:
                line(f"    api-basketball: {e}")
        else:
            line("    api-basketball: no APIFOOTBALL_KEY here")

        # FlashScore and SofaScore: answer at all?
        for name, url, hdr in (("flashscore", "https://d.flashscore.com/x/feed/f_3_-1_3_en_1", {"x-fsign": "SW9D1eZo"}),
                               ("sofascore", "https://www.sofascore.com/api/v1/sport/basketball/scheduled-events/2025-03-01", {})):
            try:
                rr = await c.get(url, headers={**hdr, "User-Agent": "Mozilla/5.0", "Referer": "https://www.flashscore.com/"})
                line(f"    {name}: HTTP {rr.status_code} {len(rr.text or '')}b {(rr.text or '')[:100]!r}")
            except Exception as e:
                line(f"    {name}: {e}")


async def livestats() -> None:
    """Live match statistics for every sport: which sources answer for the
    events SportyBet has in play (its ids are Sportradar's, "sr:match:N")."""
    from curl_cffi.requests import AsyncSession
    line("\n=== Live stats sources ===")
    session = sportybet.shared_session()
    sports = {"football": "sr:sport:1", "basketball": BASKETBALL, "tennis": "sr:sport:5", "table_tennis": "sr:sport:20"}
    OUT["livestats"] = {}
    sb = {"Origin": "https://www.sportybet.com", "Referer": "https://www.sportybet.com/"}

    async def show(hc, label, url, headers=None, n=700):
        try:
            resp = await hc.get(url, headers=headers or {})
            line(f"    {label}: HTTP {resp.status_code} len {len(resp.text)} :: {resp.text[:n]!r}")
            return resp
        except Exception as ex:
            line(f"    {label}: {str(ex)[:200]}")
            return None

    async with AsyncSession(impersonate="chrome", timeout=20) as hc:
        picked: Dict[str, List[Dict]] = {}
        for sport, sid in sports.items():
            try:
                data = await sportybet._request(session, "GET", "/factsCenter/liveOrPrematchEvents",
                                                params={"sportId": sid, "_t": sportybet._now_ms()})
                found: List[Dict] = []
                sportybet._collect_events(data.get("data"), found)
            except Exception as e:
                found = []
            picked[sport] = [e for e in found if str(e.get("eventId", "")).rsplit(":", 1)[-1].isdigit()
                             and len(str(e.get("eventId")).rsplit(":", 1)[-1]) <= 9][:1]
        line("  picked: " + json.dumps({k: [(e["eventId"], e.get("homeTeamName"), e.get("awayTeamName")) for e in v] for k, v in picked.items()}))

        # Sportradar stats host: the full stats doc per sport
        for sport, evs in picked.items():
            for e in evs:
                num = str(e["eventId"]).rsplit(":", 1)[-1]
                line(f"\n  -- stats.fn {sport} {num} {e.get('homeTeamName')} v {e.get('awayTeamName')}")
                for feed in ("match_detailsextended", "match_timelinedelta", "match_info"):
                    await show(hc, feed, f"https://stats.fn.sportradar.com/common/en/Etc:UTC/gismo/{feed}/{num}", None, 6000)
        return
        # Sportradar: hosts, client aliases, feeds
        for sport, evs in picked.items():
            for e in evs:
                num = str(e["eventId"]).rsplit(":", 1)[-1]
                line(f"\n  -- Sportradar {sport} {num}")
                for host in ("lmt.fn.sportradar.com", "stats.fn.sportradar.com", "widgets.fn.sportradar.com"):
                    for alias in ("common", "sportybet", "betradar", "demolmt", "sportradar"):
                        await show(hc, f"{host}/{alias} match_detailsextended",
                                   f"https://{host}/{alias}/en/Etc:UTC/gismo/match_detailsextended/{num}", sb, 300)
                    if sport == "football":
                        break
        # SportyBet's own stats endpoints (guesses; the site's match tracker)
        for sport, evs in picked.items():
            for e in evs[:1]:
                eid = e["eventId"]
                for path in ("/factsCenter/eventStatistics", "/factsCenter/statistics", "/factsCenter/matchStatistics",
                             "/factsCenter/liveMatchStatistics", "/factsCenter/eventTimeline"):
                    try:
                        d = await sportybet._request(session, "GET", path, params={"eventId": eid})
                        line(f"    sportybet {path} {sport}: " + json.dumps(d, default=str)[:300])
                    except Exception as ex:
                        line(f"    sportybet {path} {sport}: {str(ex)[:150]}")
        # SofaScore on other hosts / fingerprints
        line("\n  -- SofaScore")
        for imp in ("chrome", "safari", "chrome_android"):
            async with AsyncSession(impersonate=imp, timeout=20) as h2:
                for host in ("https://api.sofascore.com/api/v1", "https://www.sofascore.com/api/v1", "https://api.sofascore.app/api/v1"):
                    await show(h2, f"{imp} {host} tennis live", f"{host}/sport/tennis/events/live",
                               {"Referer": "https://www.sofascore.com/"}, 200)
        # Flashscore feeds (x-fsign), sport ids: 1 football, 3 basketball, 2 tennis, 25 table tennis
        line("\n  -- Flashscore")
        fs = {"x-fsign": "SW9D1eZo", "Referer": "https://www.flashscore.com/"}
        for host in ("https://d.flashscore.com/x/feed", "https://global.flashscore.ninja/2/x/feed",
                     "https://local-global.flashscore.ninja/2/x/feed", "https://2.flashscore.ninja/2/x/feed"):
            resp = await show(hc, f"{host} tennis today", f"{host}/f_2_0_3_en_1", fs, 300)
            if resp is not None and resp.status_code == 200 and "~AA÷" in resp.text:
                ids = re.findall(r"~AA÷([A-Za-z0-9]{8})", resp.text)
                line(f"    {len(ids)} tennis events; first {ids[:3]}")
                for fid in ids[:1]:
                    for feed in (f"df_st_1_{fid}", f"df_st_2_{fid}", f"dc_1_{fid}"):
                        await show(hc, f"{host} {feed}", f"{host}/{feed}", fs, 600)
                for sid_, name in (("3", "basketball"), ("25", "table tennis")):
                    r2 = await show(hc, f"{host} {name} today", f"{host}/f_{sid_}_0_3_en_1", fs, 120)
                    if r2 is not None and r2.status_code == 200:
                        ids2 = re.findall(r"~AA÷([A-Za-z0-9]{8})", r2.text)
                        line(f"    {len(ids2)} {name} events")
                        for fid in ids2[:1]:
                            await show(hc, f"{host} {name} df_st_1", f"{host}/df_st_1_{fid}", fs, 600)
                break
        # ESPN: tennis and basketball scoreboards (which have per-match stats)
        line("\n  -- ESPN")
        for path in ("tennis/atp", "tennis/wta", "basketball/nbl", "basketball/fiba"):
            resp = await show(hc, f"espn {path}", f"https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard", None, 0)
            if resp is not None and resp.status_code == 200:
                j = resp.json()
                evs = j.get("events") or []
                comps = [c for ev in evs for g in (ev.get("groupings") or [{"competitions": ev.get("competitions") or []}]) for c in g.get("competitions") or []]
                live = [c for c in comps if ((c.get("status") or {}).get("type") or {}).get("state") == "in"]
                line(f"    {len(evs)} events, {len(comps)} competitions, {len(live)} live")
                for c in (live or comps)[:1]:
                    cs = c.get("competitors") or []
                    line("    stats keys: " + json.dumps([[s.get("name") for s in (x.get("statistics") or [])] for x in cs])[:400])


async def main(pages: int, parts: str) -> None:
    every = {"listing": lambda: sportybet_listing(pages), "results": sportybet_results, "espn": espn,
             "others": others, "logos": logos, "depth": results_depth, "espn_debug": espn_debug,
             "crests": crests, "euroleague": euroleague_sample, "pipeline": pipeline,
             "props": props, "live": live, "livestats": livestats, "history": history, "history2": history2}
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
