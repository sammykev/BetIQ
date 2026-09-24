"""
SportyBet codes checked against the model (code_check.py), value bets at
SportyBet's prices (value_bets.py), tennis from SportyBet's listing, and the
optimizer's explanations when nothing qualifies.
"""

import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import booking_slip
import code_check
import main
import sportybet
import sports_fetcher
import value_bets
from predictor import goal_markets

TOMORROW = (date.today() + timedelta(days=1)).isoformat()
KICKOFF_MS = int(datetime.fromisoformat(TOMORROW + "T15:00:00+00:00").timestamp() * 1000)


def pred(**kw):
    return {"home": "Arsenal", "away": "Chelsea", "date": TOMORROW, "time": "15:00", "league": "PL",
            "league_name": "Premier League", "p_home": 0.62, "p_draw": 0.22, "p_away": 0.16,
            "p_over15": 0.8, "p_over25": 0.58, **goal_markets(1.8, 1.0, 0.58, 0.62, 0.16), **kw}


def market(mid, outcomes, spec="", desc=""):
    return {"id": mid, "specifier": spec, "desc": desc,
            "outcomes": [{"id": o, "odds": str(p), "desc": d, "isActive": 1} for o, p, d in outcomes]}


SHARE = {"shareCode": "ABC123", "outcomes": [
    {"eventId": "sr:match:1", "homeTeamName": "Arsenal FC", "awayTeamName": "Chelsea FC", "estimateStartTime": KICKOFF_MS,
     "markets": [market("18", [("13", 1.9, "Under 2.5")], "total=2.5", "Over/Under")]},
    {"eventId": "sr:match:2", "homeTeamName": "Nobody", "awayTeamName": "Someone", "estimateStartTime": KICKOFF_MS,
     "markets": [market("1", [("1", 1.5, "Home")], "", "1X2")]},
]}


class TestParseShare:
    def test_one_selection_per_outcome(self):
        sels = sportybet.parse_share(SHARE)
        assert [(s["eventId"], s["marketId"], s["specifier"], s["outcomeId"], s["odds"]) for s in sels] == [
            ("sr:match:1", "18", "total=2.5", "13", 1.9), ("sr:match:2", "1", "", "1", 1.5)]

    def test_rejects_bad_codes(self):
        with pytest.raises(sportybet.SportyBetError):
            asyncio.run(sportybet.load_share_code("../../x"))


class TestAnalyse:
    def run(self):
        sels = sportybet.parse_share(SHARE)
        p = pred()
        return code_check.analyse(sels, lambda s: p if s["eventId"] == "sr:match:1" else None,
                                  lambda _: None, confirmed={})

    def test_legs_get_our_chance_and_a_verdict(self):
        r = self.run()
        under, unknown = r["legs"]
        assert under["our_prob"] == pytest.approx(0.42) and under["verdict"] == "risky"
        assert under["implied"] == pytest.approx(1 / 1.9, abs=1e-3)
        assert under["suggestion"] and under["suggestion"]["prob"] >= 0.5
        assert unknown["verdict"] == "not_modelled" and unknown["note"] == "We don't predict this match"

    def test_improved_slips_keep_unmodelled_legs(self):
        r = self.run()
        for slip in (r["same_odds"], r["safest"]):
            assert slip and any(p.get("sb") for p in slip["picks"])  # the unknown leg, booked by its ids
            assert slip["unmodelled"] == 1
        assert r["safest"]["win_chance"] > r["original"]["win_chance"]

    def test_unconfirmed_markets_are_not_matched(self):
        sel = {"eventId": "sr:match:1", "marketId": "19", "specifier": "total=0.5", "outcomeId": "12"}
        assert code_check.model_pick(pred(), sel, confirmed={}) is None
        got = code_check.model_pick(pred(), sel, confirmed={"home_goals_ou": "19"})
        assert got["market"] == "home_goals_ou" and got["prob"] > 0.5


