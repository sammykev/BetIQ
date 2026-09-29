"""
Basketball history (bb_history.py) and how the walk-forward check uses it:
ESPN and EuroLeague games read into SportyBet's format, teams renamed to
SportyBet's, no double counting where SportyBet's results begin, and the
league constants the check measures reaching the server's ratings.
"""

from datetime import date

import pytest

import backtest_basketball as bt
import basketball_data as bd
import bb_history as bh
from tests.unit.test_basketball_model import simulate


def espn_event(home="Los Angeles Lakers", away="Boston Celtics", hs=110, as_=104, period=4, lines=True, done=True):
    def comp(side, name, score, ls):
        return {"homeAway": side, "score": str(score), "team": {"displayName": name},
                **({"linescores": [{"value": v} for v in ls]} if lines else {})}
    return {"id": "401", "date": "2019-01-15T03:30Z", "competitions": [{
        "status": {"period": period, "type": {"completed": done}}, "neutralSite": False,
        "competitors": [comp("home", home, hs, [30, 25, 28, 27] + ([10] if period > 4 else [])),
                        comp("away", away, as_, [26, 26, 26, 26] + ([4] if period > 4 else []))]}]}


class TestSources:
    def test_espn_game(self):
        g = bh.parse_espn(espn_event(), "USA · NBA", 4)
        assert (g["t"], g["h"], g["a"], g["hs"], g["as"], g["ot"]) == ("USA · NBA", "Los Angeles Lakers", "Boston Celtics", 110, 104, False)
        assert g["q"] == [[30, 26], [25, 26], [28, 26], [27, 26]]
        ot = bh.parse_espn(espn_event(period=5), "USA · NBA", 4)
        assert ot["ot"] and len(ot["q"]) == 4                   # regulation quarters only
        # College men play halves: no quarters, overtime after the 2nd
        halves = bh.parse_espn(espn_event(period=3, lines=False), "USA · NCAA, Regular Season", 2)
        assert halves["q"] is None and halves["ot"]
        assert bh.parse_espn(espn_event(done=False), "USA · NBA", 4) is None

    def test_euroleague_game(self):
        g = {"played": True, "gameCode": 12, "date": "2019-10-03T18:00:00", "isNeutralVenue": False,
             "local": {"club": {"name": "Real Madrid"}, "score": 85,
                       "partials": {"partials1": 20, "partials2": 22, "partials3": 21, "partials4": 22, "extraPeriods": None}},
             "road": {"club": {"name": "FC Barcelona"}, "score": 80,
                      "partials": {"partials1": 18, "partials2": 22, "partials3": 20, "partials4": 20}}}
        x = bh.parse_euroleague(g, "International · Euroleague")
        assert x["hs"] == 85 and x["q"][0] == [20, 18] and not x["ot"]
        assert bh.parse_euroleague({**g, "played": False}, "x") is None

    def test_seasons_and_days(self):
        assert bh.season_of(date(2019, 10, 1)) == 2019 and bh.season_of(date(2020, 3, 1)) == 2019
        days = bh.espn_days((11, 12), date(2026, 9, 29))
        assert days[0] == date(2016, 11, 1) and all(d.month in (11, 12) for d in days)
        assert len({bh.season_of(d) for d in days}) == 10


class TestJoin:
    def test_teams_renamed_to_sportybet(self):
        games = [{"h": "Los Angeles Lakers", "a": "Boston Celtics"}, {"h": "Seattle SuperSonics", "a": "Boston Celtics"}]
        bh.map_teams(games, ["LA Lakers", "Boston Celtics"])
        assert games[0]["a"] == "Boston Celtics"
        assert games[1]["h"] == "Seattle SuperSonics"          # no clear match: kept

    def test_no_double_counting(self):
        sb = [{"t": "L", "ko": 1000, "id": "sb1"}, {"t": "L", "ko": 2000, "id": "sb2"}]
        hist = [{"t": "L", "ko": 500, "id": "h1"}, {"t": "L", "ko": 1500, "id": "h2"}, {"t": "M", "ko": 1500, "id": "h3"}]
        ids = {g["id"] for g in bh.combine(sb, hist)}
        assert ids == {"sb1", "sb2", "h1", "h3"}


class TestConstants:
    def test_measured_out_of_sample_and_used_by_the_server(self):
        games = simulate(seasons=3)[0]
        got = bt.league_backtest("Sim · League", games)
        c = got["constants"]
        assert c["n"] >= bt.MIN_CONSTANT_GAMES
        assert sum(c["margin_shares"][1:]) == pytest.approx(1.0, abs=0.02)     # relative to the whole game
        assert sum(c["q_shares"]) == pytest.approx(1.0, abs=1e-3)
        assert 1.0 <= c["tie_factor"] <= 3.0
        results = [{"id": str(i), "t": "Sim · League", "h": g.home, "a": g.away, "hs": g.hs, "as": g.as_,
                    "ko": int(date.fromisoformat(g.date).strftime("%s")), "q": [list(p) for p in g.periods], "ot": g.ot}
                   for i, g in enumerate(games)]
        lg = bd.fit_all(results, backtest={"leagues": {"Sim · League": got}})["Sim · League"]
        assert list(lg.margin_shares) == c["margin_shares"] and lg.tie_factor == c["tie_factor"]
        # Too few tested games: the league's own measurements stay
        few = {**got, "constants": {**c, "n": 50}}
        lg2 = bd.fit_all(results, backtest={"leagues": {"Sim · League": few}})["Sim · League"]
        assert list(lg2.margin_shares) != c["margin_shares"]

    def test_relative_shares(self):
        rows = [(em, [0.6 * em, 0.3 * em, 0.3 * em, 0.3 * em, 0.3 * em], 1.0) for em in (-10, -4, 3, 8, 12)]
        shares = bt.relative_shares(rows)
        assert shares[1:] == pytest.approx([0.25] * 4) and shares[0] == pytest.approx(0.5)
