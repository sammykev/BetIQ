"""
Which SportyBet markets carry the names our label check looks for
(booking_slip.VERIFIED) — for diagnosing markets that never get confirmed.

1. The API server's last check, as saved in Redis: per market, confirmed or why not.
2. SportyBet itself: a few top-league match pages, and every market whose
   name matches PATTERN (default: shots), with how our check reads it.

    python probe_sportybet_markets.py [--pattern shot] [--pages 5]
"""

import argparse
import asyncio
import json
import re
import sys

import booking_slip
import model_store
import sportybet

LINK_STATUS_KEY = "betiq:sportybet:link_status"   # main.SB_LINK_STATUS_KEY
TOP = re.compile(r"premier league|laliga|la liga|serie a|bundesliga|ligue 1", re.I)


def saved_check(pattern: str) -> None:
    r = model_store._client()
    if r is None:
        print("UPSTASH_REDIS_URL isn't set — skipping the server's saved check.")
        return
    status = json.loads(r.get(LINK_STATUS_KEY) or "{}")
    print("Server's last check:", status.get("at"), "trigger", status.get("trigger"))
    for kind, v in (status.get("market_map") or {}).items():
        if re.search(pattern, kind) or re.search(pattern, booking_slip.VERIFIED.get(kind, {}).get("name", "")):
            print(f"  {kind}: {json.dumps(v)}")
    labels = status.get("market_labels") or {}
    print(f"  labels the server saw: {len(labels)}; matching /{pattern}/:",
          json.dumps({k: v for k, v in labels.items() if re.search(pattern, v, re.I)}))
    for line in (status.get("report") or [])[-8:]:
        print("  report:", line)
    print("  every label the server saw:")
    for mid, label in labels.items():
        print(f"    {mid}: {label}")


async def live(pattern: str, pages: int) -> None:
    session = sportybet.shared_session()
    events, report = await sportybet.fetch_catalog(session)
    print("SportyBet listing:", "; ".join(report))
    top = [e for e in events if TOP.search(e.get("_tournament") or "") and "women" not in (e.get("_tournament") or "").lower()]
    print(f"{len(events)} events, {len(top)} in the top leagues")
    found = {}
    for ev in top[:pages]:
        try:
            page = await sportybet.event_market_details(str(ev["eventId"]))
        except Exception as e:
            print(f"  {ev.get('homeTeamName')} v {ev.get('awayTeamName')}: {type(e).__name__}: {e}")
            continue
        hits = {mid: d for mid, d in page.items() if re.search(pattern, d["label"], re.I)}
        print(f"  {ev.get('_tournament')}: {ev.get('homeTeamName')} v {ev.get('awayTeamName')} — "
              f"{len(page)} markets, {len(hits)} matching")
        for mid, d in hits.items():
            found.setdefault(mid, d)
    print(f"Markets matching /{pattern}/ across those pages:")
    for mid, d in sorted(found.items(), key=lambda kv: kv[0]):
        kinds = [k for k in booking_slip.VERIFIED if booking_slip._label_matches(k, d["norm"])]
        print(f"  id {mid}: {d['label']!r} → read as {d['norm']!r}; outcomes {json.dumps(d['outcomes'])}; "
              f"our check matches: {kinds or 'nothing'}")
    print("Our check per market over these pages:")
    for kind, v in booking_slip.resolve_markets({"markets": found}).items():
        if kind in booking_slip.VERIFIED and re.search(pattern, booking_slip.VERIFIED[kind]["name"] + kind):
            print(f"  {kind}: {json.dumps(v)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pattern", default="shot")
    ap.add_argument("--pages", type=int, default=5)
    args = ap.parse_args()
    saved_check(args.pattern)
    try:
        asyncio.run(live(args.pattern, args.pages))
    except Exception as e:
        print(f"SportyBet from here: {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
