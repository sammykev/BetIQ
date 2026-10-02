"""
Basketball markets and predictions (basketball_markets.py, basketball_data.py,
basketball_predictions.py): SportyBet's lines read into our codes, priced,
settled from the result, and turned into optimizer choices.
"""

from datetime import datetime, timedelta, timezone

import pytest

import basketball_data as bd
import basketball_markets as bmk
import basketball_model as bm
import basketball_predictions as bp
from tests.unit.test_basketball_model import simulate


def market(mid, outcomes, spec=None):
    return {"id": mid, "specifier": spec, "outcomes": [{"id": oid, "odds": str(o), "isActive": 1} for oid, o in outcomes]}


def event(home="T0", away="T1", hours=24, tournament="Sim · League", markets=None):
    ko = datetime.now(timezone.utc) + timedelta(hours=hours)
    return {"eventId": "sr:match:123", "homeTeamName": home, "awayTeamName": away, "_tournament": tournament,
            "homeTeamId": "sr:competitor:3515", "awayTeamId": "sr:competitor:3514",
            "estimateStartTime": int(ko.timestamp() * 1000), "markets": markets if markets is not None else [
                market("219", [("4", 1.45), ("5", 2.75)]),
                market("223", [("1714", 1.90), ("1715", 1.90)], "hcp=-5.5"),
                market("223", [("1714", 1.25), ("1715", 3.80)], "hcp=4.5"),
                market("225", [("12", 1.90), ("13", 1.90)], "total=160.5"),
                market("225", [("12", 1.20), ("13", 4.20)], "total=148.5"),
                market("227", [("12", 1.85), ("13", 1.95)], "total=83.5"),
                market("1", [("1", 1.55), ("2", 15.0), ("3", 2.90)]),
                market("68", [("12", 1.88), ("13", 1.92)], "total=80.5"),
                market("66", [("1714", 1.90), ("1715", 1.90)], "hcp=-2.5"),
                market("236", [("12", 1.85), ("13", 1.95)], "total=40.5|quarternr=3"),
                market("303", [("1714", 1.85), ("1715", 1.95)], "hcp=-1.5|quarternr=1"),
                market("220", [("74", 12.0), ("76", 1.02)]),
            ]}


class TestOffers:
    def test_codes_and_ids(self):
        offs = {(o["market"], o["code"]): o for o in bmk.offers(event())}
        assert offs[("bb_winner", "1")]["sb"] == {"eventId": "sr:match:123", "marketId": "219", "specifier": "",
                                                   "outcomeId": "4"}
        # The specifier is the home side's line; the away side's is the opposite
        assert ("bb_handicap", "H-5.5") in offs and ("bb_handicap", "A+5.5") in offs
        assert ("bb_handicap", "H+4.5") in offs and ("bb_handicap", "A-4.5") in offs
        assert offs[("bb_total", "O160.5")]["sb"]["specifier"] == "total=160.5"
        assert ("bb_q3_total", "U40.5") in offs and ("bb_q1_handicap", "H-1.5") in offs
        assert ("bb_h1_total", "O80.5") in offs and ("bb_overtime", "OT-N") in offs
        assert ("bb_1x2", "X") in offs

    def test_labels_and_names(self):
        by = {(o["market"], o["code"]): o for o in bmk.offers(event(home="Lakers", away="Celtics"))}
        assert bmk.label(by[("bb_handicap", "A+5.5")], "Lakers", "Celtics") == "Celtics +5.5"
        assert bmk.label(by[("bb_q3_total", "O40.5")], "Lakers", "Celtics") == "3rd quarter: Over 40.5 points"
        assert bmk.label(by[("bb_home_total", "U83.5")], "Lakers", "Celtics") == "Lakers under 83.5 points"
        assert bmk.label(by[("bb_1x2", "1")], "Lakers", "Celtics") == "Lakers win in regulation"
        assert bmk.label(by[("bb_winner", "1")], "Lakers", "Celtics") == "Lakers win"
        assert bmk.market_name("bb_q1_handicap") == "1st quarter Handicap"
        assert bmk.market_name("bb_total") == "Total Points"

    def test_the_markets_middle_lines(self):
        lines = bmk.main_lines(bmk.offers(event()))
        assert lines["handicap"] == (-5.5, 1.90, 1.90) and lines["total"] == (160.5, 1.90, 1.90)
        assert lines["winner"] == (1.45, 2.75)


