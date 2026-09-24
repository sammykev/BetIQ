"""
International fixtures: ESPN and The Odds API parsing, merging the two,
national-team names, the results CSV refresh, and the pipeline/endpoint wiring.
Both APIs are replaced by canned responses.
"""

import asyncio
import json
from datetime import date

import httpx
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import football_data_sync as fds
import international_fixtures as intl
import main
import odds_fetcher
from team_names import TeamResolver


def espn_event(eid, home, away, when="2026-09-24T18:45Z", state="pre", completed=False,
               status="STATUS_SCHEDULED", score=(None, None)):
    return {
        "id": eid, "date": when,
        "competitions": [{
            "competitors": [
                {"homeAway": "home", "score": score[0],
                 "team": {"displayName": home, "logo": f"https://flags/{home}.png"}},
                {"homeAway": "away", "score": score[1],
                 "team": {"displayName": away, "logo": f"https://flags/{away}.png"}},
            ],
            "status": {"type": {"state": state, "completed": completed, "name": status}},
        }],
    }


NATIONS = {"events": [
    espn_event("1", "Czechia", "USA"),
    espn_event("2", "Spain", "Italy", when="2026-09-21T18:45Z", state="post", completed=True,
               status="STATUS_FULL_TIME", score=("2", "1")),
    espn_event("3", "France", "Germany", when="2026-09-21T19:00Z", state="post", completed=True,
               status="STATUS_FINAL_AET", score=("1", "0")),
    espn_event("4", "TBD", "England"),
]}


class TestEspn:
    def test_upcoming_fixture(self):
        fixtures, _ = intl.parse_espn(NATIONS, "uefa.nations")
        [f] = fixtures
        assert (f["home"], f["away"], f["date"], f["time"]) == ("Czechia", "USA", "2026-09-24", "18:45")
        assert (f["league"], f["league_name"], f["odds_sport"]) == ("INT", "UEFA Nations League", "soccer_uefa_nations_league")
        assert f["home_crest"] == "https://flags/Czechia.png" and f["match_id"] == "espn:1"

    def test_results_are_regulation_time_only(self):
        _, results = intl.parse_espn(NATIONS, "uefa.nations")
        assert results == [{"Date": "2026-09-21", "HomeTeam": "Spain", "AwayTeam": "Italy",
                            "Result": "H", "FTHG": 2, "FTAG": 1, "league": "INT"}]

    def test_empty_or_odd_payloads(self):
        assert intl.parse_espn({}, "fifa.friendly") == ([], [])
        assert intl.parse_espn({"events": [{"id": "x"}]}, "fifa.friendly") == ([], [])


class TestOddsApi:
    SPORTS = [
        {"key": "soccer_uefa_nations_league", "group": "Soccer", "title": "UEFA Nations League", "active": True, "has_outrights": False},
        {"key": "soccer_fifa_world_cup_winner", "group": "Soccer", "title": "World Cup Winner", "active": True, "has_outrights": True},
        {"key": "soccer_epl", "group": "Soccer", "title": "EPL", "active": True, "has_outrights": False},
        {"key": "basketball_nba", "group": "Basketball", "title": "NBA", "active": True, "has_outrights": False},
    ]

    def test_only_national_team_competitions(self):
        assert intl.international_sport_keys(self.SPORTS) == {"soccer_uefa_nations_league": "UEFA Nations League"}

    def test_events_within_the_window(self):
        events = [
            {"id": "a", "commence_time": "2026-09-24T18:45:00Z", "home_team": "Czech Republic", "away_team": "United States"},
            {"id": "b", "commence_time": "2026-12-01T18:45:00Z", "home_team": "Wales", "away_team": "Iceland"},
        ]
        [f] = intl.parse_odds_events(events, "soccer_uefa_nations_league", "UEFA Nations League",
                                     date(2026, 9, 23), date(2026, 10, 14))
        assert (f["home"], f["odds_sport"], f["match_id"]) == ("Czech Republic", "soccer_uefa_nations_league", "odds:a")


