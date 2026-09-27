"""
Collect corners/cards for international matches (international_stats.py),
then tune and test the international corners/bookings and shots models on
them (set_pieces.tune_international) and store the verdicts with the data, so
the API server knows whether to use it. Run by .github/workflows/
collect-international-stats.yml; needs UPSTASH_REDIS_URL, and
APIFOOTBALL_KEY for the API-Football top-up (60 requests a night by
default: the API server's referee lookup may use up to 12 more of the
free plan's 100 a day).

    python collect_international_stats.py --minutes 150 --af-budget 60
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

import international_stats
import intl_elo
import model_store
import set_pieces
import shots


def load_elo():
    """National-team Elo from the latest results history (the repository's
    copy if the download fails)."""
    import urllib.request
    import football_data_sync
    try:
        with urllib.request.urlopen(football_data_sync.INTERNATIONAL_URL, timeout=60) as resp:
            return intl_elo.EloTimeline.from_csv_text(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"Results history download failed ({e}); using the local copy")
        return intl_elo.EloTimeline.from_file(football_data_sync.INTERNATIONAL_PATH)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=float(os.getenv("INTL_STATS_MINUTES", "150")))
    ap.add_argument("--af-budget", type=int, default=int(os.getenv("APIFOOTBALL_DAILY_BUDGET", "60")))
    args = ap.parse_args()
    r = model_store._client()
    if r is None:
        print("UPSTASH_REDIS_URL isn't set — nothing to store to. Skipping.")
        return 0
    api_key = os.getenv("APIFOOTBALL_KEY", "").strip()
    if not api_key:
        print("APIFOOTBALL_KEY isn't set — SofaScore only this run.")
    report = asyncio.run(international_stats.run(r, args.minutes, api_key, args.af_budget))
    print("Collection:", json.dumps(report, indent=1, default=str))

    data = international_stats.load(r)
    frame = international_stats.rows_frame(data)
    verdict = set_pieces.tune_international(frame)
    data["model"] = {**verdict, "at": datetime.now(timezone.utc).isoformat()}
    # Shots and shots on target: the same test, on the matches with shot counts,
    # also trying each team's Elo (from the full results history) as where its
    # ratings start: the check keeps it only if it scores better unseen
    shot_frame = frame.dropna(subset=list(shots.ShotModel.REQUIRED)) if not frame.empty else frame
    elo = load_elo()
    set_pieces.STRENGTH = elo.strength if elo else None
    try:
        shot_verdict = set_pieces.tune_international(shot_frame, model_cls=shots.ShotModel)
    finally:
        set_pieces.STRENGTH = None
    if elo:
        rated = sum(1 for rec in shot_frame.itertuples(index=False)
                    if elo.strength(rec.HomeTeam, rec.Date) is not None and elo.strength(rec.AwayTeam, rec.Date) is not None)
        shot_verdict["elo_coverage"] = {"teams": elo.teams(), "matches_rated": rated, "matches": int(len(shot_frame))}
    data["shots_model"] = {**shot_verdict, "at": datetime.now(timezone.utc).isoformat()}
    international_stats.save(r, data)
    print("Model check:", json.dumps(data["model"], indent=1, default=str))
    print("Shots model check:", json.dumps(data["shots_model"], indent=1, default=str))
    print("Dataset:", json.dumps(international_stats.summary(data), indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
