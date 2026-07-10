"""
Multi-sport predictions using The Odds API.
Covers: Basketball (NBA, EuroLeague, NCAAB, NBL), Lawn Tennis (ATP/WTA), Table Tennis.

Requires: ODDS_API_KEY env var (free tier: 500 req/month).
Uses implied bookmaker odds as model probabilities (very accurate for these sports).
"""

import os
import asyncio
import httpx
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

ODDS_API_KEY  = os.getenv("ODDS_API_KEY", "")
ODDS_BASE     = "https://api.the-odds-api.com/v4"

# ── BetsAPI — free, no key, covers all tennis + table tennis year-round ────
BETSAPI_BASE  = "https://api.betsapi.com/v3"
BETSAPI_TOKEN = os.getenv("BETSAPI_TOKEN", "")  # optional paid token for higher limits

# ── Basketball leagues ─────────────────────────────────────────────────────
# Used only as a fallback when live discovery (below) can't reach The Odds API.
# Deliberately NOT the source of truth — a fixed list always misses seasonal
# competitions (e.g. NBA Summer League only runs in July) and new leagues The
# Odds API adds over time. fetch_basketball_predictions() discovers whatever
# basketball leagues are actually active right now instead.
BASKETBALL_SPORTS = [
    ("basketball_nba",          "NBA",          "🏀"),
    ("basketball_euroleague",   "EuroLeague",   "🏀"),
    ("basketball_ncaab",        "NCAA Basketball","🏀"),
    ("basketball_nbl",          "NBL",          "🏀"),
    ("basketball_wnba",         "WNBA",         "🏀"),
    ("basketball_euroleague_women", "EuroLeague Women", "🏀"),
]

# ── Tennis: we discover active tournaments via /sports endpoint ────────────
TENNIS_KEYWORDS  = ["tennis_atp", "tennis_wtp", "tennis_itf"]
TABLE_TENNIS_KEY = "table_tennis"


async def _get_active_sports() -> List[str]:
    """Fetch all currently active sport keys from The Odds API."""
    sports = await _get_active_sports_full()
    return [s["key"] for s in sports]


async def _get_active_sports_full() -> List[Dict]:
    """
    Fetch all currently active sports from The Odds API, full objects
    ({key, group, title, ...}) — "active" here means the sport currently has
    games scheduled, which is exactly how a seasonal league like NBA Summer
    League shows up automatically in July and disappears afterward without
    us having to track its calendar.
    """
    if not ODDS_API_KEY:
        return []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{ODDS_BASE}/sports/",
                                 params={"apiKey": ODDS_API_KEY, "all": "false"})
            if r.status_code == 200:
                return r.json()
    except Exception as e:
        print(f"[Sports] active sports fetch error: {e}")
    return []


async def _fetch_odds(sport_key: str, markets: str = "h2h,totals") -> List[Dict]:
    """Fetch odds for a sport from The Odds API."""
    if not ODDS_API_KEY:
        return []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{ODDS_BASE}/sports/{sport_key}/odds/",
                params={
                    "apiKey":      ODDS_API_KEY,
                    "regions":     "eu",
                    "markets":     markets,
                    "oddsFormat":  "decimal",
                    "dateFormat":  "iso",
                },
            )
            remaining = r.headers.get("x-requests-remaining", "?")
            if r.status_code == 200:
                data = r.json()
                # The Odds API's pre-match /odds/ feed can lag in pulling events
                # once they start (and our own 1h response cache compounds this),
                # so defensively drop anything whose commence_time has already
                # passed — otherwise finished/in-play games linger in the list.
                now = datetime.now(timezone.utc)
                upcoming = []
                for ev in data:
                    ct = ev.get("commence_time", "")
                    try:
                        start = datetime.fromisoformat(ct.replace("Z", "+00:00"))
                    except Exception:
                        continue  # no parseable start time — skip rather than risk a stale/played game
                    if start > now:
                        upcoming.append(ev)
                dropped = len(data) - len(upcoming)
                print(f"[Sports] {sport_key}: {len(upcoming)} upcoming events "
                      f"({dropped} already started/unparsed dropped, quota remaining={remaining})")
                return upcoming
            elif r.status_code == 422:
                return []   # sport not currently active
            else:
                print(f"[Sports] {sport_key}: HTTP {r.status_code}")
    except Exception as e:
        print(f"[Sports] {sport_key} error: {e}")
    return []


