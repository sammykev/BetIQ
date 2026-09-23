"""
Live (football-data.org) team names resolve to the training CSVs' names, and
never to the wrong club.
"""

import glob
import os

import numpy as np
import pandas as pd
import pytest

from predictor import LeaguePredictor
from team_names import UCL_ALIASES, TeamResolver, normalise

DATA = os.path.join(os.path.dirname(__file__), "..", "..", "data", "football")


@pytest.fixture(scope="module")
def csv_names():
    names = set()
    for f in glob.glob(os.path.join(DATA, "*.csv")):
        d = pd.read_csv(f, usecols=["HomeTeam", "AwayTeam"])
        names |= set(d.HomeTeam.dropna()) | set(d.AwayTeam.dropna())
    return names


# football-data.org names as the fixtures API returns them → training CSV name
API_TO_CSV = {
    "Arsenal FC": "Arsenal", "AFC Bournemouth": "Bournemouth",
    "Brighton & Hove Albion FC": "Brighton", "Manchester City FC": "Man City",
    "Manchester United FC": "Man United", "Newcastle United FC": "Newcastle",
    "Nottingham Forest FC": "Nott'm Forest", "Tottenham Hotspur FC": "Tottenham",
    "West Ham United FC": "West Ham", "Wolverhampton Wanderers FC": "Wolves",
    "Sheffield United FC": "Sheffield United", "Sheffield Wednesday FC": "Sheffield Weds",
    "Queens Park Rangers FC": "QPR", "West Bromwich Albion FC": "West Brom",
    "Real Madrid CF": "Real Madrid", "FC Barcelona": "Barcelona",
    "Club Atlético de Madrid": "Ath Madrid", "Athletic Club": "Ath Bilbao",
    "Real Betis Balompié": "Betis", "Real Sociedad de Fútbol": "Sociedad",
    "RC Celta de Vigo": "Celta", "Rayo Vallecano de Madrid": "Vallecano",
    "RCD Espanyol de Barcelona": "Espanol", "Deportivo Alavés": "Alaves",
    "FC Bayern München": "Bayern Munich", "Borussia Dortmund": "Dortmund",
    "Bayer 04 Leverkusen": "Leverkusen", "Eintracht Frankfurt": "Ein Frankfurt",
    "Borussia Mönchengladbach": "M'gladbach", "1. FSV Mainz 05": "Mainz",
    "TSG 1899 Hoffenheim": "Hoffenheim", "1. FC Köln": "FC Koln",
    "FC St. Pauli 1910": "St Pauli", "Hamburger SV": "Hamburg", "RB Leipzig": "RB Leipzig",
    "FC Internazionale Milano": "Inter", "AC Milan": "Milan", "SSC Napoli": "Napoli",
    "AS Roma": "Roma", "Hellas Verona FC": "Verona", "Bologna FC 1909": "Bologna",
    "Paris Saint-Germain FC": "Paris SG", "Olympique de Marseille": "Marseille",
    "Olympique Lyonnais": "Lyon", "Stade Rennais FC 1901": "Rennes",
    "AS Saint-Étienne": "St Etienne", "Le Havre AC": "Le Havre", "Lille OSC": "Lille",
    "AFC Ajax": "Ajax", "PSV": "PSV Eindhoven", "AZ": "AZ Alkmaar", "NEC": "Nijmegen",
    "Feyenoord Rotterdam": "Feyenoord", "Fortuna Sittard": "For Sittard",
    "Sport Lisboa e Benfica": "Benfica", "Sporting Clube de Portugal": "Sp Lisbon",
    "FC Porto": "Porto", "Sporting Clube de Braga": "Sp Braga", "Vitória SC": "Guimaraes",
}


