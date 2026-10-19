"""
Ticket watch: follow chosen tickets until every one is done, then report
how each did against what the model expected, which one underperformed,
and how the legs from 70%+ markets did next to the fallback ones.

The admin starts a watch (Admin → Predictions → Ticket watch); the
match-day job checks it after settling tickets and sends each finished
ticket, then the final comparison, to the admin's Telegram chat.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

KEY = "betiq:ticket_watch"
# A ticket whose last match is this many days old is reported as it stands
# (a leg we can't settle mustn't hold the watch open for ever)
GIVE_UP_DAYS = 3
FINAL = ("won", "lost", "void")


def start(items: List[Dict], codes: Optional[List[str]] = None) -> Dict[str, Any]:
    """A new watch over `codes`, or over every ticket still being played
    when none are given. `items`: every account's tickets (with uid)."""
    want = {c.strip().upper() for c in codes or [] if c and c.strip()}
    picked = [t for t in items if (str(t.get("code") or "").upper() in want if want
                                  else t.get("status") in ("pending", "open"))]
    seen, watched = set(), []
    for t in picked:
        if t.get("code") not in seen:
            seen.add(t.get("code"))
            watched.append({"code": t.get("code"), "uid": t.get("uid")})
    return {"started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tickets": watched, "reported": [], "done_at": None, "summary": None,
            "missing": sorted(want - {str(t["code"]).upper() for t in watched})}


def _last_date(t: Dict) -> str:
    return max((l.get("date") or "" for l in t.get("legs") or []), default="")


def finished(t: Dict, today: date) -> bool:
    """Every leg graded and the ticket settled, or its last match is
    GIVE_UP_DAYS old."""
    legs = t.get("legs") or []
    graded = all(l.get("status") not in (None, "pending") for l in legs)
    if t.get("status") in FINAL and graded:
        return True
    return bool(_last_date(t)) and _last_date(t) < (today - timedelta(days=GIVE_UP_DAYS)).isoformat()


def _rank_key(leg: Dict, sport_of: Callable[[Dict], str]) -> str:
    m = str(leg.get("market") or "")
    sport = "basketball" if m.startswith("bb_player_") else sport_of(leg)
    return f"{sport}:{m}"


def performance(t: Dict, ranked: Optional[Dict[str, Dict]], min_hit: float,
                sport_of: Callable[[Dict], str]) -> Dict[str, Any]:
    """One ticket against the model: legs won of those graded, how many the
    model expected (sum of its chances), the gap, and the 70%+ market legs
    next to the fallback ones."""
    legs, graded = t.get("legs") or [], []
    tiers = {"top": {"won": 0, "n": 0}, "fallback": {"won": 0, "n": 0}, "unranked": {"won": 0, "n": 0}}
    out_legs = []
    for l in legs:
        x = (ranked or {}).get(_rank_key(l, sport_of))
        tier = "unranked" if not x else "top" if x["hit_rate"] >= min_hit else "fallback"
        st = l.get("status") or "pending"
        out_legs.append({"match": f"{l.get('home')} v {l.get('away')}", "pick": l.get("label") or l.get("code"),
                         "market": l.get("marketName") or l.get("market"), "prob": l.get("prob"),
                         "odds": l.get("odds"), "status": st, "tier": tier,
                         "market_hit_rate": x["hit_rate"] if x else None})
        if st in ("won", "lost"):
            graded.append(l)
            tiers[tier]["n"] += 1
            tiers[tier]["won"] += st == "won"
    won = sum(1 for l in graded if l.get("status") == "won")
    expected = sum(float(l.get("prob") or 0) for l in graded if l.get("prob") is not None)
    has_prob = all(l.get("prob") is not None for l in graded)
    return {"code": t.get("code"), "source": t.get("source"), "status": t.get("status"),
            "total_odds": t.get("total_odds"), "legs": out_legs, "graded": len(graded), "won": won,
            "expected": round(expected, 2) if graded and has_prob else None,
            "gap": round(won - expected, 2) if graded and has_prob else None,
            "tiers": tiers}


def _pct(won: int, n: int) -> str:
    return f"{won}/{n} ({round(100 * won / n)}%)" if n else "none"