class TestSettle:
    R = {"status": "finished", "final": [88, 79], "periods": [[21, 26], [22, 15], [21, 11], [24, 27]], "ot": False}

    @pytest.mark.parametrize("market, code, want", [
        ("bb_winner", "1", "won"), ("bb_winner", "2", "lost"),
        ("bb_handicap", "H-5.5", "won"), ("bb_handicap", "H-9.5", "lost"), ("bb_handicap", "A+9.5", "won"),
        ("bb_handicap", "H-9", "void"),
        ("bb_total", "O160.5", "won"), ("bb_total", "U160.5", "lost"),
        ("bb_home_total", "O85.5", "won"), ("bb_away_total", "U80.5", "won"),
        ("bb_1x2", "1", "won"), ("bb_overtime", "OT-N", "won"),
        ("bb_h1_1x2", "1", "won"), ("bb_h1_total", "O83.5", "won"),     # 43-41
        ("bb_h2_handicap", "A+0.5", "lost"), ("bb_h2_1x2", "1", "won"),  # 45-38
        ("bb_q1_1x2", "2", "won"), ("bb_q4_total", "O50.5", "won"), ("bb_q3_handicap", "H-9.5", "won"),
    ])
    def test_from_the_score(self, market, code, want):
        assert bmk.settle(market, code, self.R) == want

    def test_overtime_counts_where_the_market_says(self):
        ot = {"status": "finished", "final": [98, 95], "periods": [[20, 20], [20, 20], [22, 22], [23, 23]], "ot": True}
        assert bmk.settle("bb_winner", "1", ot) == "won"          # overtime included
        assert bmk.settle("bb_1x2", "X", ot) == "won"             # regulation: a draw
        assert bmk.settle("bb_overtime", "OT-Y", ot) == "won"
        assert bmk.settle("bb_total", "O190.5", ot) == "won"

    def test_not_finished_or_no_quarters(self):
        assert bmk.settle("bb_total", "O160.5", None) == "pending"
        assert bmk.settle("bb_q1_total", "O40.5", {"status": "finished", "final": [80, 70], "periods": None}) == "void"


class TestResults:
    def test_parsed_from_sportybet(self):
        ev = {"eventId": "sr:match:9", "homeTeamName": "Korfez Basket", "awayTeamName": "Esenler Erokspor",
              "setScore": "88:79", "gameScore": ["21:26", "22:15", "21:11", "24:27"],
              "regularTimeScore": ["21:26", "22:15", "21:11", "24:27"], "status": 4, "matchStatus": "Ended",
              "estimateStartTime": 1790611200000, "_tournament": "Turkiye · Super Lig"}
        g = bd.parse_result(ev)
        assert (g["hs"], g["as"], g["ot"], g["q"][0]) == (88, 79, False, [21, 26])
        ot = bd.parse_result({**ev, "setScore": "98:95", "gameScore": ["20:20", "20:20", "22:22", "23:23", "13:10"],
                              "regularTimeScore": ["20:20", "20:20", "22:22", "23:23"]})
        assert ot["ot"] is True and ot["q"][3] == [23, 23]
        assert bd.parse_result({**ev, "status": 1, "matchStatus": "Not started"}) is None
        assert bd.decode(bd.encode([g])) == [g]
        assert bd.result_for({"sr:match:9": g}, {"event_id": "sr:match:9"})["final"] == [88, 79]

    def test_the_days_to_collect(self):
        from datetime import date
        today = date(2026, 9, 29)
        days = bd.days_to_collect({"2026-09-27"}, today)
        assert days[:2] == ["2026-09-28", "2026-09-29"] and "2026-09-27" not in days
        assert len(days) == 2 + bd.BACKFILL_PER_RUN and days[2] == "2026-09-26"

    def test_friendlies_are_not_rated(self):
        games = [{"id": str(i), "t": t, "h": "A", "a": "B", "ko": 1790611200, "hs": 80, "as": 70, "q": None}
                 for i, t in enumerate(["International · Club Friendly Games", "Spain · Liga ACB"])]
        assert list(bd.games_by_league(games)) == ["Spain · Liga ACB"]
        assert bd.split_tournament("Spain · Liga ACB") == ("Spain", "Liga ACB") and bd.flag("Spain · Liga ACB") == "🇪🇸"
        assert bd.crest("sr:competitor:3515") == "https://img.sportradar.com/ls/crest/medium/3515.png"