class TestResolver:
    @pytest.mark.parametrize("api,csv", API_TO_CSV.items())
    def test_api_names(self, csv_names, api, csv):
        assert TeamResolver(csv_names).resolve(api) == csv

    @pytest.mark.parametrize("api", ["Paris FC", "Inter Miami CF", "Real Oviedo", "Manchester FC"])
    def test_never_guesses_a_different_club(self, csv_names, api):
        # Clubs without training history stay themselves rather than borrowing one
        assert TeamResolver(csv_names).resolve(api) == api

    def test_known_names_pass_through(self, csv_names):
        r = TeamResolver(csv_names)
        assert all(r.resolve(n) == n for n in csv_names)

    def test_ambiguous_containment_is_refused(self):
        r = TeamResolver(["Bristol City", "Bristol Rovers"])
        assert r.resolve("Bristol") == "Bristol"

    def test_a_new_team_can_later_be_matched(self):
        r = TeamResolver(["Arsenal"])
        assert r.resolve("Almere City FC") == "Almere City FC"
        r.add("Almere City")
        assert r.resolve("Almere City FC") == "Almere City"

    def test_source_aliases_stay_local(self, csv_names):
        ucl = TeamResolver(csv_names, aliases=UCL_ALIASES)
        assert ucl.resolve("Paris") == "Paris SG" and ucl.resolve("Atleti") == "Ath Madrid"
        assert ucl.resolve("B. Dortmund") == "Dortmund" and ucl.resolve("Bayern München") == "Bayern Munich"
        assert TeamResolver(csv_names).resolve("Paris") == "Paris"

    def test_resolve_frame(self, csv_names):
        df = pd.DataFrame({"HomeTeam": ["Arsenal FC", None], "AwayTeam": ["Chelsea FC", "Wolves"]})
        out = TeamResolver(csv_names).resolve_frame(df)
        assert out.HomeTeam.tolist()[0] == "Arsenal" and out.AwayTeam.tolist() == ["Chelsea", "Wolves"]
        assert df.HomeTeam[0] == "Arsenal FC"  # input untouched

    def test_normalise(self):
        assert normalise("1. FC Köln") == normalise("FC Koln") == "koln"
        assert normalise("Nott'm Forest") == "nottm forest"


def _season(teams, rounds=6, seed=0):
    rng = np.random.default_rng(seed)
    rows, day = [], pd.Timestamp("2024-08-01")
    for _ in range(rounds):
        for h in teams:
            for a in teams:
                if h == a:
                    continue
                hg, ag = int(rng.poisson(1.5)), int(rng.poisson(1.1))
                rows.append({"Date": day, "HomeTeam": h, "AwayTeam": a, "FTHG": hg, "FTAG": ag,
                             "Result": "H" if hg > ag else "A" if hg < ag else "D", "league": "PL"})
                day += pd.Timedelta(days=3)
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def model():
    m = LeaguePredictor()
    m.train(_season(["Man United", "Man City", "Arsenal", "Wolves"]))
    return m


class TestPredictorUsesTrainedNames:
    def test_api_names_get_the_trained_teams_features(self, model):
        live = model._feats("Manchester United FC", "Wolverhampton Wanderers FC", match_date="2025-06-01")
        trained = model._feats("Man United", "Wolves", match_date="2025-06-01")
        assert live == trained

    def test_predictions_match(self, model):
        assert model.predict_match("Manchester City FC", "Arsenal FC") == model.predict_match("Man City", "Arsenal")

    def test_live_results_update_the_trained_team(self, model):
        before = len(model.team_stats["Man United"]["pts"])
        model._update("Manchester United FC", "Arsenal FC", "H", 2, 0, match_date="2025-06-02")
        assert len(model.team_stats["Man United"]["pts"]) == before + 1
        assert "Manchester United FC" not in model.team_stats

    def test_training_takes_names_as_given(self):
        # Mid-training, a club's first match must not be merged into a similar name
        m = LeaguePredictor()
        m.train(_season(["Sheffield United", "Sheffield Weds", "Leeds", "Hull"], rounds=4))
        assert {"Sheffield United", "Sheffield Weds"} <= set(m.team_stats)
