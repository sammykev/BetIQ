"""
Fetches fixtures and results from football-data.org free API.
Free tier: 10 req/min, covers the World Cup, Euros, Champions League, Premier
League, Championship, La Liga, Bundesliga, Serie A, Ligue 1, Eredivisie,
Primeira Liga and Brasileirão. Competitions outside the key's plan (the
Europa League on the free tier) answer 403 and are skipped (NOT_IN_PLAN).
"""

import asyncio
import time
import httpx
import pandas as pd
from datetime import date, timedelta
from typing import List, Dict, Optional
import os
import ssl


_tls_ctx: List[ssl.SSLContext] = []


def _tls() -> ssl.SSLContext:
    """One TLS context for every request (each new one loads the CA bundle again)."""
    if not _tls_ctx:
        import certifi
        _tls_ctx.append(ssl.create_default_context(cafile=certifi.where()))
    return _tls_ctx[0]


API_BASE = "https://api.football-data.org/v4"

# Competitions this process's API key got a 401/403 for (not in its plan);
# the Europa and Conference League come from ESPN instead, never asked for.
# A 403 can also be passing (football-data.org answers some throttled or
# restricted requests with one), so a competition is skipped only for
# SKIP_HOURS and then asked for again; it used to be skipped until the
# server restarted, which left the site with no Premier League, La Liga,
# Serie A... for days after one bad answer.
ESPN_ONLY = {"EL", "UECL"}
NOT_IN_PLAN: set = set(ESPN_ONLY)
SKIP_HOURS = 6
_skipped_at: Dict[str, float] = {}


def not_in_plan(comp: Optional[str]) -> bool:
    """Whether to skip this competition now (ESPN's always; a 401/403 one for SKIP_HOURS)."""
    if not comp or comp not in NOT_IN_PLAN:
        return False
    if comp in ESPN_ONLY:
        return True
    at = _skipped_at.get(comp)
    if at is not None and time.time() - at > SKIP_HOURS * 3600:
        NOT_IN_PLAN.discard(comp)
        _skipped_at.pop(comp, None)
        return False
    return True


def competition_of(url: str) -> Optional[str]:
    """The competition code in a /competitions/{code}... URL."""
    part = url.split("/competitions/", 1)
    if len(part) != 2:
        return None
    return part[1].split("/")[0].split("?")[0] or None

# Competitions we fetch from football-data.org (EL needs a paid plan)
LEAGUES: Dict[str, Dict] = {
    "WC":  {"name": "World Cup",        "country": "World",   "flag": "🌍"},
    "EC":  {"name": "Euro Championship","country": "Europe",  "flag": "🇪🇺"},
    "CL":  {"name": "Champions League", "country": "Europe",  "flag": "🏆"},
    "PL":  {"name": "Premier League",   "country": "England", "flag": "🏴󠁧󠁢󠁥󠁮󠁧󠁿"},
    "PD":  {"name": "La Liga",          "country": "Spain",   "flag": "🇪🇸"},
    "BL1": {"name": "Bundesliga",       "country": "Germany", "flag": "🇩🇪"},
    "SA":  {"name": "Serie A",          "country": "Italy",   "flag": "🇮🇹"},
    "FL1": {"name": "Ligue 1",          "country": "France",  "flag": "🇫🇷"},
    "EL":  {"name": "Europa League",    "country": "Europe",  "flag": "🟠"},
    "DED": {"name": "Eredivisie",       "country": "Netherlands","flag": "🇳🇱"},
    "PPL": {"name": "Primeira Liga",    "country": "Portugal","flag": "🇵🇹"},
    "BSA": {"name": "Brasileirão",      "country": "Brazil",  "flag": "🇧🇷"},
    "ELC": {"name": "Championship",     "country": "England", "flag": "🏴󠁧󠁢󠁥󠁮󠁧󠁿"},
    # Fixtures from ESPN (europe_fixtures.py): not in football-data.org's free plan
    "UECL": {"name": "Conference League", "country": "Europe", "flag": "🟢"},
}


