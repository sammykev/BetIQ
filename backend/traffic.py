"""
Website traffic for the admin panel: page views the site reports (the
frontend's /api/t route, which adds Vercel's visitor location), counted in
memory and flushed to Redis every few minutes.

Privacy: no IP addresses are stored. A visitor is a random id the browser
keeps; location is city-level, as Vercel reports it.

Redis cost: Upstash bills per command, so a flush is one RPUSH of that
period's counts (JSON) onto the day's list plus one PFADD of its visitor
ids — a handful of commands however busy the site is. Reading a finished
day merges its entries into one (compaction).
"""

import json
import re
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

PREFIX = "betiq:traffic"
KEEP_DAYS = 90
LIVE_SECONDS = 300
FEED_SIZE = 40

# Counters kept per day; each maps a label to a count
DIMENSIONS = ("country", "country_sessions", "city", "path", "landing", "referrer", "utm",
              "device", "browser", "os", "lang", "plan", "hour")

_CLEAN = re.compile(r"[^\w\s./:+&|@'’-]", re.UNICODE)


def _text(value: Any, limit: int) -> str:
    return _CLEAN.sub("", str(value or ""))[:limit].strip()


def clean_hit(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A page view with only known, bounded fields — or None if unusable."""
    if not isinstance(raw, dict):
        return None
    path = str(raw.get("path") or "")
    if not path.startswith("/"):
        return None
    path = re.sub(r"\?.*$", "", path)[:80] or "/"
    visitor = re.sub(r"[^A-Za-z0-9-]", "", str(raw.get("visitor") or ""))[:40]
    if not visitor:
        return None
    country = str(raw.get("country") or "").upper()
    country = country if re.fullmatch(r"[A-Z]{2}", country) else "??"

    def coord(key, bound):
        try:
            v = float(raw.get(key))
            return round(v, 1) if -bound <= v <= bound else None
        except (TypeError, ValueError):
            return None

    device = raw.get("device") if raw.get("device") in ("mobile", "tablet", "desktop") else "desktop"
    plan = raw.get("plan") if raw.get("plan") in ("anon", "free", "premium") else "anon"
    utm = "/".join(_text(raw.get(k), 30) or "-" for k in ("utm_source", "utm_medium", "utm_campaign"))
    return {
        "path": path, "visitor": visitor, "country": country,
        "region": _text(raw.get("region"), 40), "city": _text(raw.get("city"), 50),
        "lat": coord("lat", 90), "lon": coord("lon", 180),
        "referrer": _text(raw.get("referrer"), 60).lower().removeprefix("www."),
        "utm": utm if utm != "-/-/-" else "",
        "device": device, "browser": _text(raw.get("browser"), 20) or "Other",
        "os": _text(raw.get("os"), 20) or "Other", "lang": _text(raw.get("lang"), 10) or "?",
        "plan": plan, "new_visitor": bool(raw.get("new_visitor")), "new_session": bool(raw.get("new_session")),
    }


class Traffic:
    """Counts since the last flush, plus who's on the site right now."""

    def __init__(self):
        self._lock = threading.Lock()
        self._pending: Dict[str, Dict[str, Any]] = {}   # day -> counts
        self._visitors: Dict[str, set] = defaultdict(set)
        self._live: Dict[str, Dict[str, Any]] = {}      # visitor -> last hit
        self._feed: List[Dict[str, Any]] = []
        self._expiring: set = set()                     # day keys given a TTL

    # ── recording ──────────────────────────────────────────────────────
    def record(self, hit: Dict[str, Any], now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        when = datetime.fromtimestamp(now, timezone.utc)
        day = when.date().isoformat()
        with self._lock:
            d = self._pending.setdefault(day, {"pv": 0, "sessions": 0, "new": 0, **{k: Counter() for k in DIMENSIONS}})
            d["pv"] += 1
            d["path"][hit["path"]] += 1
            d["country"][hit["country"]] += 1
            d["device"][hit["device"]] += 1
            d["browser"][hit["browser"]] += 1
            d["os"][hit["os"]] += 1
            d["lang"][hit["lang"]] += 1
            d["plan"][hit["plan"]] += 1
            d["hour"][str(when.hour)] += 1
            if hit["city"]:
                d["city"]["|".join(str(x if x is not None else "") for x in
                                   (hit["city"], hit["region"], hit["country"], hit["lat"], hit["lon"]))] += 1
            if hit["new_session"]:
                d["sessions"] += 1
                d["country_sessions"][hit["country"]] += 1
                d["landing"][hit["path"]] += 1
                if hit["referrer"]:
                    d["referrer"][hit["referrer"]] += 1
                if hit["utm"]:
                    d["utm"][hit["utm"]] += 1
            if hit["new_visitor"]:
                d["new"] += 1
            self._visitors[day].add(hit["visitor"])
            self._live[hit["visitor"]] = {"at": now, "country": hit["country"], "city": hit["city"],
                                          "lat": hit["lat"], "lon": hit["lon"], "path": hit["path"],
                                          "device": hit["device"]}
            self._feed.insert(0, {"at": int(now), "country": hit["country"], "city": hit["city"],
                                  "path": hit["path"], "device": hit["device"], "plan": hit["plan"],
                                  "referrer": hit["referrer"] if hit["new_session"] else ""})
            del self._feed[FEED_SIZE:]
            for v in [v for v, h in self._live.items() if h["at"] < now - LIVE_SECONDS]:
                del self._live[v]

    def live(self, now: Optional[float] = None) -> Dict[str, Any]:
        now = time.time() if now is None else now
        with self._lock:
            here = [h for h in self._live.values() if h["at"] >= now - LIVE_SECONDS]
            return {"online": len(here), "places": Counter(f"{h['city'] or '?'}, {h['country']}" for h in here).most_common(10),
                    "pins": [{"lat": h["lat"], "lon": h["lon"]} for h in here if h["lat"] is not None],
                    "recent": list(self._feed)}

    # ── Redis ──────────────────────────────────────────────────────────
    def flush(self, r) -> int:
        """Write the counts since the last flush. Returns the page views written."""
        with self._lock:
            pending, self._pending = self._pending, {}
            visitors, self._visitors = self._visitors, defaultdict(set)
        if not r or not pending:
            return 0
        written = 0
        for day, counts in pending.items():
            key = f"{PREFIX}:{day}"
            try:
                r.rpush(key, json.dumps({k: (dict(v) if isinstance(v, Counter) else v) for k, v in counts.items()}))
                if visitors.get(day):
                    r.pfadd(f"{key}:uv", *visitors[day])
                if day not in self._expiring:
                    r.expire(key, KEEP_DAYS * 86400)
                    r.expire(f"{key}:uv", KEEP_DAYS * 86400)
                    self._expiring.add(day)
                written += counts["pv"]
            except Exception as e:
                print(f"[Traffic] flush failed for {day}: {e}")
        return written


def _merge(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"pv": 0, "sessions": 0, "new": 0, **{k: Counter() for k in DIMENSIONS}}
    for e in entries:
        for k in ("pv", "sessions", "new"):
            out[k] += int(e.get(k) or 0)
        for k in DIMENSIONS:
            out[k].update(e.get(k) or {})
    return out


def _day(r, day: str, today: str) -> Dict[str, Any]:
    key = f"{PREFIX}:{day}"
    raw = r.lrange(key, 0, -1) or []
    entries = [json.loads(x) for x in raw]
    merged = _merge(entries)
    if day < today and len(entries) > 1:  # a finished day: keep one merged entry
        try:
            ttl = r.ttl(key)
            r.delete(key)
            r.rpush(key, json.dumps({k: (dict(v) if isinstance(v, Counter) else v) for k, v in merged.items()}))
            r.expire(key, ttl if isinstance(ttl, int) and ttl > 0 else KEEP_DAYS * 86400)
        except Exception:
            pass
    return merged


def summary(r, days: int = 30, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Totals, a daily series and top lists over the last `days` days, today
    included (flush first so nothing is left in memory)."""
    now = now or datetime.now(timezone.utc)
    today = now.date().isoformat()
    day_list = [(now.date() - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    per_day = {d: _day(r, d, today) for d in day_list} if r else {d: _merge([]) for d in day_list}

    def uniques(days_: List[str]) -> int:
        if not r:
            return 0
        try:
            return int(r.pfcount(*[f"{PREFIX}:{d}:uv" for d in days_]) or 0)
        except Exception:
            return 0

    total = _merge(list(per_day.values()))
    series = [{"date": d, "pageviews": c["pv"], "sessions": c["sessions"], "visitors": uniques([d])}
              for d, c in per_day.items()]
    visitors = uniques(day_list)

    def top(counter: Counter, n: int) -> List[Dict[str, Any]]:
        return [{"key": k, "count": v} for k, v in counter.most_common(n)]

    cities = []
    for label, count in total["city"].most_common(300):
        city, region, country, lat, lon = (label.split("|") + ["", "", "", "", ""])[:5]
        try:
            lat_f, lon_f = float(lat), float(lon)
        except ValueError:
            lat_f = lon_f = None
        cities.append({"city": city, "region": region, "country": country, "lat": lat_f, "lon": lon_f,
                       "pageviews": count})

    sessions = total["sessions"] or 0
    return {
        "range": {"from": day_list[0], "to": day_list[-1], "days": days},
        "totals": {"pageviews": total["pv"], "visitors": visitors, "sessions": sessions,
                   "new_visitors": total["new"],
                   "pages_per_session": round(total["pv"] / sessions, 2) if sessions else None},
        "series": series,
        "countries": [{"code": k, "pageviews": v, "sessions": total["country_sessions"].get(k, 0)}
                      for k, v in total["country"].most_common(60)],
        "cities": cities,
        "pages": top(total["path"], 20), "landing": top(total["landing"], 10),
        "referrers": top(total["referrer"], 15), "utm": top(total["utm"], 15),
        "devices": top(total["device"], 5), "browsers": top(total["browser"], 8), "os": top(total["os"], 8),
        "languages": top(total["lang"], 10), "plans": top(total["plan"], 3),
        "hours": [total["hour"].get(str(h), 0) for h in range(24)],
    }
