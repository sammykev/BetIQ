"""
Do our predictions hold up in the European leagues we publish beyond
football-data.org's (league_fixtures.COMPETITIONS: Belgium, Scotland, Greece,
Turkey, Austria, Switzerland, Denmark, Norway, Sweden, Poland, Romania)?

Walk-forward over the last WINDOW_DAYS, as the server predicts them: the
model trains on its own leagues with these leagues as context rows (ratings
and form, no training rows), and each of their matches is predicted before it
is played, with the bookmaker's closing odds as a feature. A league passes
when it has MIN_MATCHES scored and our 1X2 log loss trails the bookmaker's by
no more than it does in the leagues we train on (plus MARGIN). One that
doesn't is still published, with a warning, and kept out of slips
(league_fixtures.unproven).

The top flights only SportyBet covers (sb_league_fixtures.py) have no odds
in their history, so they're replayed without odds: a league passes when it
has MIN_MATCHES scored and our 1X2 log loss beats knowing nothing (the main
leagues' home/draw/away shares) by at least SB_SHARE of what it does in the
main leagues, predicted the same way.

    python check_extra_leagues.py      (GitHub Actions: "Basketball data", job extra_leagues)
"""

import asyncio
import json
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List

import pandas as pd

WINDOW_DAYS = 365
MIN_MATCHES = 100
MARGIN = 0.01
SEASONS = 3
SB_SHARE = 0.5


def make_model(groups: set):
    """A LeaguePredictor that trains as the server's does: these leagues'
    rows only as context (walk_forward scores them, so they come in unflagged)."""
    import main as app
    from predictor import LeaguePredictor

    class Model(LeaguePredictor):
        def train(self, matches: pd.DataFrame):
            m = matches.copy()
            m["Context"] = m["Context"].fillna(False).astype(bool) | m["league"].isin(groups) \
                if "Context" in m.columns else m["league"].isin(groups)
            self.use_league_strength = app._club_league_strength
            self.elo.seed_national_teams()
            return super().train(m)
    return Model


def verdicts(records: List[Dict], groups: set) -> Dict:
    """Per league: matches, model and bookmaker log loss and accuracy, the gap,
    and whether it passes against the trained leagues' gap."""
    from backtest import _market_1x2, _model_1x2, _scores_1x2
    by: Dict[str, List[Dict]] = {}
    for r in records:
        if _market_1x2(r):
            by.setdefault(r["league"] if r["league"] in groups else "_main", []).append(r)

    def score(rs):
        m = _scores_1x2([_model_1x2(r) for r in rs], [r["result"] for r in rs])
        b = _scores_1x2([_market_1x2(r) for r in rs], [r["result"] for r in rs])
        gap = round(m["log_loss"] - b["log_loss"], 4) if rs else None
        return {"matches": len(rs), "model": m, "bookmaker": b, "gap": gap}

    main = score(by.get("_main", []))
    bar = (main["gap"] if main["gap"] is not None else 0.0) + MARGIN
    leagues = {}
    for code in sorted(groups):
        s = score(by.get(code, []))
        s["pass"] = bool(s["matches"] >= MIN_MATCHES and s["gap"] is not None and s["gap"] <= bar)
        s["why"] = (f"only {s['matches']} matches" if s["matches"] < MIN_MATCHES else
                    f"trails the bookmaker by {s['gap']} (bar {round(bar, 4)})" if not s["pass"] else "ok")
        leagues[code] = s
    return {"main": main, "bar": round(bar, 4), "leagues": leagues}


def sb_verdicts(records: List[Dict], groups: set) -> Dict:
    """Per league (no odds): matches, model log loss, the know-nothing log
    loss, the gain over it, and whether it passes against the main leagues' gain."""
    from backtest import _model_1x2, _scores_1x2
    main = [r for r in records if r["league"] not in groups and not str(r["league"]).startswith("SB-")]
    n = max(len(main), 1)
    base = [sum(r["result"] == k for r in main) / n for k in "HDA"] if main else [0.45, 0.26, 0.29]

    def score(rs):
        m = _scores_1x2([_model_1x2(r) for r in rs], [r["result"] for r in rs])
        b = _scores_1x2([base for _ in rs], [r["result"] for r in rs])
        gain = round(b["log_loss"] - m["log_loss"], 4) if rs else None
        return {"matches": len(rs), "model": m, "know_nothing": b, "gain": gain}

    main_s = score(main)
    bar = round(SB_SHARE * (main_s["gain"] or 0.0), 4)
    leagues = {}
    for code in sorted(groups):
        s = score([r for r in records if r["league"] == code])
        s["pass"] = bool(s["matches"] >= MIN_MATCHES and s["gain"] is not None and s["gain"] >= bar)
        s["why"] = (f"only {s['matches']} matches" if s["matches"] < MIN_MATCHES else
                    f"gains {s['gain']} on knowing nothing (bar {bar})" if not s["pass"] else "ok")
        leagues[code] = s
    return {"main": main_s, "bar": bar, "base": [round(x, 3) for x in base], "leagues": leagues}