class TestMerge:
    def test_same_match_from_both_sources_appears_once(self):
        espn = intl._fixture("espn:1", "Czechia", "USA", pd.Timestamp("2026-09-24T18:45Z"), "Friendly", None)
        odds = intl._fixture("odds:a", "Czech Republic", "United States", pd.Timestamp("2026-09-24T18:45Z"),
                             "Friendlies", "soccer_international_friendlies")
        other = intl._fixture("odds:b", "Wales", "Iceland", pd.Timestamp("2026-09-24T16:00Z"), "Friendlies", None)
        merged = intl.merge([espn, odds, other])
        assert [f["match_id"] for f in merged] == ["odds:b", "espn:1"]
        assert merged[1]["odds_sport"] == "soccer_international_friendlies"

    @pytest.mark.parametrize("a,b", [("USA", "United States"), ("Korea Republic", "South Korea"),
                                     ("Türkiye", "Turkey"), ("Côte d'Ivoire", "Ivory Coast"),
                                     ("Bosnia-Herzegovina", "Bosnia and Herzegovina"), ("Congo DR", "DR Congo")])
    def test_team_key(self, a, b):
        assert intl.team_key(a) == intl.team_key(b)


class TestNationalTeamNames:
    KNOWN = ["United States", "South Korea", "North Korea", "Czech Republic", "Republic of Ireland",
             "Northern Ireland", "Turkey", "DR Congo", "Congo", "Ivory Coast", "Arsenal", "Chelsea"]

    @pytest.mark.parametrize("live,trained", [
        ("USA", "United States"), ("Korea Republic", "South Korea"), ("Czechia", "Czech Republic"),
        ("Ireland", "Republic of Ireland"), ("Northern Ireland", "Northern Ireland"),
        ("Türkiye", "Turkey"), ("Congo DR", "DR Congo"), ("Congo", "Congo"),
        ("Côte d'Ivoire", "Ivory Coast"), ("Arsenal FC", "Arsenal"),
    ])
    def test_resolves_to_the_training_name(self, live, trained):
        assert TeamResolver(self.KNOWN).resolve(live) == trained


def mock_client(routes):
    """routes: {url path substring: (status, json body)}; records requested URLs."""
    seen = []

    def handler(request):
        seen.append(str(request.url))
        for part, (status, body) in routes.items():
            if part in request.url.path:
                return httpx.Response(status, content=json.dumps(body).encode())
        return httpx.Response(404)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen


class TestFetch:
    def test_both_sources_merged_with_counts_and_errors(self):
        client, seen = mock_client({
            "uefa.nations/scoreboard": (200, NATIONS),
            "fifa.friendly/scoreboard": (200, {"events": [espn_event("9", "Nigeria", "Ghana", when="2026-09-25T17:00Z")]}),
            "/v4/sports/soccer_uefa_nations_league/events": (200, [
                {"id": "a", "commence_time": "2026-09-24T18:45:00Z", "home_team": "Czech Republic", "away_team": "United States"},
                {"id": "b", "commence_time": "2026-09-26T18:45:00Z", "home_team": "Wales", "away_team": "Iceland"},
            ]),
            "/v4/sports": (200, TestOddsApi.SPORTS),
        })
        report = asyncio.run(intl.fetch_international(days_ahead=21, client=client, today=date(2026, 9, 23),
                                                      odds_api_key="k"))
        assert [(f["home"], f["away"]) for f in report["fixtures"]] == [
            ("Czechia", "USA"), ("Nigeria", "Ghana"), ("Wales", "Iceland")]
        assert report["sources"]["espn"]["uefa.nations"] == 1
        assert report["sources"]["odds_api"] == {"soccer_uefa_nations_league": 2}
        assert "espn fifa.worldq.uefa: HTTP 404" in report["errors"]
        assert [r["HomeTeam"] for r in report["results"]] == ["Spain"]
        assert any("dates=20260923-20261014" in u for u in seen)

    def test_no_odds_key_means_espn_only(self):
        client, seen = mock_client({"uefa.nations/scoreboard": (200, NATIONS)})
        report = asyncio.run(intl.fetch_international(client=client, today=date(2026, 9, 23), odds_api_key=""))
        assert len(report["fixtures"]) == 1 and report["sources"]["odds_api"] == {}
        assert not any("the-odds-api" in u for u in seen)

    def test_past_fixtures_are_not_upcoming(self):
        # ESPN still lists a match as "pre" after a postponement; it isn't upcoming
        client, _ = mock_client({"uefa.nations/scoreboard": (200, {"events": [
            espn_event("1", "Czechia", "USA", when="2026-09-20T18:45Z")]})})
        report = asyncio.run(intl.fetch_international(days_back=5, client=client, today=date(2026, 9, 23),
                                                      odds_api_key=""))
        assert report["fixtures"] == []