class TestPredictions:
    @pytest.fixture(scope="class")
    def league(self):
        games, *_ = simulate()
        return bm.fit("Sim · League", games)

    def test_a_rated_match_blends_ours_with_the_market(self, league):
        p = bp.predict(event(), league)
        assert p["model"] == "blend" and p["rated"] and p["home_logo"].endswith("/3515.png")
        assert p["league_name"] == "League" and p["sportybet_event_id"] == "sr:match:123"
        by = {(x["market"], x["code"]): x for x in p["bb_markets"]}
        # The long handicap and low total are likelier than the middle lines
        assert by[("bb_handicap", "A+5.5")]["prob"] < 1 and by[("bb_total", "O148.5")]["prob"] > 0.6
        assert all(0.5 <= x["prob"] <= 0.985 or x["market"] == "bb_winner" for x in p["bb_markets"])
        assert p["p_home"] + p["p_away"] == pytest.approx(1.0)

    def test_an_unrated_league_uses_the_market(self):
        p = bp.predict(event(tournament="Nowhere · Cup"), None)
        assert p["model"] == "market" and not p["rated"]
        # Home -5.5 at evens: the market expects home by about 5.5
        assert p["exp_home_pts"] - p["exp_away_pts"] == pytest.approx(5.5, abs=0.6)

    def test_no_prices_no_prediction(self):
        assert bp.predict(event(markets=[]), None) is None

    def test_started_matches_are_left_out(self, league):
        assert bp.build([event(hours=-1), event(hours=5)], {"Sim · League": league})[0]["time"]
        assert len(bp.build([event(hours=-1)], {})) == 0

    def test_optimizer_choices_carry_sportybets_ids(self, league):
        p = bp.predict(event(), league)
        opts = bp.options(p, 0.6)
        assert opts and all(o.sb and o.sport == "basketball" and o.odds_source == "sportybet" for o in opts)
        assert all(o.prob >= 0.6 for o in opts)
        only_totals = bp.options(p, 0.5, {"bb_total"})
        assert only_totals and {o.market for o in only_totals} == {"bb_total"}


def test_a_basketball_ticket_settles_from_its_event():
    import tickets
    sel = {"home": "Korfez", "away": "Esenler", "date": "2026-09-27", "market": "bb_total", "marketName": "Total Points",
           "code": "O160.5", "label": "Over 160.5 points",
           "sb": {"eventId": "sr:match:9", "marketId": "225", "specifier": "total=160.5", "outcomeId": "12"}}
    t = tickets.new_ticket("BB1", [sel], [{"status": "booked", "odds": 1.9}], "optimizer", None, 1.9, "x")
    leg = t["legs"][0]
    assert (leg["sport"], leg["event_id"], leg["status"]) == ("basketball", "sr:match:9", "pending")
    game = {"id": "sr:match:9", "hs": 88, "as": 79, "q": [[21, 26], [22, 15], [21, 11], [24, 27]], "ot": False}
    assert tickets.settle(t, lambda l: bd.result_for({"sr:match:9": game}, l))
    assert t["status"] == "won" and leg["status"] == "won"


