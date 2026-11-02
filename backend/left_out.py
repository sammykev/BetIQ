"""
What the site leaves out, and where more data would help:

1. Our football predictions kept off slips, by league and reason: a club
   with fewer than THIN_HISTORY matches in our data (which clubs), a league
   that hasn't passed its walk-forward check, a paused league, or chances
   that repeat (not a real prediction).
2. SportyBet's upcoming football we don't predict at all, by country and
   tournament, with how many of that tournament's matches finished in the
   last RESULT_DAYS days (SportyBet's results list: the history
   collect_sb_leagues.py could learn from).

    python left_out.py      (GitHub Actions: "Left-out matches", by hand)
"""

import asyncio
import json
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List

RESULT_DAYS = 14
THIN_HISTORY = 5          # as main.THIN_HISTORY
SHOW = 60


def reasons(p: Dict) -> List[str]:
    out = []
    if p.get("constant"):
        out.append("repeated chances")
    elif p.get("thin_history"):
        out.append("thin club data")
    if p.get("league_unproven"):
        out.append("league not proven")
    if p.get("league_paused"):
        out.append("league paused")
    return out


def predictions_report(preds: List[Dict], model=None, europe=None, europe_leagues=()) -> None:
    from team_names import suggest
    football = [p for p in preds if p.get("sport") in (None, "football")]
    by_league: Dict[str, Counter] = defaultdict(Counter)
    thin_clubs: Dict[str, Counter] = defaultdict(Counter)
    names: Dict[str, str] = {}
    for p in football:
        lg = str(p.get("league") or "?")
        names[lg] = names.get(lg) or p.get("league_name") or lg
        by_league[lg]["matches"] += 1
        why = reasons(p)
        if why:
            by_league[lg]["left out"] += 1
        for w in why:
            by_league[lg][w] += 1
        m = europe if (europe is not None and lg in europe_leagues) else model
        if "thin club data" in why and m is not None:
            stats, canon = m.team_stats, m.canon
            for side in ("home", "away"):
                n = len((stats.get(canon(p[side])) or {}).get("pts") or [])
                if n < THIN_HISTORY:
                    # The names the model does know that look like it: a missing alias?
                    near = [f"{k} ({len((stats.get(k) or {}).get('pts') or [])})"
                            for k in suggest(p[side], [k for k, v in stats.items() if len(v.get("pts") or []) >= 10])]
                    thin_clubs[lg][f"{p[side]} ({n}) ~ {' / '.join(near) or 'nothing close'}"] += 1
    out = sum(c["left out"] for c in by_league.values())
    print(f"== Our football predictions: {len(football)}, {out} left out of slips ==")
    for lg, c in sorted(by_league.items(), key=lambda kv: (-kv[1]["left out"], kv[0])):
        if not c["left out"]:
            continue
        why = ", ".join(f"{k} {v}" for k, v in c.items() if k not in ("matches", "left out"))
        print(f"  {lg:<10} {names[lg][:34]:<34} {c['left out']:>3} of {c['matches']:>3}: {why}")
        if thin_clubs.get(lg):
            for c in sorted(thin_clubs[lg]):
                print(f"             {c}")
    print()


async def sportybet_report(linked_ids: set) -> None:
    import results_feed
    import sportybet
    events, notes = await sportybet.fetch_catalog()
    print(f"== SportyBet's upcoming football: {len(events)} events ({'; '.join(notes)}) ==")
    not_ours: Counter = Counter()
    ours: Counter = Counter()
    for ev in events:
        if results_feed.not_senior(ev):
            continue
        t = ev.get("_tournament") or "?"
        (ours if str(ev.get("eventId")) in linked_ids else not_ours)[t] += 1
    # How much history SportyBet's results list has for each: the last RESULT_DAYS days
    session = sportybet.shared_session()
    finished: Counter = Counter()
    today = datetime.now(timezone.utc).date()
    for i in range(1, RESULT_DAYS + 1):
        d = today - timedelta(days=i)
        start = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        try:
            res, _, _ = await sportybet._paged(session, "/factsCenter/eventResultList", {
                "pageSize": 100, "sportId": sportybet.FOOTBALL, "startTime": int(start.timestamp() * 1000),
                "endTime": int((start + timedelta(days=1)).timestamp() * 1000)}, 60)
        except Exception as e:
            print(f"  results {d}: {type(e).__name__}: {e}")
            continue
        for ev in res:
            if not results_feed.not_senior(ev):
                finished[ev.get("_tournament") or "?"] += 1
    print(f"\n  Not predicted by us ({sum(not_ours.values())} events in {len(not_ours)} tournaments), "
          f"with finished matches in the last {RESULT_DAYS} days:")
    for t, n in not_ours.most_common(SHOW):
        print(f"    {n:>4} upcoming · {finished.get(t, 0):>4} finished · {t}")
    print(f"\n  Predicted ({sum(ours.values())} events in {len(ours)} tournaments): "
          + ", ".join(f"{t} ({n})" for t, n in ours.most_common(25)))
    by_country: Counter = Counter()
    for t, n in not_ours.items():
        by_country[t.partition(" · ")[0]] += n
    print("\n  Not predicted, by country: " + ", ".join(f"{c} {n}" for c, n in by_country.most_common(40)))


def main() -> None:
    import model_store
    from predictor import MODEL_CACHE_VERSION, LeaguePredictor
    r = model_store._client()
    if r is None:
        raise SystemExit("UPSTASH_REDIS_URL isn't set")
    started = time.time()
    raw = r.get("betiq:predictions")
    preds = json.loads(raw).get("predictions", []) if raw else []
    got = model_store.fetch(MODEL_CACHE_VERSION, max_age_hours=24 * 14, client=r)
    model = LeaguePredictor.from_bytes(got[0]) if got else None
    import europe_model
    eu = model_store.fetch(MODEL_CACHE_VERSION, max_age_hours=24 * 14, client=r, name=europe_model.MODEL_NAME)
    europe = LeaguePredictor.from_bytes(eu[0]) if eu else None
    try:
        verdict = json.loads(r.get(europe_model.CHECK_KEY) or "{}")
        eu_leagues = europe_model.competitions(verdict)
    except Exception:
        eu_leagues = ["CL", "EL", "UECL"]
    predictions_report(preds, model, europe, eu_leagues)
    links = json.loads(r.get("betiq:sportybet:links") or "{}")
    linked = {str((v or {}).get("eventId")) for v in links.values() if isinstance(v, dict)}
    asyncio.run(sportybet_report(linked))
    print(f"\n({time.time() - started:.0f}s)")


if __name__ == "__main__":
    main()
