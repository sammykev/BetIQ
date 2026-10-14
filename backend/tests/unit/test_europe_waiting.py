"""Europa / Conference League matches with a club our data barely knows:
held until SportyBet prices them, then published from its odds with the
thin-history badge; and their competition badges from ESPN."""

from datetime import date, timedelta

import main

DAY = (date.today() + timedelta(days=1)).isoformat()


class Model:
    def __init__(self):
        self.seen = []

    def predict_match(self, home, away, **k):
        self.seen.append((k.get("odds_home"), k.get("odds_draw"), k.get("odds_away")))
        return {"p_home": 0.5, "p_draw": 0.3, "p_away": 0.2, "tip_code": "1X", "p_over15": 0.7, "p_over25": 0.5}


def _event():
    return {"eventId": "sr:match:9", "markets": [{"id": "1", "specifier": "", "desc": "1X2", "outcomes": [
        {"id": "1", "odds": "1.40", "isActive": 1}, {"id": "2", "odds": "4.80", "isActive": 1},
        {"id": "3", "odds": "7.50", "isActive": 1}]}]}


FX = {"home": "Atalanta", "away": "Pafos", "date": DAY, "time": "19:00", "league": "UECL",
      "league_name": "Conference League", "needs_odds": True}


def test_waits_until_sportybet_prices_it(monkeypatch):
    monkeypatch.setattr(main, "_linked_event", lambda fx: None)
    assert main._build_predictions(Model(), [FX], {}) == []


def test_then_predicted_from_its_odds_with_the_badge(monkeypatch):
    monkeypatch.setattr(main, "_linked_event", lambda fx: _event())
    m = Model()
    [p] = main._build_predictions(m, [FX], {})
    assert m.seen == [(1.4, 4.8, 7.5)]                         # SportyBet's prices go into the model
    assert p["thin_history"] and (p["odds_home"], p["odds_away"]) == (1.4, 7.5)
    assert p["p_home_model"] == 0.5 and p["p_home"] != 0.5      # half the published chance is SportyBet's


def test_published_after_linking(monkeypatch):
    monkeypatch.setattr(main, "_predictor", Model())
    monkeypatch.setattr(main, "_europe_waiting", [FX])
    monkeypatch.setattr(main, "_predictions_cache", [])
    monkeypatch.setattr(main, "_save_predictions_cache", lambda: None)
    monkeypatch.setattr(main, "_linked_event", lambda fx: _event())
    assert main._publish_waiting_europe() == 1
    assert main._publish_waiting_europe() == 0                 # once
    assert main._predictions_cache[0]["home"] == "Atalanta"


def test_europe_badges_come_from_espn(monkeypatch):
    monkeypatch.setattr(main, "_intl_logos", {"EL": "https://a.espncdn.com/el.png", "UECL": "https://a.espncdn.com/uecl.png"})
    assert main._international_competition_logo("Europa League") == "https://a.espncdn.com/el.png"
    assert main._international_competition_logo("Conference League") == "https://a.espncdn.com/uecl.png"