class TestRawIdBooking:
    def test_kept_legs_book_by_their_ids(self):
        shared = []

        async def share(sels):
            shared.append(sels)
            return {"code": "RAW1", "url": "u", "odds": {}, "unavailable": set()}
        s = {"home": "A", "away": "B", "date": TOMORROW, "market": "sportybet", "code": "1",
             "sb": {"eventId": "sr:match:9", "marketId": "999", "specifier": "", "outcomeId": "1"}}
        out = asyncio.run(booking_slip.to_sportybet(booking_slip.validate([s]), None, None, share))
        assert out["code"] == "RAW1" and shared[0][0]["marketId"] == "999"

    def test_rejects_malformed_ids(self):
        s = {"home": "A", "away": "B", "date": TOMORROW, "market": "sportybet", "code": "1",
             "sb": {"eventId": "http://evil", "marketId": "1", "specifier": "", "outcomeId": "1"}}
        with pytest.raises(ValueError):
            booking_slip.validate([s])


class TestValueBets:
    EVENT = {"eventId": "sr:match:1", "markets": [
        market("1", [("1", 2.2, "Home"), ("2", 3.4, "Draw"), ("3", 3.6, "Away")]),
        market("18", [("12", 1.7, "Over"), ("13", 2.1, "Under")], "total=2.5")]}

    def test_best_edge_at_sportybet_prices(self):
        v = value_bets.best_value(pred(), self.EVENT)
        assert v["bookie"] == "SportyBet" and v["value_outcome"] == "1"  # 62% vs ~43% implied
        assert v["edge"] > 10 and v["value_odds"] == 2.2 and v["expected_return"] > 0

    def test_no_value_when_prices_agree(self):
        fair = {"markets": [market("1", [("1", 1.55, ""), ("2", 4.3, ""), ("3", 5.9, "")])]}
        assert value_bets.best_value(pred(p_over25=0.5, p_over15=0.7, p_over35=None, p_btts=None), fair) is None

    def test_draws_are_never_value(self):
        drawish = {"markets": [market("1", [("1", 2.0, ""), ("2", 9.0, ""), ("3", 3.0, "")])]}
        v = value_bets.best_value(pred(p_home=0.3, p_draw=0.45, p_away=0.25), drawish)
        assert v is None or v["value_outcome"] != "X"


class TestTennis:
    EV = {"eventId": "sr:match:77", "homeTeamName": "Player One", "awayTeamName": "Player Two",
          "estimateStartTime": KICKOFF_MS, "_tournament": "ATP · Tokyo, Japan",
          "markets": [market("186", [("4", 1.4, "1"), ("5", 2.9, "2")])]}

    def test_prediction_from_sportybet_prices(self, monkeypatch):
        monkeypatch.setattr(sports_fetcher, "_apply_tennis_elo", lambda *a: {"p1_win": a[3], "p2_win": a[4]})
        p = sports_fetcher.sportybet_prediction(self.EV, "tennis")
        assert p["p_home"] == pytest.approx((1 / 1.4) / (1 / 1.4 + 1 / 2.9), abs=1e-3)
        assert p["tip_code"] == "1" and p["sportybet_event_id"] == "sr:match:77"
        assert p["league_name"] == "ATP · Tokyo, Japan" and p["date"] == TOMORROW and p["model"] == "market"

    def test_events_without_prices_are_skipped(self):
        assert sports_fetcher.sportybet_prediction({**self.EV, "markets": []}, "tennis") is None

    def test_merges_other_sources_without_duplicates(self):
        a = [{"home": "Player One", "away": "Player Two", "date": TOMORROW}]
        b = [{"home": "Player Two", "away": "Player One", "date": TOMORROW}, {"home": "C", "away": "D", "date": TOMORROW}]
        assert len(sports_fetcher._merge_sources(a, b)) == 2


