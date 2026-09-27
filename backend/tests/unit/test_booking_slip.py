"""
Bet slip → SportyBet booking code: market translation, per-pick outcomes,
the SportyBet client (event listing, matching, share call), and the endpoint.
SportyBet itself is replaced by a fake session that records requests.
"""

import asyncio
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import booking_slip
import main
import sportybet
from booking_slip import sportybet_ids, to_sportybet, validate


def sel(home="Arsenal FC", away="Chelsea FC", date="2026-09-26", market="1x2", code="1", **kw):
    return {"home": home, "away": away, "date": date, "market": market, "code": code, **kw}


def ms(iso):
    return int(datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp() * 1000)


@pytest.fixture(autouse=True)
def before_kickoff(monkeypatch):
    """The fixtures kick off on 2026-09-26: book them as if it were earlier."""
    class Clock:
        @staticmethod
        def time():
            return ms("2026-09-20T12:00:00") / 1000
    monkeypatch.setattr(booking_slip, "time", Clock)


EVENT = {
    "eventId": "sr:match:111", "homeTeamName": "Arsenal", "awayTeamName": "Chelsea",
    "estimateStartTime": ms("2026-09-26T16:30:00"),
    "markets": [
        {"id": "1", "specifier": "", "outcomes": [
            {"id": "1", "desc": "Home", "odds": "1.85"}, {"id": "2", "desc": "Draw", "odds": "3.60"},
            {"id": "3", "desc": "Away", "odds": "4.20"}]},
        {"id": "18", "specifier": "total=2.5", "outcomes": [
            {"id": "12", "odds": "1.72"}, {"id": "13", "odds": "2.05", "isActive": 0}]},
    ],
}


# ── Market translation ───────────────────────────────────────────────────────

class TestMarketIds:
    @pytest.mark.parametrize("market,code,expected", [
        ("1x2", "1", ("1", "", "1")), ("1x2", "X", ("1", "", "2")), ("1x2", "2", ("1", "", "3")),
        ("double_chance", "1X", ("10", "", "9")), ("double_chance", "X2", ("10", "", "11")),
        ("double_chance", "2X", ("10", "", "11")), ("double_chance", "12", ("10", "", "10")),
        ("btts", "BTTS-Y", ("29", "", "74")), ("btts", "BTTS-N", ("29", "", "76")),
        ("goals_ou", "O25", ("18", "total=2.5", "12")), ("goals_ou", "U35", ("18", "total=3.5", "13")),
        ("goals_ou", "O05", ("18", "total=0.5", "12")),
        ("draw_no_bet", "DNB-A", ("11", "", "5")), ("half_time", "HTX", ("60", "", "2")),
    ])
    def test_supported(self, market, code, expected):
        ids = sportybet_ids(market, code)
        assert (ids["marketId"], ids["specifier"], ids["outcomeId"]) == expected

    @pytest.mark.parametrize("market,code", [
        ("correct_score", "CS-2-1"), ("asian_handicap", "AH-H05"), ("goals_ou", "O2"),
        ("1x2", "Z"), ("result_btts", "RB-H-Y"),
    ])
    def test_unsupported(self, market, code):
        assert sportybet_ids(market, code) is None


class TestValidate:
    def test_one_pick_per_match_last_wins(self):
        out = validate([sel(code="1"), sel(code="X"), sel(home="Leeds", away="Hull")])
        assert [s["code"] for s in out] == ["X", "1"]

    @pytest.mark.parametrize("bad", [None, [], "x", [{"home": "A"}], [sel(date="26/09/2026")], [sel(), 5]])
    def test_rejects_malformed(self, bad):
        with pytest.raises(ValueError):
            validate(bad)

    def test_size_limit(self):
        with pytest.raises(ValueError):
            validate([sel(home=f"T{i}") for i in range(booking_slip.MAX_SELECTIONS + 1)])


# ── Converting a slip ────────────────────────────────────────────────────────

def run(selections, events=(EVENT,), share=None, share_error=None):
    fetched, posted = [], []

    async def fetch(date):
        fetched.append(date)
        return list(events)

    async def post(selections):
        posted.append(selections)
        if share_error:
            raise share_error
        return share if share is not None else {"code": "ABC123", "url": "https://sb/ABC123", "odds": {}, "unavailable": set()}

    result = asyncio.run(to_sportybet(selections, fetch, sportybet.find_event, post))
    return result, fetched, posted


