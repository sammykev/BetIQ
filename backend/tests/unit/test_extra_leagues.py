"""The European leagues published beyond football-data.org's (league_fixtures):
priced from SportyBet once linked, flagged league_unproven until their
walk-forward check passes, and then kept out of slips; the check's verdicts."""

import json
from datetime import date, timedelta

import check_extra_leagues
import league_fixtures
import main
import matchday

DAY = (date.today() + timedelta(days=1)).isoformat()
FX = {"home": "Club Brugge", "away": "Genk", "date": DAY, "time": "18:00", "league": "JPL",
      "league_name": "Belgian Pro League", "match_id": "espn:1"}


class Model:
    def __init__(self):
        self.seen = []

    def predict_match(self, home, away, **k):
        self.seen.append((k.get("odds_home"), k.get("odds_draw"), k.get("odds_away")))
        return {"p_home": 0.5, "p_draw": 0.3, "p_away": 0.2, "tip_code": "1X", "p_over15": 0.7, "p_over25": 0.5}


def _event():
    return {"eventId": "sr:match:9", "markets": [{"id": "1", "specifier": "", "desc": "1X2", "outcomes": [
        {"id": "1", "odds": "1.90", "isActive": 1}, {"id": "2", "odds": "3.60", "isActive": 1},
        {"id": "3", "odds": "4.00", "isActive": 1}]}]}


class R:
    def __init__(self, v=None):
        self.v = v

    def get(self, k):
        return self.v


def test_unproven_until_checked():
    assert league_fixtures.unproven(R()) == set(league_fixtures.COMPETITIONS)
    got = {"leagues": {"JPL": {"pass": True}, "SPL": {"pass": False}}}
    left = league_fixtures.unproven(R(json.dumps(got)))
    assert "JPL" not in left and "SPL" in left and "TSL" in left       # unchecked stays unproven


def test_published_without_odds_then_with_sportybets(monkeypatch):
    monkeypatch.setattr(main, "_unproven_leagues", {"JPL"})
    monkeypatch.setattr(main, "_linked_event", lambda fx: None)
    m = Model()
    [p] = main._build_predictions(m, [FX], {})
    assert m.seen == [(0.0, 0.0, 0.0)] and p["league_unproven"] and not p["odds_home"]
    monkeypatch.setattr(main, "_linked_event", lambda fx: _event())
    [p] = main._build_predictions(m, [FX], {})
    assert m.seen[-1] == (1.9, 3.6, 4.0) and p["odds_home"] == 1.9


def test_priced_after_linking(monkeypatch):
    monkeypatch.setattr(main, "_unproven_leagues", set())
    monkeypatch.setattr(main, "_linked_event", lambda fx: None)
    m = Model()
    monkeypatch.setattr(main, "_predictor", m)
    monkeypatch.setattr(main, "_predictions_cache", main._build_predictions(m, [FX], {}))
    monkeypatch.setattr(main, "_save_predictions_cache", lambda: None)
    assert main._price_unpriced() == 0
    monkeypatch.setattr(main, "_linked_event", lambda fx: _event())
    assert main._price_unpriced() == 1
    assert main._predictions_cache[0]["odds_home"] == 1.9
    assert main._price_unpriced() == 0                    # once


def test_flag_kept_in_snapshots():
    assert "league_unproven" in matchday._PRED_FIELDS


def _rec(league, p_home, result, odds=(2.0, 3.4, 3.8)):
    return {"league": league, "p_home": p_home, "p_draw": 0.25, "p_away": 0.75 - p_home, "result": result,
            "odds_home": odds[0], "odds_draw": odds[1], "odds_away": odds[2]}


def test_check_verdicts():
    recs = [_rec("PL", 0.5, "H") for _ in range(200)]
    recs += [_rec("JPL", 0.5, "H") for _ in range(120)]           # as good as the main leagues
    recs += [_rec("SPL", 0.2, "H") for _ in range(120)]           # far worse
    recs += [_rec("TSL", 0.5, "H") for _ in range(20)]            # too few
    out = check_extra_leagues.verdicts(recs, {"JPL", "SPL", "TSL"})
    assert out["leagues"]["JPL"]["pass"]
    assert not out["leagues"]["SPL"]["pass"] and "trails" in out["leagues"]["SPL"]["why"]
    assert not out["leagues"]["TSL"]["pass"] and "only 20" in out["leagues"]["TSL"]["why"]


def test_any_unpriced_match_takes_sportybets_1x2(monkeypatch):
    # A cup tie the odds feed skipped (e.g. a Conference League match): not
    # one of the extra leagues, still blended once SportyBet prices it
    monkeypatch.setattr(main, "_unproven_leagues", set())
    monkeypatch.setattr(main, "_linked_event", lambda fx: _event())
    m = Model()
    [p] = main._build_predictions(m, [{**FX, "league": "UECL", "league_name": "Conference League"}], {})
    assert m.seen[-1] == (1.9, 3.6, 4.0) and p["odds_home"] == 1.9 and "p_home_model" in p


def test_optimizer_blends_1x2_of_a_match_predicted_without_odds():
    import optimizer
    # ours: away or draw 82%; SportyBet's X2 price says about 25%
    pred = {"home": "Panathinaikos", "away": "Borac", "date": DAY, "time": "16:45", "league": "UECL",
            "p_home": 0.18, "p_draw": 0.30, "p_away": 0.52, "p_over15": 0.7, "p_over25": 0.45,
            "odds_home": None, "odds_draw": None, "odds_away": None}
    ev = {"markets": [{"id": "1", "specifier": "", "outcomes": [
        {"id": "1", "odds": "1.30", "isActive": 1}, {"id": "2", "odds": "5.20", "isActive": 1},
        {"id": "3", "odds": "9.50", "isActive": 1}]}]}
    got = optimizer._with_market_1x2(pred, ev)
    assert got["p_draw"] + got["p_away"] < 0.6
    assert abs(got["p_home"] + got["p_draw"] + got["p_away"] - 1) < 1e-9
    priced = {**pred, "odds_home": 1.3, "odds_draw": 5.2, "odds_away": 9.5}
    assert optimizer._with_market_1x2(priced, ev) is priced       # blended where built already
    assert optimizer._with_market_1x2(pred, None) is pred
