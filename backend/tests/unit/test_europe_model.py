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
    x = _league(["A", "B", "C", "D"], "PL", 400, seed=2)
    y = _league(["P", "Q", "R", "S"], "NOR", 400, seed=3)
    rng = np.random.default_rng(4)
    eu = []
    for i in range(220):
        h, a = ("A", "P") if i % 2 else ("Q", "B")
        day = pd.Timestamp("2024-09-01") + pd.Timedelta(days=i * 3)
        hg, ag = int(rng.integers(0, 3)), int(rng.integers(0, 3))
        eu.append({"Date": day, "HomeTeam": h, "AwayTeam": a, "FTHG": hg, "FTAG": ag,
                   "Result": "H" if hg > ag else "A" if hg < ag else "D", "league": "EL"})
    v = em.check(x, y, pd.DataFrame(eu), pd.DataFrame(), today=pd.Timestamp("2026-02-01").date(), log=lambda *_: None)
    counts = {n: c["all"]["matches"] for n, c in v["configs"].items()}
    assert len(set(counts.values())) == 1 and counts["main"] > 0
    assert v["naive"]["matches"] == counts["main"]
    # Clubs P-S are only known with the extra league
    assert v["configs"]["extras"]["both_known_here"]["matches"] > v["configs"]["main"]["both_known_here"]["matches"]


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