class TestToSportybet:
    def test_books_matched_picks(self):
        result, fetched, posted = run([sel(code="1"), sel(home="Arsenal", away="Chelsea", market="goals_ou", code="O25")])
        # Both picks are the same match, so validate() would merge them; to_sportybet trusts its input
        assert result["code"] == "ABC123" and result["share_url"] == "https://sb/ABC123"
        assert posted == [[
            {"eventId": "sr:match:111", "marketId": "1", "specifier": "", "outcomeId": "1"},
            {"eventId": "sr:match:111", "marketId": "18", "specifier": "total=2.5", "outcomeId": "12"},
        ]]
        assert [p["status"] for p in result["picks"]] == ["booked", "booked"]
        assert [p["odds"] for p in result["picks"]] == [1.85, 1.72]  # from the listing
        assert result["total_odds"] == round(1.85 * 1.72, 2)
        assert fetched == ["2026-09-26"]  # one listing per date

    def test_prefers_the_price_sportybet_booked(self):
        share = {"code": "C1", "url": "u", "odds": {("sr:match:111", "1", "1"): 1.9}, "unavailable": set()}
        result, _, _ = run([sel()], share=share)
        assert result["picks"][0]["odds"] == 1.9 and result["total_odds"] == 1.9

    def test_reports_each_pick_that_cant_be_booked(self):
        result, _, posted = run([
            sel(),
            sel(home="Leeds United FC", away="Hull City FC"),
            sel(home="Arsenal", away="Chelsea", market="correct_score", code="CS-2-1"),
        ])
        assert [p["status"] for p in result["picks"]] == ["booked", "not_found", "unsupported"]
        assert len(posted[0]) == 1 and result["code"] == "ABC123"

    def test_picks_sportybet_rejects_are_marked(self):
        share = {"code": "C2", "url": "u", "odds": {},
                 "unavailable": {("sr:match:111", "18", "12")}}
        result, _, _ = run([sel(), sel(home="Arsenal", away="Chelsea", market="goals_ou", code="O25")], share=share)
        assert [p["status"] for p in result["picks"]] == ["booked", "unavailable"]
        assert result["code"] == "C2" and result["total_odds"] == 1.85

    def test_everything_rejected_means_no_code(self):
        share = {"code": "C3", "url": "u", "odds": {}, "unavailable": {("sr:match:111", "1", "1")}}
        result, _, _ = run([sel()], share=share)
        assert result["code"] is None and "rejected" in result["error"]

    def test_total_odds_unknown_when_a_price_is_missing(self):
        result, _, _ = run([sel(market="goals_ou", code="U25")])  # suspended in the listing
        assert result["picks"][0]["odds"] is None and result["total_odds"] is None
        assert result["code"] == "ABC123"

    def test_nothing_listed(self):
        result, _, posted = run([sel(home="Nobody FC")])
        assert result["code"] is None and "hasn't listed" in result["error"] and posted == []
        assert result["picks"][0]["reason"] == "SportyBet hasn't listed this match (yet)"

    def test_share_failure_keeps_picks_matched(self):
        result, _, _ = run([sel()], share_error=sportybet.SportyBetError("HTTP 403"))
        assert result["code"] is None and "booking code" in result["error"]
        assert result["picks"][0]["status"] == "matched"

    def test_share_failure_passes_on_sportybets_reason(self):
        result, _, _ = run([sel()], share_error=sportybet.SportyBetError("POST /orders/share: bizCode 4302 Event closed"))
        assert "SportyBet said: 4302 Event closed" in result["error"]

    def test_started_matches_are_left_out_and_the_rest_booked(self):
        later = {**EVENT, "eventId": "sr:match:222", "homeTeamName": "Leeds", "awayTeamName": "Everton",
                 "estimateStartTime": ms("2026-09-26T19:00:00")}

        async def fetch(day):
            return [EVENT, later]
        shared = []

        async def post(ids):
            shared.append(ids)
            return {"code": "NEW1", "url": "u", "odds": {}, "unavailable": set()}
        # 17:00: Arsenal v Chelsea (16:30) has started, Leeds v Everton (19:00) hasn't
        now = ms("2026-09-26T17:00:00") / 1000
        out = asyncio.run(to_sportybet([sel(), sel(home="Leeds", away="Everton")], fetch, sportybet.find_event, post, now=now))
        assert [p["status"] for p in out["picks"]] == ["unavailable", "booked"]
        assert out["picks"][0]["reason"] == "Already started" and out["code"] == "NEW1"
        assert [i["eventId"] for i in shared[0]] == ["sr:match:222"]
        # All started: no request to SportyBet, and a plain reason
        out = asyncio.run(to_sportybet([sel()], fetch, sportybet.find_event, post, now=now))
        assert out["code"] is None and out["error"] == "Every match on this slip has already started."
        assert len(shared) == 1


# ── SportyBet client ─────────────────────────────────────────────────────────

class FakeResponse:
    def __init__(self, status, body):
        self.status_code = status
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        return json.loads(self.text)