class FootballDataClient:
    def __init__(self, api_key: str):
        self.headers = {"X-Auth-Token": api_key}
        self._semaphore = asyncio.Semaphore(1)  # 1 at a time to respect rate limit

    async def _get(self, client: httpx.AsyncClient, url: str, _attempt: int = 0) -> Optional[Dict]:
        """GET a JSON endpoint; None when it can't be had. Retries network
        errors and 5xx (twice, 10 s apart) and waits out 429s. A 401/403 on
        a competition means the key's plan doesn't cover it: remembered in
        NOT_IN_PLAN and not asked for again. Other 4xx aren't retried.

        The lock is held for each request only: the retry used to call _get
        again from inside it, and asyncio.Semaphore(1) isn't re-entrant, so
        the first retry waited on itself forever (one 403 on the Europa
        League stalled the whole pipeline's fixture loop there)."""
        comp = competition_of(url)
        if not_in_plan(comp):
            return None
        attempts = 3
        for attempt in range(_attempt, attempts):
            error: Optional[Exception] = None
            async with self._semaphore:
                try:
                    r = await client.get(url, headers=self.headers, timeout=20)
                except Exception as e:
                    error = e
            last = attempt == attempts - 1
            if error is not None:
                if last:
                    print(f"[API] Failed after {attempts} attempts: {url} — {error}")
                    return None
                print(f"[API] Error (attempt {attempt+1}), retrying: {error}")
                await asyncio.sleep(10)
                continue
            if r.status_code == 429:
                if last:
                    print(f"[API] Still rate-limited after {attempts} attempts: {url}")
                    return None
                wait = 65 if attempt == 0 else 120
                print(f"[API] 429 rate-limited — waiting {wait}s (attempt {attempt+1})")
                await asyncio.sleep(wait)
                continue
            if r.status_code in (500, 502, 503, 504):
                if last:
                    print(f"[API] {r.status_code} after {attempts} attempts: {url}")
                    return None
                print(f"[API] {r.status_code} server error — retrying in 10s")
                await asyncio.sleep(10)
                continue
            if r.status_code in (401, 403) and comp:
                NOT_IN_PLAN.add(comp)
                _skipped_at[comp] = time.time()
                print(f"[API] {comp}: football-data.org answered {r.status_code} for {url} — skipping it for "
                      f"{SKIP_HOURS} h: {r.text[:200]}")
                return None
            if r.status_code >= 400:
                print(f"[API] {r.status_code} for {url} — not retrying")
                return None
            try:
                r.raise_for_status()
                return r.json()
            except Exception as e:
                print(f"[API] Bad response from {url}: {e}")
                return None
        return None

    async def fetch_upcoming(
        self, league_code: str, days_ahead: int = 7
    ) -> List[Dict]:
        """Return upcoming fixtures for a league within the next N days."""
        today = date.today()
        date_to = today + timedelta(days=days_ahead)
        # NOTE: do NOT filter by status=SCHEDULED here. football-data.org marks a
        # match as TIMED (not SCHEDULED) once its exact kickoff time is confirmed,
        # which is the case for essentially every match happening today or in the
        # next few days. Filtering on SCHEDULED-only silently drops those. Instead
        # fetch the full date range and keep any not-yet-played fixture below.
        url = (
            f"{API_BASE}/competitions/{league_code}/matches"
            f"?dateFrom={today}&dateTo={date_to}"
        )
        async with httpx.AsyncClient(verify=_tls()) as client:
            data = await self._get(client, url)

        if not data or "matches" not in data:
            return []

        _SKIP = {"tbd", "tba", "to be announced", "", "none"}
        _UPCOMING = {"SCHEDULED", "TIMED"}  # not yet played
        fixtures = []
        for m in data["matches"]:
            # Only keep fixtures that haven't kicked off / finished yet.
            if m.get("status") not in _UPCOMING:
                continue
            home_name = (m["homeTeam"].get("name") or "").strip()
            away_name = (m["awayTeam"].get("name") or "").strip()
            if home_name.lower() in _SKIP or away_name.lower() in _SKIP:
                continue
            fixtures.append({
                "match_id": m["id"],
                "home": home_name,
                "away": away_name,
                # Team crests ride along for free on every matches response —
                # no extra API call needed, unlike a per-team lookup.
                "home_crest": m["homeTeam"].get("crest") or None,
                "away_crest": m["awayTeam"].get("crest") or None,
                "date": m["utcDate"][:10],
                "time": m["utcDate"][11:16],
                "league": league_code,
                "league_name": LEAGUES.get(league_code, {}).get("name", league_code),
                "flag": LEAGUES.get(league_code, {}).get("flag", "⚽"),
            })
        return fixtures

    async def fetch_matches_raw(self, league_code: str, days_ahead: int = 90) -> Optional[Dict]:
        """Raw, unfiltered competition matches over the date window (for diagnostics)."""
        today = date.today()
        date_to = today + timedelta(days=days_ahead)
        url = (
            f"{API_BASE}/competitions/{league_code}/matches"
            f"?dateFrom={today}&dateTo={date_to}"
        )
        async with httpx.AsyncClient(verify=_tls()) as client:
            return await self._get(client, url)

    async def fetch_h2h(self, match_id: int, limit: int = 10) -> Optional[Dict]:
        """Fetch head-to-head record for a specific match ID."""
        url = f"{API_BASE}/matches/{match_id}/head2head?limit={limit}"
        async with httpx.AsyncClient(verify=_tls()) as client:
            return await self._get(client, url)

    async def fetch_competition_emblem(self, league_code: str) -> Optional[str]:
        """
        Fetch a competition's emblem/logo URL from football-data.org — an API
        we already have a real (non-rate-limited-demo) key for, and one whose
        competition codes are exactly our own LEAGUES dict keys ("WC", "PL",
        ...), so no name-matching is needed at all.

        Raises on a failed/errored request (network error, exhausted 429
        retries, ...) rather than returning None for that case too — callers
        need to tell "confirmed: this competition has no emblem" apart from
        "we don't actually know, the request failed," since only the former
        is safe to cache for a long time.
        """
        url = f"{API_BASE}/competitions/{league_code}"
        async with httpx.AsyncClient(verify=_tls()) as client:
            data = await self._get(client, url)
        if data is None:
            raise RuntimeError(f"football-data.org request failed for competition {league_code!r}")
        return data.get("emblem")

    async def fetch_recent_results(
        self, league_code: str, days_back: int = 30
    ) -> pd.DataFrame:
        """Return recent played results (for updating Elo/form on the fly)."""
        today = date.today()
        date_from = today - timedelta(days=days_back)
        url = (
            f"{API_BASE}/competitions/{league_code}/matches"
            f"?status=FINISHED&dateFrom={date_from}&dateTo={today}"
        )
        async with httpx.AsyncClient(verify=_tls()) as client:
            data = await self._get(client, url)

        if not data or "matches" not in data:
            return pd.DataFrame()

        rows = []
        for m in data["matches"]:
            score = m.get("score", {}).get("fullTime", {})
            home_g = score.get("home")
            away_g = score.get("away")
            if home_g is None or away_g is None:
                continue
            result = "H" if home_g > away_g else ("A" if away_g > home_g else "D")
            rows.append({
                "Date": pd.to_datetime(m["utcDate"][:10]),
                "HomeTeam": m["homeTeam"]["name"],
                "AwayTeam": m["awayTeam"]["name"],
                "Result": result,
                "FTHG": float(home_g),
                "FTAG": float(away_g),
            })

        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows).sort_values("Date").reset_index(drop=True)
        return df

    async def fetch_all_upcoming(self, days_ahead: int = 7) -> List[Dict]:
        """Fetch upcoming fixtures for all leagues (with larger delays for rate limit)."""
        all_fixtures = []
        for code in LEAGUES:
            fixtures = await self.fetch_upcoming(code, days_ahead)
            all_fixtures.extend(fixtures)
            await asyncio.sleep(10)  # 10 req/min = 6s, using 10s for safety margin
        return all_fixtures