def _best_odds(event: Dict, market_key: str = "h2h") -> Dict[str, float]:
    """Extract best (highest) decimal odds per outcome across all bookmakers."""
    best: Dict[str, float] = {}
    for bookie in (event.get("bookmakers") or []):
        for market in (bookie.get("markets") or []):
            if market.get("key") != market_key:
                continue
            for o in (market.get("outcomes") or []):
                name  = o.get("name", "")
                price = float(o.get("price") or 0)
                if price > 1:
                    best[name] = max(best.get(name, 0), price)
    return best


def _best_market_with_points(event: Dict, market_key: str) -> Dict[str, Dict]:
    """
    Like _best_odds, but also keeps each outcome's point/line — needed for
    spreads (and totals), where the number (-4.5, +4.5, ...) matters as much
    as the price. Returns {name: {"price": float, "point": float|None}}.
    """
    best: Dict[str, Dict] = {}
    for bookie in (event.get("bookmakers") or []):
        for market in (bookie.get("markets") or []):
            if market.get("key") != market_key:
                continue
            for o in (market.get("outcomes") or []):
                name  = o.get("name", "")
                price = float(o.get("price") or 0)
                point = o.get("point")
                if price > 1:
                    existing = best.get(name, {})
                    if price > existing.get("price", 0):
                        best[name] = {"price": price, "point": point}
    return best


def _implied_probs(odds: Dict[str, float]) -> Dict[str, float]:
    """Convert decimal odds to overround-adjusted implied probabilities."""
    raw = {k: 1 / v for k, v in odds.items() if v > 1}
    total = sum(raw.values())
    if total <= 0:
        return raw
    return {k: v / total for k, v in raw.items()}


def _event_date(commence_time: str) -> tuple[str, str]:
    """Return (YYYY-MM-DD, HH:MM) in UTC from an ISO timestamp."""
    try:
        dt = datetime.fromisoformat(commence_time.replace("Z", "+00:00")).astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")
    except Exception:
        return "", ""


# ── Basketball ─────────────────────────────────────────────────────────────

def _apply_elo_blend(home: str, away: str, market_p_home: float, market_p_away: float) -> Dict:
    """Try to blend Elo ratings into market probabilities for basketball."""
    try:
        from basketball_predictor import get_basketball_elo, blend_with_market
        elo = get_basketball_elo()
        if elo:
            elo_pred = elo.predict(home, away)
            if elo_pred and (elo_pred.get("games_home", 0) > 5 or elo_pred.get("games_away", 0) > 5):
                return blend_with_market(elo_pred, market_p_home, market_p_away)
    except Exception:
        pass
    return {"p_home": market_p_home, "p_away": market_p_away, "elo_home": None, "elo_away": None}


SAFE_CONFIDENCE_THRESHOLD = 0.68  # model backs the market favorite with real conviction
UPSET_MARKET_THRESHOLD    = 0.42  # market prices the model's pick as a clear underdog


def _classify_pick(tip_code: str, p_home: float, p_away: float,
                    market_p_home: float, market_p_away: float) -> Optional[str]:
    """
    "safe": the model's pick is also the market's favorite, with strong
    model confidence behind it — a low-drama, high-likelihood outcome.

    "upset": the model's pick is priced as the underdog by the market — the
    model thinks the team the crowd doesn't expect to win, wins anyway.

    Deliberately not a value-bet edge calculation (model prob vs. market
    implied prob, flagged whenever the model is a few points more confident
    than the market) — that framing rarely resulted in wins in practice and
    isn't the objective here. This is a simpler, more honest split of what
    the model is actually telling you: agree with the crowd with real
    conviction, or go against it outright. Returns None when neither applies
    (a close call the model doesn't have a strong opinion on either way).
    """
    tip_is_home = tip_code == "1"
    model_conf = p_home if tip_is_home else p_away
    market_p_tip = market_p_home if tip_is_home else market_p_away
    market_favorite_is_home = market_p_home >= market_p_away

    if tip_is_home == market_favorite_is_home:
        if model_conf >= SAFE_CONFIDENCE_THRESHOLD:
            return "safe"
        return None
    else:
        if market_p_tip <= UPSET_MARKET_THRESHOLD:
            return "upset"
        return None