class TestOddsSportKeys:
    def test_fixture_sport_key_wins_over_the_league_name(self):
        keys = odds_fetcher._sport_keys_for_predictions([
            {"league_name": "International Friendly", "odds_sport": None},
            {"league_name": "UEFA Nations League", "odds_sport": "soccer_uefa_nations_league"},
            {"league_name": "Premier League"},
        ])
        assert sorted(keys) == ["soccer_epl", "soccer_uefa_nations_league"]


class TestResultsCsvSync:
    HEADER = "date,home_team,away_team,home_score,away_score,tournament,city,country,neutral\n"

    def rows(self, n):
        return self.HEADER + "".join(f"2026-08-{d:02d},Spain,Italy,1,0,Friendly,Madrid,Spain,FALSE\n"
                                     for d in range(1, n + 1))

    def run(self, path, status, body):
        client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda r: httpx.Response(status, content=body.encode())))
        return asyncio.run(fds.sync_international(str(path), client=client))

    def test_stores_a_longer_file(self, tmp_path):
        path = tmp_path / "intl.csv"
        path.write_text(self.rows(2))
        assert self.run(path, 200, self.rows(3))["updated"] == ["intl.csv"]
        assert path.read_text() == self.rows(3)

    @pytest.mark.parametrize("status,body", [(200, "<html>rate limited</html>"), (500, ""), (200, "")])
    def test_never_replaces_good_data_with_a_bad_download(self, tmp_path, status, body):
        path = tmp_path / "intl.csv"
        path.write_text(self.rows(3))
        assert self.run(path, status, body)["failed"]
        assert path.read_text() == self.rows(3)

    def test_never_replaces_with_fewer_matches(self, tmp_path):
        path = tmp_path / "intl.csv"
        path.write_text(self.rows(3))
        assert self.run(path, 200, self.rows(2))["failed"]


def test_international_training_rows_share_the_fixture_league(tmp_path, monkeypatch):
    path = tmp_path / "intl.csv"
    path.write_text(TestResultsCsvSync.HEADER
                    + "2024-06-01,Spain,Italy,1,0,Friendly,Madrid,Spain,FALSE\n"
                    + "2026-06-27,Panama,England,NA,NA,FIFA World Cup,East Rutherford,United States,TRUE\n")
    monkeypatch.setattr(main, "INTERNATIONAL_CSV", str(path))
    df = main._load_international_csv()
    assert len(df) == 1 and list(df["league"]) == [intl.LEAGUE_CODE]


def test_leagues_endpoint_lists_internationals_first():
    leagues = TestClient(main.app).get("/api/leagues").json()
    assert leagues[0] == {"code": "INT", "name": "Internationals", "country": "World", "flag": "🌍"}


def test_pipeline_helper_records_what_each_source_found(monkeypatch):
    async def fake(**kw):
        return {"fixtures": [{"home": "Czechia"}], "results": [],
                "sources": {"espn": {"uefa.nations": 1}, "odds_api": {}}, "errors": ["espn uefa.euroq: HTTP 400"]}
    monkeypatch.setattr(intl, "fetch_international", fake)
    assert asyncio.run(main._fetch_international_fixtures()) == [{"home": "Czechia"}]
    assert main._intl_status["fixtures"] == 1 and main._intl_status["errors"] == ["espn uefa.euroq: HTTP 400"]


