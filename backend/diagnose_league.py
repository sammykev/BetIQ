"""
Why a league's live predictions look the same on every match: loads the
published model and the live predictions, and prints for each of the
league's fixtures the names the model resolved, how many matches it has for
each, the chances it gives now, and what the site has stored.

    python diagnose_league.py PL     (GitHub Actions: "Diagnose league")
"""

import json
import sys

import model_store
from predictor import LeaguePredictor, MODEL_CACHE_VERSION


def run(league: str) -> None:
    r = model_store._client()
    got = model_store.fetch(MODEL_CACHE_VERSION, max_age_hours=24 * 14, client=r)
    if got is None:
        print("no published model")
        return
    model = LeaguePredictor.from_bytes(got[0])
    print(f"model: {got[1].get('rows')} matches, calibration {(getattr(model, 'calibration', None) or {}).get(league)}")
    print(f"league params: {(getattr(model, 'league_params', None) or {}).get(league)}")
    raw = r.get("betiq:predictions") if r is not None else None
    preds = [p for p in (json.loads(raw).get("predictions", []) if raw else []) if p.get("league") == league]
    print(f"{len(preds)} live {league} predictions")
    stats = model.team_stats
    for p in preds[:12]:
        names = [model.canon(p["home"]), model.canon(p["away"])]
        n = [len((stats.get(x) or {}).get("pts") or []) for x in names]
        now = model.predict_match(p["home"], p["away"], odds_home=float(p.get("odds_home") or 0),
                                  odds_draw=float(p.get("odds_draw") or 0), odds_away=float(p.get("odds_away") or 0),
                                  match_date=p.get("date"), league=p.get("model_league") or league) or {}
        print(f"  {p['home']} → {names[0]} ({n[0]}) v {p['away']} → {names[1]} ({n[1]}) | "
              f"model_league {p.get('model_league')} goals_only {p.get('goals_only')} needs_odds {p.get('needs_odds')} "
              f"odds {p.get('odds_home')}/{p.get('odds_draw')}/{p.get('odds_away')} | "
              f"stored {p.get('p_home')}/{p.get('p_draw')}/{p.get('p_away')} o25 {p.get('p_over25')} "
              f"constant {p.get('constant')} | now {now.get('p_home')}/{now.get('p_draw')}/{now.get('p_away')} "
              f"o25 {now.get('p_over25')}")
    # Names in the model that normalise like the league's clubs (duplicates block a match)
    from team_names import normalise
    by = {}
    for t in stats:
        by.setdefault(normalise(t), []).append(t)
    for p in preds[:12]:
        for side in ("home", "away"):
            k = normalise(p[side])
            if len(by.get(k, [])) > 1:
                print(f"  duplicate names for {p[side]!r}: {[(t, len(stats[t].get('pts') or [])) for t in by[k]]}")



def stored_days(r, league: str, days: int = 50) -> None:
    """The league's graded match days: what was stored for each match, and
    every league whose stored 1X2 chances repeat on 3+ matches of a day."""
    from collections import Counter
    from datetime import date, timedelta
    keys = [(date.today() - timedelta(days=i)).isoformat() for i in range(days)]
    repeats = Counter()
    for d, raw in zip(keys, r.mget([f"betiq:md:{d}" for d in keys])):
        if not raw:
            continue
        day = json.loads(raw)
        sig = Counter()
        for e in day.values():
            p = e.get("pred") or {}
            sig[(e.get("league"), p.get("p_home"), p.get("p_draw"), p.get("p_away"))] += 1
            if e.get("league") == league:
                print(f"  {d} {e.get('home')} v {e.get('away')}: {p.get('p_home')}/{p.get('p_draw')}/{p.get('p_away')} "
                      f"model {p.get('p_home_model')} o25 {p.get('p_over25')} odds {p.get('odds_home')} "
                      f"snap {e.get('snap_at')} locked {e.get('locked')} flags "
                      f"{[k for k in ('thin_history', 'constant', 'league_unproven') if p.get(k)]} "
                      f"result {(e.get('result') or {}).get('source')}")
        for (lg, *probs), n in sig.items():
            if n >= 3 and probs[0] is not None:
                repeats[(lg, tuple(probs))] += n
    print("\nrepeated 1X2 chances (league, chances: matches):")
    for (lg, probs), n in repeats.most_common(30):
        print(f"  {lg} {probs}: {n}")


if __name__ == "__main__":
    lg = sys.argv[1] if len(sys.argv) > 1 else "PL"
    run(lg)
    stored_days(model_store._client(), lg)