def _build_basketball_prediction(event: Dict, league_name: str, flag: str) -> Optional[Dict]:
    """Convert an Odds API basketball event into a BetIQ prediction dict."""
    home = event.get("home_team", "")
    away = event.get("away_team", "")
    if not home or not away:
        return None

    date_str, time_str = _event_date(event.get("commence_time", ""))

    h2h_odds = _best_odds(event, "h2h")
    if len(h2h_odds) < 2:
        return None

    probs = _implied_probs(h2h_odds)
    market_p_home = probs.get(home, 0)
    market_p_away = probs.get(away, 0)

    # Blend with Elo model if we have basketball data
    blended = _apply_elo_blend(home, away, market_p_home, market_p_away)
    p_home = blended["p_home"]
    p_away = blended["p_away"]

    # Best tip
    if p_home >= p_away:
        tip, tip_code = f"{home} Win", "1"
        confidence = p_home
    else:
        tip, tip_code = f"{away} Win", "2"
        confidence = p_away

    pick_type = _classify_pick(tip_code, p_home, p_away, market_p_home, market_p_away)

    # Over/Under line
    totals = _best_odds(event, "totals")
    total_line = None
    tip_total  = None
    if totals:
        # Find the line (key looks like "Over 218.5" — extract the number)
        for k in totals:
            if k.startswith("Over"):
                try:
                    total_line = float(k.split()[-1])
                    over_odds  = totals.get(k, 0)
                    under_odds = totals.get(f"Under {total_line}", 0)
                    if over_odds and under_odds:
                        tip_total = "Over" if over_odds < under_odds else "Under"
                        tip_total = f"{tip_total} {total_line}"
                except Exception:
                    pass
                break

    # Point spread — shown as market info, not a model pick: the Elo/market
    # blend estimates win probability, not margin of victory, so we have no
    # real signal on which side of the spread to back.
    spreads = _best_market_with_points(event, "spreads")
    spread_home = spreads.get(home)
    spread_away = spreads.get(away)

    return {
        "home":            home,
        "away":            away,
        "date":            date_str,
        "time":            time_str,
        "sport":           "basketball",
        "league":          league_name,
        "league_name":     league_name,
        "flag":            flag,
        "tip_1x2":         tip,
        "tip_code":        tip_code,
        "tip_goals":       tip_total or "",
        "goals_type":      "value" if confidence > 0.65 else "normal",
        "goals_confidence": round(confidence, 3),
        "p_home":          round(p_home, 3),
        "p_draw":          0,
        "p_away":          round(p_away, 3),
        "p_over15":        0,
        "p_over25":        0,
        "total_line":      total_line,
        "odds_home":       round(h2h_odds.get(home, 0), 2),
        "odds_away":       round(h2h_odds.get(away, 0), 2),
        "spread_home":     {"point": spread_home["point"], "odds": round(spread_home["price"], 2)} if spread_home else None,
        "spread_away":     {"point": spread_away["point"], "odds": round(spread_away["price"], 2)} if spread_away else None,
        "pick_type":       pick_type,
        "elo_home":        blended.get("elo_home"),
        "elo_away":        blended.get("elo_away"),
        "elo_blend":       blended.get("blend_weight", 0),
    }


def _basketball_league_name(sport_key: str, title: str = "") -> str:
    """Human league name for a basketball sport key — prefers the API's own title."""
    if title:
        return title
    return (sport_key.replace("basketball_", "")
                     .replace("_", " ").title())


async def _discover_basketball_leagues() -> List[Tuple[str, str, str]]:
    """
    Return (sport_key, league_name, flag) for every basketball league The Odds
    API currently has games scheduled for. Discovered live rather than
    hardcoded so seasonal competitions (NBA Summer League only runs in July,
    EuroBasket only every 2 years, etc.) show up automatically while active
    and disappear on their own once the season ends — no manual list to
    maintain. Falls back to the static BASKETBALL_SPORTS list if discovery
    is unavailable (no API key, or The Odds API's /sports/ call fails).
    """
    try:
        active = await _get_active_sports_full()
    except Exception:
        active = []
    leagues = [
        (s["key"], _basketball_league_name(s["key"], s.get("title", "")), "🏀")
        for s in active
        if s.get("key", "").startswith("basketball_")
    ]
    if leagues:
        return leagues
    return BASKETBALL_SPORTS