class TestEspnFallbacks:
    def run(self, handler):
        seen = []

        def wrapped(request):
            seen.append(request)
            return handler(request)
        client = httpx.AsyncClient(transport=httpx.MockTransport(wrapped), headers=intl._ESPN_HEADERS)
        report = asyncio.run(intl.fetch_international(days_ahead=3, client=client, today=date(2026, 9, 23),
                                                      odds_api_key=""))
        return report, seen

    @staticmethod
    def events(*evs):
        return httpx.Response(200, json={"events": list(evs)})

    def test_range_refused_falls_back_to_single_days(self):
        def handler(request):
            if "caf.nations_qual" not in request.url.path:
                return self.events()
            dates = request.url.params.get("dates")
            if dates and "-" in dates:
                return httpx.Response(400, text='{"code":400,"message":"bad dates"}')
            if dates == "20260924":
                return self.events(espn_event("5", "Ivory Coast", "Ghana", when="2026-09-24T16:00Z"))
            if dates is None:  # current matchday
                return self.events(espn_event("5", "Ivory Coast", "Ghana", when="2026-09-24T16:00Z"))
            return self.events()

        report, seen = self.run(handler)
        assert [(f["home"], f["league_name"]) for f in report["fixtures"]] == [("Ivory Coast", "AFCON Qualifying")]
        assert report["sources"]["espn"]["caf.nations_qual"] == 1 and report["errors"] == []
        afcon_days = [r.url.params.get("dates") for r in seen if "caf.nations_qual" in r.url.path]
        assert afcon_days == ["20260923-20260926", None, "20260923", "20260924", "20260925", "20260926"]

    def test_quiet_competitions_cost_two_requests_and_no_error(self):
        report, seen = self.run(lambda request: self.events())
        per_slug = {slug: sum(slug + "/" in r.url.path for r in seen) for slug in intl.ESPN_COMPETITIONS}
        assert set(per_slug.values()) == {2}
        assert report["errors"] == [] and report["fixtures"] == []

    def test_errors_carry_the_start_of_the_reply(self):
        report, _ = self.run(lambda request: httpx.Response(403, text="<html>Access Denied</html>"))
        assert "espn fifa.friendly: HTTP 403 '<html>Access Denied</html>'" in report["errors"]

    def test_sends_browser_headers(self):
        _, seen = self.run(lambda request: self.events())
        espn = [r for r in seen if "espn" in r.url.host]
        assert espn[0].headers["referer"] == "https://www.espn.com/"

    def test_default_client_imitates_chrome(self, monkeypatch):
        made = []

        class Session:
            def __init__(self, **kw):
                made.append(kw)

            async def get(self, url, params=None, headers=None):
                return httpx.Response(200, json={"events": []})

            async def close(self):
                made.append("closed")

        monkeypatch.setattr(intl, "AsyncSession", Session)
        asyncio.run(intl.fetch_international(days_ahead=1, today=date(2026, 9, 23), odds_api_key=""))
        assert made[0]["impersonate"].startswith("chrome") and made[-1] == "closed"


class TestAdminCheck:
    URL = "/api/admin/international-check"

    @pytest.fixture(autouse=True)
    def admin(self, monkeypatch):
        monkeypatch.setattr(main, "ADMIN_SECRET", "s3cret")

    def test_admin_only(self):
        assert TestClient(main.app).get(self.URL, params={"secret": "nope"}).status_code == 403

    def test_fetches_reports_and_publishes(self, monkeypatch):
        fx = intl._fixture("espn:5", "Ivory Coast", "Ghana", pd.Timestamp("2026-09-24T16:00Z"), "AFCON Qualifying", None)

        async def fake(**kw):
            return {"fixtures": [fx], "results": [], "sources": {"espn": {"caf.nations_qual": 1}, "odds_api": {}},
                    "errors": ["espn uefa.euroq: HTTP 400"]}

        class Model:
            def predict_match(self, *a, **k):
                return {"p_home": 0.5, "p_draw": 0.3, "p_away": 0.2, "tip_code": "1X"}

        monkeypatch.setattr(intl, "fetch_international", fake)
        monkeypatch.setattr(main, "_predictor", Model())
        monkeypatch.setattr(main, "_predictions_cache", [{"home": "Old", "away": "Game", "league": "INT"},
                                                         {"home": "Arsenal", "away": "Chelsea", "league": "PL"}])
        monkeypatch.setattr(main, "_save_predictions_cache", lambda: None)
        monkeypatch.setattr(main, "_load_cached_live_odds", lambda: ({}, None))

        r = TestClient(main.app).get(self.URL, params={"secret": "s3cret"}).json()
        assert (r["fixtures"], r["published"]) == (1, 1)
        assert r["by_competition"] == {"AFCON Qualifying": 1} and r["errors"] == ["espn uefa.euroq: HTTP 400"]
        assert sorted(p["home"] for p in main._predictions_cache) == ["Arsenal", "Ivory Coast"]


