import asyncio
from datetime import date

import pandas as pd

import europe_fixtures as ef


def _page(events):
    return {"leagues": [{"logos": [{"href": "https://a.espncdn.com/el.png"}]}], "events": events}


def _event(eid, home, away, when, state="pre"):
    return {"id": eid, "date": when, "competitions": [{
        "status": {"type": {"state": state, "completed": state == "post", "name": "STATUS_SCHEDULED"}},
        "competitors": [{"homeAway": "home", "team": {"displayName": home, "logo": "h.png"}, "score": "0"},
                        {"homeAway": "away", "team": {"displayName": away, "logo": "a.png"}, "score": "0"}]}]}


class FakeClient:
    def __init__(self, page):
        self.page = page

    async def get(self, url, params=None, headers=None):
        class R:
            status_code = 200
            text = "{}"
            def json(s):
                return self.page
        return R()

    async def close(self):
        pass


def test_fixtures_are_filed_as_europa_league_club_matches():
    page = _page([_event("1", "Aston Villa", "SK Brann", "2026-10-02T19:00Z"),
                  _event("2", "Celtic", "FC Porto", "2026-09-20T19:00Z", state="post")])
    report = asyncio.run(ef.fetch(14, today=date(2026, 9, 26), client=FakeClient(page)))
    assert report["sources"] == {"EL": 1}
    f = report["fixtures"][0]
    assert (f["home"], f["away"], f["date"], f["time"]) == ("Aston Villa", "SK Brann", "2026-10-02", "19:00")
    assert f["league"] == "EL" and f["league_name"] == "Europa League"
    assert f["odds_sport"] == "soccer_uefa_europa_league" and "model_league" not in f


def test_only_clubs_the_model_knows_are_published():
    rows = [{"Date": "2026-05-01", "HomeTeam": "Aston Villa", "AwayTeam": "Porto"}] * 20 \
        + [{"Date": "2026-05-01", "HomeTeam": "Brann", "AwayTeam": "Porto"}] * 3 \
        + [{"Date": "2024-01-01", "HomeTeam": "Celtic", "AwayTeam": "Porto"}] * 30   # too long ago
    counts = ef.recent_counts(pd.DataFrame(rows), today=date(2026, 9, 26))
    canon = {"FC Porto": "Porto", "SK Brann": "Brann"}.get
    fx = [{"home": "Aston Villa", "away": "FC Porto", "date": "2026-10-02"},
          {"home": "Aston Villa", "away": "SK Brann", "date": "2026-10-02"},
          {"home": "Celtic", "away": "FC Porto", "date": "2026-10-02"}]
    keep, skipped = ef.known(fx, lambda n: canon(n) or n, counts)
    assert [(f["home"], f["away"]) for f in keep] == [("Aston Villa", "FC Porto")]
    assert [(s["home"], s["away"], s["home_matches"], s["away_matches"]) for s in skipped] == [
        ("Aston Villa", "SK Brann", 20, 3), ("Celtic", "FC Porto", 0, 23)]
