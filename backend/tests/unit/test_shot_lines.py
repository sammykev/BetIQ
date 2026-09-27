"""
SportyBet's own shot lines (shots.from_prices / blend / blend_report): the
fallback for national teams we have too little data on, and a mix with ours
only where settled matches showed it predicts better.
"""

import random

import pytest

import main
import matchday
import shots


def event(home_sot=(4.5, 1.80, 1.95), sot=None):
    markets = [{"id": "900546", "specifier": f"total={home_sot[0]}", "desc": "Home Team Shots on Target Over/Under",
                "outcomes": [{"id": "12", "odds": str(home_sot[1])}, {"id": "13", "odds": str(home_sot[2])}]}]
    if sot:
        markets.append({"id": "900393", "specifier": f"total={sot[0]}", "desc": "Shots on Target Over/Under",
                        "outcomes": [{"id": "12", "odds": str(sot[1])}, {"id": "13", "odds": str(sot[2])}]})
    return {"eventId": "sr:match:1", "markets": markets}


class TestLines:
    def test_prices_become_lines(self):
        got = shots.from_prices(event())
        line = got["sot_home"]
        assert line["source"] == "sportybet"
        # 1.80 / 1.95 over 4.5: a bit over half (margin removed), and the lines fall away
        assert 0.5 < line["over"]["4.5"] < 0.56
        assert line["over"]["2.5"] > line["over"]["4.5"] > line["over"]["6.5"]

    def test_a_wrongly_named_market_is_ignored(self):
        ev = event()
        ev["markets"][0]["desc"] = "Player shots on target"
        assert not shots.from_prices(ev)

    def test_blend(self):
        ours = {"mean": 3.6, "over": {"3.5": 0.40, "4.5": 0.25}}
        theirs = {"mean": 4.4, "over": {"3.5": 0.60, "4.5": 0.45}}
        got = shots.blend(ours, theirs, 0.5)
        assert got == {"mean": 4.0, "over": {"3.5": 0.5, "4.5": 0.35}, "blend": 0.5}


def settled(league, ours, theirs_over, hit, not_model=None):
    odds_o = round(1.06 / theirs_over, 3)                 # SportyBet's price with a margin
    odds_u = round(1.06 / (1 - theirs_over), 3)
    pred = {"prices": [["home_sot_ou", "O45", ours, odds_o], ["home_sot_ou", "U45", round(1 - ours, 3), odds_u]]}
    if not_model:
        pred["not_model"] = not_model
    return {"league": league, "pred": pred,
            "result": {"status": "finished", "hg": 1, "ag": 0, "sot": [6 if hit else 3, 2], "shots": [12, 7]}}


class TestReport:
    def entries(self, n, league="INT-FRI", seed=1):
        # SportyBet knows better here: the truth follows its number
        rng = random.Random(seed)
        out = []
        for _ in range(n):
            truth = rng.uniform(0.2, 0.8)
            out.append(settled(league, 0.5, truth, rng.random() < truth))
        return out

    def test_a_mix_is_chosen_only_with_enough_lines_and_only_if_better(self):
        got = shots.blend_report(self.entries(120))["international"]
        assert got["lines"] == 120 and got["chosen"] > 0
        assert got["brier"][str(got["chosen"])] < got["brier"]["0.0"]
        few = shots.blend_report(self.entries(30))["international"]
        assert few["chosen"] == 0.0 and "needs 80" in few["reason"]

    def test_ours_kept_when_ours_is_better(self):
        rng = random.Random(2)
        entries = []
        for _ in range(150):
            truth = rng.uniform(0.2, 0.8)
            entries.append(settled("INT-FRI", round(truth, 3), 0.5, rng.random() < truth))
        assert shots.blend_report(entries)["international"]["chosen"] == 0.0

    def test_groups_and_only_our_models_numbers(self):
        got = shots.blend_report(self.entries(10, "PL") + [settled("INT", 0.5, 0.6, True, {"sot_home": "sportybet"})])
        assert got["club"]["lines"] == 10 and got["international"]["lines"] == 0


class TestLive:
    def test_fallback_and_mix_in_international_predictions(self, monkeypatch):
        monkeypatch.setattr(main, "_intl_set_pieces", None)
        monkeypatch.setattr(main, "_intl_shots", None)
        monkeypatch.setattr(main, "_linked_event", lambda fx: event())
        monkeypatch.setitem(main._shot_blend, "weights", {})
        fx = {"home": "China", "away": "New Zealand", "date": "2026-09-27", "league": "INT-FRI"}
        got = main._international_set_pieces(fx)
        assert got["sot_home"]["source"] == "sportybet"            # no data of ours: SportyBet's line

        class Model:
            size = {}
            def markets(self, home, away, league):
                return {"sot_home": {"mean": 3.0, "over": {"4.5": 0.2}}}
        monkeypatch.setattr(main, "_intl_shots", Model())
        monkeypatch.setitem(main._intl_sp_info, "shots_check", {"use": {"sot_home": True}})
        assert main._international_set_pieces(fx)["sot_home"]["over"]["4.5"] == 0.2   # ours, no mix yet
        monkeypatch.setitem(main._shot_blend, "weights", {"international": 0.5})
        mixed = main._international_set_pieces(fx)["sot_home"]
        assert mixed["blend"] == 0.5 and 0.2 < mixed["over"]["4.5"] < 0.4

    def test_snapshot_marks_numbers_that_arent_ours(self):
        snap = matchday.snapshot({"home": "A", "away": "B", "date": "2026-09-27", "set_pieces": {
            "sot_home": {"mean": 4, "over": {}, "source": "sportybet"}, "sot_away": {"mean": 3, "over": {}, "blend": 0.5},
            "corners": {"mean": 9, "over": {}}}})
        assert snap["not_model"] == {"sot_home": "sportybet", "sot_away": "blend"}
