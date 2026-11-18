"""
SportyBet's weekly jackpot: 12 matches, home/draw/away on each; prizes for
12, 11 and 10 correct (its /jackpot/banner says which). Found by
probe_jackpot.py: the site's own API, GET with its OperId / Currency /
CountryCode headers.

For the current week:
- each match's chances: our model's (a prediction linked to the same
  SportyBet event) where we have one, else SportyBet's own odds without
  their margin;
- the single line (each match's likeliest outcome) and its chance;
- the best combination ticket for each number of lines in BUDGETS: every
  extra outcome goes where it raises the all-correct chance most per line
  it adds (a double doubles the lines, a treble triples them);
- for each ticket, the chance its best line gets 12, 11, 10... right.

Past weeks (/jackpot/previous) are kept with their results, so the record
shows how many the favourites (and our picks, once stored) got right.

    python jackpot.py [--history N]      (GitHub Actions: "Jackpot")
"""

import argparse
import asyncio
import json
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

KEY = "betiq:jackpot:v1"            # {"current": analysis, "history": {periodNumber: week}}
OUTCOMES = ("1", "X", "2")
BUDGETS = (1, 2, 4, 8, 16, 32, 64, 128, 256)


# ── chances ───────────────────────────────────────────────────────────────

def fair(odds: Sequence[float]) -> Optional[List[float]]:
    """1X2 prices → chances without the bookmaker's margin."""
    try:
        inv = [1 / float(o) for o in odds]
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    total = sum(inv)
    return [x / total for x in inv] if total > 0 and len(inv) == 3 else None


def element_odds(el: Dict) -> List[Optional[float]]:
    """SportyBet's prices on an element, home / draw / away (outcome ids 1, 2, 3)."""
    by = {str(o.get("id")): o.get("odds") for o in el.get("outcomes") or []}
    out = []
    for k in ("1", "2", "3"):
        try:
            out.append(float(by.get(k)))
        except (TypeError, ValueError):
            out.append(None)
    return out


def chances(el: Dict, ours: Optional[Dict] = None) -> Tuple[List[float], str]:
    """(home, draw, away) chances and where they came from."""
    if ours and all(isinstance(ours.get(k), (int, float)) for k in ("p_home", "p_draw", "p_away")):
        p = [float(ours["p_home"]), float(ours["p_draw"]), float(ours["p_away"])]
        s = sum(p)
        if s > 0:
            return [x / s for x in p], "model"
    f = fair(element_odds(el))
    return (f, "odds") if f else ([1 / 3] * 3, "none")


# ── tickets ───────────────────────────────────────────────────────────────

def single(probs: List[List[float]]) -> List[List[int]]:
    """Each match's likeliest outcome."""
    return [[max(range(3), key=lambda i: p[i])] for p in probs]


def covered(probs: List[List[float]], picks: List[List[int]]) -> List[float]:
    return [sum(p[i] for i in c) for p, c in zip(probs, picks)]


def lines(picks: List[List[int]]) -> int:
    return math.prod(len(c) for c in picks)


def best_ticket(probs: List[List[float]], budget: int) -> List[List[int]]:
    """Greedy: from the single line, add the outcome that raises the
    all-correct chance most per line added (log gain over log cost), while
    the ticket stays within `budget` lines."""
    picks = single(probs)
    while True:
        best, best_score = None, 0.0
        n = lines(picks)
        for m, (p, c) in enumerate(zip(probs, picks)):
            if len(c) == 3:
                continue
            nxt = max((i for i in range(3) if i not in c), key=lambda i: p[i])
            k = len(c)
            if n // k * (k + 1) > budget:
                continue
            gain = math.log((sum(p[i] for i in c) + p[nxt]) / max(sum(p[i] for i in c), 1e-12))
            score = gain / math.log((k + 1) / k)
            if score > best_score:
                best, best_score = (m, nxt), score
        if best is None:
            return picks
        picks[best[0]] = sorted(picks[best[0]] + [best[1]])


def correct_counts(q: List[float]) -> List[float]:
    """Chance the best line gets exactly k right, k = 0..n (each match
    covered with chance q_i, independently: a Poisson-binomial)."""
    dist = [1.0]
    for x in q:
        nxt = [0.0] * (len(dist) + 1)
        for k, v in enumerate(dist):
            nxt[k] += v * (1 - x)
            nxt[k + 1] += v * x
        dist = nxt
    return dist


def at_least(dist: List[float], k: int) -> float:
    return sum(dist[k:])


