import numpy as np
import pandas as pd
import pytest
from datetime import date, timedelta


@pytest.fixture(scope="module")
def training_df() -> pd.DataFrame:
    """Synthetic training data: 60 matches across 6 Premier League clubs."""
    np.random.seed(42)
    teams = ["Arsenal", "Chelsea", "Liverpool", "Man City", "Tottenham", "Everton"]
    base_date = date(2023, 8, 1)
    rows = []
    for i in range(60):
        home = teams[i % len(teams)]
        away = teams[(i + 3) % len(teams)]
        fthg = int(np.random.randint(0, 4))
        ftag = int(np.random.randint(0, 4))
        result = "H" if fthg > ftag else ("A" if ftag > fthg else "D")
        rows.append({
            "Date": pd.Timestamp(base_date + timedelta(weeks=i)),
            "HomeTeam": home,
            "AwayTeam": away,
            "Result": result,
            "FTHG": float(fthg),
            "FTAG": float(ftag),
            "league": "PL",
        })
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def trained_predictor(training_df):
    """LeaguePredictor trained on synthetic data."""
    from predictor import LeaguePredictor
    predictor = LeaguePredictor()
    predictor.train(training_df)
    return predictor


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    """Each test starts with empty rate-limit and admin-lockout counters."""
    import security
    security._limiter = security.SlidingWindow()
    security._admin_failures = security.SlidingWindow()
    security._logged = security.SlidingWindow()
    yield
