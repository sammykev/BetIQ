"""
Player props priced against SportyBet's own lines (props_pricing.py), in
the formats the probe saw on its match pages.
"""

import pytest

import player_props as pp
import props_pricing as pr


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
        lines = {x["code"].split("|")[1] + ":" + x["player"]: x for x in pr.bb_price(EVENT, PLAYERS, {})}
        nine = lines["9+:T.J. Warren"]
        assert nine["prob"] > 0.85 and nine["expected"] == pytest.approx(16, abs=1.5) and nine["sb"]["marketId"] == "768"
        assert "25+:T.J. Warren" not in lines            # well under 50%: left out
        assert lines["O1.5:Frank Ntilikina"]["prob"] > 0.7
        assert not any("Nobody" in k for k in lines)      # no box scores, no price
        assert nine["label"] == "T.J. Warren 9+ points"

    def test_a_faster_game_raises_the_count(self):
        slow = {x["code"]: x["prob"] for x in pr.bb_price(EVENT, PLAYERS, {"Paris Basketball": 0.9})}
        fast = {x["code"]: x["prob"] for x in pr.bb_price(EVENT, PLAYERS, {"Paris Basketball": 1.1})}
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
        got = {x["player"]: x for x in pr.scorer_price(FB_EVENT, FB_PLAYERS, {"Arsenal": usual})}
        assert 0.25 < got["Bukayo Saka"]["prob"] < 0.5 and got["Ben White"]["prob"] < 0.12
        more = {x["player"]: x for x in pr.scorer_price(FB_EVENT, FB_PLAYERS, {"Arsenal": usual * 1.4})}
        assert more["Bukayo Saka"]["prob"] > got["Bukayo Saka"]["prob"]
        assert got["Bukayo Saka"]["label"] == "Bukayo Saka to score"

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