def analyse(period: Dict, ours: Optional[Dict[str, Dict]] = None, prizes: Sequence[int] = (12, 11, 10)) -> Dict:
    """The week's matches with their chances, the single line and the best
    ticket per budget, each with its chance of every prize."""
    ours = ours or {}
    els = sorted(period.get("elements") or [], key=lambda e: e.get("index") or 0)
    matches, probs = [], []
    for el in els:
        p, src = chances(el, ours.get(str(el.get("eventId"))))
        probs.append(p)
        matches.append({"index": el.get("index"), "home": el.get("home"), "away": el.get("away"),
                        "eventId": el.get("eventId"), "kickoff": el.get("date"),
                        "odds": element_odds(el), "p": [round(x, 4) for x in p], "source": src,
                        "pick": OUTCOMES[max(range(3), key=lambda i: p[i])]})
    tickets = []
    seen = set()
    for b in BUDGETS:
        picks = best_ticket(probs, b)
        n = lines(picks)
        if n in seen:
            continue
        seen.add(n)
        dist = correct_counts(covered(probs, picks))
        tickets.append({"budget": b, "lines": n, "picks": ["".join(OUTCOMES[i] for i in c) for c in picks],
                        "prizes": {str(k): round(at_least(dist, k), 8) for k in prizes},
                        "one_in": round(1 / dist[-1]) if dist[-1] > 0 else None})
    return {"periodNumber": period.get("periodNumber"), "periodId": period.get("id"),
            "deadline": period.get("deadline"), "maxWinnings": period.get("maxWinnings"),
            "currency": period.get("currency"), "games": len(els), "matches": matches, "tickets": tickets,
            "model_matches": sum(1 for m in matches if m["source"] == "model")}


def pool_of(week: Dict) -> Dict[str, Any]:
    """A settled week's money: sales, the top prize then, and per prize tier
    (correct results) its winners and each one's payout. Tiers 2 and 3 are
    shares of sales split among their winners (Nov 2026: 12% and 3%)."""
    tiers = {}
    for w in week.get("winnings") or []:
        try:
            tiers[str(int(w["correctEvents"]))] = {"winners": int(w.get("winNum") or 0),
                                                  "each": float(w.get("perWinnings") or 0)}
        except (KeyError, TypeError, ValueError):
            continue
    try:
        sales = float(week.get("sales") or 0) or None
    except (TypeError, ValueError):
        sales = None
    return {"sales": sales, "top_prize": week.get("maxWinnings"), "tiers": tiers}


def ticket_value(ticket: Dict, price: float, top_prize: float, past: List[Dict]) -> Optional[Dict[str, Any]]:
    """What a ticket's stake buys, without pretending to know how many others
    share a prize (prizes 2 and 3 are shares of sales split among however
    many hit them, and favourites-heavy results have many winners):
    - stake: lines × price;
    - lower_back: what prizes 2 and 3 paid out per 1 staked across all
      players in recent weeks (their share of sales);
    - top_ev_unshared: the top prize × this ticket's chance of all correct;
    - breakeven_sharers: how many others could also hit all correct before
      the top prize alone, on average, stops paying back the stake."""
    if not price:
        return None
    stake = ticket["lines"] * price
    keys = sorted(ticket.get("prizes") or {}, key=int, reverse=True)
    lower = set(keys[1:])
    # Prizes 2 and 3 only: the top prize, when someone hits it, isn't a share of sales
    shares = [sum(t["winners"] * t["each"] for k, t in (w.get("tiers") or {}).items() if k in lower) / w["sales"]
              for w in past if w.get("sales")]
    top_ev = (ticket["prizes"][keys[0]] if keys else 0) * top_prize
    return {"stake": stake, "lower_back": round(sum(shares) / len(shares), 3) if shares else None,
            "top_ev_unshared": round(top_ev, 2), "breakeven_sharers": max(0, int(top_ev / stake) - 1) if stake else None}


def weeks_without_top(history: Dict[str, Dict], top: str = "12") -> Optional[int]:
    """Settled weeks in a row, newest first, where nobody hit every match."""
    n = 0
    for _, h in sorted(history.items(), reverse=True):
        if "tiers" not in h:
            continue
        if (h["tiers"].get(top) or {}).get("winners"):
            return n
        n += 1
    return n if n else None


