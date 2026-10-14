"""
Daily odds (daily_slips.py): five slips a day from the day's 80%+ picks, none at 2.0 odds or more, made just
before midnight in Lagos, graded from the results, remade when cut, and counted in the record once each.
"""

import json
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import daily_slips
import main
import matchday
from tests.unit.test_matchday import finished, pred
from tests.unit.test_user_endpoints import FakeRedis


def result(target, legs, time="00:00"):
    picks = [{"home": h, "away": a, "date": d, "time": time, "league": "PL", "market": m, "market_name": m,
              "code": c, "label": c, "prob": 0.88, "odds": 1.2, "odds_source": "sportybet", "bookable": True}
             for h, a, d, m, c in legs]
    return {"picks": picks, "total_odds": target, "win_chance": 0.16, "within_target": True}


class TestSlips:
    def test_requests_ask_for_80_percent_picks_under_2_odds_near_the_target(self):
        body = daily_slips.request(10, daily_slips.ATTEMPTS[0])
        assert body["min_prob"] == 0.80 and body["max_leg_odds"] == 2.0
        assert body["min_odds"] < 10 < body["max_odds"]
        assert body["bookable_only"] is True and body["days"] == 1

    def test_basketball_too_but_not_overtime_or_props(self):
        body = daily_slips.request(10, daily_slips.ATTEMPTS[0])
        assert body["sport"] == "all" and body["no_props"] is True
        assert "bb_winner" in body["bb_markets"] and "bb_quarters" in body["bb_markets"]
        assert "bb_overtime" not in body["bb_markets"] and "bb_player" not in body["bb_markets"]

    def test_basketball_picks_keep_their_sportybet_ids(self):
        sb = {"eventId": "sr:match:9", "marketId": "225", "specifier": "total=160.5", "outcomeId": "12"}
        res = result(10.2, [("Real", "Barca", "2026-09-27", "bb_total", "O160.5")], time="18:00")
        res["picks"][0].update(sb=sb, sport="basketball")
        s = daily_slips.slip(10, res, daily_slips.ATTEMPTS[0])
        assert s["picks"][0]["sb"] == sb and s["picks"][0]["sport"] == "basketball"
        sel = daily_slips.selections(s, datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc))
        assert sel[0]["sb"] == sb
        s["booking"] = {"on_code": [{"home": "Real", "away": "Barca", "market": "bb_total", "code": "O160.5"}]}
        sels, _ = daily_slips.ticket_legs(s)
        assert sels[0]["sb"] == sb

    def test_five_slips_a_day(self):
        assert daily_slips.TARGETS == (5, 10, 20)

    def test_only_the_days_own_matches(self):
        assert all(a["days"] == 1 for a in daily_slips.ATTEMPTS)

    def test_slip_and_status(self):
        s = daily_slips.slip(10, result(10.2, [("A", "B", "2026-09-27", "1x2", "1")]), daily_slips.ATTEMPTS[0])
        assert (s["status"], s["win_chance"], s["picks"][0]["status"]) == ("pending", 0.16, "pending")
        none = daily_slips.slip(20, {"error": "Not enough matches"}, daily_slips.ATTEMPTS[-1])
        assert none["status"] == "none" and none["error"] == "Not enough matches"
        assert daily_slips.status([{"status": "won"}, {"status": "lost"}]) == "lost"
        assert daily_slips.status([{"status": "won"}, {"status": "void"}]) == "won"
        assert daily_slips.status([{"status": "won"}, {"status": "pending"}]) == "pending"


# Mid-morning: the Lagos day and the UTC date agree
NOW = datetime.combine(date.today(), datetime.min.time(), timezone.utc) + timedelta(hours=10)


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(daily_slips, "now_utc", lambda: NOW)
    fake = FakeRedis()
    monkeypatch.setattr(main, "_get_redis", lambda: fake)
    monkeypatch.setattr(main, "_paywall_enabled", lambda: False)
    monkeypatch.setattr(main, "_features_cache", [0.0, None])
    monkeypatch.setattr(main, "_daily_memory", {})
    return fake


