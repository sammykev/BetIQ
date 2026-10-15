"""
Is the site up and its data fresh? Run every 15 minutes from GitHub Actions
(.github/workflows/monitor.yml), from outside the server, as a visitor:

  site         the web app (Vercel) and the API answer
  model        the football model is loaded and predicting
  fresh        the football predictions were updated in the last STALE_HOURS
  football     there are upcoming football predictions, and their number
               hasn't collapsed (below DROP_SHARE of the most in the last
               BASELINE_HOURS: the "cards stopped loading" failure)
  paused       no football-data.org league is paused (a 401/403)
  basketball, tennis, table tennis
               upcoming games listed (skipped when the sport is switched
               off or kept for testers)

A problem has to show in FAIL_RUNS runs in a row before a GitHub issue is
opened (GitHub emails the repository's owner); the issue is updated while it
lasts and closed when every check passes again. Counts per league go to the
run's summary. State between runs (the recent totals, the failure streak)
lives in the Actions cache, in STATE_FILE.

    python monitor.py            (reads SITE_URL, APP_URL; GITHUB_TOKEN and
                                  GITHUB_REPOSITORY to open / close issues)
"""

import json
import os
import time
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

import httpx

API = os.getenv("SITE_URL", "https://13-140-188-212.sslip.io").rstrip("/")
APP = os.getenv("APP_URL", "https://predict-withbetiq.vercel.app").rstrip("/")
STATE_FILE = os.getenv("MONITOR_STATE", "monitor-state.json")
STALE_HOURS = 13
BACKUP_STALE_HOURS = 36  # the backup runs nightly at 04:10 UTC
BASELINE_HOURS = 6
DROP_SHARE = 0.4
FAIL_RUNS = 2
LABEL = "monitor"
TITLE = "Site monitor: something is wrong"

Get = Callable[[str], Tuple[int, Optional[object], float]]


def http_get(client: httpx.Client) -> Get:
    def get(url: str):
        t0 = time.time()
        try:
            r = client.get(url)
        except Exception as e:
            return 0, f"{type(e).__name__}: {e}", time.time() - t0
        try:
            body = r.json()
        except ValueError:
            body = None
        return r.status_code, body, time.time() - t0
    return get