def score_week(elements: List[Dict], picks: Dict[int, str]) -> Optional[int]:
    """How many of `picks` ({index: "1"/"X"/"2"}) came in, once every match has a result."""
    res = {}
    for el in elements:
        r = str(el.get("result") or "")
        mapped = {"1": "1", "2": "X", "3": "2", "home": "1", "draw": "X", "away": "2"}.get(r.lower())
        if mapped is None:
            return None
        res[el.get("index")] = mapped
    return sum(1 for i, p in picks.items() if res.get(i) == p)


# ── SportyBet ─────────────────────────────────────────────────────────────

def _headers() -> Dict[str, str]:
    import sportybet
    return {"Content-Type": "application/json;charset=UTF-8", "OperId": "1",
            "Currency": "NGN" if sportybet.COUNTRY == "ng" else "", "CountryCode": sportybet.COUNTRY}


async def _get(session, path: str, params: Optional[Dict] = None) -> Any:
    import sportybet
    r = await session.get(sportybet.BASE + path, headers=_headers(), params=params or {})
    body = r.json() if r.text else {}
    if r.status_code != 200 or body.get("bizCode") != sportybet.OK:
        raise RuntimeError(f"{path}: HTTP {r.status_code} {str(body)[:200]}")
    return body.get("data")


async def fetch_line_price(session) -> Optional[float]:
    """The stake per line: the jackpot page's window.jackpotCfg.min (its
    bundle multiplies the line count by it)."""
    import re
    import sportybet
    try:
        html = (await session.get(sportybet.SITE + "jackpot", headers={"Accept": "text/html"})).text or ""
    except Exception:
        return None
    m = re.search(r"jackpotCfg\s*=\s*(\{.*?\})\s*[;<]", html, re.S)
    if m:
        try:
            return float(json.loads(m.group(1)).get("min"))
        except (ValueError, TypeError, AttributeError):
            pass
    m = re.search(r"jackpotCfg[^<]{0,400}?[\"']?min[\"']?\s*:\s*[\"']?([0-9.]+)", html, re.S)
    if m:
        return float(m.group(1))
    i = html.find("jackpotCfg")
    print(f"  line price not found{': page says …' + html[max(0, i - 100):i + 400] + '…' if i >= 0 else ''}")
    return None


def add_returns(a: Dict, price: Optional[float], history: Dict[str, Dict], weeks: int = 8) -> None:
    """Each ticket's rough return at `price` a line, from the last `weeks`
    settled weeks' payouts and this week's top prize."""
    past = [h for _, h in sorted(history.items(), reverse=True) if h.get("tiers")][:weeks]
    a["line_price"] = price
    a["weeks_without_top"] = weeks_without_top(history)
    for t in a["tickets"]:
        t["value"] = ticket_value(t, price or 0, float(a.get("maxWinnings") or 0), past)


async def fetch_current(session) -> Tuple[Dict, Dict]:
    return await _get(session, "/jackpot/period"), await _get(session, "/jackpot/banner")


async def fetch_previous(session, period: Dict) -> Optional[Dict]:
    """A past week with its results (the page sends periodNumber and periodId)."""
    import sportybet
    params = {"periodNumber": period.get("periodNumber"), "periodId": period.get("id")}
    try:
        return await _get(session, "/jackpot/previous", params)
    except RuntimeError as first:
        r = await session.post(sportybet.BASE + "/jackpot/previous", headers=_headers(), json=params)
        body = r.json() if r.text else {}
        if r.status_code == 200 and body.get("bizCode") == sportybet.OK:
            return body.get("data")
        print(f"  previous {params['periodNumber']}: GET {first}; POST HTTP {r.status_code} {str(body)[:200]}")
        return None


def our_predictions(r) -> Dict[str, Dict]:
    """Our football predictions by the SportyBet event they're linked to."""
    if r is None:
        return {}
    try:
        preds = json.loads(r.get("betiq:predictions") or "{}").get("predictions", [])
        links = json.loads(r.get("betiq:sportybet:links") or "{}")
    except Exception:
        return {}
    by_key = {f"{p.get('home')}|{p.get('away')}|{p.get('date')}": p for p in preds}
    out = {}
    for k, ev in links.items():
        if isinstance(ev, dict) and ev.get("eventId") and k in by_key:
            out[str(ev["eventId"])] = by_key[k]
    return out