class TestServer:
    """The API: basketball from the priced SportyBet matches."""

    @pytest.fixture
    def priced(self, monkeypatch):
        import main
        games, *_ = simulate()
        lg = bm.fit("Sim · League", games)
        evs = [event(home=f"T{i}", away=f"T{i + 1}", hours=6 + i) for i in range(0, 10, 2)]
        for i, e in enumerate(evs):
            e["eventId"] = f"sr:match:{i}"
        preds = bp.build(evs, {"Sim · League": lg})
        monkeypatch.setattr(main, "_bb_predictions", preds)
        monkeypatch.setattr(main, "_get_redis", lambda: None)
        monkeypatch.setattr(main, "_sports_memory_cache", {})

        async def open_to_all(request, sport):
            return None
        monkeypatch.setattr(main, "_check_sport_access", open_to_all)
        return preds

    def test_the_list_is_slim_and_a_match_has_every_line(self, priced):
        import main
        from fastapi.testclient import TestClient
        c = TestClient(main.app)
        rows = c.get("/api/sports/basketball").json()
        assert len(rows) == 5 and "bb_markets" not in rows[0] and rows[0]["top_lines"]
        assert all(x["odds"] >= 1.15 for x in rows[0]["top_lines"])
        full = c.get("/api/basketball/match", params={"event": priced[0]["sportybet_event_id"]}).json()
        assert len(full["bb_markets"]) >= len(rows[0]["top_lines"])
        assert c.get("/api/basketball/match", params={"event": "sr:match:999"}).status_code == 404

    def test_the_optimizer_builds_basketball_slips(self, priced):
        import asyncio
        import main
        r = asyncio.run(main._optimize_request({"sport": "basketball", "min_odds": 1.5, "max_odds": 3.5,
                                                "min_prob": 0.6, "days": 3}))
        assert r.get("picks"), r
        assert all(p["sb"] and p["bookable"] and p["sport"] == "basketball" for p in r["picks"])
        only_totals = asyncio.run(main._optimize_request({"sport": "basketball", "min_odds": 1.2, "max_odds": 3,
                                                          "min_prob": 0.55, "days": 3, "bb_markets": ["bb_total"]}))
        assert {p["market"] for p in only_totals["picks"]} == {"bb_total"}
        none = asyncio.run(main._optimize_request({"sport": "basketball", "min_odds": 2, "max_odds": 3,
                                                   "min_prob": 0.95, "days": 3, "bb_markets": ["bb_quarters"]}))
        assert "rates 95%" in none["error"] and "markets you chose" in none["error"]
        with pytest.raises(Exception):
            asyncio.run(main._optimize_request({"sport": "cricket"}))

    def test_the_optimizer_respects_the_basketball_switch(self, priced, monkeypatch):
        import main
        from fastapi import HTTPException
        from fastapi.testclient import TestClient

        async def off(request, sport):
            raise HTTPException(status_code=404, detail="feature_off")
        monkeypatch.setattr(main, "_check_sport_access", off)
        r = TestClient(main.app).post("/api/optimizer", json={"sport": "basketball", "min_odds": 2, "max_odds": 3})
        assert (r.status_code, r.json()["detail"]) == (404, "feature_off")

    def test_the_optimizer_takes_several_sports(self, priced, monkeypatch):
        import asyncio
        import main
        from fastapi import HTTPException
        from fastapi.testclient import TestClient
        from tests.unit.test_optimizer import pred
        monkeypatch.setattr(main, "_predictions_cache", [pred(i, p_home=0.7, p_draw=0.2, p_away=0.1, odds_home=1.6,
                                                              odds_draw=4.0, odds_away=6.0) for i in range(4)])
        body = {"min_odds": 1.5, "max_odds": 30, "min_prob": 0.6, "days": 3, "no_live_check": True}
        both = asyncio.run(main._optimize_request({**body, "sports": ["football", "basketball"]}))
        alone = asyncio.run(main._optimize_request({**body, "sports": ["basketball"]}))
        assert both["matches_considered"] == alone["matches_considered"] + 4
        assert {p.get("sport", "football") for p in both["picks"]} <= {"football", "basketball"}
        # A list with "all" is every sport; anything else is refused
        assert main._optimizer_sports({"sports": ["tennis", "all"]}) == list(main.OPT_SPORTS)
        assert main._optimizer_sports({"sport": "table_tennis"}) == ["table_tennis"]
        assert main._optimizer_sports({}) == ["football"]
        for bad in ({"sports": ["cricket"]}, {"sports": "football"}):
            with pytest.raises(HTTPException):
                main._optimizer_sports(bad)

        # A sport switched off is left out of a mixed slip, and refused alone
        async def basketball_off(request, sport):
            if sport == "basketball":
                raise HTTPException(status_code=404, detail="feature_off")
        monkeypatch.setattr(main, "_check_sport_access", basketball_off)
        c = TestClient(main.app)
        mixed = c.post("/api/optimizer", json={**body, "sports": ["football", "basketball"]})
        assert mixed.status_code == 200 and mixed.json()["matches_considered"] == 4
        assert c.post("/api/optimizer", json={**body, "sports": ["basketball"]}).status_code == 404
