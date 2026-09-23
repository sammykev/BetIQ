"""
Bet slip → SportyBet booking code: market translation, per-pick outcomes,
the share call, and the endpoint's validation.
"""

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import booking_slip
import main
import sportybet
from booking_slip import sportybet_ids, to_sportybet, validate


def sel(home="Arsenal FC", away="Chelsea FC", date="2026-09-26", market="1x2", code="1", **kw):
    return {"home": home, "away": away, "date": date, "market": market, "code": code, **kw}


EVENT = {
    "eventId": "sr:match:111", "homeTeamName": "Arsenal", "awayTeamName": "Chelsea",
    "markets": [
        {"id": "1", "specifier": "", "outcomes": [
            {"id": "1", "odds": "1.85"}, {"id": "2", "odds": "3.60"}, {"id": "3", "odds": "4.20"}]},
        {"id": "18", "specifier": "total=2.5", "outcomes": [
            {"id": "12", "odds": "1.72"}, {"id": "13", "odds": "2.05", "isActive": 0}]},
    ],
}


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


def run(selections, events=(EVENT,), share=("ABC123", "https://sb/ABC123")):
    fetched, posted = [], []

    async def fetch(date):
        fetched.append(date)
        return list(events)

    def find(home, away, evs):
        return next((e for e in evs if e["homeTeamName"].split()[0] in home), None)

    async def post(selections):
        posted.append(selections)
        return {"code": share[0], "url": share[1]} if share else None

    result = asyncio.run(to_sportybet(selections, fetch, find, post))
    return result, fetched, posted


class TestToSportybet:
    def test_books_matched_picks_with_live_odds(self):
        result, fetched, posted = run([sel(code="1"), sel(home="Arsenal", away="X", market="goals_ou", code="O25", date="2026-09-26")])
        assert result["code"] == "ABC123" and result["share_url"] == "https://sb/ABC123"
        assert posted == [[
            {"eventId": "sr:match:111", "marketId": "1", "specifier": "", "outcomeId": "1"},
            {"eventId": "sr:match:111", "marketId": "18", "specifier": "total=2.5", "outcomeId": "12"},
        ]]
        assert [p["odds"] for p in result["picks"]] == [1.85, 1.72]
        assert result["total_odds"] == round(1.85 * 1.72, 2)
        assert fetched == ["2026-09-26"]  # one listing per date

    def test_reports_each_pick_that_cant_be_booked(self):
        result, _, posted = run([
            sel(),
            sel(home="Leeds United FC", away="Hull City FC"),        # not listed
            sel(home="Arsenal", away="Y", market="correct_score", code="CS-2-1"),
        ])
        assert [p["status"] for p in result["picks"]] == ["booked", "not_found", "unsupported"]
        assert len(posted[0]) == 1 and result["code"] == "ABC123"

    def test_total_odds_unknown_when_a_price_is_missing(self):
        # Under 2.5 is suspended in the listing, so its price is unknown
        result, _, _ = run([sel(market="goals_ou", code="U25")])
        assert result["picks"][0]["odds"] is None and result["total_odds"] is None
        assert result["code"] == "ABC123"

    def test_nothing_bookable(self):
        result, _, posted = run([sel(home="Nobody FC")])
        assert result["code"] is None and result["error"] and posted == []

    def test_share_failure(self):
        result, _, _ = run([sel()], share=None)
        assert result["code"] is None and "booking code" in result["error"]


class TestShareCall:
    def _client(self, responses):
        calls = []

        def handler(request):
            if request.method == "GET":  # session warm-up
                return httpx.Response(200, text="<html></html>")
            calls.append(json.loads(request.content))
            status, body = responses[len(calls) - 1]
            return httpx.Response(status, json=body)
        return httpx.AsyncClient(transport=httpx.MockTransport(handler)), calls

    SELS = [{"eventId": "sr:match:1", "marketId": "18", "specifier": "total=2.5", "outcomeId": "12"}]

    def test_uses_the_selections_payload(self):
        client, calls = self._client([(200, {"bizCode": 10000, "data": {
            "shareCode": "XY12Z", "shareURL": "https://www.sportybet.com/?shareCode=XY12Z&c=ng"}})])
        out = asyncio.run(sportybet.share_selections(self.SELS, client=client))
        assert out == {"code": "XY12Z", "url": "https://www.sportybet.com/?shareCode=XY12Z&c=ng"}
        assert calls[0] == {"selections": [{"eventId": "sr:match:1", "marketId": "18",
                                            "specifier": "total=2.5", "outcomeId": "12"}]}

    def test_falls_back_to_the_older_payload(self):
        client, calls = self._client([(200, {"bizCode": 4000, "message": "bad"}),
                                      (200, {"data": {"bookingCode": "OLD99"}})])
        out = asyncio.run(sportybet.share_selections(self.SELS, client=client))
        assert out["code"] == "OLD99" and "OLD99" in out["url"]
        assert "betList" in calls[1]

    def test_no_code(self):
        client, _ = self._client([(500, {}), (200, {"bizCode": 4000})])
        assert asyncio.run(sportybet.share_selections(self.SELS, client=client)) is None


class TestEndpoint:
    def test_converts(self, monkeypatch):
        async def fetch(date): return [EVENT]
        async def share(s): return {"code": "ABC123", "url": "u"}
        monkeypatch.setattr(sportybet, "fetch_events_for_date", fetch)
        monkeypatch.setattr(sportybet, "share_selections", share)
        r = TestClient(main.app).post("/api/booking/convert", json={
            "platform": "sportybet", "selections": [sel(home="Arsenal FC", away="Chelsea FC")]})
        assert r.status_code == 200 and r.json()["code"] == "ABC123"

    @pytest.mark.parametrize("body", [{"selections": []}, {"platform": "bet9ja", "selections": [sel()]}, {}])
    def test_bad_requests(self, body):
        assert TestClient(main.app).post("/api/booking/convert", json=body).status_code == 400
