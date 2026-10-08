"""
Which fixture clubs does the model know nothing (or next to nothing) about?
For every football club in the current predictions: the name the model
resolves it to (team_names.TeamResolver), how many matches it has, and,
for clubs under MIN_MATCHES, the closest names the model does know (same
league first), so a missing alias can be added to team_names.py. Reads only.

    python club_check.py      (GitHub Actions: "Basketball data", job clubs)
"""

import difflib
import json
from collections import defaultdict
from typing import Dict, List

MIN_MATCHES = 5


def known_teams(model) -> Dict[str, int]:
    return {t: len(s.get("pts") or []) for t, s in (getattr(model, "team_stats", None) or {}).items()}


def suggestions(name: str, model, league: str, n: int = 4) -> List[str]:
    """Known clubs with history whose names come closest, the fixture's league first."""
    import team_names
    counts = known_teams(model)
    pool = [t for t, c in counts.items() if c >= MIN_MATCHES]
    same = [t for t in pool if (getattr(model, "team_league", {}) or {}).get(t) == league]
    norm = {t: team_names.normalise(t) for t in pool}
    target = team_names.normalise(name)
    out: List[str] = []
    for group in (same, pool):
        by_norm = defaultdict(list)
        for t in group:
            by_norm[norm[t]].append(t)
        for hit in difflib.get_close_matches(target, list(by_norm), n=n, cutoff=0.5):
            for t in by_norm[hit]:
                if t not in out:
                    out.append(t)
        if len(out) >= n:
            break
    return [f"{t} [{counts[t]}, {(getattr(model, 'team_league', {}) or {}).get(t, '?')}]" for t in out[:n]]


def check(models: Dict[str, object], preds: List[Dict]) -> List[Dict]:
    seen, rows = set(), []
    for p in preds:
        if p.get("sport") not in (None, "football"):
            continue
        league = p.get("model_league") or p.get("league") or ""
        for side in ("home", "away"):
            name = p.get(side)
            if not name or name in seen:
                continue
            seen.add(name)
            row = {"club": name, "league": p.get("league") or "", "league_name": p.get("league_name") or ""}
            for label, m in models.items():
                key = m.canon(name)
                row[label] = {"as": key, "matches": len(((m.team_stats or {}).get(key) or {}).get("pts") or [])}
            if all(row[label]["matches"] < MIN_MATCHES for label in models):
                main = models.get("main") or next(iter(models.values()))
                row["closest"] = suggestions(name, main, league)
                rows.append(row)
    return sorted(rows, key=lambda r: (r["league"], r["club"]))


def main() -> None:
    import model_store
    from predictor import LeaguePredictor, MODEL_CACHE_VERSION
    import europe_model

    r = model_store._client()
    if r is None:
        raise SystemExit("UPSTASH_REDIS_URL isn't set")
    models = {}
    for label, name in (("main", ""), ("europe", europe_model.MODEL_NAME)):
        got = model_store.fetch(MODEL_CACHE_VERSION, max_age_hours=24 * 14, client=r, name=name)
        m = LeaguePredictor.from_bytes(got[0]) if got else None
        if m is not None:
            models[label] = m
            print(f"{label} model: {len(known_teams(m))} teams")
    if not models:
        raise SystemExit("no model published")
    raw = r.get("betiq:predictions")
    preds = (json.loads(raw) or {}).get("predictions", []) if raw else []
    rows = check(models, preds)
    print(f"\n{len(preds)} predictions; {len(rows)} clubs with under {MIN_MATCHES} matches in every model:\n")
    by_league = defaultdict(list)
    for row in rows:
        by_league[f"{row['league']} {row['league_name']}"].append(row)
    for lg, rs in by_league.items():
        print(f"{lg}: {len(rs)}")
        for row in rs:
            seen = ", ".join(f"{k}: {v['as']!r} {v['matches']}" for k, v in row.items() if isinstance(v, dict))
            print(f"   {row['club']!r} → {seen} · closest: {'; '.join(row['closest']) or '—'}")
    r.set("betiq:club_check", json.dumps({"clubs": rows}), ex=7 * 86400)


if __name__ == "__main__":
    main()
