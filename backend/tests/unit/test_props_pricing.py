"""
Player props priced against SportyBet's own lines (props_pricing.py), in
the formats the probe saw on its match pages.
"""

from datetime import date

import pytest

import player_props as pp
import props_pricing as pr

FRESH = date(2026, 2, 1)     # a week or two after the rows' last game


def bb_rows(pts, reb, ast, tpm, mins=30, n=20, team="Paris Basketball"):
    return [[f"2026-0{1 + i // 28}-{1 + i % 28:02d}", team, "X", True, mins, pts, reb, ast, tpm, True] for i in range(n)]


PLAYERS = {pp.name_key("T.J. Warren"): {"name": "T.J. Warren", "team": "Paris Basketball", "games": bb_rows(16, 4, 2, 2)},
           pp.name_key("Frank Ntilikina"): {"name": "Frank Ntilikina", "team": "Paris Basketball", "games": bb_rows(9, 3, 4, 1, 24)}}

EVENT = {"eventId": "sr:match:77", "markets": [
    {"id": "921", "desc": "Warren, T.J. total points (incl. overtime)", "specifier": "total=14.5|player=sr:player:607880",
     "outcomes": [{"id": "12", "desc": "Over 14.5", "odds": "1.97"}, {"id": "13", "desc": "Under 14.5", "odds": "1.74"}]},
    {"id": "768", "desc": "Player points (incl. overtime)", "specifier": "variant=pre:playerprops:73262972:607880",
     "outcomes": [{"id": "pre:playerprops:73262972:607880:9", "desc": "Warren, T.J. 9+", "odds": "1.10"},
                  {"id": "pre:playerprops:73262972:607880:25", "desc": "Warren, T.J. 25+", "odds": "9.0"}]},
    {"id": "923", "desc": "Ntilikina, Frank total rebounds (incl. overtime)", "specifier": "total=1.5|player=sr:player:922446",
     "outcomes": [{"id": "12", "desc": "Over 1.5", "odds": "1.30"}, {"id": "13", "desc": "Under 1.5", "odds": "3.2"}]},
    {"id": "921", "desc": "Nobody, Known total points (incl. overtime)", "specifier": "total=10.5|player=sr:player:1",
     "outcomes": [{"id": "12", "desc": "Over 10.5", "odds": "1.9"}]},
    {"id": "225", "desc": "Over/Under", "specifier": "total=160.5", "outcomes": [{"id": "12", "odds": "1.9"}]},
]}


class TestBasketball:
    def test_lines_read_from_both_formats(self):
        offs = pr.bb_offers(EVENT)
        warren = [o for o in offs if o["player"] == "Warren, T.J."]
        assert {o["code"] for o in warren} == {"O14.5", "U14.5", "9+", "25+"}
        plus = next(o for o in warren if o["code"] == "9+")
        assert plus["line"] == 8.5 and plus["sb"]["outcomeId"] == "pre:playerprops:73262972:607880:9"
        assert all(o["stat"] in ("pts", "reb") for o in offs)       # not the game total

    def test_priced_with_his_games(self):
        lines = {x["code"].split("|")[1] + ":" + x["player"]: x for x in pr.bb_price(EVENT, PLAYERS, {}, today=FRESH)}
        nine = lines["9+:T.J. Warren"]
        # His 16 a game, pulled towards SportyBet's ~14 (its even line is 14.5, over at 1.97)
        assert nine["prob"] > 0.8 and 14 < nine["expected"] < 16 and nine["sb"]["marketId"] == "768"
        assert "25+:T.J. Warren" not in lines            # well under 50%: left out
        assert lines["O1.5:Frank Ntilikina"]["prob"] > 0.65
        assert not any("Nobody" in k for k in lines)      # no box scores, no price
        assert nine["label"] == "T.J. Warren 9+ points"

    def test_old_box_scores_or_a_new_team_defer_to_sportybets_line(self):
        def expected(**kw):
            return next(x["expected"] for x in pr.bb_price(EVENT, PLAYERS, {}, **kw) if x["code"].endswith("|9+"))
        fresh = expected(today=FRESH)
        stale = expected(today=date(2026, 9, 29))                                  # last season's games
        moved = expected(today=FRESH, teams=("Real Madrid", "Olympiacos"))         # he's on neither team
        assert stale < fresh and moved < fresh
        market = pr.market_means(pr.bb_offers(EVENT), pr.DEFAULT_DISPERSION)[(pp.name_key("Warren, T.J."), "pts")]
        assert stale == pytest.approx(market, abs=0.3)            # ~95% SportyBet's

    def test_freshness(self):
        assert pr.freshness("2026-01-20", date(2026, 2, 1)) == 1.0
        assert pr.freshness("2025-05-01", date(2026, 1, 1)) == pr.STALE_FLOOR
        assert pr.STALE_FLOOR < pr.freshness("2026-01-01", date(2026, 3, 15)) < 1.0

    def test_market_means_take_the_margin_out(self):
        means = pr.market_means(pr.bb_offers(EVENT), pr.DEFAULT_DISPERSION)
        assert means[(pp.name_key("Warren, T.J."), "pts")] == pytest.approx(14.3, abs=0.8)

    def test_a_faster_game_raises_the_count(self):
        slow = {x["code"]: x["prob"] for x in pr.bb_price(EVENT, PLAYERS, {"Paris Basketball": 0.9}, today=FRESH)}
        fast = {x["code"]: x["prob"] for x in pr.bb_price(EVENT, PLAYERS, {"Paris Basketball": 1.1}, today=FRESH)}
        k = pp.name_key("T.J. Warren") + "|9+"
        assert fast[k] > slow[k]