def print_analysis(a: Dict, banner: Dict) -> None:
    dl = datetime.fromtimestamp((a.get("deadline") or 0) / 1000, tz=timezone.utc).strftime("%a %d %b %H:%M UTC")
    print(f"Jackpot {a['periodNumber']}: {a['games']} matches, deadline {dl}, top prize "
          f"{a.get('currency')} {a.get('maxWinnings'):,} · prizes for "
          f"{banner.get('firstPrizeCorrect')}/{banner.get('secondPrizeCorrect')}/{banner.get('thirdPrizeCorrect')} correct · "
          f"our model on {a['model_matches']} of {a['games']}")
    print("\n  #  match                                    1      X      2    odds 1/X/2          from   pick")
    for m in a["matches"]:
        p = m["p"]
        print(f"  {m['index']:>2} {(m['home'] + ' v ' + m['away'])[:40]:<40} {p[0]:5.1%} {p[1]:5.1%} {p[2]:5.1%}   "
              f"{'/'.join(str(o) for o in m['odds']):<18} {m['source']:<6} {m['pick']}")
    print("\n  lines  picks (match 1 → 12)                          12 right        11+          10+")
    for t in a["tickets"]:
        pz = t["prizes"]
        print(f"  {t['lines']:>5}  {' '.join(t['picks']):<45} {pz.get('12', 0):.5%} (1 in {t['one_in']:,})  "
              f"{pz.get('11', 0):7.3%}  {pz.get('10', 0):7.2%}")


async def main_async(history: int) -> None:
    import model_store
    import sportybet
    session = sportybet.shared_session()
    period, banner = await fetch_current(session)
    r = model_store._client()
    prizes = [banner.get(k) for k in ("firstPrizeCorrect", "secondPrizeCorrect", "thirdPrizeCorrect") if banner.get(k)]
    a = analyse(period, our_predictions(r), prizes or (12, 11, 10))
    print_analysis(a, banner)
    store = {}
    if r is not None:
        try:
            store = json.loads(r.get(KEY) or "{}")
        except Exception:
            store = {}
    hist = store.get("history") or {}
    # This week's picks, scored once the results are in
    hist.setdefault(str(a["periodNumber"]), {})["ours"] = {str(m["index"]): m["pick"] for m in a["matches"]}
    if history:
        options = await _get(session, "/jackpot/periodOptions") or []
        print(f"\n== Past weeks ({min(history, len(options))} of {len(options)}): favourites vs results ==")
        tally = []
        for opt in options[:history]:
            week = await fetch_previous(session, opt)
            if not week:
                continue
            els = week.get("elements") or []
            fav = {}
            for el in els:
                f = fair(element_odds(el))
                if f:
                    fav[el.get("index")] = OUTCOMES[max(range(3), key=lambda i: f[i])]
            got = score_week(els, fav)
            res = "".join({"1": "1", "2": "X", "3": "2"}.get(str(el.get("result")), "?") for el in
                          sorted(els, key=lambda e: e.get("index") or 0))
            h = hist.setdefault(str(opt.get("periodNumber")), {})
            h.update({"results": res, "favourites_right": got, **pool_of(week)})
            if h.get("ours"):
                h["ours_right"] = score_week(els, {int(k): v for k, v in h["ours"].items()})
            if got is not None:
                tally.append(got)
            print(f"  {opt.get('periodNumber')}  results {res}  favourites right {got}"
                  + (f"  ours {h.get('ours_right')}" if h.get("ours_right") is not None else ""))
        if tally:
            print(f"\n  favourites: average {sum(tally) / len(tally):.2f} of 12 right over {len(tally)} weeks; "
                  f"best {max(tally)}; 10+ in {sum(1 for t in tally if t >= 10)} weeks")
        if not tally and options:
            print("  (no past week came back with results: the first one's raw answer follows)")
            print(json.dumps(await fetch_previous(session, options[0]), default=str)[:3000])
    price = await fetch_line_price(session)
    add_returns(a, price, hist)
    print(f"\n== At {a.get('currency')} {price} a line: prizes 2 and 3 paid back "
          f"{(a['tickets'][0].get('value') or {}).get('lower_back')} per 1 staked across all players lately; "
          f"{a.get('weeks_without_top')} weeks in a row without anyone hitting all {a['games']} ==")
    for t in a["tickets"]:
        v = t.get("value")
        if v:
            print(f"  {t['lines']:>4} lines: stake {v['stake']:,.0f}; the top prize alone pays back on average only if "
                  f"no more than {v['breakeven_sharers']:,} others also hit all {a['games']}")
    if r is not None:
        r.set(KEY, json.dumps({"current": a, "banner": banner, "history": hist,
                               "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}))
        print(f"\nSaved to {KEY}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--history", type=int, default=20, help="past weeks to fetch and score")
    asyncio.run(main_async(p.parse_args().history))


if __name__ == "__main__":
    main()