class TestOptimizerExplains:
    @pytest.fixture
    def client(self, monkeypatch):
        monkeypatch.setattr(main, "_get_redis", lambda: None)
        monkeypatch.setattr(main, "_sb_link_status", {"at": "x", "market_map": {}})
        return TestClient(main.app)

    def post(self, client, **kw):
        body = {"target_odds": 5, "min_odds": 4.75, "max_odds": 5.25, "days": 3, **kw}
        return client.post("/api/optimizer", json=body).json()

    def test_corners_for_internationals(self, client, monkeypatch):
        monkeypatch.setattr(main, "_predictions_cache", [pred(league="INT-WCQ")])
        r = self.post(client, markets=["corners_ou"])
        assert "no predictions" in r["error"] and "SportyBet's own line" in r["error"]
        assert r["reasons"][0]["reason"] == "no_data"

    def test_btts_below_the_minimum(self, client, monkeypatch):
        monkeypatch.setattr(main, "_predictions_cache", [pred(p_btts=0.55)])
        r = self.post(client, markets=["btts"], min_prob=0.6)
        assert "likeliest pick is 55%" in r["error"]

    def test_unconfirmed_markets_left_out_of_bookable_slips(self, client, monkeypatch):
        p = pred()
        monkeypatch.setattr(main, "_predictions_cache", [p])
        monkeypatch.setattr(main, "_sb_links", {f"Arsenal|Chelsea|{TOMORROW}": {"eventId": "sr:match:1", "markets": []}})
        r = self.post(client, markets=["home_goals_ou"], bookable_only=True)
        assert "hasn't confirmed" in r["error"] and r["reasons"][0]["reason"] == "not_bookable"
        # Confirmed → allowed
        monkeypatch.setattr(main, "_sb_link_status", {"at": "x", "market_map": {"home_goals_ou": {"id": "19", "ok": True}}})
        r = self.post(client, markets=["home_goals_ou"], bookable_only=True, target_odds=1.2, min_odds=1.1, max_odds=1.3)
        assert "picks" in r


class TestPricedCorners:
    CORNERS = {"id": "166", "specifier": "total=9.5", "desc": "Total Corners",
               "outcomes": [{"id": "12", "odds": "1.85"}, {"id": "13", "odds": "1.85"}]}

    def test_lines_fitted_to_sportybets_price(self):
        import set_pieces
        sp = set_pieces.from_prices({"markets": [self.CORNERS]})
        assert sp["corners"]["over"]["9.5"] == pytest.approx(0.5, abs=0.005) and sp["corners"]["source"] == "sportybet"
        over = [sp["corners"]["over"][l] for l in ("7.5", "8.5", "9.5", "10.5", "11.5")]
        assert over == sorted(over, reverse=True) and "bookings" not in sp

    def test_mislabelled_market_is_ignored(self):
        import set_pieces
        assert set_pieces.from_prices({"markets": [{**self.CORNERS, "desc": "Total Goals"}]}) is None

    def test_internationals_get_corner_picks_from_prices(self, monkeypatch):
        p = pred(league="INT-WCQ")
        monkeypatch.setattr(main, "_get_redis", lambda: None)
        monkeypatch.setattr(main, "_sb_link_status", {"at": "x", "market_map": {"corners_ou": {"id": "166", "ok": True}}})
        monkeypatch.setattr(main, "_predictions_cache", [p])
        monkeypatch.setattr(main, "_sb_links", {f"Arsenal|Chelsea|{TOMORROW}": {"eventId": "sr:match:1", "markets": [self.CORNERS]}})
        body = {"target_odds": 1.25, "min_odds": 1.15, "max_odds": 1.35, "days": 3, "markets": ["corners_ou"],
                "bookable_only": True, "min_prob": 0.7}
        r = TestClient(main.app).post("/api/optimizer", json=body).json()
        assert r["picks"][0]["market"] == "corners_ou" and r["picks"][0]["code"] == "O75"