@pytest.fixture
def sportybet(monkeypatch):
    """SportyBet booking: a code per call, unless told to fail."""
    import booking_slip
    calls = []

    async def to_sportybet(sels, *a, **kw):
        if kw.get("platform") == "football_com":      # the same slip's football.com code
            return {"code": f"FC{len(calls)}", "share_url": f"https://fc/{len(calls)}", "total_odds": 10.1,
                    "picks": [{"status": "booked", "odds": 1.2} for _ in sels], "error": None}
        calls.append(sels)
        if calls and getattr(to_sportybet, "fail", False):
            return {"code": None, "error": "SportyBet didn't return a booking code.", "picks": []}
        return {"code": f"CODE{len(calls)}", "share_url": f"https://sb/{len(calls)}", "total_odds": 10.1,
                "picks": [{"status": "booked", "odds": 1.2} for _ in sels], "error": None}
    to_sportybet.fail = False
    monkeypatch.setattr(booking_slip, "to_sportybet", to_sportybet)
    return to_sportybet, calls


def test_built_once_booked_graded_and_recorded(api, monkeypatch, sportybet):
    import asyncio
    today = date.today().isoformat()
    monkeypatch.setattr(main, "_predictions_cache", [pred(d=today)])
    calls = []

    async def optimize(body, window=None):
        calls.append(body)
        if body["target_odds"] >= 20:
            return {"error": "Not enough 80% picks for 20x"}
        return result(body["target_odds"], [("Arsenal", "Chelsea", today, "goals_ou", "O15")], time="23:59")
    monkeypatch.setattr(main, "_optimize_request", optimize)

    c = TestClient(main.app)
    # Visitors never make the slips: before the tick makes them there are none
    assert c.get("/api/daily-slips").json()["slips"] == [] and calls == []

    asyncio.run(main._build_daily(today))
    got = c.get("/api/daily-slips").json()
    five, ten, twenty = got["slips"]
    assert [s["target"] for s in got["slips"]] == [5, 10, 20]
    assert (five["target"], five["status"], five["win_chance"]) == (5, "pending", 0.16)
    assert twenty["status"] == "none" and len([b for b in calls if b["target_odds"] == 20]) == len(daily_slips.ATTEMPTS)
    # Each asks for at least break-even: 1 in its odds
    assert {b["target_odds"]: b["min_win_chance"] for b in calls} == {5: 0.2, 10: 0.1, 20: 0.05}
    # Booked by the server, one code per slip, the same for everyone
    assert five["booking"]["code"] == "CODE1" and ten["booking"]["code"] == "CODE2"
    assert five["booking"]["football_com"]["code"] == "FC1"
    assert (five["booking"]["booked"], five["booking"]["of"]) == (1, 1) and "on_code" not in five["booking"]
    assert "booking" not in twenty and len(sportybet[1]) == 2
    # Kept: asking again (or the tick running again) doesn't rebuild or rebook
    n = len(calls)
    c.get("/api/daily-slips")
    asyncio.run(main._daily_tick())
    assert len(calls) == n and len(sportybet[1]) == 2

    # The match finishes 2-1: over 1.5 comes in, both slips win, counted once
    day = {}
    matchday.merge_predictions(day, [pred(d=today, t="00:00")], datetime.now(timezone.utc) - timedelta(days=1))
    matchday.apply_result(day["arsenal|chelsea"], finished(2, 1))
    main._md_save(api, today, day)
    got = c.get("/api/daily-slips").json()
    assert [s["status"] for s in got["slips"]] == ["won", "won", "none"]
    assert got["slips"][0]["picks"][0]["live"]["score"] == [2, 1]
    c.get("/api/daily-slips")
    assert c.get("/api/daily-slips").json()["record"] == {"5": {"won": 1, "lost": 0}, "10": {"won": 1, "lost": 0}}
    saved = json.loads(api.kv[f"betiq:daily:{today}"])
    assert "live" not in saved["slips"][0]["picks"][0]          # live scores are never stored


