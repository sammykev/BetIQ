"""
Keep the training CSVs current: download this season's and last season's
results (with Bet365 odds) from football-data.co.uk into data/football/.

The pipeline calls sync() at most once a day; a changed file bumps the data
mtime, so the next pipeline run retrains instead of loading the cached model.
A file is only replaced by a download that parses, has the expected columns
and has at least as many played matches — a truncated or error response
never overwrites good data. The same goes for the international results
(martj42/international_results), which train national-team ratings.

    python football_data_sync.py              # sync into data/football/
    python football_data_sync.py --seasons 3  # also the season before last
"""

import argparse
import asyncio
import io
import os
from datetime import date
from typing import Dict, Iterable, List, Optional

import httpx
import pandas as pd

BASE_URL = "https://www.football-data.co.uk/mmz4281"
DEST_DIR = os.path.join(os.path.dirname(__file__), "data", "football")

# football-data.co.uk division codes we train on (see main._LEAGUE_CODES)
DEFAULT_DIVISIONS = ("E0", "E1", "SP1", "I1", "D1", "D2", "F1", "N1", "P1")
REQUIRED = ["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR"]

INTERNATIONAL_URL = "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"
INTERNATIONAL_PATH = os.path.join(os.path.dirname(__file__), "data", "international_results.csv")
INTERNATIONAL_REQUIRED = ["date", "home_team", "away_team", "home_score", "away_score"]


def season_code(d: date) -> str:
    """Season containing `d`, as football-data names it: 2026-09 → "2627".
    A season starts in July."""
    start = d.year if d.month >= 7 else d.year - 1
    return f"{start % 100:02d}{(start + 1) % 100:02d}"


def recent_seasons(today: date, n: int = 2) -> List[str]:
    """The current season and the n-1 before it, newest first."""
    return [season_code(date(today.year - i, today.month, 1)) for i in range(n)]


def _decode(content: bytes) -> str:
    # Newer files are UTF-8 with a BOM, older ones Windows-1252
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return content.decode("latin-1")


def played_matches(text: str, required: List[str] = REQUIRED) -> Optional[int]:
    """Number of completed matches in a CSV, or None if it isn't a results CSV."""
    try:
        df = pd.read_csv(io.StringIO(text), low_memory=False)
    except Exception:
        return None
    df.columns = [c.strip() for c in df.columns]
    if not all(c in df.columns for c in required):
        return None
    return int(df.dropna(subset=required).shape[0])


def _write_atomic(path: str, text: str) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(tmp, path)


ATTEMPTS = 3
RETRY_PAUSE = 3.0  # seconds, doubled after each failed attempt


async def _get(client: httpx.AsyncClient, url: str, pause: float = RETRY_PAUSE):
    """GET with retries for timeouts, dropped connections and server errors
    (football-data.co.uk is sometimes slow). Raises the last network error;
    returns the last response otherwise."""
    for attempt in range(ATTEMPTS):
        try:
            res = await client.get(url)
            if res.status_code < 500 or attempt == ATTEMPTS - 1:
                return res
        except httpx.HTTPError:
            if attempt == ATTEMPTS - 1:
                raise
        await asyncio.sleep(pause * 2 ** attempt)


def failed_files(report: Dict[str, list]) -> List[tuple]:
    """(division, season) of the league files a sync couldn't download."""
    out = []
    for item in report.get("failed", []):
        name = item.split(":", 1)[0]
        div, _, season = name.removesuffix(".csv").rpartition("_")
        if div in DEFAULT_DIVISIONS and season.isdigit():
            out.append((div, season))
    return out


async def sync(dest_dir: str = DEST_DIR,
               divisions: Iterable[str] = DEFAULT_DIVISIONS,
               seasons: Optional[Iterable[str]] = None,
               client: Optional[httpx.AsyncClient] = None,
               today: Optional[date] = None, pause: float = RETRY_PAUSE) -> Dict[str, list]:
    """Download and store changed files. Returns {"updated", "unchanged", "skipped", "failed"}."""
    seasons = list(seasons or recent_seasons(today or date.today()))
    report: Dict[str, list] = {"updated": [], "unchanged": [], "skipped": [], "failed": []}
    os.makedirs(dest_dir, exist_ok=True)

    own_client = client is None
    client = client or httpx.AsyncClient(
        timeout=httpx.Timeout(60, connect=15), follow_redirects=True,
        headers={"User-Agent": "BetIQ/1.0 (+https://predict-withbetiq.vercel.app)"},
    )
    try:
        for season in seasons:
            for div in divisions:
                name = f"{div}_{season}.csv"
                path = os.path.join(dest_dir, name)
                try:
                    res = await _get(client, f"{BASE_URL}/{season}/{div}.csv", pause)
                except httpx.HTTPError as e:
                    report["failed"].append(f"{name}: {type(e).__name__}")
                    continue
                if res.status_code == 404:  # season or division not published yet
                    report["skipped"].append(name)
                    continue
                if res.status_code != 200:
                    report["failed"].append(f"{name}: HTTP {res.status_code}")
                    continue

                text = _decode(res.content)
                played = played_matches(text)
                if not played:
                    report["failed"].append(f"{name}: not a results CSV")
                    continue

                if os.path.exists(path):
                    with open(path, encoding="utf-8", errors="replace") as f:
                        current = f.read()
                    if current == text:
                        report["unchanged"].append(name)
                        continue
                    if played < (played_matches(current) or 0):
                        report["failed"].append(f"{name}: download has fewer matches than the saved file")
                        continue
                _write_atomic(path, text)
                report["updated"].append(name)
    finally:
        if own_client:
            await client.aclose()
    return report


async def sync_international(path: str = INTERNATIONAL_PATH,
                             client: Optional[httpx.AsyncClient] = None) -> Dict[str, list]:
    """Refresh the international results CSV, under the same rules as sync()."""
    name = os.path.basename(path)
    report: Dict[str, list] = {"updated": [], "unchanged": [], "skipped": [], "failed": []}
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=60, follow_redirects=True)
    try:
        res = await client.get(INTERNATIONAL_URL)
    except httpx.HTTPError as e:
        report["failed"].append(f"{name}: {type(e).__name__}")
        return report
    finally:
        if own_client:
            await client.aclose()
    if res.status_code != 200:
        report["failed"].append(f"{name}: HTTP {res.status_code}")
        return report

    text = _decode(res.content)
    played = played_matches(text, INTERNATIONAL_REQUIRED)
    if not played:
        report["failed"].append(f"{name}: not a results CSV")
        return report
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="replace") as f:
            current = f.read()
        if current == text:
            report["unchanged"].append(name)
            return report
        if played < (played_matches(current, INTERNATIONAL_REQUIRED) or 0):
            report["failed"].append(f"{name}: download has fewer matches than the saved file")
            return report
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    _write_atomic(path, text)
    report["updated"].append(name)
    return report


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seasons", type=int, default=2, help="how many recent seasons (default 2)")
    ap.add_argument("--dest", default=DEST_DIR)
    args = ap.parse_args(argv)
    report = asyncio.run(sync(args.dest, seasons=recent_seasons(date.today(), args.seasons)))
    for key, items in asyncio.run(sync_international()).items():
        report[key] += items
    for key, items in report.items():
        print(f"{key:9} {len(items):3}  {' '.join(items)}")


if __name__ == "__main__":
    main()