async def fetch_basketball_predictions() -> List[Dict]:
    results = []
    leagues = await _discover_basketball_leagues()
    for sport_key, league_name, flag in leagues:
        events = await _fetch_odds(sport_key, markets="h2h,totals,spreads")
        for ev in events:
            p = _build_basketball_prediction(ev, league_name, flag)
            if p:
                results.append(p)
        await asyncio.sleep(0.2)
    print(f"[Sports] Basketball: {len(results)} predictions across {len(leagues)} leagues")
    return sorted(results, key=lambda x: x["date"] + x["time"])


# ── Tennis ─────────────────────────────────────────────────────────────────

def _build_tennis_prediction(event: Dict, league_name: str, flag: str,
                              sport: str = "tennis") -> Optional[Dict]:
    """Convert a tennis/table-tennis event into a BetIQ prediction dict."""
    p1 = event.get("home_team", "")  # In tennis API, home_team = player 1
    p2 = event.get("away_team", "")
    if not p1 or not p2:
        return None

    date_str, time_str = _event_date(event.get("commence_time", ""))

    odds = _best_odds(event, "h2h")
    if len(odds) < 2:
        return None

    probs    = _implied_probs(odds)
    market_p1 = probs.get(p1, 0)
    market_p2 = probs.get(p2, 0)

    # Detect surface from league name
    surface = "Hard"
    ln = league_name.lower()
    if any(x in ln for x in ["wimbledon", "halle", "queens", "grass"]):
        surface = "Grass"
    elif any(x in ln for x in ["roland", "french", "clay", "barcelona", "monte"]):
        surface = "Clay"

    # Blend with tennis Elo model if available
    blended = _apply_tennis_elo(p1, p2, surface, market_p1, market_p2)
    p_home  = blended["p1_win"]
    p_away  = blended["p2_win"]

    if p_home >= p_away:
        tip, tip_code = f"{p1} Win", "1"
        confidence = p_home
    else:
        tip, tip_code = f"{p2} Win", "2"
        confidence = p_away

    return {
        "home":            p1,
        "away":            p2,
        "date":            date_str,
        "time":            time_str,
        "sport":           sport,
        "league":          league_name,
        "league_name":     league_name,
        "flag":            flag,
        "tip_1x2":         tip,
        "tip_code":        tip_code,
        "tip_goals":       "",
        "goals_type":      "value" if confidence > 0.65 else "normal",
        "goals_confidence": round(confidence, 3),
        "p_home":          round(p_home, 3),
        "p_draw":          0,
        "p_away":          round(p_away, 3),
        "p_over15":        0,
        "p_over25":        0,
        "surface":         surface,
        "odds_home":       round(odds.get(p1, 0), 2),
        "odds_away":       round(odds.get(p2, 0), 2),
    }



# ── BetsAPI helpers ────────────────────────────────────────────────────────
# BetsAPI sport IDs: 13 = Tennis, 18 = Table Tennis
BETSAPI_SPORTS = {"tennis": 13, "table_tennis": 18}

async def _fetch_betsapi(sport_id: int) -> List[Dict]:
    """
    Fetch upcoming events + odds from BetsAPI (free, no auth required).
    Returns list of {home, away, date, time, league, p_home, p_away, odds_home, odds_away}.
    """
    results = []
    try:
        params: Dict = {"sport_id": sport_id, "token": BETSAPI_TOKEN or "1"}
        async with httpx.AsyncClient(timeout=15) as client:
            # Get upcoming events
            r = await client.get(f"{BETSAPI_BASE}/events/upcoming", params=params)
            if r.status_code != 200:
                return results
            data = r.json()
            events = data.get("results", [])
            print(f"[BetsAPI] sport_id={sport_id}: {len(events)} upcoming events")

            for ev in events[:80]:  # cap at 80 to stay within rate limits
                try:
                    home = ev.get("home", {}).get("name", "")
                    away = ev.get("away", {}).get("name", "")
                    league = ev.get("league", {}).get("name", "")
                    ts = ev.get("time", 0)
                    dt = datetime.fromtimestamp(int(ts), tz=timezone.utc) if ts else None
                    ev_date = dt.strftime("%Y-%m-%d") if dt else ""
                    ev_time = dt.strftime("%H:%M") if dt else "TBD"

                    if not home or not away:
                        continue

                    # Get odds for this event
                    odds_data = {}
                    ev_id = ev.get("id")
                    if ev_id:
                        or_ = await client.get(f"{BETSAPI_BASE}/event/odds",
                                               params={"token": params["token"], "event_id": ev_id,
                                                       "source": "bet365", "since": ""})
                        if or_.status_code == 200:
                            ods = or_.json().get("results", {}).get("odds", {})
                            # full_time odds: 1_1=home, 1_2=draw, 1_3=away
                            ft = ods.get("full_time", {})
                            if ft:
                                odds_data = {
                                    "home": float(ft.get("home_od") or ft.get("1_1") or 0),
                                    "away": float(ft.get("away_od") or ft.get("1_3") or 0),
                                }

                    if odds_data.get("home") and odds_data.get("away"):
                        raw_imp = {"h": 1/odds_data["home"], "a": 1/odds_data["away"]}
                        total = sum(raw_imp.values())
                        p_home = raw_imp["h"] / total
                        p_away = raw_imp["a"] / total
                    else:
                        p_home, p_away = 0.5, 0.5

                    results.append({
                        "home": home, "away": away,
                        "date": ev_date, "time": ev_time,
                        "league": league, "league_name": league,
                        "p_home": round(p_home, 3),
                        "p_away": round(p_away, 3),
                        "odds_home": round(odds_data.get("home", 0), 2),
                        "odds_away": round(odds_data.get("away", 0), 2),
                    })
                    await asyncio.sleep(0.05)
                except Exception:
                    continue
    except Exception as e:
        print(f"[BetsAPI] sport_id={sport_id} error: {e}")
    return results