def test_a_slip_sportybet_refused_is_tried_again_later(api, monkeypatch, sportybet):
    import asyncio
    book, calls = sportybet
    book.fail = True
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    monkeypatch.setattr(main, "_predictions_cache", [pred(d=tomorrow)])

    async def optimize(body, window=None):
        return result(body["target_odds"], [("Arsenal", "Chelsea", tomorrow, "goals_ou", "O15")])
    monkeypatch.setattr(main, "_optimize_request", optimize)
    today = date.today().isoformat()
    asyncio.run(main._build_daily(today))
    slip = TestClient(main.app).get("/api/daily-slips").json()["slips"][0]
    assert slip["booking"]["code"] is None and "didn't return" in slip["booking"]["error"]
    # Too soon: not tried again
    asyncio.run(main._daily_tick())
    assert len(calls) == len(daily_slips.TARGETS)   # one try per slip
    # After RETRY_MINUTES the tick books it
    book.fail = False
    doc = json.loads(api.kv[f"betiq:daily:{today}"])
    old = (daily_slips.now_utc() - timedelta(minutes=daily_slips.RETRY_MINUTES + 1)).isoformat(timespec="seconds")
    for s in doc["slips"]:
        s["booking"]["at"] = old
    api.kv[f"betiq:daily:{today}"] = json.dumps(doc)
    main._daily_memory.clear()
    asyncio.run(main._daily_tick())
    slips = TestClient(main.app).get("/api/daily-slips").json()["slips"]
    assert all(s["booking"]["code"] for s in slips) and len(calls) == 2 * len(daily_slips.TARGETS)


class TestLagosDay:
    def at(self, hhmm, d="2026-09-29"):
        return datetime.fromisoformat(f"{d}T{hhmm}:00+00:00")

    def test_the_day_turns_at_midnight_in_lagos(self):
        assert daily_slips.today(self.at("22:59")) == "2026-09-29"
        assert daily_slips.today(self.at("23:00")) == "2026-09-30"    # 00:00 in Lagos

    def test_a_days_window_is_its_lagos_midnight_to_midnight(self):
        assert daily_slips.window("2026-09-30") == (self.at("23:00"), self.at("23:00", "2026-09-30"))

    def test_tomorrows_slips_are_made_ten_minutes_before_midnight(self):
        assert daily_slips.days_to_run(self.at("22:49")) == ["2026-09-29"]
        assert daily_slips.days_to_run(self.at("22:50")) == ["2026-09-29", "2026-09-30"]
        assert daily_slips.days_to_run(self.at("23:00")) == ["2026-09-30"]


def test_the_tick_makes_tomorrows_slips_by_midnight(api, monkeypatch):
    import asyncio
    made = []

    async def build(day, force=False):
        made.append(day)
    monkeypatch.setattr(main, "_build_daily", build)
    today = NOW.date()
    monkeypatch.setattr(daily_slips, "now_utc", lambda: NOW.replace(hour=22, minute=49))
    asyncio.run(main._daily_tick())
    assert made == [today.isoformat()]                     # today's, if missing: not tomorrow's yet
    monkeypatch.setattr(daily_slips, "now_utc", lambda: NOW.replace(hour=22, minute=51))
    asyncio.run(main._daily_tick())
    assert made[1:] == [today.isoformat(), (today + timedelta(days=1)).isoformat()]


def test_the_optimizer_takes_only_the_windows_kickoffs(monkeypatch):
    import asyncio
    day = "2030-06-15"
    times = {"A": ("2030-06-14", "22:30"),   # 23:30 Lagos the day before: out
             "B": ("2030-06-14", "23:30"),   # 00:30 Lagos: in
             "C": ("2030-06-15", "18:00"),   # in
             "D": ("2030-06-15", "23:15")}   # 00:15 Lagos the next day: out
    monkeypatch.setattr(main, "_predictions_cache", [
        {**pred(home=h, away=h + "2", d=d, t=t), "sport": "football"} for h, (d, t) in times.items()])
    monkeypatch.setattr(main, "_get_redis", lambda: None)
    seen = []
    monkeypatch.setattr(main, "_linked_event", lambda p: seen.append(p["home"]) or None)
    asyncio.run(main._optimize_request({"min_odds": 2, "max_odds": 3, "bookable_only": True},
                                       window=daily_slips.window(day)))
    assert sorted(seen) == ["B", "C"]


