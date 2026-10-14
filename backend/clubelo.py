"""
ClubElo (clubelo.com): a daily Elo rating for every club in the European
competitions, on one scale across countries — the strength of a club whose
league we have little data on. GitHub's runners can't reach it, so the
server fetches it (main._clubelo_refresh): the day's ratings, and weekly
snapshots back to SINCE, a few a run, for the Europe model's check.

    Redis: LATEST_KEY  {"date", "ratings": {club: elo}, "countries": {club: code}}
           HISTORY_KEY  hash {date: gzip JSON {club: elo}}
           STATUS_KEY   the last fetch's result
"""

import csv
import gzip
import io
import json
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

URL = "http://api.clubelo.com/{}"
LATEST_KEY = "betiq:clubelo:latest"
HISTORY_KEY = "betiq:clubelo:history"
STATUS_KEY = "betiq:clubelo:status"
SINCE = date(2024, 7, 1)
BACKFILL_PER_RUN = 12


def parse(text: str) -> Tuple[Dict[str, float], Dict[str, str]]:
    """({club: elo}, {club: country}) from ClubElo's CSV (Rank,Club,Country,Level,Elo,From,To)."""
    ratings, countries = {}, {}
    for row in csv.DictReader(io.StringIO(text)):
        try:
            club, elo = row["Club"].strip(), float(row["Elo"])
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        ratings[club] = round(elo, 1)
        countries[club] = (row.get("Country") or "").strip()
    return ratings, countries


def weeks(today: date) -> List[str]:
    """Mondays from SINCE to today, newest first."""
    d = today - timedelta(days=today.weekday())
    out = []
    while d >= SINCE:
        out.append(d.isoformat())
        d -= timedelta(days=7)
    return out


def fetch(client, day: str) -> Tuple[Dict[str, float], Dict[str, str]]:
    r = client.get(URL.format(day), timeout=30)
    r.raise_for_status()
    return parse(r.text)


def refresh(r, client, today: Optional[date] = None) -> Dict:
    """Today's ratings, then up to BACKFILL_PER_RUN missing weekly snapshots."""
    today = today or datetime.now(timezone.utc).date()
    status: Dict = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    try:
        ratings, countries = fetch(client, today.isoformat())
        status["clubs"] = len(ratings)
        if ratings:
            r.set(LATEST_KEY, json.dumps({"date": today.isoformat(), "ratings": ratings, "countries": countries}))
        have = {k.decode() if isinstance(k, bytes) else k for k in (r.hkeys(HISTORY_KEY) or [])}
        added = 0
        for d in weeks(today):
            if added >= BACKFILL_PER_RUN:
                break
            if d in have:
                continue
            snap, _ = fetch(client, d)
            if snap:
                r.hset(HISTORY_KEY, d, gzip.compress(json.dumps(snap, separators=(",", ":")).encode()))
                added += 1
        status["history_added"] = added
        status["history_weeks"] = len(have) + added
        status["history_missing"] = len([d for d in weeks(today) if d not in have]) - added
    except Exception as e:
        status["error"] = f"{type(e).__name__}: {e}"[:300]
    r.set(STATUS_KEY, json.dumps(status))
    return status


def latest(r) -> Dict[str, float]:
    try:
        return (json.loads(r.get(LATEST_KEY) or "{}")).get("ratings") or {}
    except Exception:
        return {}


def history(r) -> Dict[str, Dict[str, float]]:
    """{monday: {club: elo}} for every stored week."""
    out = {}
    for k, v in (r.hgetall(HISTORY_KEY) or {}).items():
        k = k.decode() if isinstance(k, bytes) else k
        try:
            out[k] = json.loads(gzip.decompress(v))
        except Exception:
            continue
    return out