def _apply_tennis_elo(p1: str, p2: str, surface: str,
                      market_p1: float, market_p2: float) -> Dict:
    """Blend tennis Elo with market probabilities."""
    try:
        from tennis_predictor import get_tennis_elo, blend_with_market
        elo = get_tennis_elo()
        if elo:
            pred = elo.predict(p1, p2, surface)
            if pred and (pred.get("matches_p1", 0) > 10 or pred.get("matches_p2", 0) > 10):
                return blend_with_market(pred, market_p1, market_p2)
    except Exception:
        pass
    return {"p1_win": market_p1, "p2_win": market_p2}


def _betsapi_to_prediction(ev: Dict, flag: str, sport: str) -> Optional[Dict]:
    """Convert a BetsAPI event dict to our unified prediction format."""
    home = ev.get("home", "")
    away = ev.get("away", "")
    if not home or not away:
        return None
    p_home = ev.get("p_home", 0.5)
    p_away = ev.get("p_away", 0.5)
    if p_home >= p_away:
        tip, tip_code = f"{home} Win", "1"
        confidence = p_home
    else:
        tip, tip_code = f"{away} Win", "2"
        confidence = p_away
    return {
        "home": home, "away": away,
        "date": ev.get("date", ""), "time": ev.get("time", "TBD"),
        "sport": sport,
        "league": ev.get("league", ""), "league_name": ev.get("league_name", ""),
        "flag": flag,
        "tip_1x2": tip, "tip_code": tip_code, "tip_goals": "",
        "goals_type": "value" if confidence > 0.65 else "normal",
        "goals_confidence": round(confidence, 3),
        "p_home": round(p_home, 3), "p_draw": 0, "p_away": round(p_away, 3),
        "p_over15": 0, "p_over25": 0,
        "odds_home": ev.get("odds_home", 0),
        "odds_away": ev.get("odds_away", 0),
        "source": "betsapi",
    }


FALLBACK_TENNIS_KEYS = [
    "tennis_atp_french_open", "tennis_wtp_french_open",
    "tennis_atp_wimbledon", "tennis_wtp_wimbledon",
    "tennis_atp_us_open", "tennis_wtp_us_open",
    "tennis_atp_australian_open", "tennis_wtp_australian_open",
    "tennis_atp_singles", "tennis_wtp_singles",
    "tennis_atp_rome", "tennis_wtp_rome",
    "tennis_atp_madrid", "tennis_wtp_madrid",
]