def test_booking_leaves_out_started_and_settled_picks():
    now = datetime(2026, 9, 27, 15, 0, tzinfo=timezone.utc)
    s = {"status": "pending", "picks": [
        {"home": "A", "away": "B", "date": "2026-09-27", "time": "14:00", "market": "1x2", "code": "1", "status": "pending"},
        {"home": "C", "away": "D", "date": "2026-09-27", "time": "18:00", "market": "1x2", "code": "2", "status": "pending"},
        {"home": "E", "away": "F", "date": "2026-09-28", "time": "12:00", "market": "btts", "code": "BTTS-Y", "status": "won"}]}
    assert [x["home"] for x in daily_slips.selections(s, now)] == ["C"]
    assert daily_slips.needs_booking(s, now)
    s["booking"] = {"code": "X1"}
    assert not daily_slips.needs_booking(s, now)


def test_an_account_can_track_the_slip(api, monkeypatch, sportybet):
    import asyncio
    import auth
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    monkeypatch.setattr(main, "_predictions_cache", [pred(d=tomorrow)])

    async def optimize(body, window=None):
        return result(body["target_odds"], [("Arsenal", "Chelsea", tomorrow, "goals_ou", "O15")])
    monkeypatch.setattr(main, "_optimize_request", optimize)
    monkeypatch.setattr(auth, "auth_enforced", lambda: False)
    today = date.today().isoformat()
    asyncio.run(main._build_daily(today))
    c = TestClient(main.app)
    r = c.post("/api/daily-slips/track", json={"date": today, "target": 5, "uid": "user_abc"}).json()
    assert r == {"tracked": True, "code": "CODE1"}
    [ticket] = json.loads(api.kv[main._ukey("user_abc", "tickets")])
    assert ticket["code"] == "CODE1" and ticket["source"] == "daily" and ticket["legs"][0]["odds"] == 1.2
    assert c.post("/api/daily-slips/track", json={"date": "2026-01-01", "target": 10, "uid": "user_abc"}).status_code == 404


def test_past_days_and_premium_only(api, monkeypatch):
    import auth
    c = TestClient(main.app)
    assert c.get("/api/daily-slips?date=2026-01-01").json()["slips"] == []
    assert c.get("/api/daily-slips?date=nope").status_code == 400
    monkeypatch.setattr(main, "_paywall_enabled", lambda: True)
    monkeypatch.setattr(auth, "premium_enforced", lambda: True)

    async def identity(request):
        return None, "u1"

    async def lite(uid):
        return "lite"
    monkeypatch.setattr(main, "_admin_identity", identity)
    monkeypatch.setattr(auth, "user_tier", lite)
    r = c.get("/api/daily-slips?date=2026-01-01")
    assert (r.status_code, r.json()["detail"]) == (402, "premium_required")


