"""
Shots and shots on target (shots.py): the team-rating model on HS/AS/HST/AST,
its markets, and the booking / settling of shots picks.
"""

import numpy as np
import pandas as pd

import booking_slip
import optimizer
import set_pieces
import shots
import tickets


def season(n_rounds=40, seed=1):
    """A league where Attackers take far more shots than Defenders."""
    rng = np.random.default_rng(seed)
    teams = ["Attackers", "Middlers", "Defenders", "Others"]
    power = {"Attackers": 1.5, "Middlers": 1.0, "Defenders": 0.6, "Others": 1.0}
    rows, day = [], pd.Timestamp("2022-08-01")
    for r in range(n_rounds):
        for i, h in enumerate(teams):
            for a in teams[i + 1:] if r % 2 else teams[:i]:
                hs, as_ = rng.poisson(13 * power[h] / power[a] ** 0.3), rng.poisson(11 * power[a] / power[h] ** 0.3)
                rows.append({"Date": day, "HomeTeam": h, "AwayTeam": a, "league": "L",
                             "HS": hs, "AS": as_, "HST": rng.binomial(hs, 0.35), "AST": rng.binomial(as_, 0.35)})
        day += pd.Timedelta(days=4)
    return pd.DataFrame(rows)


class TestModel:
    def test_counts_need_all_four_and_sane(self):
        assert shots._counts({"HS": 12, "AS": 9, "HST": 5, "AST": 3}) == {"shots": (12, 9), "sot": (5, 3)}
        assert shots._counts({"HS": 12, "AS": 9, "HST": 5}) is None
        assert shots._counts({"HS": 4, "AS": 9, "HST": 5, "AST": 3}) is None   # more on target than shots

    def test_learns_the_attacking_team(self):
        model = shots.ShotModel.fit(season())
        m = model.markets("Attackers", "Defenders", "L")
        r = model.markets("Defenders", "Attackers", "L")
        assert set(m) == {"shots", "sot", "shots_home", "shots_away", "sot_home", "sot_away"}
        assert m["shots_home"]["mean"] > r["shots_home"]["mean"] + 4
        assert m["shots_home"]["over"]["12.5"] > r["shots_home"]["over"]["12.5"]
        assert "corners_1x2" not in m and "shots_1x2" not in m

    def test_corners_model_unchanged_in_shape(self):
        assert set_pieces.SetPieceModel.all_stats() == ("corners", "bookings", "corners_home", "corners_away")
        assert shots.ShotModel.all_stats() == ("shots", "sot", "shots_home", "shots_away", "sot_home", "sot_away")

    def test_check_beats_the_league_average(self):
        got = shots.check(season(200), "2023-12-01")
        assert got["shots_home"]["model"] < got["shots_home"]["baseline"]
        assert set(got["shots_home"]) >= {"model", "baseline", "matches", "use"}

    def test_rescale(self):
        assert set_pieces.rescale(0.9, 1.0) == 0.9
        assert 0.5 < set_pieces.rescale(0.9, 0.6) < 0.9
        assert abs(set_pieces.rescale(0.5, 0.6) - 0.5) < 1e-9


