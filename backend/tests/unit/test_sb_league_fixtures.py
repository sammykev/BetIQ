"""Group B: the top flights only SportyBet covers — fixtures from its catalog
with its prices, result and goals markets only, the warning until the
no-odds check passes."""

from datetime import date, datetime, timedelta, timezone

import check_extra_leagues
import collect_sb_leagues
import league_fixtures
import main
import optimizer
import sb_league_fixtures as sbf

TODAY = date.today()
KO = datetime.combine(TODAY + timedelta(days=1), datetime.min.time(), timezone.utc) + timedelta(hours=18)


def _event(home="FK Partizan", away="Crvena Zvezda", tournament="Serbia · Superliga", eid="sr:match:1"):
    return {"eventId": eid, "homeTeamName": home, "awayTeamName": away, "_tournament": tournament,
            "estimateStartTime": int(KO.timestamp() * 1000),
            "markets": [{"id": "1", "specifier": "", "outcomes": [
                {"id": "1", "odds": "2.60", "isActive": 1}, {"id": "2", "odds": "3.20", "isActive": 1},
                {"id": "3", "odds": "2.70", "isActive": 1}]}]}


def _data(n=70):
    data = collect_sb_leagues.empty()
    data["uefa"] = {"FK Partizan": "2025-08-01"}
    data["rows"] = {str(i): {"Date": "2025-05-01", "HomeTeam": "FK Partizan", "AwayTeam": f"Club {i}", "FTHG": 1,
                             "FTAG": 0, "Result": "H", "league": "SB-SRB", "tournament": "Superliga"}
                    for i in range(n)}
    return data


def test_only_leagues_with_enough_history():
    assert sbf.leagues(_data(70)) == {"SB-SRB": "Superliga"}
    assert sbf.leagues(_data(10)) == {}


def test_fixtures_from_the_catalog_with_prices():
    events = [_event(), _event(tournament="Serbia · Kup Srbije", eid="sr:match:2"),
              _event(home="FK Partizan U19", away="Zvezda U19", eid="sr:match:3")]
    [fx] = sbf.from_catalog(events, {"SB-SRB": "Superliga"}, today=TODAY)
    assert (fx["home"], fx["league"], fx["league_name"]) == ("FK Partizan", "SB-SRB", "Serbia Superliga")
    assert fx["sb_odds"] == {"1": 2.6, "X": 3.2, "2": 2.7} and fx["goals_only"]
    assert fx["date"] == KO.date().isoformat() and fx["time"] == "18:00"


class Model:
    def __init__(self):
        self.seen = []
        self.elo = type("E", (), {"ratings": {"FK Partizan (SB-SRB)": 1600}})()

    def predict_match(self, home, away, **k):
        self.seen.append((home, away, k.get("odds_home"), k.get("league")))
        return {"p_home": 0.4, "p_draw": 0.3, "p_away": 0.3, "tip_code": "1X", "p_over15": 0.7, "p_over25": 0.5}


def test_predicted_from_its_prices_goals_only(monkeypatch):
    monkeypatch.setattr(main, "_linked_event", lambda fx: None)
    monkeypatch.setattr(main, "_unproven_leagues", {"SB-SRB"})
    called = []
    monkeypatch.setattr(main, "_set_piece_extras", lambda fx, p: called.append(1) or ({"corners": 1}, None))
    [fx] = sbf.from_catalog([_event()], {"SB-SRB": "Superliga"}, today=TODAY)
    m = Model()
    [p] = main._build_predictions(m, [fx], {})
    # A clashing club trains under its tagged name; the page shows SportyBet's
    assert m.seen == [("FK Partizan (SB-SRB)", "Crvena Zvezda", 2.6, "SB-SRB")]
    assert p["home"] == "FK Partizan" and p["odds_home"] == 2.6 and p["league_unproven"]
    assert not called and "set_pieces" not in p


def test_optimizer_offers_result_and_goals_only():
    pred = {"home": "A", "away": "B", "date": "2030-01-01", "p_home": 0.6, "p_draw": 0.2, "p_away": 0.2,
            "p_over15": 0.8, "p_over25": 0.6, "goals_only": True,
            "set_pieces": {"corners": {"lines": {"9.5": 0.7}}}}
    markets = {o.market for o in optimizer.candidates(pred, None, 0.0, calibrate=False)}
    assert markets and not any(optimizer.NOT_GOALS.search(m) for m in markets)


def test_unproven_for_any_codes():
    class R:
        def get(self, k):
            return '{"leagues": {"SB-SRB": {"pass": true}}}'
    assert league_fixtures.unproven(R(), {"SB-SRB", "SB-CRO"}) == {"SB-CRO"}


def _rec(league, p_home, result):
    return {"league": league, "p_home": p_home, "p_draw": 0.2, "p_away": 0.8 - p_home, "result": result}


def test_no_odds_check():
    # Main leagues: the model leans the right way; so does it in Serbia, not in Croatia
    told = lambda lg, n: [_rec(lg, 0.7, "H") for _ in range(n)] + [_rec(lg, 0.1, "A") for _ in range(n)]
    recs = told("PL", 100) + told("SB-SRB", 60)
    recs += [_rec("SB-CRO", 0.1, "H") for _ in range(60)] + [_rec("SB-CRO", 0.7, "A") for _ in range(60)]
    out = check_extra_leagues.sb_verdicts(recs, {"SB-SRB", "SB-CRO"})
    assert out["main"]["gain"] > 0 and out["leagues"]["SB-SRB"]["pass"]
    assert not out["leagues"]["SB-CRO"]["pass"] and "gains" in out["leagues"]["SB-CRO"]["why"]