def ticket_text(p: Dict[str, Any]) -> str:
    lines = [f"Ticket {p['code']} ({p.get('source') or 'other'}, {p.get('total_odds') or '?'} odds): "
             f"{str(p.get('status') or 'pending').upper()}",
             f"Legs won {_pct(p['won'], p['graded'])}"
             + (f", model expected {p['expected']} ({p['gap']:+.2f})" if p.get("gap") is not None else "")]
    tiers = p["tiers"]
    lines.append(f"70%+ markets {_pct(tiers['top']['won'], tiers['top']['n'])} · "
                 f"fallback markets {_pct(tiers['fallback']['won'], tiers['fallback']['n'])}")
    for l in p["legs"]:
        if l["status"] == "lost":
            rate = f", market {round(100 * l['market_hit_rate'])}%" if l.get("market_hit_rate") is not None else ""
            chance = f"{round(100 * l['prob'])}%" if l.get("prob") is not None else "?"
            lines.append(f"  ✗ {l['match']}: {l['pick']} (model {chance}{rate})")
    return "\n".join(lines)


def summary(perfs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """All the watched tickets together, and the one furthest under what
    the model expected per graded leg."""
    top = {"won": sum(p["tiers"]["top"]["won"] for p in perfs), "n": sum(p["tiers"]["top"]["n"] for p in perfs)}
    fb = {"won": sum(p["tiers"]["fallback"]["won"] for p in perfs), "n": sum(p["tiers"]["fallback"]["n"] for p in perfs)}
    scored = [p for p in perfs if p.get("gap") is not None and p["graded"]]
    worst = min(scored, key=lambda p: p["gap"] / p["graded"], default=None)
    return {"tickets": len(perfs), "won": sum(1 for p in perfs if p.get("status") == "won"),
            "legs_won": sum(p["won"] for p in perfs), "legs": sum(p["graded"] for p in perfs),
            "expected": round(sum(p["expected"] for p in scored), 2) if scored else None,
            "top": top, "fallback": fb,
            "underperformer": worst["code"] if worst and worst["gap"] < 0 else None}


def summary_text(s: Dict[str, Any], perfs: List[Dict[str, Any]]) -> str:
    lines = [f"Ticket watch done: {s['won']} of {s['tickets']} tickets won, "
             f"legs {_pct(s['legs_won'], s['legs'])}"
             + (f" (model expected {s['expected']})" if s.get("expected") is not None else ""),
             f"70%+ markets {_pct(s['top']['won'], s['top']['n'])} · fallback {_pct(s['fallback']['won'], s['fallback']['n'])}"]
    for p in sorted(perfs, key=lambda p: (p["gap"] / p["graded"]) if p.get("gap") is not None and p["graded"] else 0):
        lines.append(f"  {p['code']}: {str(p.get('status')).upper()}, legs {_pct(p['won'], p['graded'])}"
                     + (f", {p['gap']:+.2f} vs model" if p.get("gap") is not None else ""))
    lines.append(f"Underperformed most: {s['underperformer']}" if s.get("underperformer")
                 else "No ticket fell short of what the model expected.")
    return "\n".join(lines)


def check(watch: Dict[str, Any], by_code: Dict[str, Dict], today: date,
          ranked: Optional[Dict[str, Dict]], min_hit: float,
          sport_of: Callable[[Dict], str]) -> List[str]:
    """Messages for tickets finished since the last check, and the final
    comparison once all are done; updates `watch` in place."""
    if not watch or watch.get("done_at"):
        return []
    messages, perfs, all_done = [], [], True
    for w in watch.get("tickets") or []:
        t = by_code.get(w["code"])
        if t is None:   # removed since: leave it out
            continue
        p = performance(t, ranked, min_hit, sport_of)
        perfs.append(p)
        if finished(t, today):
            if w["code"] not in watch["reported"]:
                watch["reported"].append(w["code"])
                messages.append(ticket_text(p))
        else:
            all_done = False
    if all_done and perfs:
        watch["summary"] = summary(perfs)
        watch["done_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        messages.append(summary_text(watch["summary"], perfs))
    return messages