def fb_rows(n, team="Arsenal", mins=85, goals_every=3, xg=0.45, pens=0):
    return [[f"2026-0{1 + i // 28}-{1 + i % 28:02d}", team, "X", True, mins, 1.0 if i % goals_every == 0 else 0.0,
             3.0, xg, 0.0, True, pens] for i in range(n)]


FB_PLAYERS = {pp.name_key("Bukayo Saka"): {"name": "Bukayo Saka", "team": "Arsenal", "games": fb_rows(25)},
              pp.name_key("Ben White"): {"name": "Ben White", "team": "Arsenal", "games": fb_rows(25, goals_every=40, xg=0.04)}}
FB_EVENT = {"eventId": "sr:match:5", "markets": [{"id": "40", "desc": "Anytime Goalscorer", "specifier": "type=prematch", "outcomes": [
    {"id": "1716", "desc": "No Goal", "odds": "12.0"},
    {"id": "sr:player:1", "desc": "Saka, Bukayo (Arsenal)", "odds": "2.60"},
    {"id": "sr:player:2", "desc": "White, Ben (Arsenal)", "odds": "15.0"}]}]}


class TestScorers:
    def test_offers(self):
        offs = pr.scorer_offers(FB_EVENT)
        assert [(o["player"], o["team"]) for o in offs] == [("Saka, Bukayo", "Arsenal"), ("White, Ben", "Arsenal")]
        assert offs[0]["sb"] == {"eventId": "sr:match:5", "marketId": "40", "specifier": "type=prematch", "outcomeId": "sr:player:1"}

    def test_a_scorer_is_likelier_than_a_defender_and_follows_the_team(self):
        usual = pr.team_xg_per_game(FB_PLAYERS, "Arsenal")
        got = {x["player"]: x for x in pr.scorer_price(FB_EVENT, FB_PLAYERS, {"Arsenal": usual}, today=FRESH)}
        assert 0.25 < got["Bukayo Saka"]["prob"] < 0.5 and got["Ben White"]["prob"] < 0.12
        more = {x["player"]: x for x in pr.scorer_price(FB_EVENT, FB_PLAYERS, {"Arsenal": usual * 1.4}, today=FRESH)}
        assert more["Bukayo Saka"]["prob"] > got["Bukayo Saka"]["prob"]
        assert got["Bukayo Saka"]["label"] == "Bukayo Saka to score"

    def test_blended_with_sportybets_price(self):
        usual = pr.team_xg_per_game(FB_PLAYERS, "Arsenal")
        saka = next(x for x in pr.scorer_price(FB_EVENT, FB_PLAYERS, {"Arsenal": usual * 1.4}, today=FRESH)
                    if x["player"] == "Bukayo Saka")
        assert saka["weight"] == pr.SCORER_WEIGHT
        lam = saka["weight"] * saka["ours"] + (1 - saka["weight"]) * saka["market"]
        assert saka["prob"] == pytest.approx(1 - 2.718281828 ** -lam, abs=1e-3)
        # 2.60 with ~20% kept: SportyBet's λ ≈ 0.37
        assert saka["market"] == pytest.approx(0.368, abs=0.01)
        stale = next(x for x in pr.scorer_price(FB_EVENT, FB_PLAYERS, {"Arsenal": usual * 1.4}, today=date(2026, 9, 29))
                     if x["player"] == "Bukayo Saka")
        assert stale["weight"] < saka["weight"]

    def test_penalty_takers_get_their_penalties(self):
        taker = {pp.name_key("Bukayo Saka"): {"name": "Bukayo Saka", "team": "Arsenal", "games": fb_rows(25, pens=1)}}
        plain = pr.scorer_lambda(FB_PLAYERS[pp.name_key("Bukayo Saka")], None, None)[0]
        with_pens = pr.scorer_lambda(taker[pp.name_key("Bukayo Saka")], None, None)[0]
        # Same total xG, but a share of it from penalties he takes: his open play is lower,
        # his penalties are counted as the team's (not every game has one)
        assert with_pens != plain

    def test_team_goals_from_the_chance_of_scoring(self):
        assert pr.team_goals(1 - 2.718281828 ** -1.5) == pytest.approx(1.5, abs=1e-6)
        assert pr.team_goals(None) is None