class FakeSession:
    """Answers by path; records (method, path, kwargs)."""
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    async def request(self, method, url, **kw):
        path = url.split("/api/ng", 1)[1]
        self.calls.append((method, path, kw))
        reply = self.routes.get(path, (404, ""))
        reply = reply.pop(0) if isinstance(reply, list) else reply
        return FakeResponse(*reply)

    async def close(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass


def ok(data):
    return (200, {"bizCode": 10000, "message": "0#0", "data": data})


SELS = [{"eventId": "sr:match:1", "marketId": "18", "specifier": "total=2.5", "outcomeId": "12"},
        {"eventId": "sr:match:2", "marketId": "1", "specifier": "", "outcomeId": "1"}]


class TestShare:
    def test_request_and_response(self):
        s = FakeSession({"/orders/share": ok({
            "shareCode": "XY12Z", "shareURL": "https://www.sportybet.com/?shareCode=XY12Z&c=ng",
            "outcomes": [
                {"eventId": "sr:match:1", "markets": [{"id": "18", "specifier": "total=2.5",
                                                        "outcomes": [{"id": "12", "odds": "1.72"}]}]},
            ],
            "unavailableOutcomes": [{"eventId": "sr:match:2", "marketId": "1", "outcomeId": "1"}],
        })})
        out = asyncio.run(sportybet.share_selections(SELS, session=s))
        assert out["code"] == "XY12Z" and out["url"].endswith("XY12Z&c=ng")
        assert out["odds"] == {("sr:match:1", "18", "12"): 1.72}
        assert out["unavailable"] == {("sr:match:2", "1", "1")}
        method, path, kw = s.calls[0]
        assert (method, path) == ("POST", "/orders/share")
        # specifier only when the market has one — the shape sportybet.com sends
        assert kw["json"] == {"selections": [
            {"eventId": "sr:match:1", "marketId": "18", "outcomeId": "12", "specifier": "total=2.5"},
            {"eventId": "sr:match:2", "marketId": "1", "outcomeId": "1"},
        ]}

    @pytest.mark.parametrize("reply,needle", [
        ((202, ""), "firewall"),
        ((403, "<html>"), "firewall"),
        ((200, {"bizCode": 4000, "message": "Invalid selections"}), "Invalid selections"),
        (ok({}), "no share code"),
    ])
    def test_failures_raise_with_the_reason(self, reply, needle):
        s = FakeSession({"/orders/share": reply})
        with pytest.raises(sportybet.SportyBetError, match=needle):
            asyncio.run(sportybet.share_selections(SELS, session=s))

    def test_uses_chrome_impersonation(self):
        session = sportybet._session()
        try:
            assert sportybet.IMPERSONATE.startswith("chrome")
            assert session.impersonate == sportybet.IMPERSONATE
        finally:
            asyncio.run(session.close())


class TestEvents:
    @pytest.fixture(autouse=True)
    def no_cache(self, monkeypatch):
        monkeypatch.setattr(sportybet, "_cache", {})

    def ev(self, eid, home, away, iso):
        return {"eventId": eid, "homeTeamName": home, "awayTeamName": away, "estimateStartTime": ms(iso)}

    def test_collects_grouped_events_near_the_date(self):
        s = FakeSession({"/factsCenter/pcUpcomingEvents": ok({"totalNum": 3, "tournaments": [
            {"events": [self.ev("sr:match:1", "Arsenal", "Chelsea", "2026-09-26T14:00:00"),
                        self.ev("sr:match:2", "Leeds", "Hull", "2026-09-27T23:30:00")]},
            {"events": [self.ev("sr:match:3", "Ajax", "PSV", "2026-10-02T18:00:00")]},
        ]})})
        events = asyncio.run(sportybet.fetch_events_for_date("2026-09-26", session=s))
        assert [e["eventId"] for e in events] == ["sr:match:1", "sr:match:2"]
        assert s.calls[0][2]["params"]["sportId"] == "sr:sport:1"
        assert len(s.calls) == 2  # page 2 added nothing new: stop

    def test_pages_through_the_upcoming_list(self):
        page = lambda eid: ok({"totalNum": 2, "tournaments": [{"events": [
            self.ev(eid, "A", "B", "2026-09-26T12:00:00")]}]})
        s = FakeSession({"/factsCenter/pcUpcomingEvents": [page("sr:match:1"), page("sr:match:2"),
                                                            ok({"totalNum": 2, "tournaments": []})]})
        events = asyncio.run(sportybet.fetch_events_for_date("2026-09-26", session=s))
        assert [e["eventId"] for e in events] == ["sr:match:1", "sr:match:2"]
        assert [c[2]["params"]["pageNum"] for c in s.calls] == [1, 2, 3]

    def test_falls_back_through_the_listings(self):
        s = FakeSession({
            "/factsCenter/pcUpcomingEvents": (202, ""),
            "/factsCenter/wapConfigurableUpcomingEvents": (404, '{"message":"Not Found"}'),
            "/factsCenter/pcEvents": ok([{"events": [self.ev("sr:match:9", "Inter", "Milan", "2026-09-26T18:45:00")]}]),
        })
        events = asyncio.run(sportybet.fetch_events_for_date("2026-09-26", session=s))
        assert [e["eventId"] for e in events] == ["sr:match:9"]
        body = s.calls[-1][2]["json"]
        assert body[0]["sportId"] == "sr:sport:1" and ["sr:tournament:17"] in body[0]["tournamentId"]

    def test_unreachable_returns_empty(self):
        s = FakeSession({})
        assert asyncio.run(sportybet.fetch_events_for_date("2026-09-26", session=s)) == []

    def test_results_are_cached(self):
        s = FakeSession({"/factsCenter/pcUpcomingEvents": ok([self.ev("sr:match:1", "A", "B", "2026-09-26T12:00:00")])})
        asyncio.run(sportybet.fetch_events_for_date("2026-09-26", session=s))
        first = len(s.calls)
        asyncio.run(sportybet.fetch_events_for_date("2026-09-26", session=s))
        assert first == 2 and len(s.calls) == first  # page 2 repeated page 1; then cached


class TestFindEvent:
    EVENTS = [
        {"eventId": "e1", "homeTeamName": "Manchester United", "awayTeamName": "Manchester City"},
        {"eventId": "e2", "homeTeamName": "Brighton & Hove Albion", "awayTeamName": "Wolverhampton Wanderers"},
        {"eventId": "e3", "homeTeamName": "Bayern Munich", "awayTeamName": "Borussia Dortmund"},
        {"eventId": "e4", "homeTeamName": "Paris Saint-Germain", "awayTeamName": "Paris FC"},
        {"eventId": "e5", "homeTeamName": "Atletico Madrid", "awayTeamName": "Real Madrid"},
    ]

    @pytest.mark.parametrize("home,away,eid", [
        ("Manchester United FC", "Manchester City FC", "e1"),
        ("Man United", "Man City", "e1"),
        ("Brighton & Hove Albion FC", "Wolverhampton Wanderers FC", "e2"),
        ("Brighton", "Wolves", "e2"),
        ("FC Bayern München", "Borussia Dortmund", "e3"),
        ("Paris Saint-Germain FC", "Paris FC", "e4"),
        ("Club Atlético de Madrid", "Real Madrid CF", "e5"),
    ])
    def test_matches(self, home, away, eid):
        assert sportybet.find_event(home, away, self.EVENTS)["eventId"] == eid

    @pytest.mark.parametrize("home,away", [
        ("Manchester City FC", "Manchester United FC"),  # reversed fixture
        ("Arsenal FC", "Chelsea FC"),
        ("Manchester United FC", "Liverpool FC"),        # only one team matches
    ])
    def test_refuses_wrong_fixtures(self, home, away):
        assert sportybet.find_event(home, away, self.EVENTS) is None


class TestTeamSimilarity:
    # football-data.org name (our fixtures) vs Betradar name (SportyBet)
    @pytest.mark.parametrize("ours,theirs", [
        ("RB Leipzig", "RasenBallsport Leipzig"), ("Stade Rennais FC 1901", "Rennes"),
        ("Paris Saint-Germain FC", "Paris Saint-Germain"), ("Paris FC", "Paris FC"),
        ("PSV", "PSV Eindhoven"), ("AZ", "AZ Alkmaar"), ("NEC", "NEC Nijmegen"),
        ("Athletic Club", "Athletic Bilbao"), ("FC Internazionale Milano", "Inter Milan"),
        ("1. FC Köln", "FC Cologne"), ("Olympique Lyonnais", "Olympique Lyon"),
        ("Sporting Clube de Portugal", "Sporting Lisbon"), ("Wolverhampton Wanderers FC", "Wolves"),
        ("Real Sociedad de Fútbol", "Real Sociedad"), ("Galatasaray SK", "Galatasaray"),
    ])
    def test_same_club(self, ours, theirs):
        assert sportybet.team_similarity(ours, theirs) >= 0.8

    @pytest.mark.parametrize("ours,theirs", [
        ("Paris FC", "Paris Saint-Germain"), ("Manchester City FC", "Manchester United"),
        ("Club Atlético de Madrid", "Real Madrid"), ("Real Sociedad de Fútbol", "Real Madrid"),
        ("Sheffield United FC", "Sheffield Wednesday"),
    ])
    def test_different_clubs(self, ours, theirs):
        assert sportybet.team_similarity(ours, theirs) < 0.8


# ── Endpoint ─────────────────────────────────────────────────────────────────

class TestEndpoint:
    def test_converts(self, monkeypatch):
        async def fetch(date): return [EVENT]
        async def share(s): return {"code": "ABC123", "url": "u", "odds": {}, "unavailable": set()}
        monkeypatch.setattr(sportybet, "fetch_events_for_date", fetch)
        monkeypatch.setattr(sportybet, "share_selections", share)
        r = TestClient(main.app).post("/api/booking/convert", json={
            "platform": "sportybet", "selections": [sel()]})
        assert r.status_code == 200 and r.json()["code"] == "ABC123"

    @pytest.mark.parametrize("body", [{"selections": []}, {"platform": "bet9ja", "selections": [sel()]}, {}])
    def test_bad_requests(self, body):
        assert TestClient(main.app).post("/api/booking/convert", json=body).status_code == 400

    def test_old_endpoint_is_gone(self):
        assert TestClient(main.app).post("/api/booking", json={"predictions": []}).status_code in (404, 405)


class TestDiagnose:
    LISTING = ok({"tournaments": [{"events": [
        {"eventId": "sr:match:7", "homeTeamName": "Arsenal", "awayTeamName": "Chelsea",
         "estimateStartTime": ms("2026-09-26T16:30:00"), "markets": [{"id": "1", "outcomes": []}]},
    ]}]})

    def diagnose(self, monkeypatch, routes, fixtures=({"home": "Arsenal FC", "away": "Chelsea FC"},)):
        fake = FakeSession(routes)
        monkeypatch.setattr(sportybet, "_session", lambda: fake)
        return asyncio.run(sportybet.diagnose(list(fixtures), today="2026-09-26")), fake

    def test_all_steps_pass(self, monkeypatch):
        out, fake = self.diagnose(monkeypatch, {"/factsCenter/pcUpcomingEvents": self.LISTING,
                                                "/orders/share": ok({"shareCode": "TEST01"})})
        assert out["ok"] and [s["ok"] for s in out["steps"]] == [True, True, True]
        assert "TEST01" in out["steps"][2]["detail"] and "1 of 1" in out["steps"][1]["detail"]
        assert fake.calls[-1][2]["json"] == {"selections": [{"eventId": "sr:match:7", "marketId": "1", "outcomeId": "1"}]}

    def test_stops_at_a_blocked_listing(self, monkeypatch):
        out, _ = self.diagnose(monkeypatch, {"/factsCenter/pcUpcomingEvents": (403, "<html>")})
        assert not out["ok"] and len(out["steps"]) == 1 and "firewall" in out["steps"][0]["detail"]

    def test_reports_every_listing_and_the_reply(self, monkeypatch):
        out, _ = self.diagnose(monkeypatch, {
            "/factsCenter/pcUpcomingEvents": (404, '{"timestamp":1,"status":404,"error":"Not Found"}'),
            "/factsCenter/pcEvents": self.LISTING,
            "/orders/share": ok({"shareCode": "TEST02"})})
        detail = out["steps"][0]["detail"]
        assert out["ok"] and "pcUpcomingEvents: GET /factsCenter/pcUpcomingEvents: HTTP 404" in detail
        assert "Not Found" in detail and "pcEvents: 1 events" in detail
        assert "wapConfigurableUpcomingEvents: GET" in detail

    def test_reports_a_refused_booking(self, monkeypatch):
        out, _ = self.diagnose(monkeypatch, {"/factsCenter/pcUpcomingEvents": self.LISTING,
                                             "/orders/share": (200, {"bizCode": 19000, "message": "Rejected"})})
        assert not out["ok"] and "Rejected" in out["steps"][2]["detail"]

    def test_admin_only(self, monkeypatch):
        monkeypatch.setattr(main, "ADMIN_SECRET", "s3cret")
        assert TestClient(main.app).get("/api/admin/sportybet-check").status_code == 403


class TestLinkedBooking:
    """Predictions matched to SportyBet events ahead of time book in one request."""

    def test_linked_selections_skip_the_event_listing(self):
        async def fetch(date):
            raise AssertionError("listing should not be fetched")
        shared = []

        async def share(sels):
            shared.append(sels)
            return {"code": "FAST01", "url": "u", "odds": {}, "unavailable": set()}
        out = asyncio.run(to_sportybet([sel()], fetch, sportybet.find_event, share,
                                       linked=lambda s: sportybet.slim_event(EVENT)))
        assert out["code"] == "FAST01" and out["picks"][0]["status"] == "booked"
        assert out["picks"][0]["odds"] == 1.85  # from the linked event's stored prices
        assert shared == [[{"eventId": "sr:match:111", "marketId": "1", "specifier": "", "outcomeId": "1"}]]

    def test_unlinked_selections_still_use_the_listing(self):
        calls = []

        async def fetch(date):
            calls.append(date)
            return [EVENT]

        async def share(sels):
            return {"code": "SLOW01", "url": "u", "odds": {}, "unavailable": set()}
        out = asyncio.run(to_sportybet([sel()], fetch, sportybet.find_event, share, linked=lambda s: None))
        assert out["code"] == "SLOW01" and calls == ["2026-09-26"]

    def test_slim_event_keeps_only_booked_markets(self):
        ev = {**EVENT, "extra": "x", "markets": EVENT["markets"] + [{"id": "999", "outcomes": []}]}
        slim = sportybet.slim_event(ev)
        assert set(slim) == {"eventId", "homeTeamName", "awayTeamName", "estimateStartTime", "markets"}
        assert [m["id"] for m in slim["markets"]] == [m["id"] for m in EVENT["markets"]
                                                       if m["id"] in sportybet.BOOKED_MARKETS]

    def test_predictions_link_to_events_around_their_date(self):
        events = [EVENT,
                  {**EVENT, "eventId": "sr:match:222", "homeTeamName": "Leeds", "awayTeamName": "Hull",
                   "estimateStartTime": ms("2026-10-20T15:00:00")}]
        preds = [{"home": "Arsenal FC", "away": "Chelsea FC", "date": "2026-09-26"},
                 {"home": "Leeds United", "away": "Hull City", "date": "2026-09-26"}]  # SportyBet's is weeks later
        links = main._match_predictions_to_events(preds, events)
        assert list(links) == ["Arsenal FC|Chelsea FC|2026-09-26"]
        assert links["Arsenal FC|Chelsea FC|2026-09-26"]["eventId"] == "sr:match:111"

    def test_linking_job_flags_bookable_predictions_and_keeps_links_when_sportybet_is_down(self, monkeypatch):
        from datetime import date, timedelta
        day = (date.today() + timedelta(days=2)).isoformat()
        event = {**EVENT, "estimateStartTime": ms(day + "T16:30:00")}
        preds = [{"home": "Arsenal FC", "away": "Chelsea FC", "date": day, "league": "PL"},
                 {"home": "Nowhere", "away": "Nobody", "date": day, "league": "PL"}]
        monkeypatch.setattr(main, "_predictions_cache", preds)
        monkeypatch.setattr(main, "_sb_links", {})
        monkeypatch.setattr(main, "_get_redis", lambda: None)

        async def catalog():
            return [event], ["pcUpcomingEvents: 1 events"]
        monkeypatch.setattr(sportybet, "fetch_catalog", catalog)
        status = asyncio.run(main._link_sportybet_events())
        assert (status["linked"], status["predictions"], status["events"]) == (1, 2, 1)
        assert [p["sportybet"] for p in preds] == [True, False]

        async def down():
            return [], ["pcUpcomingEvents: HTTP 403"]
        monkeypatch.setattr(sportybet, "fetch_catalog", down)
        status = asyncio.run(main._link_sportybet_events())
        assert status["linked"] == 1 and preds[0]["sportybet"] is True  # last links kept

    def test_convert_endpoint_uses_the_links(self, monkeypatch):
        monkeypatch.setattr(main, "_sb_links", {"Arsenal FC|Chelsea FC|2026-09-26": sportybet.slim_event(EVENT)})

        async def no_listing(date):
            raise AssertionError("listing should not be fetched")

        async def share(sels):
            return {"code": "FAST02", "url": "u", "odds": {}, "unavailable": set()}
        monkeypatch.setattr(sportybet, "fetch_events_for_date", no_listing)
        monkeypatch.setattr(sportybet, "share_selections", share)
        r = TestClient(main.app).post("/api/booking/convert", json={"platform": "sportybet", "selections": [sel()]})
        assert r.json()["code"] == "FAST02"


class TestSharedSession:
    def test_bookings_reuse_one_connection_and_reconnect_once(self, monkeypatch):
        made = []

        class Session(FakeSession):
            def __init__(self, fail_first):
                super().__init__({"/orders/share": ok({"shareCode": "KEEP01"})})
                self.fail = fail_first
                made.append(self)

            async def request(self, method, url, **kw):
                if self.fail:
                    self.fail = False
                    raise ConnectionError("stale connection")
                return await super().request(method, url, **kw)

        monkeypatch.setattr(sportybet, "_shared", None)
        monkeypatch.setattr(sportybet, "_session", lambda: Session(fail_first=not made))
        one = [{"eventId": "sr:match:1", "marketId": "1", "outcomeId": "1"}]
        assert asyncio.run(sportybet.share_selections(one))["code"] == "KEEP01"   # reconnected once
        assert asyncio.run(sportybet.share_selections(one))["code"] == "KEEP01"   # same connection again
        assert len(made) == 2 and len(made[1].calls) == 2


def test_an_empty_sportybet_listing_is_reported_as_unreachable_not_missing():
    async def fetch(date):
        return []

    async def share(sels):
        raise AssertionError("nothing to book")
    out = asyncio.run(to_sportybet([sel(), sel(home="Leeds", away="Hull")], fetch, sportybet.find_event, share))
    assert {p["reason"] for p in out["picks"]} == {"Couldn't load SportyBet's match list"}
    assert out["error"].startswith("Couldn't reach SportyBet's match list")


def test_upcoming_listing_uses_the_sites_parameters():
    s = FakeSession({"/factsCenter/pcUpcomingEvents": ok({"totalNum": 0, "tournaments": []})})
    asyncio.run(sportybet._pc_upcoming(s, max_pages=1))
    params = s.calls[0][2]["params"]
    assert params["todayGames"] == "false" and "option" not in params and params["sportId"] == "sr:sport:1"


class TestKickoffMatching:
    KO = ms("2026-09-26T18:45:00")

    def ev(self, eid, home, away, iso="2026-09-26T18:45:00"):
        return {"eventId": eid, "homeTeamName": home, "awayTeamName": away, "estimateStartTime": ms(iso)}

    @pytest.mark.parametrize("ours,theirs", [("Czechia", "Czech Republic"), ("USA", "United States"),
                                             ("Türkiye", "Turkey"), ("Côte d'Ivoire", "Ivory Coast"),
                                             ("Korea Republic", "South Korea")])
    def test_national_team_spellings_match(self, ours, theirs):
        assert sportybet.team_similarity(ours, theirs) == 1.0

    def test_one_clear_name_and_the_same_kickoff(self):
        events = [self.ev("sr:match:1", "Palmeiras SP", "Clube Atletico Mineiro")]
        assert sportybet.find_event("SE Palmeiras", "Atletico Mineiro", events) is None  # strict pass misses
        assert sportybet.find_event_by_kickoff("SE Palmeiras", "Atletico Mineiro", self.KO, events)["eventId"] == "sr:match:1"

    def test_not_at_another_kickoff(self):
        events = [self.ev("sr:match:1", "Palmeiras SP", "Clube Atletico Mineiro", "2026-09-26T21:00:00")]
        assert sportybet.find_event_by_kickoff("SE Palmeiras", "Atletico Mineiro", self.KO, events) is None

    def test_never_a_womens_or_youth_side(self):
        events = [self.ev("sr:match:1", "Arsenal W", "Chelsea W"), self.ev("sr:match:2", "Brazil U17", "Chile U17")]
        assert sportybet.find_event_by_kickoff("Arsenal", "Chelsea", self.KO, events) is None
        assert sportybet.find_event_by_kickoff("Brazil", "Chile", self.KO, events) is None
        assert sportybet.find_event_by_kickoff("Brazil U17", "Chile U17", self.KO, events)["eventId"] == "sr:match:2"

    def test_never_the_reverse_fixture(self):
        events = [self.ev("sr:match:1", "Chelsea", "Arsenal")]
        assert sportybet.find_event_by_kickoff("Arsenal", "Chelsea", self.KO, events) is None

    def test_linking_uses_the_kickoff_and_reports_the_rest(self):
        events = [self.ev("sr:match:1", "Palmeiras SP", "Clube Atletico Mineiro"),
                  self.ev("sr:match:2", "Sao Paulo", "Santos")]
        preds = [{"home": "SE Palmeiras", "away": "Atletico Mineiro", "date": "2026-09-26", "time": "18:45"},
                 {"home": "Botafogo", "away": "Fluminense", "date": "2026-09-26", "time": "18:45"}]
        unlinked = []
        links = main._match_predictions_to_events(preds, events, unlinked)
        assert list(links) == ["SE Palmeiras|Atletico Mineiro|2026-09-26"]
        assert unlinked[0]["match"] == "Botafogo vs Fluminense" and unlinked[0]["closest"]



class TestCatalog:
    def test_pages_past_thirty_until_nothing_new(self):
        pages = [ok({"tournaments": [{"name": "Premier League", "categoryName": "England", "events": [
            {"eventId": f"sr:match:{i}", "homeTeamName": f"H{i}", "awayTeamName": f"A{i}",
             "estimateStartTime": ms("2026-09-26T15:00:00")}]}]}) for i in range(45)]
        s = FakeSession({"/factsCenter/pcUpcomingEvents": pages + [ok({"tournaments": []})],
                         "/factsCenter/wapConfigurableUpcomingEvents": [ok({"tournaments": [{"name": "Friendly", "events": [
                             {"eventId": "sr:match:0", "homeTeamName": "H0", "awayTeamName": "A0"},
                             {"eventId": "sr:match:999", "homeTeamName": "Wales", "awayTeamName": "Iceland"}]}]}),
                             ok({"tournaments": []})],
                         "/factsCenter/commonThumbnailEvents": ok([])})
        events, report = asyncio.run(sportybet.fetch_catalog(s))
        assert len(events) == 46  # 45 desktop pages + 1 only on the mobile feed
        assert report[0].startswith("pcUpcomingEvents: 45 events in 46 pages")
        assert "1 new" in report[1]
        assert events[0]["_tournament"] == "England · Premier League"

    def test_summary_by_day_and_international_competitions(self):
        events = [{"eventId": "1", "estimateStartTime": ms("2026-09-26T15:00:00"), "_tournament": "International · Int. Friendly Games"},
                  {"eventId": "2", "estimateStartTime": ms("2026-09-26T18:00:00"), "_tournament": "England · Premier League"},
                  {"eventId": "3", "estimateStartTime": ms("2026-09-27T15:00:00"), "_tournament": "Africa · Africa Cup of Nations, Qualification"}]
        summary = main._catalog_summary(events)
        assert summary["days"] == {"2026-09-26": 2, "2026-09-27": 1}
        assert set(summary["international"]) == {"International · Int. Friendly Games",
                                                 "Africa · Africa Cup of Nations, Qualification"}


class TestCornersAndBookings:
    def test_ids_for_the_new_lines(self):
        from booking_slip import sportybet_ids
        assert sportybet_ids("corners_ou", "O95") == {"marketId": "166", "specifier": "total=9.5", "outcomeId": "12"}
        assert sportybet_ids("corners_ou", "U105") == {"marketId": "166", "specifier": "total=10.5", "outcomeId": "13"}
        assert sportybet_ids("cards_ou", "O45") == {"marketId": "139", "specifier": "total=4.5", "outcomeId": "12"}
        assert sportybet_ids("goals_ou", "U35")["specifier"] == "total=3.5"
        assert sportybet_ids("goals_ou", "O20") is None

    def _book(self, market_map):
        shared = []

        async def share(sels):
            shared.append(sels)
            return {"code": "CRN01", "url": "u", "odds": {}, "unavailable": set()}
        s = {**sel(), "market": "corners_ou", "code": "O95", "label": "Over 9.5 corners"}
        out = asyncio.run(to_sportybet([s], None, sportybet.find_event, share,
                                       linked=lambda s: sportybet.slim_event(EVENT), market_map=market_map))
        return out, shared

    def test_not_booked_until_sportybet_confirms_the_market(self):
        for market_map in (None, {}, {"corners_ou": {"id": "166", "ok": False}}):
            out, shared = self._book(market_map)
            assert out["picks"][0]["status"] == "unsupported" and not shared
            assert "corners" in out["picks"][0]["reason"]

    def test_booked_under_the_confirmed_id(self):
        out, shared = self._book({"corners_ou": {"id": "167", "ok": True}})
        assert out["code"] == "CRN01"
        assert shared == [[{"eventId": "sr:match:111", "marketId": "167", "specifier": "total=9.5", "outcomeId": "12"}]]

    def test_market_labels_from_the_listing(self):
        events = [{"markets": [{"id": "1", "desc": "1X2"}, {"id": "166", "desc": "Total Corners"}]},
                  {"markets": [{"id": "139", "name": "Total Bookings"}]}]
        assert sportybet.market_labels(events) == {"1": "1X2", "166": "Total Corners", "139": "Total Bookings"}

    def test_catalog_retries_without_the_new_markets(self):
        page = ok({"tournaments": [{"name": "Premier League", "events": [
            {"eventId": "sr:match:1", "homeTeamName": "H", "awayTeamName": "A"}]}]})
        calls = []

        class Picky(FakeSession):
            async def request(self, method, url, params=None, **kw):
                calls.append(params.get("marketId"))
                if "166" in params["marketId"]:
                    return FakeResponse(400, "")
                return FakeResponse(*(page if params.get("pageNum", 1) == 1 else ok({"tournaments": []})))
        events, report = asyncio.run(sportybet.fetch_catalog(Picky({})))
        assert len(events) == 1
        assert "without corners/bookings markets" in report[0]
        assert sportybet.BASE_MARKETS in calls


class TestLinkStatus:
    def test_status_is_saved_with_its_trigger_and_restored_after_a_restart(self, monkeypatch):
        from datetime import date, timedelta
        day = (date.today() + timedelta(days=2)).isoformat()
        event = {**EVENT, "estimateStartTime": ms(day + "T16:30:00"),
                 "markets": EVENT["markets"] + [{"id": "166", "specifier": "total=9.5", "desc": "Total Corners",
                                                 "outcomes": [{"id": "12", "odds": "1.8", "desc": "Over 9.5"},
                                                              {"id": "13", "odds": "1.9", "desc": "Under 9.5"}]}]}
        store = {}

        class Redis:
            def get(self, k): return store.get(k)
            def set(self, k, v, ex=None): store[k] = v
        monkeypatch.setattr(main, "_predictions_cache", [{"home": "Arsenal FC", "away": "Chelsea FC", "date": day, "league": "PL"}])
        monkeypatch.setattr(main, "_sb_links", {})
        monkeypatch.setattr(main, "_sb_link_status", {"at": None})
        monkeypatch.setattr(main, "_get_redis", lambda: Redis())

        async def catalog():
            return [event], ["pcUpcomingEvents: 1 events"]

        async def event_page(event_id, session=None):
            return {"139": {"label": "Total Bookings", "norm": "total bookings",
                            "outcomes": {"12": "over", "13": "under"}}}
        monkeypatch.setattr(sportybet, "fetch_catalog", catalog)
        monkeypatch.setattr(sportybet, "event_market_details", event_page)
        asyncio.run(main._link_on_startup())
        status = main._sb_link_status
        assert status["trigger"] == "startup" and status["linked"] == 1
        assert status["market_labels"]["166"] == "Total Corners"
        assert status["market_labels"]["139"] == "Total Bookings"  # from the event page
        assert status["market_map"]["corners_ou"] == {"id": "166", "label": "Total Corners", "ok": True, "why": None}
        assert status["market_map"]["cards_ou"]["ok"] is True
        assert status["market_coverage"]["166"] == 1

        # After a restart the last result shows before any new run
        monkeypatch.setattr(main, "_sb_link_status", {"at": None})
        monkeypatch.setattr(main, "_predictions_cache", [])
        body = TestClient(main.app).get("/api/sportybet/status").json()
        assert body["trigger"] == "startup" and body["linked"] == 1
        assert body["markets"]["corners_ou"] is True and body["markets"]["cards_ou"] is True
        assert body["markets"]["handicap"] is False  # not on that page

    def test_empty_cache_on_startup_waits_for_the_pipeline(self, monkeypatch):
        monkeypatch.setattr(main, "_predictions_cache", [])
        monkeypatch.setattr(main, "_get_redis", lambda: None)

        async def catalog():
            raise AssertionError("nothing to link")
        monkeypatch.setattr(sportybet, "fetch_catalog", catalog)
        asyncio.run(main._link_on_startup())


class TestNewMarkets:
    def test_ids(self):
        from booking_slip import sportybet_ids
        assert sportybet_ids("home_goals_ou", "O15") == {"marketId": "19", "specifier": "total=1.5", "outcomeId": "12"}
        assert sportybet_ids("away_corners_ou", "U45") == {"marketId": "170", "specifier": "total=4.5", "outcomeId": "13"}
        assert sportybet_ids("clean_sheet", "CS-A") == {"marketId": "32", "specifier": "", "outcomeId": "74"}
        assert sportybet_ids("win_to_nil", "WTN-H")["marketId"] == "33"
        assert sportybet_ids("corners_1x2", "CR-X") == {"marketId": "162", "specifier": "", "outcomeId": "2"}
        # Handicap: the specifier is always the home side's line
        assert sportybet_ids("handicap", "H-1.5") == {"marketId": "16", "specifier": "hcp=-1.5", "outcomeId": "1714"}
        assert sportybet_ids("handicap", "A+1.5") == {"marketId": "16", "specifier": "hcp=-1.5", "outcomeId": "1715"}
        assert sportybet_ids("handicap", "A-2.5") == {"marketId": "16", "specifier": "hcp=2.5", "outcomeId": "1715"}
        assert sportybet_ids("handicap", "H+2.5") == {"marketId": "16", "specifier": "hcp=2.5", "outcomeId": "1714"}
        assert sportybet_ids("dc_goals", "X2&U35") == {"marketId": "547", "specifier": "total=3.5", "outcomeId": "802"}
        assert sportybet_ids("handicap", "H-1.0") is None and sportybet_ids("dc_goals", "1X&O20") is None


class TestResolveMarkets:
    def _page(self, markets):
        return {"home": "Arsenal", "away": "Chelsea", "markets": markets}

    def test_confirms_guessed_ids_by_label_with_team_names(self):
        from booking_slip import resolve_markets
        out = resolve_markets(self._page({
            "19": {"label": "Arsenal Total", "outcomes": {"12": "Over 1.5", "13": "Under 1.5"}},
            "20": {"label": "Chelsea Total", "outcomes": {"12": "Over", "13": "Under"}},
            "16": {"label": "Asian Handicap", "outcomes": {"1714": "Arsenal (-1.5)", "1715": "Chelsea (+1.5)"}},
            "547": {"label": "Double Chance & Total", "outcomes": {
                "794": "Arsenal or Draw & Under 2.5", "796": "Arsenal or Draw & Over 2.5",
                "798": "Arsenal or Chelsea & Under 2.5", "800": "Arsenal or Chelsea & Over 2.5",
                "802": "Draw or Chelsea & Under 2.5", "804": "Draw or Chelsea & Over 2.5"}},
        }))
        assert out["home_goals_ou"]["ok"] and out["away_goals_ou"]["ok"]
        assert out["handicap"]["ok"] and out["dc_goals"]["ok"]
        assert not out["corners_ou"]["ok"] and out["corners_ou"]["why"] == "not on this match's page"

    def test_finds_the_right_id_when_the_guess_is_wrong(self):
        from booking_slip import resolve_markets
        out = resolve_markets(self._page({
            "166": {"label": "1st Half - Total Corners", "outcomes": {"12": "Over", "13": "Under"}},
            "167": {"label": "Total Corners", "outcomes": {"12": "Over", "13": "Under"}},
        }))
        assert out["corners_ou"] == {"id": "167", "label": "Total Corners", "ok": True, "why": None}

    def test_wrong_outcome_labels_are_not_booked(self):
        from booking_slip import resolve_markets
        out = resolve_markets(self._page({
            "547": {"label": "Double Chance & Total", "outcomes": {
                "794": "Draw or Chelsea & Over 2.5", "796": "x", "798": "x", "800": "x", "802": "x", "804": "x"}},
        }))
        assert not out["dc_goals"]["ok"] and "794" in out["dc_goals"]["why"]

    def test_listing_details_normalise_each_events_names(self):
        events = [{"homeTeamName": "Arsenal", "awayTeamName": "Chelsea", "markets": [
            {"id": "19", "desc": "Arsenal Total", "outcomes": [{"id": "12", "desc": "Over 1.5"}]}]}]
        d = sportybet.market_details(events)
        assert d["19"]["norm"] == "home total" and d["19"]["outcomes"]["12"] == "over 1.5"