class TestCut:
    """A slip whose pick loses is cut: a new one for the same target is made
    from the day's matches not kicked off yet, and booked."""

    @pytest.fixture
    def day(self, api, monkeypatch, sportybet):
        import asyncio
        today = NOW.date().isoformat()
        monkeypatch.setattr(main, "_predictions_cache", [pred(d=today)])
        self.next = [("Arsenal", "Chelsea", today, "goals_ou", "O15")]
        self.windows = []

        async def optimize(body, window=None):
            self.windows.append(window)
            if body["target_odds"] != 5:
                return {"error": "Not enough picks"}
            if not self.next:
                return {"error": "No matches left today"}
            return result(5, self.next, time="23:59")
        monkeypatch.setattr(main, "_optimize_request", optimize)
        asyncio.run(main._build_daily(today))
        self.today, self.api = today, api
        return today

    def cut(self):
        """Arsenal v Chelsea ends 0-0: over 1.5 loses, the 5x slip is cut."""
        d = {}
        matchday.merge_predictions(d, [pred(d=self.today, t="00:00")], NOW - timedelta(days=1))
        matchday.apply_result(d["arsenal|chelsea"], finished(0, 0))
        main._md_save(self.api, self.today, d)

    def test_a_cut_slip_is_remade_and_booked(self, day, sportybet, monkeypatch):
        import asyncio
        self.next = [("Spurs", "Leeds", day, "1x2", "1X")]
        self.cut()
        asyncio.run(main._daily_tick())                              # 11am Lagos: not yet
        assert TestClient(main.app).get("/api/daily-slips").json()["cut"] == []
        monkeypatch.setattr(daily_slips, "now_utc", lambda: daily_slips.second_at(day) + timedelta(minutes=1))
        asyncio.run(main._daily_tick())                              # 12pm: the second slip
        got = TestClient(main.app).get("/api/daily-slips").json()
        five = got["slips"][0]
        assert (five["status"], five["picks"][0]["home"], five["booking"]["code"]) == ("pending", "Spurs", "CODE2")
        assert five["replaces"] == {"code": "CODE1", "cut_by": "Arsenal v Chelsea", "total_odds": 5}
        [old] = got["cut"]
        assert (old["status"], old["booking"]["code"]) == ("lost", "CODE1")
        assert got["record"]["5"] == {"won": 0, "lost": 1}           # the cut slip counts
        assert self.windows[-1] == daily_slips.window(day)          # the same Lagos day
        # Settled once: another tick doesn't remake or rebook it
        asyncio.run(main._daily_tick())
        assert len(TestClient(main.app).get("/api/daily-slips").json()["cut"]) == 1 and len(sportybet[1]) == 2

    def test_no_matches_left_keeps_the_cut_slip(self, day, sportybet, monkeypatch):
        import asyncio
        self.next = []
        self.cut()
        monkeypatch.setattr(daily_slips, "now_utc", lambda: daily_slips.second_at(day) + timedelta(minutes=1))
        built = len(self.windows)
        asyncio.run(main._daily_tick())
        asyncio.run(main._daily_tick())
        got = TestClient(main.app).get("/api/daily-slips").json()
        assert got["cut"] == [] and got["slips"][0]["status"] == "lost" and got["slips"][0]["remade"] == "none"
        # Tried once at 12pm, not every tick: the cut 5x, and the 10x and 20x that had no slip at 12am
        assert len(self.windows) - built == 3 * len(daily_slips.ATTEMPTS)


def test_at_most_max_remakes_a_day():
    doc = {"date": "2026-09-29", "slips": [{"target": 10, "status": "lost", "picks": []}],
           "cut": [{"target": 10}] * daily_slips.MAX_REMAKES}
    assert not daily_slips.needs_remake(doc, doc["slips"][0])
    doc["cut"] = doc["cut"][:-1]
    assert daily_slips.needs_remake(doc, doc["slips"][0])
    assert not daily_slips.needs_remake(doc, {"target": 10, "status": "won", "picks": []})


def test_the_optimizer_leaves_out_thin_history_matches_and_lists_them(monkeypatch):
    import asyncio
    day = "2030-06-15"
    monkeypatch.setattr(main, "_predictions_cache", [
        {**pred(home="A", away="A2", d=day, t="18:00"), "sport": "football"},
        {**pred(home="B", away="B2", d=day, t="18:00"), "sport": "football", "thin_history": True}])
    monkeypatch.setattr(main, "_get_redis", lambda: None)
    seen = []
    monkeypatch.setattr(main, "_linked_event", lambda p: seen.append(p["home"]) or None)
    res = asyncio.run(main._optimize_request({"min_odds": 2, "max_odds": 3, "bookable_only": True},
                                             window=daily_slips.window(day)))
    assert seen == ["A"]
    assert [(m["home"], m["away"]) for m in res["thin_history"]] == [("B", "B2")]


def test_the_optimizer_leaves_out_unproven_leagues_too(monkeypatch):
    import asyncio
    day = "2030-06-15"
    monkeypatch.setattr(main, "_predictions_cache", [
        {**pred(home="A", away="A2", d=day, t="18:00"), "sport": "football"},
        {**pred(home="C", away="C2", d=day, t="18:00"), "sport": "football", "league_unproven": True}])
    monkeypatch.setattr(main, "_get_redis", lambda: None)
    seen = []
    monkeypatch.setattr(main, "_linked_event", lambda p: seen.append(p["home"]) or None)
    res = asyncio.run(main._optimize_request({"min_odds": 2, "max_odds": 3, "bookable_only": True},
                                             window=daily_slips.window(day)))
    assert seen == ["A"]
    assert [(m["home"], m.get("league_unproven")) for m in res["thin_history"]] == [("C", True)]


