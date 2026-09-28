"""
Recent form and head-to-head for the match page: each side's last five
matches and the last five meetings between them, before the match's date.

The results come from everything the server holds (main._results_index):
the league CSVs and the results saved from the APIs (what the model trains
on), every European and cup match collected from ESPN (club_cups.py), the
international results, and the finished matches in the match-day store (the
last week's scores, often newer than the CSVs). Names are the model's
(predictor.canon), so one club is one team whichever source named it.
"""

from datetime import date
from typing import Callable, Dict, Iterable, List, Optional

import pandas as pd

N = 5
AVG_N = 10          # matches each team's market averages are taken over
# Per-side stats where a source has them: corners, booking points (yellow 1,
# red 2, as SportyBet counts them), shots, shots on target
STAT_COLS = ["HC", "AC", "HB", "AB", "HS", "AS", "HST", "AST"]
COLUMNS = ["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "comp", *STAT_COLS]
# Competition codes the frames carry → names
_EXTRA_NAMES = {
    "INT": "International", "ELC": "Championship", "EL1": "League One", "EL2": "League Two",
    "SD": "La Liga 2", "SB": "Serie B", "BL2": "2. Bundesliga", "FL2": "Ligue 2",
    "JPL": "Pro League", "SPL": "Scottish Premiership", "D1": "Scottish Championship",
    "GSL": "Super League Greece", "TSL": "Süper Lig",
}


def comp_name(code: Optional[str]) -> str:
    import club_cups
    from data_fetcher import LEAGUES
    code = str(code or "")
    if code in LEAGUES:
        return LEAGUES[code]["name"]
    for c, name, _ in club_cups.COMPETITIONS.values():
        if c == code:
            return name
    return _EXTRA_NAMES.get(code, code)


def frame(df: pd.DataFrame) -> pd.DataFrame:
    """A results frame (league CSV shape, `league` codes) in the index's shape."""
    if df is None or df.empty:
        return pd.DataFrame(columns=COLUMNS)
    out = df[[c for c in ("Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG") if c in df.columns]].copy()
    codes = df["league"] if "league" in df.columns else pd.Series("", index=df.index)
    names = {c: comp_name(c) for c in codes.dropna().unique()}
    out["comp"] = codes.map(lambda c: names.get(c, "")).fillna("")
    num = lambda c: pd.to_numeric(df[c], errors="coerce") if c in df.columns else pd.Series(float("nan"), index=df.index)
    for ours, theirs in (("HC", "HC"), ("AC", "AC"), ("HS", "HS"), ("AS", "AS"), ("HST", "HST"), ("AST", "AST")):
        out[ours] = num(theirs)
    # Booking points: yellows + 2 × reds (reds count 0 where only yellows are known)
    out["HB"] = num("HY") + num("HR").fillna(0) * 2
    out["AB"] = num("AY") + num("AR").fillna(0) * 2
    return out


def index(frames: Iterable[pd.DataFrame]) -> pd.DataFrame:
    """One results table, newest first, each match once: sources that date a
    match a day apart (UTC vs local kick-off) keep the first frame's row."""
    parts = [f for f in frames if f is not None and not f.empty]
    if not parts:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.concat(parts, ignore_index=True)
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.normalize()
    df["FTHG"] = pd.to_numeric(df["FTHG"], errors="coerce")
    df["FTAG"] = pd.to_numeric(df["FTAG"], errors="coerce")
    df = df.dropna(subset=["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"])
    df["comp"] = df["comp"].fillna("") if "comp" in df.columns else ""
    for c in STAT_COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce") if c in df.columns else float("nan")
    # One source's copy may have the stats another's lacks: every copy gets them
    df[STAT_COLS] = df.groupby(["Date", "HomeTeam", "AwayTeam"])[STAT_COLS].transform("first")
    df["_order"] = range(len(df))
    # Prefer a row with a competition name when two sources have the same match
    df["_named"] = df["comp"].astype(str).str.len() > 0
    df = df.sort_values(["Date", "_named", "_order"], ascending=[False, False, True])
    df = df.drop_duplicates(subset=["Date", "HomeTeam", "AwayTeam"])
    # The same pairing a day apart is one match
    prev = df.groupby(["HomeTeam", "AwayTeam"])["Date"].shift(1)
    df = df[~((prev - df["Date"]).dt.days.abs() <= 1)]
    return df[COLUMNS].reset_index(drop=True)


def _before(before: Optional[str]) -> pd.Timestamp:
    try:
        return pd.Timestamp(date.fromisoformat(str(before)[:10]))
    except ValueError:
        return pd.Timestamp(date.today()) + pd.Timedelta(days=1)


def _row(r, team: str) -> Dict:
    hg, ag = int(r.FTHG), int(r.FTAG)
    home = r.HomeTeam == team
    gf, ga = (hg, ag) if home else (ag, hg)
    return {
        "date": r.Date.date().isoformat(), "home": r.HomeTeam, "away": r.AwayTeam,
        "hg": hg, "ag": ag, "comp": r.comp or None,
        "venue": "H" if home else "A", "opponent": r.AwayTeam if home else r.HomeTeam,
        "outcome": "W" if gf > ga else "L" if gf < ga else "D",
    }


def last_matches(idx: pd.DataFrame, team: str, before: Optional[str] = None, n: int = N) -> List[Dict]:
    """The team's last `n` results before `before` (an ISO date), newest first."""
    if idx.empty or not team:
        return []
    cut = _before(before)
    rows = idx[((idx["HomeTeam"] == team) | (idx["AwayTeam"] == team)) & (idx["Date"] < cut)].head(n)
    return [_row(r, team) for r in rows.itertuples(index=False)]


def meetings(idx: pd.DataFrame, home: str, away: str, before: Optional[str] = None, n: int = N) -> List[Dict]:
    """The last `n` meetings, newest first; outcomes are `home`'s."""
    if idx.empty or not home or not away:
        return []
    cut = _before(before)
    pair = (((idx["HomeTeam"] == home) & (idx["AwayTeam"] == away)) |
            ((idx["HomeTeam"] == away) & (idx["AwayTeam"] == home)))
    return [_row(r, home) for r in idx[pair & (idx["Date"] < cut)].head(n).itertuples(index=False)]


def summary(rows: List[Dict]) -> Optional[Dict]:
    if not rows:
        return None
    return {"played": len(rows),
            "won": sum(r["outcome"] == "W" for r in rows),
            "drawn": sum(r["outcome"] == "D" for r in rows),
            "lost": sum(r["outcome"] == "L" for r in rows),
            "scored": sum(r["hg"] if r["venue"] == "H" else r["ag"] for r in rows),
            "conceded": sum(r["ag"] if r["venue"] == "H" else r["hg"] for r in rows)}


StatsFor = Callable[[str, str, str], Optional[Dict[str, float]]]


def _stat(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def averages(idx: pd.DataFrame, team: str, before: Optional[str] = None, n: int = AVG_N,
             stats_for: Optional[StatsFor] = None) -> Optional[Dict]:
    """The team's numbers for each market over its last `n` matches before
    `before`: goals for / against / total, how often 1.5 / 2.5 / 3.5 goals
    were passed, both teams scored, clean sheets, failed to score, results;
    and, from the matches whose stats are known, corners, booking points,
    shots and shots on target (for, against, match total). `stats_for(date,
    home, away)` can fill in stats the index lacks (another source)."""
    if idx.empty or not team:
        return None
    cut = _before(before)
    rows = idx[((idx["HomeTeam"] == team) | (idx["AwayTeam"] == team)) & (idx["Date"] < cut)].head(n)
    if rows.empty:
        return None
    games = len(rows)
    gf, ga, tot = [], [], []
    results = {"W": 0, "D": 0, "L": 0}
    stat_rows = {k: [] for k in ("corners", "bookings", "shots", "sot")}
    cols = {"corners": ("HC", "AC"), "bookings": ("HB", "AB"), "shots": ("HS", "AS"), "sot": ("HST", "AST")}
    for r in rows.itertuples(index=False):
        home = r.HomeTeam == team
        f, a = (int(r.FTHG), int(r.FTAG)) if home else (int(r.FTAG), int(r.FTHG))
        gf.append(f)
        ga.append(a)
        tot.append(f + a)
        results["W" if f > a else "L" if f < a else "D"] += 1
        extra = None
        for stat, (hc, ac) in cols.items():
            h_v, a_v = _stat(getattr(r, hc, None)), _stat(getattr(r, ac, None))
            if (h_v is None or a_v is None) and stats_for is not None:
                if extra is None:
                    extra = stats_for(r.Date.date().isoformat(), r.HomeTeam, r.AwayTeam) or {}
                h_v, a_v = _stat(extra.get(hc)), _stat(extra.get(ac))
            if h_v is not None and a_v is not None:
                stat_rows[stat].append((h_v, a_v) if home else (a_v, h_v))
    avg = lambda xs: round(sum(xs) / len(xs), 2) if xs else None
    pct = lambda k: round(k / games, 3)
    out = {
        "played": games,
        "goals": {"for": avg(gf), "against": avg(ga), "total": avg(tot)},
        "over": {line: pct(sum(t > float(line) for t in tot)) for line in ("0.5", "1.5", "2.5", "3.5")},
        "btts": pct(sum(f > 0 and a > 0 for f, a in zip(gf, ga))),
        "clean_sheet": pct(sum(a == 0 for a in ga)),
        "failed_to_score": pct(sum(f == 0 for f in gf)),
        "results": {"won": pct(results["W"]), "drawn": pct(results["D"]), "lost": pct(results["L"])},
    }
    for stat, pairs in stat_rows.items():
        out[stat] = ({"for": avg([p[0] for p in pairs]), "against": avg([p[1] for p in pairs]),
                      "total": avg([p[0] + p[1] for p in pairs]), "matches": len(pairs)} if pairs else None)
    return out


def facts(idx: pd.DataFrame, home: str, away: str, before: Optional[str] = None,
          stats_for: Optional[StatsFor] = None) -> Dict:
    h, a = last_matches(idx, home, before), last_matches(idx, away, before)
    h2h = meetings(idx, home, away, before)
    return {"home": h, "away": a, "h2h": h2h,
            "summary": {"home": summary(h), "away": summary(a), "h2h": summary(h2h)},
            "averages": {"home": averages(idx, home, before, stats_for=stats_for),
                         "away": averages(idx, away, before, stats_for=stats_for), "n": AVG_N}}


def text(f: Dict, home: str, away: str) -> str:
    """The facts as plain lines for the AI analysis prompt."""
    def line(r):
        return f"{r['date']}: {r['home']} {r['hg']}-{r['ag']} {r['away']}" + (f" ({r['comp']})" if r.get("comp") else "")
    out = []
    for side, name in (("home", home), ("away", away)):
        if f.get(side):
            out.append(f"{name}'s last {len(f[side])} results: " + "; ".join(line(r) for r in f[side]))
    out.append(f"Last {len(f['h2h'])} meetings: " + "; ".join(line(r) for r in f["h2h"])
               if f.get("h2h") else "No previous meetings on record.")
    return "\n".join(out)
