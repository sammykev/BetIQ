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
        assert 0.3 < under["our_prob"] < 0.55 and under["verdict"] == "risky" and under["chance_from"] == "model"
        assert under["implied"] == pytest.approx(1 / 1.9, abs=1e-3)
        assert under["suggestion"] and under["suggestion"]["prob"] >= 0.5
        # a match we don't predict still gets a chance: SportyBet's odds less their margin
        assert unknown["verdict"] == "not_modelled" and unknown["chance_from"] == "odds"
        assert unknown["chance"] == pytest.approx(code_check.ODDS_MARGIN / 1.5, abs=1e-3)
        assert "SportyBet's odds" in unknown["note"]
        assert r["original"]["win_chance"] == pytest.approx(under["chance"] * unknown["chance"], abs=1e-3)

    def test_three_tickets_at_a_half_a_quarter_and_an_eighth(self):
        share = {"shareCode": "T3", "outcomes": SHARE["outcomes"] + [
            {"eventId": "sr:match:3", "homeTeamName": "Leeds", "awayTeamName": "Everton", "estimateStartTime": KICKOFF_MS,
             "markets": [market("1", [("1", 2.1, "Home")], "", "1X2")]},
            {"eventId": "sr:match:4", "homeTeamName": "Fulham", "awayTeamName": "Brentford", "estimateStartTime": KICKOFF_MS,
             "markets": [market("18", [("12", 1.8, "Over 2.5")], "total=2.5", "Over/Under")]}]}
        preds = {"sr:match:1": pred(),
                 "sr:match:3": pred(home="Leeds", away="Everton", p_home=0.48, p_draw=0.27, p_away=0.25),
                 "sr:match:4": pred(home="Fulham", away="Brentford", p_home=0.4, p_draw=0.28, p_away=0.32, p_over25=0.55)}
        # Every other option priced by SportyBet (the tickets only take bookable prices)
        def priced(o):
            return o if o.odds_source == "sportybet" else None
        import optimizer
        orig = optimizer.candidates

        def with_prices(*a, **k):
            from dataclasses import replace
            return [replace(o, odds_source="sportybet") for o in orig(*a, **k)]
        code_check.candidates = with_prices
        try:
            r = code_check.analyse(sportybet.parse_share(share), lambda sel: preds.get(sel["eventId"]),
                                   lambda _: None, confirmed={}, adjust=priced)
        finally:
            code_check.candidates = orig
        t = r["tickets"]
        safe, cons, risky = t["safe"], t["conservative"], t["risky"]
        for ticket, least in ((safe, 0.5), (cons, 0.25), (risky, 0.125)):
            assert ticket and ticket["win_chance"] >= least - 1e-4 and ticket["target_chance"] == least
            assert all(p.get("sb") or p["market"] != "sportybet" for p in ticket["picks"])
            assert ticket["games"] + len(ticket["left_out"]) == 4
        assert safe["total_odds"] < cons["total_odds"] < risky["total_odds"]
        assert safe["win_chance"] > cons["win_chance"] > risky["win_chance"]
        # the game we don't predict is on the tickets at its odds' chance, booked by its ids
        assert any(p["odds_source"] == "odds_only" and p["sb"]["eventId"] == "sr:match:2"
                   for p in risky["picks"])

    def test_tickets_from_the_odds_alone(self):
        sels = [s for s in sportybet.parse_share(SHARE) if s["eventId"] == "sr:match:2"]
        r = code_check.analyse(sels, lambda _: None, lambda _: None, confirmed={})
        # 1.5 → 63%: on the safe ticket; nothing else to choose from
        assert r["tickets"]["safe"]["games"] == 1 and r["tickets"]["safe"]["from_odds"] == 1

    def test_a_basketball_leg_is_rated_by_its_sportybet_event(self):
        sb = {"eventId": "sr:match:7", "marketId": "18", "specifier": "total=160.5", "outcomeId": "12"}
        sel = {**sb, "home": "Lakers", "away": "Celtics", "start": KICKOFF_MS, "market": "Over/Under",
               "outcome": "Over 160.5", "odds": 1.5, "active": True}
        line = lambda code, prob, odds, out: {"market": "bb_total", "market_name": "Total points", "code": code,
                                              "label": code, "prob": prob, "odds": odds,
                                              "sb": {**sb, "outcomeId": out}}
        bb = {"sport": "basketball", "home": "Lakers", "away": "Celtics", "date": TOMORROW, "time": "15:00",
              "bb_markets": [line("O160.5", 0.66, 1.5, "12"), line("U160.5", 0.34, 2.5, "13")]}
        r = code_check.analyse([sel], lambda _: None, lambda _: None, confirmed={},
                               find_other=lambda s: bb if s["eventId"] == "sr:match:7" else None)
        leg = r["legs"][0]
        assert leg["chance_from"] == "model" and leg["our_prob"] is not None and leg["matched"]

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
        import routers.optimizer as ro
        monkeypatch.setattr(ro, "HELD_OUT_MARKETS", set())   # corners are held out of slips (Nov 2026)
        monkeypatch.setattr(main, "_get_redis", lambda: None)
        monkeypatch.setattr(main, "_sb_link_status", {"at": "x", "market_map": {"corners_ou": {"id": "166", "ok": True}}})
        monkeypatch.setattr(main, "_predictions_cache", [p])
        monkeypatch.setattr(main, "_sb_links", {f"Arsenal|Chelsea|{TOMORROW}": {"eventId": "sr:match:1", "markets": [self.CORNERS]}})
        body = {"target_odds": 1.25, "min_odds": 1.15, "max_odds": 1.35, "days": 3, "markets": ["corners_ou"],
                "bookable_only": True, "min_prob": 0.7}
        r = TestClient(main.app).post("/api/optimizer", json=body).json()
        assert r["picks"][0]["market"] == "corners_ou" and r["picks"][0]["code"] == "O75"


