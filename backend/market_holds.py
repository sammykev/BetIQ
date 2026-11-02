"""
Markets left out of every slip (the optimizer's and the daily ones): in the
confidence checks their 80%+ picks came in well under what the model said.

  Oct 30 2026: basketball total 78% vs 82%, Q1 74% vs 85%, Q3 78% vs 87%,
               first-half total 50% vs 81%, tennis set 1 handicap 76% vs 83%
  Nov 2 2026:  basketball Q2 winner 80.5% vs 86.4%, tennis total games
               79.5% vs 84.3%

Codes are "sport:market". HELD_OUT_MARKETS (comma-separated) adds codes;
"none" clears the list.
"""

import os

_ENV = os.getenv("HELD_OUT_MARKETS", "")

HELD_OUT_MARKETS = (set() if _ENV.strip().lower() == "none" else
                    {"basketball:bb_total", "basketball:bb_q1_1x2", "basketball:bb_q2_1x2", "basketball:bb_q3_1x2",
                     "basketball:bb_h1_total", "tennis:rk_s1_handicap", "tennis:rk_total_games"}
                    | {c.strip() for c in _ENV.split(",") if ":" in c})
