import numpy as np
import pandas as pd
import pytest


@pytest.fixture(scope="module")
def training_df() -> pd.DataFrame:
    """Synthetic training data: 60 matches across 6 Premier League clubs."""
    np.random.seed(42)
    teams = ["Arsenal", "Chelsea", "Liverpool", "Man City", "Tottenham", "Everton"]
    rows = []
    for i in range(60):
        home = teams[i % len(teams)]
        away = teams[(i + 3) % len(teams)]
        fthg = int(np.random.randint(0, 4))
        ftag = int(np.random.randint(0, 4))
        result = "H" if fthg > ftag else ("A" if ftag > fthg else "D")
        rows.append({
            "HomeTeam": home,
            "AwayTeam": away,
            "Result": result,
            "FTHG": float(fthg),
            "FTAG": float(ftag),
        })
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def trained_predictor(training_df):
    """LeaguePredictor trained on synthetic data."""
    from predictor import LeaguePredictor
    predictor = LeaguePredictor()
    predictor.train(training_df)
    return predictor