def test_a_slip_below_break_even_isnt_offered(monkeypatch):
    import asyncio
    today = date.today().isoformat()
    monkeypatch.setattr(main, "_predictions_cache", [{**pred(d=today), "sport": "football"}])
    monkeypatch.setattr(main, "_get_redis", lambda: None)

    async def solve(*a, **k):
        return {"picks": [], "total_odds": 10.0, "win_chance": 0.07, "within_target": True}
    monkeypatch.setattr(main, "_solve", solve)
    body = {"target_odds": 10, "min_odds": 9.3, "max_odds": 10.7, "min_prob": 0.5, "no_live_check": True}
    got = asyncio.run(main._optimize_request({**body, "min_win_chance": 0.1}))
    assert "at least 10% likely" in got["error"] and got["best_win_chance"] == 0.07
    assert "error" not in asyncio.run(main._optimize_request(body))          # no floor asked: offered


def test_a_slip_still_running_at_noon_gets_no_second():
    day = "2030-06-15"
    noon = daily_slips.second_at(day)
    running = {"target": 5, "status": "pending", "picks": [{"home": "A", "away": "B", "date": day, "status": "won"}]}
    doc = {"date": day, "slips": [running]}
    assert not daily_slips.needs_remake(doc, running, noon - timedelta(minutes=1))
    assert not daily_slips.needs_remake(doc, running, noon)
    assert daily_slips.second_decided(doc, noon) and running["second"] == "not needed"
    running["status"] = "lost"                                       # cut after 12pm: still no second
    assert not daily_slips.needs_remake(doc, running, noon + timedelta(hours=3))


def test_a_cut_slip_and_a_missing_one_get_their_second_at_noon():
    day = "2030-06-15"
    noon = daily_slips.second_at(day)
    assert noon.hour == 11 and noon.tzinfo is not None             # 12pm Lagos is 11:00 UTC
    cut = {"target": 5, "status": "lost", "picks": []}
    none = {"target": 20, "status": "none", "picks": []}
    doc = {"date": day, "slips": [cut, none]}
    assert daily_slips.needs_remake(doc, cut, noon) and daily_slips.needs_remake(doc, none, noon)
    new = {"target": 20, "status": "pending", "picks": [{"home": "C", "away": "D", "date": day}]}
    assert daily_slips.remake(doc, 1, new, "t") and doc["slips"][1]["second"] == "first try at 12pm"
    assert not daily_slips.needs_remake(doc, doc["slips"][1], noon)


def test_no_match_on_two_targets(monkeypatch, api, sportybet):
    import asyncio
    today = date.today().isoformat()
    monkeypatch.setattr(main, "_predictions_cache", [pred(d=today)])
    seen = []

    async def optimize(body, window=None):
        seen.append(sorted(map(tuple, body["exclude_matches"])))
        home = f"Home{int(body['target_odds'])}"
        return result(body["target_odds"], [(home, "Away", today, "goals_ou", "O15")], time="23:59")
    monkeypatch.setattr(main, "_optimize_request", optimize)
    asyncio.run(main._build_daily(today))
    assert seen == [[], [("Home5", "Away", today)], [("Home10", "Away", today), ("Home5", "Away", today)]]


def test_admin_sees_every_daily_ticket(api, monkeypatch):
    today = daily_slips.today()
    api.kv[main.DAILY_KEY.format(today)] = json.dumps({"date": today, "built_at": "x", "slips": [
        {"target": 5, "status": "pending", "picks": [], "replaces": {"code": "OLD"}, "booking": {"code": "NEW"}},
        {"target": 10, "status": "won", "picks": [], "booking": {"code": "TEN"}}],
        "cut": [{"target": 5, "status": "lost", "picks": [], "booking": {"code": "OLD"}}]})
    main.app.dependency_overrides[main.require_admin] = lambda: "admin"
    try:
        got = TestClient(main.app).get("/api/admin/daily-slips?days=3").json()
    finally:
        main.app.dependency_overrides.pop(main.require_admin, None)
    [day] = [d for d in got["days"] if d["date"] == today]
    assert [(s["target"], s["slot"], s.get("was_cut", False), s["booking"]["code"]) for s in day["slips"]] == [
        (5, "12am", True, "OLD"), (5, "12pm", False, "NEW"), (10, "12am", False, "TEN")]
