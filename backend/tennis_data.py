"""
Tennis match history from the web, for the tennis model (GitHub Actions,
"Racket data"): ATP and WTA match archives in Jeff Sackmann's columns
(his repositories are gone; TML-Database and mirrors carry them on) —
every tour-level match with its surface, level, round, score and serve
statistics.

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

# Jeff Sackmann's repositories are gone (404 since 2026). A full copy of
# them (ATP and WTA: tour, Challenger/qualifying, Futures/ITF) carries on at
# Aneeshers/tennis-sackmann-archive; Tennismylife's TML database (ATP tour
# level, same columns) and a Hugging Face mirror are the fallbacks.
RAW = "https://raw.githubusercontent.com/{repo}/{branch}/{file}"
HF = "https://huggingface.co/datasets/{repo}/resolve/main/{file}"
ARCHIVE = "Aneeshers/tennis-sackmann-archive"


def _archive(file: str) -> str:
    return RAW.format(repo=ARCHIVE, branch="main", file=file)


# (url template with {y}, tour, level of play); the first that answers per tour/level wins
FILES = (
    (_archive("atp/atp_matches_{y}.csv"), "ATP", "tour"),
    (RAW.format(repo="Tennismylife/TML-Database", branch="master", file="{y}.csv"), "ATP", "tour"),
    (HF.format(repo="davidtadediji/tennis-atp", file="atp_matches_{y}.csv"), "ATP", "tour"),
    (_archive("atp/atp_matches_qual_chall_{y}.csv"), "ATP", "challenger"),
    (_archive("atp/atp_matches_futures_{y}.csv"), "ATP", "itf"),
    (_archive("wta/wta_matches_{y}.csv"), "WTA", "tour"),
    (_archive("wta/wta_matches_qual_itf_{y}.csv"), "WTA", "itf"),
)

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
               "surface": (r.get("surface") or "Hard").strip() or "Hard", "tlevel": r.get("tourney_level") or "",
               "best_of": int(_num(r.get("best_of")) or 3),
               "w": r["winner_name"].strip(), "l": r["loser_name"].strip(), "sets": sc["sets"], "ret": sc["ret"]}
        # Serve points played and won by each (for serve/return rates)
        w_sv, l_sv = _num(r.get("w_svpt")), _num(r.get("l_svpt"))
        w_won = (_num(r.get("w_1stWon")) or 0) + (_num(r.get("w_2ndWon")) or 0) if w_sv else None
        l_won = (_num(r.get("l_1stWon")) or 0) + (_num(r.get("l_2ndWon")) or 0) if l_sv else None
        if w_sv and l_sv and w_won is not None and l_won is not None:
            row["sv"] = [w_sv, w_won, l_sv, l_won]
        out.append(row)
    return out


def _label(url: str) -> str:
    parts = url.split("/")
    return f"{parts[3] if 'raw.github' in url else parts[4]}/{parts[-1]}"


async def fetch_year(client, year: int, files=FILES) -> Tuple[List[Dict], Dict[str, int]]:
    rows: List[Dict] = []
    counts: Dict[str, int] = {}
    done = set()
    for pattern, tour, level in files:
        if (tour, level) in done:
            continue
        url = pattern.format(y=year)
        name = _label(url)
        try:
            r = await client.get(url)
        except Exception:
            counts[name] = -1
            continue
        if r.status_code != 200:
            counts[name] = -r.status_code
            continue
        got = parse_rows(r.text, tour, level)
        counts[name] = len(got)
        if got:
            done.add((tour, level))
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
            print(f"[tennis] {y}: {len(rows)} matches " + " ".join(f"{k}={v}" for k, v in counts.items()), flush=True)
    out.sort(key=lambda r: r["date"])
    return out


async def probe() -> None:
    import os
    import httpx
    gh = {"Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        gh["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        for y in (2005, 2015, date.today().year - 1, date.today().year):
            rows, counts = await fetch_year(client, y)
            last = max((r["date"] for r in rows), default="")
            sv = sum(1 for r in rows if "sv" in r)
            print(f"{y}: {len(rows)} matches (serve stats on {sv}); latest {last}; files {counts}", flush=True)
            if rows:
                print("   e.g.", rows[-1])
        # WTA: where a match archive lives now (repos with WTA csv files)
        for q in ("wta_matches", "wta matches", "wta tennis data", "tennis_wta", "wta elo", "tennis atp wta csv"):
            try:
                r = await client.get("https://api.github.com/search/repositories",
                                     params={"q": q, "sort": "stars", "per_page": 10}, headers=gh)
                items = r.json().get("items", []) if r.status_code == 200 else []
                print(f"GitHub '{q}': HTTP {r.status_code}: " + " | ".join(
                    f"{x['full_name']} ({x.get('stargazers_count')}*, pushed {x.get('pushed_at','')[:10]}, {x.get('size')}kB, "
                    f"{x.get('default_branch')})" for x in items))
            except Exception as e:
                print(f"GitHub '{q}': {e}")
        for repo in (ARCHIVE,):
            try:
                r = await client.get(f"https://api.github.com/repos/{repo}", headers=gh)
                if r.status_code != 200:
                    print(f"{repo}: HTTP {r.status_code}")
                    continue
                branch = r.json().get("default_branch")
                t = await client.get(f"https://api.github.com/repos/{repo}/git/trees/{branch}", params={"recursive": 1},
                                     headers=gh)
                tree = t.json().get("tree", []) if t.status_code == 200 else []
                data = [x for x in tree if x.get("type") == "blob" and x["path"].lower().endswith((".csv", ".parquet", ".csv.gz", ".json"))]
                wta = [x for x in data if "matches" in x["path"].lower() and "20" in x["path"]]
                print(f"{repo} ({branch}): {len(tree)} paths, {len(data)} data files, {len(wta)} WTA: "
                      + ", ".join(f"{x['path']}({x.get('size')})" for x in sorted(wta or data, key=lambda x: x['path'])[-60:]))
                for x in sorted(wta, key=lambda x: x["path"])[-2:]:
                    if x["path"].endswith(".csv"):
                        fr = await client.get(RAW.format(repo=repo, branch=branch, file=x["path"]))
                        lines = (fr.text or "").splitlines()
                        print(f"   {x['path']}: HTTP {fr.status_code} {len(lines)} lines; header {lines[0][:350] if lines else ''!r}; "
                              f"last {lines[-1][:220] if lines else ''!r}")
            except Exception as e:
                print(f"{repo}: {e}")
        tok = os.environ.get("HF_TOKEN", "")
        hf = {"Authorization": f"Bearer {tok}"} if tok else {}
        for q in ("wta", "women tennis", "tennis matches", "tennis"):
            try:
                r = await client.get("https://huggingface.co/api/datasets", params={"search": q, "limit": 20}, headers=hf)
                items = r.json() if r.status_code == 200 else []
                print(f"HF datasets '{q}': HTTP {r.status_code}: " + " | ".join(
                    f"{d.get('id')} ({(d.get('lastModified') or '')[:10]})" for d in items))
            except Exception as e:
                print(f"HF '{q}': {e}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true")
    a = ap.parse_args()
    if a.probe:
        asyncio.run(probe())