async def fetch_tennis_predictions() -> List[Dict]:
    results = []

    # Primary: BetsAPI (covers all ATP/WTA/ITF year-round)
    betsapi_events = await _fetch_betsapi(BETSAPI_SPORTS["tennis"])
    for ev in betsapi_events:
        p = _betsapi_to_prediction(ev, "🎾", "tennis")
        if p:
            results.append(p)

    # Secondary: The Odds API (for Grand Slams — better odds accuracy)
    if ODDS_API_KEY:
        try:
            active = await _get_active_sports()
            tennis_keys = [k for k in active if any(k.startswith(kw) for kw in TENNIS_KEYWORDS)]
        except Exception:
            tennis_keys = []
        if not tennis_keys:
            tennis_keys = FALLBACK_TENNIS_KEYS
        seen = {f"{p['home']}:{p['away']}:{p['date']}" for p in results}
        for sport_key in tennis_keys:
            label = (sport_key.replace("tennis_atp_", "ATP ")
                              .replace("tennis_wtp_", "WTA ")
                              .replace("_", " ").title())
            events = await _fetch_odds(sport_key, markets="h2h")
            for ev in events:
                p = _build_tennis_prediction(ev, label, "🎾", sport="tennis")
                if p:
                    key = f"{p['home']}:{p['away']}:{p['date']}"
                    if key not in seen:  # deduplicate
                        results.append(p)
                        seen.add(key)
            if events:
                await asyncio.sleep(0.2)

    print(f"[Sports] Tennis: {len(results)} predictions")
    return sorted(results, key=lambda x: x["date"] + x["time"])


TABLE_TENNIS_KEYS = ["table_tennis", "table_tennis_wtt", "table_tennis_ittf"]

async def fetch_table_tennis_predictions() -> List[Dict]:
    results = []

    # Primary: BetsAPI covers table tennis year-round
    betsapi_events = await _fetch_betsapi(BETSAPI_SPORTS["table_tennis"])
    for ev in betsapi_events:
        p = _betsapi_to_prediction(ev, "🏓", "table_tennis")
        if p:
            results.append(p)

    # Secondary: The Odds API (only during major WTT events)
    if ODDS_API_KEY and not results:
        for key in TABLE_TENNIS_KEYS:
            events = await _fetch_odds(key, markets="h2h")
            for ev in events:
                p = _build_tennis_prediction(ev, "Table Tennis", "🏓", sport="table_tennis")
                if p:
                    results.append(p)
            if events:
                break

    print(f"[Sports] Table Tennis: {len(results)} predictions")
    return sorted(results, key=lambda x: x["date"] + x["time"])


# ── Sport-specific market configs ─────────────────────────────────────────

SPORT_MARKETS = {
    "basketball": {
        "markets":    "h2h,spreads,totals",
        "market_defs": {
            "h2h":     {"name": "Moneyline",        "icon": "🏀"},
            "spreads":  {"name": "Point Spread",     "icon": "⚖️"},
            "totals":   {"name": "Total Points",     "icon": "📊"},
        },
    },
    "tennis": {
        "markets":    "h2h,alternate_spreads",
        "market_defs": {
            "h2h":              {"name": "Match Winner",   "icon": "🎾"},
            "alternate_spreads":{"name": "Game Handicap",  "icon": "⚖️"},
            "totals":           {"name": "Total Games",    "icon": "📊"},
        },
    },
    "table_tennis": {
        "markets":    "h2h,totals",
        "market_defs": {
            "h2h":   {"name": "Match Winner",  "icon": "🏓"},
            "totals":{"name": "Total Games",   "icon": "📊"},
        },
    },
}


async def fetch_event_detail(sport: str, home: str, away: str, date: str) -> Optional[Dict]:
    """
    Fetch full market detail for a specific match from The Odds API.
    Returns structured market data for the sport modal.
    """
    if not ODDS_API_KEY:
        return None

    from difflib import SequenceMatcher

    def sim(a: str, b: str) -> float:
        return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()

    # Determine which sport keys to search
    if sport == "basketball":
        sport_keys = [k for k, _, _ in BASKETBALL_SPORTS]
        market_str = "h2h,spreads,totals"
    elif sport == "tennis":
        # First try to get active sports, fall back to common tournament keys
        try:
            active = await _get_active_sports()
            sport_keys = [k for k in active if any(k.startswith(kw) for kw in TENNIS_KEYWORDS)]
        except Exception:
            sport_keys = []
        # If discovery fails, try common tournament keys directly
        if not sport_keys:
            sport_keys = [
                "tennis_atp_french_open", "tennis_wtp_french_open",
                "tennis_atp_wimbledon", "tennis_wtp_wimbledon",
                "tennis_atp_us_open", "tennis_wtp_us_open",
                "tennis_atp_australian_open", "tennis_wtp_australian_open",
                "tennis_atp_singles", "tennis_wtp_singles",
            ]
        market_str = "h2h"
    elif sport == "table_tennis":
        sport_keys = [TABLE_TENNIS_KEY, "table_tennis_wtt"]
        market_str = "h2h"
    else:
        return None

    for sport_key in sport_keys:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(
                    f"{ODDS_BASE}/sports/{sport_key}/odds/",
                    params={
                        "apiKey": ODDS_API_KEY,
                        "regions": "eu",
                        "markets": market_str,
                        "oddsFormat": "decimal",
                    },
                )
                if r.status_code != 200:
                    continue

                events = r.json()
                # Find matching event by team names + date
                best, best_score = None, 0.0
                for ev in events:
                    h = ev.get("home_team", "")
                    a = ev.get("away_team", "")
                    ev_date, _ = _event_date(ev.get("commence_time", ""))
                    if ev_date != date:
                        continue
                    score = (sim(home, h) + sim(away, a)) / 2
                    if score > best_score:
                        best_score = score
                        best = ev

                if best and best_score >= 0.45:
                    return _structure_event_detail(best, sport, sport_key)

            await asyncio.sleep(0.2)
        except Exception as e:
            print(f"[Sports Detail] {sport_key} error: {e}")

    return None


