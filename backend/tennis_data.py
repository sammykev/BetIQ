"""
Tennis match history from the web, for the tennis model (GitHub Actions,
"Racket data"): Jeff Sackmann's public ATP and WTA archives — tour-level,
Challenger and qualifying, Futures/ITF — every match since 2000 with its
surface, level, round, score and serve statistics.

    python tennis_data.py --probe            # what answers, how far back
    python tennis_data.py --since 2010 ...   # used by tennis_fit.py

Rows are read straight from the CSVs (nothing stored): the fit keeps only
what it learns (tennis_model.py ratings) in Redis.
"""

import argparse
import asyncio
import csv
import io
import re
from datetime import date
from typing import Dict, Iterable, List, Optional, Tuple

RAW = "https://raw.githubusercontent.com/JeffSackmann/{repo}/master/{file}"
# (repo, file pattern, tour, level of play)
FILES = (
    ("tennis_atp", "atp_matches_{y}.csv", "ATP", "tour"),
    ("tennis_atp", "atp_matches_qual_chall_{y}.csv", "ATP", "challenger"),
    ("tennis_atp", "atp_matches_futures_{y}.csv", "ATP", "itf"),
    ("tennis_wta", "wta_matches_{y}.csv", "WTA", "tour"),
    ("tennis_wta", "wta_matches_qual_itf_{y}.csv", "WTA", "itf"),
)
TENNIS_DATA_UK = "http://www.tennis-data.co.uk/{y}/{y}.xlsx"     # ATP odds (probed only)

_SCORE_SET = re.compile(r"^(\d+)-(\d+)(?:\((\d+)\))?$")


def parse_score(score: str) -> Optional[Dict]:
    """ "6-4 3-6 7-6(5)" -> {sets: [[6,4],[3,6],[7,6]], ret: False}; None for walkovers/defaults."""
    s = (score or "").strip()
    if not s or re.search(r"W/O|walkover|DEF|Def\.|unfinished|played and abandoned", s, re.I):
        return None
    ret = bool(re.search(r"RET|ABD|ABN", s, re.I))
    sets = []
    for tok in s.split():
        m = _SCORE_SET.match(tok.strip("[]"))
        if m:
            sets.append([int(m.group(1)), int(m.group(2))])
    return {"sets": sets, "ret": ret} if sets else None


def _num(x: str) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def parse_rows(text: str, tour: str, level: str) -> List[Dict]:
    """The rows a model needs, oldest first."""
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        sc = parse_score(r.get("score") or "")
        d = r.get("tourney_date") or ""
        if not sc or len(d) != 8 or not r.get("winner_name") or not r.get("loser_name"):
            continue
        row = {"date": f"{d[:4]}-{d[4:6]}-{d[6:]}", "tour": tour, "level": level,
               "surface": (r.get("surface") or "Hard").strip() or "Hard", "tourney": r.get("tourney_name") or "",
               "tlevel": r.get("tourney_level") or "", "round": r.get("round") or "", "best_of": int(_num(r.get("best_of")) or 3),
               "w": r["winner_name"].strip(), "l": r["loser_name"].strip(), "sets": sc["sets"], "ret": sc["ret"]}
        # Serve points played and won by each (for serve/return rates)
        w_sv, l_sv = _num(r.get("w_svpt")), _num(r.get("l_svpt"))
        w_won = (_num(r.get("w_1stWon")) or 0) + (_num(r.get("w_2ndWon")) or 0) if w_sv else None
        l_won = (_num(r.get("l_1stWon")) or 0) + (_num(r.get("l_2ndWon")) or 0) if l_sv else None
        if w_sv and l_sv and w_won is not None and l_won is not None:
            row["sv"] = [w_sv, w_won, l_sv, l_won]
        out.append(row)
    return out


async def fetch_year(client, year: int, files=FILES) -> Tuple[List[Dict], Dict[str, int]]:
    rows: List[Dict] = []
    counts: Dict[str, int] = {}
    for repo, pattern, tour, level in files:
        name = pattern.format(y=year)
        try:
            r = await client.get(RAW.format(repo=repo, file=name))
        except Exception:
            counts[name] = -1
            continue
        if r.status_code != 200:
            counts[name] = 0
            continue
        got = parse_rows(r.text, tour, level)
        counts[name] = len(got)
        rows += got
    return rows, counts


async def fetch_all(since: int, until: Optional[int] = None) -> List[Dict]:
    import httpx
    until = until or date.today().year
    out: List[Dict] = []
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        for y in range(since, until + 1):
            rows, counts = await fetch_year(client, y)
            out += rows
            print(f"[tennis] {y}: {len(rows)} matches " + " ".join(f"{k.split('_matches')[0]}{k.split('_matches')[1][:-4] or ''}={v}"
                                                            for k, v in counts.items()), flush=True)
    out.sort(key=lambda r: r["date"])
    return out


async def probe() -> None:
    import httpx
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        for y in (2000, 2010, 2020, date.today().year - 1, date.today().year):
            rows, counts = await fetch_year(client, y)
            last = max((r["date"] for r in rows), default="")
            sv = sum(1 for r in rows if "sv" in r)
            print(f"{y}: {len(rows)} matches (serve stats on {sv}); latest {last}; files {counts}", flush=True)
            if rows:
                print("   e.g.", rows[-1])
        for y in (2024, 2025):
            try:
                r = await client.get(TENNIS_DATA_UK.format(y=y))
                print(f"tennis-data.co.uk {y}: HTTP {r.status_code} {len(r.content)} bytes")
            except Exception as e:
                print(f"tennis-data.co.uk {y}: {e}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true")
    a = ap.parse_args()
    if a.probe:
        asyncio.run(probe())