def main() -> int:
    import main as app
    import model_store
    from backtest import walk_forward
    from football_data_sync import recent_seasons, sync, sync_extra
    from league_fixtures import COMPETITIONS, UNPROVEN_KEY
    groups = set(COMPETITIONS)
    seasons = recent_seasons(date.today(), SEASONS)
    asyncio.run(sync(app.FOOTBALL_DATA_DIR, seasons=seasons))
    print("[Extra] Extra leagues:", json.dumps(asyncio.run(sync_extra(app.EXTRA_DATA_DIR, seasons=seasons))))
    assembled = app._assemble_training_data()
    if assembled is None:
        print("No training data")
        return 1
    _, combined, _ = assembled
    combined = combined[combined["league"].fillna("") != "INT"]       # national teams: not needed here
    start = (date.today() - timedelta(days=WINDOW_DAYS)).isoformat()
    end = date.today().isoformat()
    # These leagues' rows in the window are scored (unflagged); Model.train flags them back
    scored = combined["league"].isin(groups) & (combined["Date"] >= start)
    combined = combined.copy()
    combined.loc[scored, "Context"] = False
    print(f"[Extra] {len(combined)} rows, {int(scored.sum())} to score in {', '.join(sorted(groups))}")
    records = walk_forward(combined, start, end, make_model=make_model(groups), log=print)
    out = verdicts(records, groups)
    out["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out["window"] = [start, end]
    for code, s in out["leagues"].items():
        print(f"{code:4} {'PASS' if s['pass'] else 'warn'}  {s['matches']:4} matches  "
              f"model {s['model']['log_loss']} / acc {s['model']['accuracy']}  "
              f"bookmaker {s['bookmaker']['log_loss']} / acc {s['bookmaker']['accuracy']}  ({s['why']})")
    print(f"Trained leagues: gap {out['main']['gap']} over {out['main']['matches']} matches; bar {out['bar']}")

    # The SportyBet-only leagues: no odds in their history, so all replayed without
    sb_groups = {lg for lg in combined["league"].dropna().unique() if str(lg).startswith("SB-")}
    if sb_groups:
        both = groups | sb_groups
        frame = combined.copy()
        frame.loc[frame["league"].isin(sb_groups) & (frame["Date"] >= start), "Context"] = False
        frame.loc[frame["league"].isin(groups), "Context"] = True
        print(f"[Extra] SportyBet-only leagues, without odds: {', '.join(sorted(sb_groups))}")
        sb_records = walk_forward(frame, start, end, make_model=make_model(both), log=print, hide_odds=True)
        sb_out = sb_verdicts(sb_records, sb_groups)
        out["sportybet"] = {k: v for k, v in sb_out.items() if k != "leagues"}
        out["leagues"].update(sb_out["leagues"])
        for code, s in sb_out["leagues"].items():
            print(f"{code:7} {'PASS' if s['pass'] else 'warn'}  {s['matches']:4} matches  "
                  f"model {s['model']['log_loss']} / acc {s['model']['accuracy']}  "
                  f"know-nothing {s['know_nothing']['log_loss']}  ({s['why']})")
        print(f"Main leagues without odds: gain {sb_out['main']['gain']} over {sb_out['main']['matches']} "
              f"matches; bar {sb_out['bar']}")
    r = model_store._client()
    if r is None:
        print("UPSTASH_REDIS_URL isn't set: not saved")
        return 0
    r.set(UNPROVEN_KEY, json.dumps(out))
    print(f"Saved to {UNPROVEN_KEY}: the server picks it up on its next predictions run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
