"""
Every ticket on the site, reviewed: how tickets and their legs did, by
market, sport, line, the chance we gave and the ticket's size. Read only;
prints totals, never an account id.

A leg's win rate is won / (won + lost); void, pending and unknown legs
(markets from pasted codes we don't settle) are counted but not in the rate.
Profit is a flat 1-unit stake on each settled leg (or ticket) at its odds.

    python ticket_review.py     (GitHub Actions: "Basketball data", job ticket_review)
"""

import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

REPORT_KEY = "betiq:ticket_review"
BANDS = [(0.0, 0.5, "<50%"), (0.5, 0.6, "50–60%"), (0.6, 0.7, "60–70%"), (0.7, 0.8, "70–80%"),
         (0.8, 0.9, "80–90%"), (0.9, 1.01, "90%+")]
SIZES = [(1, 1, "1 leg"), (2, 3, "2–3"), (4, 6, "4–6"), (7, 10, "7–10"), (11, 999, "11+")]


def sport_of(leg: Dict) -> str:
    m = str(leg.get("market") or "")
    if leg.get("sport") in ("basketball", "tennis", "table_tennis", "racket"):
        return {"racket": "tennis/table tennis"}.get(leg["sport"], leg["sport"]).replace("_", " ")
    if m.startswith("bb_"):
        return "basketball"
    if m.startswith("rk_"):
        return "tennis/table tennis"
    return "football"


def _num(x) -> Optional[float]:
    return float(x) if isinstance(x, (int, float)) else None


class Tally:
    def __init__(self):
        self.n = self.won = self.lost = self.void = self.pending = self.other = 0
        self.probs: List[float] = []
        self.odds: List[float] = []
        self.profit = 0.0
        self.priced = 0

    def add(self, status: str, prob=None, odds=None):
        self.n += 1
        if status == "won":
            self.won += 1
        elif status == "lost":
            self.lost += 1
        elif status == "void":
            self.void += 1
        elif status == "pending":
            self.pending += 1
        else:
            self.other += 1
        if status in ("won", "lost"):
            p, o = _num(prob), _num(odds)
            if p is not None and 0 < p <= 1:
                self.probs.append(p)
            if o is not None and o > 1:
                self.odds.append(o)
                self.priced += 1
                self.profit += (o - 1) if status == "won" else -1.0

    def row(self) -> Dict:
        settled = self.won + self.lost
        return {"total": self.n, "settled": settled, "won": self.won, "lost": self.lost,
                "void": self.void, "pending": self.pending, "unsettled_other": self.other,
                "win_rate": round(self.won / settled, 4) if settled else None,
                "avg_prob_given": round(sum(self.probs) / len(self.probs), 4) if self.probs else None,
                "avg_odds": round(sum(self.odds) / len(self.odds), 2) if self.odds else None,
                "profit_units": round(self.profit, 2) if self.priced else None,
                "roi": round(self.profit / self.priced, 4) if self.priced else None}