class TestMarkets:
    def test_optimizer_picks_read_the_shot_lines(self):
        pred = {"set_pieces": {"sot_home": {"over": {"3.5": 0.7}}, "shots": {"over": {"24.5": 0.4}}}}
        picks = {(m, c): fn for m, _, c, _, fn in optimizer._PICKS}
        assert picks[("home_sot_ou", "O35")](pred) == 0.7
        assert abs(picks[("shots_ou", "U245")](pred) - 0.6) < 1e-9
        assert picks[("away_sot_ou", "O35")](pred) is None

    def test_booking_ids_come_from_sportybet_labels(self):
        assert booking_slip.sportybet_ids("sot_ou", "O85") == {"marketId": "900393", "specifier": "total=8.5",
                                                                "outcomeId": "12"}
        details = {"home": "Arsenal", "away": "Chelsea", "markets": {
            "901": {"label": "Total shots on target", "outcomes": {"12": "Over", "13": "Under"}},
            "902": {"label": "Arsenal total shots", "outcomes": {"12": "Over", "13": "Under"}},
            "903": {"label": "Player shots on target", "outcomes": {"12": "Over", "13": "Under"}},
            "904": {"label": "1st half - total shots", "outcomes": {"12": "Over", "13": "Under"}}}}
        got = booking_slip.resolve_markets(details)
        assert got["sot_ou"] == {"id": "901", "label": "Total shots on target", "ok": True, "why": None}
        assert got["home_shots_ou"]["id"] == "902" and got["home_shots_ou"]["ok"]
        assert not got["shots_ou"]["ok"] and not got["away_sot_ou"]["ok"]

    def test_tickets_settle_shots_and_two_digit_lines(self):
        res = {"status": "finished", "hg": 2, "ag": 1, "shots": [15, 11], "sot": [6, 2], "corners": [7, 4]}
        assert tickets.grade_leg("shots_ou", "O245", res) == "won"
        assert tickets.grade_leg("shots_ou", "O265", res) == "lost"
        assert tickets.grade_leg("home_sot_ou", "O55", res) == "won"
        assert tickets.grade_leg("away_sot_ou", "O25", res) == "lost"
        assert tickets.grade_leg("corners_ou", "U105", res) == "lost"      # 11 corners
        assert tickets.grade_leg("sot_ou", "O85", {**res, "sot": None}) == "pending"


class TestSportyBetShots:
    """SportyBet's own shots markets (found on Slovenia v Scotland, Nations League)."""
    LABELS = {"900394": "Shots Over/Under", "900393": "Shots on Target Over/Under",
              "900552": "Home Team Shots Over/Under", "900553": "Away Team Shots Over/Under",
              "900546": "Home Team Shots on Target Over/Under", "900547": "Away Team Shots on Target Over/Under",
              "800054": "Match Shots Outside Box", "900318": "Shots on Target 1X2", "830230": "Most Shots",
              "19": "Slovenia Over/Under", "23": "Home Team Goals"}

    def test_every_shots_market_confirms_by_its_name(self):
        details = {"home": "Slovenia", "away": "Scotland",
                   "markets": {k: {"label": v, "outcomes": {"12": "Over 4.5", "13": "Under 4.5"}} for k, v in self.LABELS.items()}}
        got = booking_slip.resolve_markets(details)
        assert {k: got[k]["id"] for k in booking_slip.LISTED_ONLY} == {
            "shots_ou": "900394", "sot_ou": "900393", "home_shots_ou": "900552", "away_shots_ou": "900553",
            "home_sot_ou": "900546", "away_sot_ou": "900547"}
        assert all(got[k]["ok"] for k in booking_slip.LISTED_ONLY)
        assert got["home_goals_ou"]["id"] == "19"      # not the team shots markets

    def test_priced_only_when_the_listing_has_that_line(self):
        pred = {"home": "Slovenia", "away": "Scotland", "date": "2026-09-26", "p_home": 0.3, "p_draw": 0.3,
                "p_away": 0.4, "p_over15": 0.7, "p_over25": 0.45,
                "set_pieces": {"sot_home": {"over": {"3.5": 0.3}}, "sot": {"over": {"8.5": 0.5}}}}
        event = {"markets": [{"id": "900546", "specifier": "total=3.5", "desc": "Home Team Shots on Target Over/Under",
                              "outcomes": [{"id": "12", "odds": "2.60"}, {"id": "13", "odds": "1.45"}]}]}
        opts = {(o.market, o.code): o for o in optimizer.candidates(pred, event, 0.0)}
        assert (opts[("home_sot_ou", "U35")].odds, opts[("home_sot_ou", "U35")].odds_source) == (1.45, "sportybet")
        assert opts[("sot_ou", "U85")].odds_source == "estimated"     # SportyBet has no 8.5 line here
        import sportybet
        slim = sportybet.slim_event({"eventId": "sr:match:9", **event})
        assert slim["markets"][0]["desc"] == "Home Team Shots on Target Over/Under"
