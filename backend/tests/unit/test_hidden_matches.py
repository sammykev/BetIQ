"""
Admin → hide a fixture a source got wrong: off the predictions, the day's
list, the optimizer and daily odds, and out of every rebuild.
"""

import json
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import international_fixtures as intl
import main
import matchday
from tests.unit.test_user_endpoints import FakeRedis


def test_youth_and_womens_games_never_come_in_as_fixtures():
    assert intl.not_senior("England U21 Slovakia U21 UEFA U21 Championship")
    assert intl.not_senior("Faroe Islands Slovakia UEFA European Under-21 Championship Qualifying")
    assert intl.not_senior("Nigeria Ghana Women's Friendly")
    assert not intl.not_senior("Faroe Islands Slovakia UEFA Nations League")
    espn = {"leagues": [{"name": "UEFA European Under-21 Championship Qualifying"}], "events": [{
        "id": "1", "date": "2026-10-02T16:00Z", "competitions": [{"status": {"type": {"state": "pre"}}, "competitors": [
            {"homeAway": "home", "team": {"displayName": "Faroe Islands"}},
            {"homeAway": "away", "team": {"displayName": "Slovakia"}}]}]}]}
    assert intl.parse_espn(espn, "uefa.nations")[0] == []
    events = [{"id": "x", "commence_time": "2026-10-02T16:00:00Z", "home_team": "England U21", "away_team": "Slovakia U21"}]
    assert intl.parse_odds_events(events, "soccer_uefa_nations_league", "UEFA Nations League",
                                  date(2026, 10, 1), date(2026, 10, 3)) == []


@pytest.fixture
def admin(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(main, "_get_redis", lambda: fake)
    monkeypatch.setattr(main, "_hidden", {})
    monkeypatch.setattr(main, "_hidden_loaded", [False])
    monkeypatch.setattr(main, "_save_predictions_cache", lambda: None)
    day = (date.today() + timedelta(days=3)).isoformat()
    bad = {"home": "Faroe Islands", "away": "Slovakia", "date": day, "time": "16:00", "match_id": "sofa:123",
           "league": "INT-UNL", "league_name": "UEFA Nations League"}
    good = {"home": "Arsenal", "away": "Chelsea", "date": day, "time": "15:00", "match_id": "555", "league": "PL"}
    monkeypatch.setattr(main, "_predictions_cache", [bad, good])
    main._md_save(fake, day, {matchday.key("Faroe Islands", "Slovakia"): {**bad}, matchday.key("Arsenal", "Chelsea"): {**good}})

    async def identity(request):
        return "secret", None
    monkeypatch.setattr(main, "_admin_identity", identity)
    return TestClient(main.app), fake, day, bad


def test_hide_and_show_again(admin):
    c, fake, day, bad = admin
    got = c.post("/api/admin/matches/hide", json={"home": "Faroe Islands", "away": "Slovakia", "date": day}).json()
    assert got == {"hidden": True, "removed_from_day": True}
    assert [p["home"] for p in main._predictions_cache] == ["Arsenal"]
    assert list(main._md_load(fake, day)) == [matchday.key("Arsenal", "Chelsea")]
    listed = c.get("/api/admin/matches/hidden").json()["hidden"]
    assert listed[0]["source"] == "SofaScore · UEFA Nations League"
    # A rebuild leaves it out too
    main._hidden_loaded[0] = False
    assert main._is_hidden(bad) and not main._is_hidden({"home": "Arsenal", "away": "Chelsea", "date": day})
    c.post("/api/admin/matches/unhide", json={"home": "Faroe Islands", "away": "Slovakia", "date": day})
    assert c.get("/api/admin/matches/hidden").json()["hidden"] == [] and not main._is_hidden(bad)


def test_bad_requests(admin):
    c, _, _, _ = admin
    assert c.post("/api/admin/matches/hide", json={"home": "A"}).status_code == 400