def review(all_tickets: Iterable[List[Dict]]) -> Dict:
    accounts = 0
    tickets = Tally()
    by_source: Dict[str, Tally] = defaultdict(Tally)
    by_size: Dict[str, Tally] = defaultdict(Tally)
    legs = Tally()
    by_sport: Dict[str, Tally] = defaultdict(Tally)
    by_market: Dict[str, Tally] = defaultdict(Tally)
    by_line: Dict[str, Tally] = defaultdict(Tally)
    by_band: Dict[str, Tally] = defaultdict(Tally)
    by_month: Dict[str, Tally] = defaultdict(Tally)
    names: Dict[str, str] = {}
    first = last = None
    for ts in all_tickets:
        if not ts:
            continue
        accounts += 1
        for t in ts:
            ls = t.get("legs") or []
            created = str(t.get("created_at") or "")
            first = min(first or created, created) if created else first
            last = max(last or created, created) if created else last
            tickets.add(t.get("status") or "pending", odds=t.get("total_odds"))
            by_source[str(t.get("source") or "unknown")].add(t.get("status") or "pending", odds=t.get("total_odds"))
            k = len(ls)
            size = next((label for lo, hi, label in SIZES if lo <= k <= hi), "0 legs")
            by_size[size].add(t.get("status") or "pending", odds=t.get("total_odds"))
            for leg in ls:
                st = leg.get("status") or "pending"
                sport = sport_of(leg)
                market = str(leg.get("market") or "?")
                names.setdefault(market, leg.get("marketName") or leg.get("market_name") or market)
                p, o = leg.get("prob"), leg.get("odds")
                legs.add(st, p, o)
                by_sport[sport].add(st, p, o)
                by_market[f"{sport} · {market}"].add(st, p, o)
                code = str(leg.get("code") or "")
                if "|" not in code:      # player props: one line per player, not useful
                    by_line[f"{sport} · {market} · {code}"].add(st, p, o)
                pp = _num(p)
                if pp is not None and 0 < pp <= 1:
                    band = next(label for lo, hi, label in BANDS if lo <= pp < hi)
                    by_band[band].add(st, p, o)
                by_month[created[:7] or "?"].add(st, p, o)
    order = lambda d: dict(sorted(((k, v.row()) for k, v in d.items()), key=lambda kv: -kv[1]["settled"]))
    return {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "accounts_with_tickets": accounts, "first_ticket": first, "last_ticket": last,
        "tickets": tickets.row(), "tickets_by_source": order(by_source), "tickets_by_legs": order(by_size),
        "legs": legs.row(), "legs_by_sport": order(by_sport),
        "legs_by_market": order(by_market), "market_names": names,
        "legs_by_line": {k: v for k, v in order(by_line).items() if v["settled"] >= 5},
        "legs_by_prob_given": {b: by_band[b].row() for _, _, b in BANDS if b in by_band},
        "legs_by_month": dict(sorted((k, v.row()) for k, v in by_month.items())),
    }


def _pct(x) -> str:
    return "—" if x is None else f"{100 * x:.1f}%"


def show(rep: Dict) -> None:
    t, l = rep["tickets"], rep["legs"]
    print(f"Tickets {rep['first_ticket'][:10] if rep['first_ticket'] else '?'} → "
          f"{rep['last_ticket'][:10] if rep['last_ticket'] else '?'}, {rep['accounts_with_tickets']} accounts")
    print(f"TICKETS {t['total']}: won {t['won']} lost {t['lost']} void {t['void']} pending {t['pending']} · "
          f"win rate {_pct(t['win_rate'])} · avg odds {t['avg_odds']} · flat-stake profit {t['profit_units']} u "
          f"(ROI {_pct(t['roi'])})")
    print(f"LEGS {l['total']}: won {l['won']} lost {l['lost']} void {l['void']} pending {l['pending']} "
          f"other {l['unsettled_other']} · win rate {_pct(l['win_rate'])} · chance we gave {_pct(l['avg_prob_given'])}")

    def table(title, rows):
        print(f"\n{title}")
        print(f"  {'':44} {'settled':>7} {'won':>5} {'win%':>7} {'we said':>8} {'odds':>6} {'ROI':>7} {'pend':>5}")
        for k, v in rows.items():
            print(f"  {k[:44]:44} {v['settled']:>7} {v['won']:>5} {_pct(v['win_rate']):>7} "
                  f"{_pct(v['avg_prob_given']):>8} {v['avg_odds'] or '—':>6} {_pct(v['roi']):>7} {v['pending']:>5}")
    table("Tickets by source", rep["tickets_by_source"])
    table("Tickets by number of legs", rep["tickets_by_legs"])
    table("Legs by sport", rep["legs_by_sport"])
    table("Legs by market", rep["legs_by_market"])
    table("Legs by line (5+ settled)", rep["legs_by_line"])
    table("Legs by the chance we gave", rep["legs_by_prob_given"])
    table("Legs by month", rep["legs_by_month"])


def main() -> None:
    import redis
    url = os.getenv("UPSTASH_REDIS_URL")
    if not url:
        raise SystemExit("UPSTASH_REDIS_URL isn't set")
    r = redis.from_url(url, decode_responses=True)
    keys = list(r.scan_iter(match="betiq:user:*:tickets", count=500))

    def load():
        for i in range(0, len(keys), 100):
            for raw in r.mget(keys[i:i + 100]):
                try:
                    yield json.loads(raw) if raw else []
                except ValueError:
                    yield []
    rep = review(load())
    show(rep)
    r.set(REPORT_KEY, json.dumps(rep))
    print("\nJSON:", json.dumps({k: rep[k] for k in ("tickets", "legs", "legs_by_sport", "legs_by_market")}))


if __name__ == "__main__":
    main()