def _age_hours(stamp: Optional[str], now: datetime) -> Optional[float]:
    try:
        t = datetime.strptime(stamp or "", "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return (now - t).total_seconds() / 3600


def checks(get: Get, state: Dict, now: datetime) -> Tuple[List[Dict], Dict]:
    """Every check's result ({name, ok, detail}) and the facts to remember."""
    out: List[Dict] = []
    facts: Dict = {}

    def add(name: str, ok: bool, detail: str) -> None:
        out.append({"name": name, "ok": ok, "detail": detail})

    code, _, dt = get(APP + "/")
    add("site", code == 200, f"web app {code or 'unreachable'} in {dt:.1f}s")

    code, health, dt = get(API + "/api/health")
    if code != 200 or not isinstance(health, dict):
        add("api", False, f"API /api/health {code or 'unreachable'} in {dt:.1f}s: {str(health)[:200]}")
        return out, facts
    add("api", True, f"API up in {dt:.1f}s")
    add("model", bool(health.get("model_ready")), "football model loaded" if health.get("model_ready")
        else "football model not loaded")
    age = _age_hours(health.get("last_updated"), now)
    add("fresh", age is not None and age < STALE_HOURS,
        f"predictions updated {age:.1f}h ago" if age is not None else "predictions never updated")
    paused = health.get("leagues_paused") or []
    add("paused", not paused, "no league paused" if not paused else "paused leagues: " + ", ".join(paused))
    # The nightly redis backup (deploy/vm/backup-redis.sh); skipped until the server has made one
    backup = health.get("backup")
    if isinstance(backup, dict):
        b_age = _age_hours(backup.get("at"), now)
        offsite = backup.get("offsite") or "off"
        add("backup", b_age is not None and b_age < BACKUP_STALE_HOURS and offsite != "failed",
            (f"redis backed up {b_age:.0f}h ago" if b_age is not None else "redis backup time unknown")
            + {"ok": ", copied off-server", "failed": ", off-server copy FAILED"}.get(offsite, ", local only"))

    code, body, _ = get(API + "/api/predictions?limit=2000")
    preds = body.get("predictions") if isinstance(body, dict) else None
    if code != 200 or not isinstance(preds, list):
        add("football", False, f"/api/predictions {code or 'unreachable'}")
    else:
        today = now.date().isoformat()
        upcoming = [p for p in preds if (p.get("date") or "") >= today]
        leagues: Dict[str, int] = {}
        for p in upcoming:
            leagues[p.get("league") or "?"] = leagues.get(p.get("league") or "?", 0) + 1
        facts["football"] = len(upcoming)
        facts["leagues"] = leagues
        recent = [n for t, n in state.get("totals", []) if now.timestamp() - t < BASELINE_HOURS * 3600]
        top = max(recent, default=0)
        if not upcoming:
            add("football", False, "no upcoming football predictions")
        elif top and len(upcoming) < DROP_SHARE * top:
            add("football", False, f"{len(upcoming)} upcoming football predictions, down from {top} "
                                   f"in the last {BASELINE_HOURS}h")
        else:
            add("football", True, f"{len(upcoming)} upcoming football predictions in {len(leagues)} leagues")

    for name, path in (("basketball", "/api/basketball/matchday/strip"), ("tennis", "/api/tennis/matchday/strip"),
                       ("table tennis", "/api/table-tennis/matchday/strip")):
        code, body, _ = get(API + path)
        if code in (401, 402, 403) or (isinstance(body, dict) and body.get("detail") == "feature_off"):
            continue                                   # switched off, or kept for testers / a tier
        days = body.get("days") if isinstance(body, dict) else None
        if code != 200 or not isinstance(days, list):
            add(name, False, f"{path} {code or 'unreachable'}")
            continue
        today = (body.get("today") or now.date().isoformat())
        n = sum(int(d.get("total") or 0) for d in days if (d.get("date") or "") >= today)
        facts[name] = n
        add(name, n > 0, f"{n} upcoming games" if n else "no upcoming games listed")
    return out, facts


def next_state(state: Dict, results: List[Dict], facts: Dict, now: datetime) -> Dict:
    keep = now.timestamp() - BASELINE_HOURS * 3600
    totals = [[t, n] for t, n in state.get("totals", []) if t >= keep]
    if "football" in facts:
        totals.append([now.timestamp(), facts["football"]])
    failing = any(not r["ok"] for r in results)
    return {"totals": totals, "streak": state.get("streak", 0) + 1 if failing else 0,
            "at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}


def report(results: List[Dict], facts: Dict, now: datetime) -> str:
    lines = [f"Checked {now.strftime('%Y-%m-%d %H:%M')} UTC.", ""]
    lines += [f"- {'✅' if r['ok'] else '❌'} **{r['name']}**: {r['detail']}" for r in results]
    if facts.get("leagues"):
        lines += ["", "<details><summary>Upcoming football predictions per league</summary>", ""]
        lines += [f"- {lg}: {n}" for lg, n in sorted(facts["leagues"].items(), key=lambda kv: -kv[1])]
        lines += ["", "</details>"]
    return "\n".join(lines)


# ── GitHub issue ─────────────────────────────────────────────────────────

class Issues:
    def __init__(self, token: str, repo: str):
        self.c = httpx.Client(base_url=f"https://api.github.com/repos/{repo}", timeout=30, headers={
            "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})

    def open_one(self) -> Optional[Dict]:
        r = self.c.get("/issues", params={"state": "open", "labels": LABEL, "per_page": 5})
        r.raise_for_status()
        return next(iter(r.json()), None)

    def open(self, body: str) -> None:
        self.c.post("/labels", json={"name": LABEL, "color": "d73a4a"})     # 422 once it exists
        self.c.post("/issues", json={"title": TITLE, "body": body, "labels": [LABEL]}).raise_for_status()

    def comment(self, number: int, body: str) -> None:
        self.c.post(f"/issues/{number}/comments", json={"body": body}).raise_for_status()

    def close(self, number: int, body: str) -> None:
        self.comment(number, body)
        self.c.patch(f"/issues/{number}", json={"state": "closed", "state_reason": "completed"}).raise_for_status()


def alert(issues: Optional[Issues], results: List[Dict], streak: int, text: str, last_failed: List[str]) -> List[str]:
    """Open, update or close the issue. Returns the failing checks' names."""
    failed = sorted(r["name"] for r in results if not r["ok"])
    if issues is None:
        return failed
    existing = issues.open_one()
    if not failed:
        if existing:
            issues.close(existing["number"], "All checks pass again.\n\n" + text)
        return failed
    if streak < FAIL_RUNS:
        return failed
    if existing is None:
        issues.open(f"Failing for {streak} runs in a row (every 15 minutes).\n\n" + text)
    elif failed != last_failed:                       # only when what's failing changes, not every 15 minutes
        issues.comment(existing["number"], "What's failing changed.\n\n" + text)
    return failed


def main() -> None:
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    now = datetime.now(timezone.utc)
    with httpx.Client(timeout=45, follow_redirects=True, headers={"User-Agent": "betiq-monitor"}) as c:
        results, facts = checks(http_get(c), state, now)
    new = next_state(state, results, facts, now)
    text = report(results, facts, now)
    print(text)
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write("## Site monitor\n\n" + text + "\n")
    token, repo = os.getenv("GITHUB_TOKEN"), os.getenv("GITHUB_REPOSITORY")
    issues = Issues(token, repo) if token and repo else None
    new["failed"] = alert(issues, results, new["streak"], text, state.get("failed") or [])
    with open(STATE_FILE, "w") as f:
        json.dump(new, f)
    # The run itself stays green: the issue is the alert (a red scheduled run
    # would email every 15 minutes)


if __name__ == "__main__":
    main()
