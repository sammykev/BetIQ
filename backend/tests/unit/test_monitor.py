"""The site monitor (monitor.py): which checks fail, and when an issue opens or closes."""

from datetime import datetime, timezone

import monitor

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)


def _site(preds=30, updated="2026-10-12T06:00:00Z", paused=(), health=200, bb=5, tennis_code=200):
    def get(url):
        path = url.split("//", 1)[1].split("/", 1)[1] if "/" in url.split("//", 1)[1] else ""
        if url.startswith(monitor.APP):
            return 200, None, 0.2
        if path == "api/health":
            return health, {"status": "ok", "last_updated": updated, "model_ready": True,
                            "leagues_paused": list(paused)}, 0.1
        if path.startswith("api/predictions"):
            return 200, {"predictions": [{"date": "2026-10-13", "league": "PL" if i % 2 else "SA"}
                                         for i in range(preds)]}, 0.1
        if "basketball" in path:
            return 200, {"today": "2026-10-12", "days": [{"date": "2026-10-11", "total": 9},
                                                         {"date": "2026-10-12", "total": bb}]}, 0.1
        if "table-tennis" in path:
            return 200, {"today": "2026-10-12", "days": [{"date": "2026-10-12", "total": 40}]}, 0.1
        if tennis_code == 404:
            return 404, {"detail": "feature_off"}, 0.1
        return tennis_code, {"today": "2026-10-12", "days": [{"date": "2026-10-13", "total": 12}]}, 0.1
    return get


def _failed(get, state=None):
    res, facts = monitor.checks(get, state or {}, NOW)
    return {r["name"] for r in res if not r["ok"]}, facts


def test_all_well():
    failed, facts = _failed(_site())
    assert failed == set() and facts["football"] == 30 and facts["leagues"] == {"PL": 15, "SA": 15}


def test_each_problem_is_named():
    assert _failed(_site(updated="2026-10-11T20:00:00Z"))[0] == {"fresh"}
    assert _failed(_site(paused=["DED"]))[0] == {"paused"}
    assert _failed(_site(preds=0))[0] == {"football"}
    assert _failed(_site(bb=0))[0] == {"basketball"}               # yesterday's games don't count
    assert _failed(_site(health=502))[0] == {"api"}
    assert _failed(_site(tennis_code=403))[0] == set()              # tennis kept for testers: skipped
    assert _failed(_site(tennis_code=404))[0] == set()              # switched off: skipped


def test_a_collapse_in_football_predictions():
    state = {"totals": [[NOW.timestamp() - 3600, 100]]}
    assert _failed(_site(preds=30), state)[0] == {"football"}
    assert _failed(_site(preds=60), state)[0] == set()
    old = {"totals": [[NOW.timestamp() - 7 * 3600, 100]]}          # past the baseline window
    assert _failed(_site(preds=30), old)[0] == set()


class FakeIssues:
    def __init__(self, open_issue=None):
        self.issue, self.calls = open_issue, []

    def open_one(self):
        return self.issue

    def open(self, body):
        self.calls.append("open")

    def comment(self, n, body):
        self.calls.append("comment")

    def close(self, n, body):
        self.calls.append("close")


def test_issue_opens_on_the_second_failing_run_and_closes_on_recovery():
    bad = [{"name": "fresh", "ok": False, "detail": ""}]
    good = [{"name": "fresh", "ok": True, "detail": ""}]
    gh = FakeIssues()
    assert monitor.alert(gh, bad, 1, "", []) == ["fresh"] and gh.calls == []
    monitor.alert(gh, bad, 2, "", ["fresh"])
    assert gh.calls == ["open"]
    gh = FakeIssues({"number": 7})
    monitor.alert(gh, bad, 3, "", ["fresh"])                         # nothing new: no comment
    monitor.alert(gh, bad + [{"name": "paused", "ok": False, "detail": ""}], 4, "", ["fresh"])
    monitor.alert(gh, good, 0, "", ["fresh", "paused"])
    assert gh.calls == ["comment", "close"]


def test_state_keeps_recent_totals_and_the_streak():
    st = monitor.next_state({"totals": [[NOW.timestamp() - 8 * 3600, 5]], "streak": 1},
                            [{"name": "x", "ok": False, "detail": ""}], {"football": 40}, NOW)
    assert st["streak"] == 2 and st["totals"] == [[NOW.timestamp(), 40]]