class TestSettle:
    def test_player_lines(self):
        import tickets
        got = {"status": "finished", "value": 16.0}
        assert tickets.grade_leg("bb_player_pts", "t j warren|O14.5", got) == "won"
        assert tickets.grade_leg("bb_player_pts", "t j warren|U14.5", got) == "lost"
        assert tickets.grade_leg("bb_player_pts", "t j warren|16+", got) == "won"
        assert tickets.grade_leg("bb_player_pts", "t j warren|17+", got) == "lost"
        assert tickets.grade_leg("bb_player_reb", "x|O3.5", {"status": "void"}) == "void"
        assert tickets.grade_leg("bb_player_ast", "x|O3.5", None) == "pending"
        assert tickets.grade_leg("anytime_scorer", "bukayo saka", {"status": "finished", "value": 1.0}) == "won"
        assert tickets.grade_leg("anytime_scorer", "bukayo saka", {"status": "finished", "value": 0.0}) == "lost"

    def test_result_from_the_box_scores(self, monkeypatch):
        import main
        monkeypatch.setattr(main, "_props_players", {("bb", "Euroleague"): PLAYERS, ("fb", "Premier League"): FB_PLAYERS})
        day = PLAYERS[pp.name_key("T.J. Warren")]["games"][-1][0]
        leg = {"market": "bb_player_pts", "code": pp.name_key("T.J. Warren") + "|O14.5", "date": day}
        assert main._props_result_for(leg) == {"status": "finished", "value": 16.0}
        # His team played that day, he didn't: void
        other = {"market": "bb_player_pts", "code": "someone else|O4.5", "date": day}
        PLAYERS["someone else"] = {"name": "Someone Else", "team": "Paris Basketball", "games": bb_rows(5, 1, 1, 0)[:3]}
        assert main._props_result_for(other) == {"status": "void"}
        del PLAYERS["someone else"]


def test_prices_go_through_the_checks_map():
    plain = {x["code"]: x["prob"] for x in pr.bb_price(EVENT, PLAYERS, {}, today=FRESH)}
    cal = {"pts": [[0.75, 0.74], [0.85, 0.78], [0.97, 0.92]]}
    hot = {x["code"]: x["prob"] for x in pr.bb_price(EVENT, PLAYERS, {}, today=FRESH, calibration=cal)}
    k = pp.name_key("T.J. Warren") + "|9+"
    assert hot[k] < plain[k]
    reb = pp.name_key("Frank Ntilikina") + "|O1.5"
    assert hot[reb] == plain[reb]                     # no map for rebounds: unchanged