def _structure_event_detail(event: Dict, sport: str, sport_key: str) -> Dict:
    """Convert a raw Odds API event into structured market data for the modal."""
    home = event.get("home_team", "")
    away = event.get("away_team", "")
    date_str, time_str = _event_date(event.get("commence_time", ""))

    market_defs = SPORT_MARKETS.get(sport, {}).get("market_defs", {})

    # Aggregate markets across bookmakers — best odds per outcome per market
    market_best: Dict[str, Dict[str, Dict]] = {}  # market_key → {outcome_name → {price, line}}

    for bookie in (event.get("bookmakers") or []):
        for market in (bookie.get("markets") or []):
            mk = market.get("key", "")
            if mk not in market_best:
                market_best[mk] = {}
            for o in (market.get("outcomes") or []):
                name  = o.get("name", "")
                price = float(o.get("price") or 0)
                point = o.get("point")  # spread/totals line
                if price > 1:
                    existing = market_best[mk].get(name, {})
                    if price > existing.get("price", 0):
                        market_best[mk][name] = {"price": price, "point": point}

    # Build structured markets for the modal
    structured_markets = []
    for mk, outcomes_raw in market_best.items():
        defn = market_defs.get(mk, {"name": mk.replace("_", " ").title(), "icon": "📌"})
        outcomes = []
        total_implied = sum(1/v["price"] for v in outcomes_raw.values() if v["price"] > 1)

        for name, data in outcomes_raw.items():
            price = data["price"]
            point = data.get("point")
            implied = (1 / price) / total_implied if total_implied > 0 else 0
            label = name
            if point is not None:
                sign = "+" if point > 0 else ""
                label = f"{name} ({sign}{point})"
            outcomes.append({
                "name":    name,
                "label":   label,
                "odds":    round(price, 2),
                "implied": round(implied, 3),
                "point":   point,
            })

        # Sort: for h2h home first; for totals Over first; for spreads by name
        if mk == "h2h":
            outcomes.sort(key=lambda o: (0 if o["name"] == home else 1))
        elif mk == "totals":
            outcomes.sort(key=lambda o: (0 if "over" in o["name"].lower() else 1))
        else:
            outcomes.sort(key=lambda o: o["name"])

        if outcomes:
            structured_markets.append({
                "id":       mk,
                "name":     defn["name"],
                "icon":     defn["icon"],
                "outcomes": outcomes,
            })

    # Sort markets: h2h first, then totals, then spreads
    order = {"h2h": 0, "totals": 1, "spreads": 2}
    structured_markets.sort(key=lambda m: order.get(m["id"], 9))

    # Compute best pick from h2h
    h2h_outs = next((m["outcomes"] for m in structured_markets if m["id"] == "h2h"), [])
    best_pick = None
    if h2h_outs:
        best = max(h2h_outs, key=lambda o: o["implied"])
        best_pick = {"label": best["label"], "odds": best["odds"], "confidence": best["implied"]}

    return {
        "home":       home,
        "away":       away,
        "date":       date_str,
        "time":       time_str,
        "sport":      sport,
        "sport_key":  sport_key,
        "markets":    structured_markets,
        "best_pick":  best_pick,
    }