class _Mem:
    def __init__(self):
        self.d = {}

    def get(self, k):
        return self.d.get(k)

    def set(self, k, v, ex=None):
        self.d[k] = v


def test_refined_tickets_are_kept_settled_and_scored_by_tier():
    pick = {"home": "Arsenal", "away": "Chelsea", "date": TOMORROW, "time": "15:00", "league": "PL",
            "market": "1x2", "market_name": "1X2", "code": "1", "label": "Arsenal", "prob": 0.6, "odds": 1.6,
            "odds_source": "sportybet"}
    report = {"tickets": {"safe": {"picks": [pick], "total_odds": 1.6, "win_chance": 0.6},
                          "conservative": None, "risky": None}}
    r = _Mem()
    assert code_check.record_tickets(r, "ABC", report, "2026-11-18T10:00:00+00:00") == 1
    # checked again: replaced, not doubled
    assert code_check.record_tickets(r, "ABC", report, "2026-11-18T11:00:00+00:00") == 1
    assert code_check.record(r)["safe"]["open"] == 1
    won = {"status": "finished", "hg": 2, "ag": 0}
    assert code_check.settle_record(r, lambda leg: won) == 1
    rec = code_check.record(r)["safe"]
    assert rec["landed"] == 1 and rec["lost"] == 0 and rec["said"] == 0.6 and rec["aim"] == 0.5


def test_zero_zero_corners_are_no_data():
    import tickets
    res = {"status": "finished", "hg": 1, "ag": 0, "corners": [0, 0]}
    assert tickets.grade_leg("corners_ou", "U85", res) == "pending"
    assert tickets.grade_leg("corners_ou", "U85", {**res, "corners": [3, 4]}) == "won"


def test_a_game_with_hundreds_of_lines():
    import optimizer
    groups = [[optimizer.Option(f"H{i}", "A", TOMORROW, "", "L", "bb_total", "T", f"c{j}", "x",
                                0.3 + 0.002 * j, round(1 / (0.3 + 0.002 * j), 2), "sportybet") for j in range(300)]
              for i in range(3)]
    res = optimizer.optimize_safe(groups, 0.5, 1e6, 3, min_edge=None, most_games=True)
    assert res and res["win_chance"] >= 0.5


def _refine(settings):
    import optimizer
    from dataclasses import replace
    share = {"shareCode": "T3", "outcomes": SHARE["outcomes"] + [
        {"eventId": "sr:match:3", "homeTeamName": "Leeds", "awayTeamName": "Everton", "estimateStartTime": KICKOFF_MS,
         "markets": [market("1", [("1", 2.1, "Home")], "", "1X2")]}]}
    preds = {"sr:match:1": pred(), "sr:match:3": pred(home="Leeds", away="Everton", p_home=0.48, p_draw=0.27, p_away=0.25)}
    orig = code_check.candidates
    code_check.candidates = lambda *a, **k: [replace(o, odds_source="sportybet") for o in orig(*a, **k)]
    try:
        return code_check.analyse(sportybet.parse_share(share), lambda sel: preds.get(sel["eventId"]),
                                  lambda _: None, confirmed={}, settings=settings)
    finally:
        code_check.candidates = orig


def test_refine_settings():
    s = code_check.settings_of({"strictness": "strict", "min_odds": "9", "max_odds": 3, "keep_all": True,
                                "max_leg_odds": "x", "main_markets": True, "keep_strong": 2})
    assert s["strictness"] == "strict" and (s["min_odds"], s["max_odds"]) == (3, 9)
    assert s["keep_all"] and s["main_markets"] and s["max_leg_odds"] is None and s["keep_strong"] is None
    assert code_check.settings_of(None) == code_check.DEFAULTS

    base = _refine(None)["tickets"]
    strict = _refine({"strictness": "strict"})["tickets"]
    assert strict["safe"]["win_chance"] >= 0.6 - 1e-4 and strict["safe"]["target_chance"] == 0.6
    # the most odds a ticket pays
    capped = _refine({"max_odds": 2.0})["tickets"]
    assert all(t is None or t["total_odds"] <= 2.0 for t in capped.values())
    # the most odds a game
    legs = _refine({"max_leg_odds": 1.3})["tickets"]
    assert all(p["odds"] <= 1.3 for t in legs.values() if t for p in t["picks"])
    # a minimum the safe ticket can't reach is explained, not faked
    r = _refine({"min_odds": 50})
    assert r["tickets"]["safe"] is None and "minimum" in r["ticket_notes"]["safe"]
    # main markets only
    main = _refine({"main_markets": True})["tickets"]
    assert all(p["market"] in code_check.MAIN_MARKETS or p["market"] == "sportybet" or p.get("sb")
               for t in main.values() if t for p in t["picks"])
    assert base["safe"]
