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


@pytest.fixture(autouse=True)
def _fresh_matchday_caches():
    """The match-day endpoints' short read caches start empty in each test."""
    import main
    for cache in (main._md_read_cache, main._strip_cache, main._accuracy_cache):
        cache.clear()
    yield


@pytest.fixture(autouse=True)
def _local_development_auth(monkeypatch):
    """Tests run as local development: without CLERK_ISSUER the client's uid
    is trusted (ALLOW_UNVERIFIED_UID). Tests of auth itself set both."""
    import auth
    monkeypatch.setattr(auth, "ALLOW_UNVERIFIED_UID", True)
    yield


@pytest.fixture(autouse=True)
def _no_live_match_pages(monkeypatch):
    """Tests never read SportyBet's real match pages: the optimizer's live
    check sees them as unreadable (picks stay as they are) unless a test
    stands in its own pages."""
    import main
    import sportybet

    async def unreadable(event_id, session=None):
        return None
    monkeypatch.setattr(sportybet, "event_page", unreadable)
    main._live_pages.clear()
    yield


@pytest.fixture(autouse=True)
def _paywall_off(monkeypatch):
    """Tests run with the paywall switched off (admin → Features), as the
    server can't check plans without Clerk keys. Paywall tests switch it on."""
    import main
    monkeypatch.setattr(main, "_paywall_enabled", lambda: False)
    yield
