import numpy as np
import pandas as pd

import europe_model as em
import main


def _league(teams, league, n, start="2023-08-01", seed=1):
    rng = np.random.default_rng(seed)
    rows, day = [], pd.Timestamp(start)
    for i in range(n):
        h, a = rng.choice(teams, 2, replace=False)
        hg, ag = int(rng.integers(0, 4)), int(rng.integers(0, 3))
        rows.append({"Date": day + pd.Timedelta(days=i * 3), "HomeTeam": h, "AwayTeam": a, "FTHG": hg, "FTAG": ag,
                     "Result": "H" if hg > ag else "A" if hg < ag else "D", "league": league})
    return pd.DataFrame(rows)


def test_european_rows_use_the_csv_before_the_test_and_espn_after():
    start = pd.Timestamp("2025-07-01")
    ucl = pd.DataFrame([{"Date": pd.Timestamp("2024-10-01 20:00"), "HomeTeam": "A", "AwayTeam": "B", "FTHG": 1, "FTAG": 0, "Result": "H"},
                        {"Date": pd.Timestamp("2025-09-17 20:00"), "HomeTeam": "A", "AwayTeam": "C", "FTHG": 1, "FTAG": 0, "Result": "H"}])
    espn = pd.DataFrame([{"Date": pd.Timestamp("2024-10-01"), "HomeTeam": "A", "AwayTeam": "B", "FTHG": 1, "FTAG": 0, "Result": "H", "league": "CL"},
                         {"Date": pd.Timestamp("2025-09-17"), "HomeTeam": "A", "AwayTeam": "C", "FTHG": 1, "FTAG": 0, "Result": "H", "league": "CL"},
                         {"Date": pd.Timestamp("2024-10-03"), "HomeTeam": "D", "AwayTeam": "E", "FTHG": 2, "FTAG": 2, "Result": "D", "league": "EL"}])
    rows = em.european_rows(espn, ucl, start)
    got = sorted((str(r.Date.date()), r.HomeTeam, r.league, bool(r.FromCSV)) for r in rows.itertuples())
    assert got == [("2024-10-01", "A", "CL", True), ("2024-10-03", "D", "EL", False), ("2025-09-17", "A", "CL", False)]


def test_the_main_configuration_trains_on_the_csv_only_before_the_test():
    start = pd.Timestamp("2025-07-01")
    europe = pd.DataFrame([
        {"Date": pd.Timestamp("2024-10-01"), "HomeTeam": "A", "AwayTeam": "B", "FTHG": 1, "FTAG": 0, "Result": "H", "league": "CL", "FromCSV": True, "Europe": True, "KeyHome": "A", "KeyAway": "B"},
        {"Date": pd.Timestamp("2024-10-03"), "HomeTeam": "D", "AwayTeam": "E", "FTHG": 2, "FTAG": 2, "Result": "D", "league": "EL", "FromCSV": False, "Europe": True, "KeyHome": "D", "KeyAway": "E"},
        {"Date": pd.Timestamp("2025-10-03"), "HomeTeam": "D", "AwayTeam": "E", "FTHG": 2, "FTAG": 2, "Result": "D", "league": "EL", "FromCSV": False, "Europe": True, "KeyHome": "D", "KeyAway": "E"}])
    league = _league(["A", "B", "C"], "PL", 10)
    main_rows = em.build(league, pd.DataFrame(), europe, "main", start)
    assert len(main_rows[main_rows["Europe"] == True]) == 2          # the 2024 EL row is left out
    ls_rows = em.build(league, pd.DataFrame(), europe, "extras_strength", start)
    assert ls_rows[ls_rows["Europe"] == True]["StrengthOnly"].all()


def test_check_scores_every_configuration_on_the_same_matches():
    # Two leagues; clubs from league Y only in the extra data
    x = _league(["A", "B", "C", "D"], "PL", 700, start="2021-08-01", seed=2)
    y = _league(["P", "Q", "R", "S"], "NOR", 700, start="2021-08-01", seed=3)
    rng = np.random.default_rng(4)
    eu = []
    for i in range(440):
        h, a = ("A", "P") if i % 2 else ("Q", "B")
        day = pd.Timestamp("2022-09-01") + pd.Timedelta(days=i * 3)
        hg, ag = int(rng.integers(0, 3)), int(rng.integers(0, 3))
        eu.append({"Date": day, "HomeTeam": h, "AwayTeam": a, "FTHG": hg, "FTAG": ag,
                   "Result": "H" if hg > ag else "A" if hg < ag else "D", "league": "EL"})
    v = em.check(x, y, pd.DataFrame(eu), pd.DataFrame(), today=pd.Timestamp("2026-02-01").date(), log=lambda *_: None)
    counts = {n: c["all"]["matches"] for n, c in v["configs"].items()}
    assert len(set(counts.values())) == 1 and counts["main"] > 0
    assert v["naive"]["matches"] == counts["main"]
    # Clubs P-S are only known with the extra league
    assert v["configs"]["extras"]["both_known_here"]["matches"] > v["configs"]["main"]["both_known_here"]["matches"]
    # A plan per competition, with where its log loss comes from
    assert set(v["plan"]) == {"EL"} and v["plan"]["EL"]["model"] in ("main", "europe")
    assert "forecast_hda" in v["diagnostics"]["EL"]["main"]