def sofa_event(eid, home, away, comp="International Friendly Games", when="2026-09-24T16:00:00+00:00",
               status="notstarted", description="Not started", national=True, gender="M", score=None):
    ev = {
        "id": eid,
        "tournament": {"name": comp, "uniqueTournament": {"name": comp}},
        "homeTeam": {"name": home, "gender": gender, **({"national": national} if national is not None else {})},
        "awayTeam": {"name": away, "gender": gender, **({"national": national} if national is not None else {})},
        "startTimestamp": int(pd.Timestamp(when).timestamp()),
        "status": {"type": status, "description": description},
    }
    if score:
        ev["homeScore"] = {"current": score[0], "normaltime": score[0]}
        ev["awayScore"] = {"current": score[1], "normaltime": score[1]}
    return ev


class TestSofaScore:
    DAY = {"events": [
        sofa_event(1, "Nigeria", "Ghana"),
        sofa_event(2, "Ivory Coast", "Gabon", comp="Africa Cup of Nations, Qualification"),
        sofa_event(3, "Arsenal", "Chelsea", comp="Premier League", national=False),
        sofa_event(4, "Barcelona", "Ajax", comp="Club Friendly Games", national=False),
        sofa_event(5, "England", "Spain", gender="F"),
        sofa_event(6, "Nigeria U20", "Ghana U20"),
        sofa_event(7, "Morocco", "Gabon", comp="Africa Cup of Nations, Qualification", national=None),
        sofa_event(8, "Egypt", "Angola", status="finished", description="Ended", score=(2, 0)),
        sofa_event(9, "Mali", "Togo", status="finished", description="AET", score=(1, 1)),
    ]}

    def test_keeps_senior_mens_national_team_matches(self):
        fixtures, _ = intl.parse_sofascore(self.DAY)
        assert [(f["home"], f["league_name"]) for f in fixtures] == [
            ("Nigeria", "International Friendly Games"),
            ("Ivory Coast", "Africa Cup of Nations, Qualification"),
            ("Morocco", "Africa Cup of Nations, Qualification"),  # no "national" field: judged by competition
        ]
        assert fixtures[0]["league"] == "INT" and fixtures[0]["match_id"] == "sofa:1"

    def test_results_are_regulation_time_only(self):
        _, results = intl.parse_sofascore(self.DAY)
        assert results == [{"Date": "2026-09-24", "HomeTeam": "Egypt", "AwayTeam": "Angola",
                            "Result": "H", "FTHG": 2, "FTAG": 0, "league": "INT"}]

    def test_fills_in_when_espn_refuses_the_server(self):
        def handler(request):
            if "espn" in request.url.host:
                return httpx.Response(403, text="<HTML><HEAD><TITLE>Access Denied</TITLE>")
            if request.url.path.endswith("/2026-09-24"):
                return httpx.Response(200, json=self.DAY)
            return httpx.Response(200, json={"events": []})
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        report = asyncio.run(intl.fetch_international(days_ahead=7, client=client, today=date(2026, 9, 23),
                                                      odds_api_key=""))
        assert {f["home"] for f in report["fixtures"]} == {"Nigeria", "Ivory Coast", "Morocco"}
        assert report["sources"]["sofascore"]["2026-09-24"] == 3
        assert any(e.startswith("espn caf.nations_qual: HTTP 403") for e in report["errors"])

    def test_stops_after_a_refusal_on_the_first_day(self):
        calls = []

        def handler(request):
            calls.append(request.url.host)
            return httpx.Response(403, text="Access Denied")
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        asyncio.run(intl.fetch_international(days_ahead=7, client=client, today=date(2026, 9, 23), odds_api_key=""))
        assert calls.count("api.sofascore.com") == 1

    def test_espn_and_sofascore_listing_one_match_count_once(self):
        def handler(request):
            if "espn" in request.url.host:
                return httpx.Response(200, json={"events": [espn_event("1", "Côte d'Ivoire", "Gabon")]}) \
                    if "caf.nations_qual" in request.url.path else httpx.Response(200, json={"events": []})
            return httpx.Response(200, json={"events": [sofa_event(2, "Ivory Coast", "Gabon",
                                                                     comp="Africa Cup of Nations, Qualification",
                                                                     when="2026-09-24T18:45:00+00:00")]})
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        report = asyncio.run(intl.fetch_international(days_ahead=3, client=client, today=date(2026, 9, 23),
                                                      odds_api_key=""))
        assert [f["match_id"] for f in report["fixtures"]] == ["espn:1"]