def test_new_league_files_and_name_clashes(tmp_path, monkeypatch):
    (tmp_path / "new_NOR.csv").write_text(
        "Country,League,Season,Date,Time,Home,Away,HG,AG,Res,PSCH,PSCD,PSCA\n"
        "Norway,Eliteserien,2025,01/08/2025,18:00,Brann,Arsenal,2,1,H,1.8,3.5,4.2\n"
        "Norway,Eliteserien,2025,08/08/2025,18:00,Molde,Brann,,,,1.8,3.5,4.2\n", encoding="utf-8")
    monkeypatch.setattr(main, "EXTRA_DATA_DIR", str(tmp_path))
    df = main._load_extra_leagues({"Arsenal"})
    assert len(df) == 1                                     # the unplayed row is dropped
    r = df.iloc[0]
    assert (r.HomeTeam, r.AwayTeam, r.league, r.B365H, bool(r.Context)) == ("Brann", "Arsenal (NOR)", "NOR", 1.8, True)
    assert main._load_extra_leagues({"Arsenal"}, leagues={"BSA"}).empty


def test_the_europe_model_only_takes_the_competitions_it_predicts_better():
    sc = lambda ll, n=200: {"matches": n, "log_loss": ll}
    verdict = {"adopted": "x", "configs": {
        "main": {"by_competition": {"CL": sc(0.96), "EL": sc(0.97), "UECL": sc(1.01, 150)}},
        "x": {"by_competition": {"CL": sc(0.93), "EL": sc(0.91), "UECL": sc(1.04, 150)}}}}
    assert em.competitions(verdict) == ["CL", "EL"]
    assert em.competitions({"adopted": None, "configs": verdict["configs"]}) == []


def test_calibration_pulls_an_overconfident_model_toward_the_rates():
    import predictor
    base = [0.45, 0.27, 0.28]
    # A model that is far too sure of the home side, which wins half the time
    recs = [{"p": [0.9, 0.05, 0.05], "result": "H" if i % 2 else ("D" if i % 4 == 0 else "A"), "league": "UECL"}
            for i in range(200)]
    cal = em.fit_calibration(recs, base)
    assert cal["w"] < 1 or cal["t"] < 1
    before = em._scores(recs)["log_loss"]
    after = em._scores(em._calibrated(recs, cal))["log_loss"]
    assert after < before - 0.1
    assert abs(sum(predictor.calibrate([0.9, 0.05, 0.05], cal)) - 1) < 1e-9
    assert em.fit_calibration(recs[:10], base) is None


def test_plan_picks_the_model_and_calibration_per_competition():
    verdict = {"adopted": "x", "plan": {
        "CL": {"model": "europe", "calibration": None},
        "UECL": {"model": "main", "calibration": {"t": 0.8, "w": 0.7, "base": [0.4, 0.3, 0.3]}}}}
    assert em.competitions(verdict) == ["CL"]
    assert em.calibrations(verdict) == {"main": {"UECL": verdict["plan"]["UECL"]["calibration"]}, "europe": {}}
    # Without an adopted Europe model, its competitions get nothing from it
    assert em.competitions({**verdict, "adopted": None}) == []


def test_calibration_is_applied_per_competition(trained_predictor):
    p0 = trained_predictor.predict_match("Arsenal", "Chelsea", league="UECL")
    trained_predictor.calibration = {"UECL": {"t": 1.0, "w": 0.0, "base": [0.5, 0.25, 0.25]}}
    try:
        p1 = trained_predictor.predict_match("Arsenal", "Chelsea", league="UECL")
        p2 = trained_predictor.predict_match("Arsenal", "Chelsea", league="PL")
    finally:
        trained_predictor.calibration = {}
    assert (p1["p_home"], p1["p_draw"], p1["p_away"]) == (0.5, 0.25, 0.25)
    assert p2["p_home"] == trained_predictor.predict_match("Arsenal", "Chelsea", league="PL")["p_home"]
    assert p0["p_over25"] == p1["p_over25"]          # goal markets untouched
