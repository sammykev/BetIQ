import confidence_check as cc


def _row(match, prob, won, date="2026-10-01", sport="football", market="1x2", league="E0"):
    return {"sport": sport, "market": market, "code": "1", "prob": prob, "won": won, "date": date,
            "match": match, "league": league, "league_name": "Premier League"}


def test_band_of():
    assert cc.band_of(0.79) is None
    assert cc.band_of(0.80) == "80–85%"
    assert cc.band_of(0.94) == "93–95%"
    assert cc.band_of(0.99) == "97–100%"


def test_wilson_low():
    assert cc.wilson_low(0, 0) is None
    assert 0.83 < cc.wilson_low(95, 100) < 0.90
    assert cc.wilson_low(10, 10) < 1


def test_proven_needs_enough_picks_and_a_clear_lower_bound():
    rows = [{"cal": 0.92, "won": True}] * 60
    assert cc.proven(rows) == 0.9
    assert cc.proven(rows[:20]) is None          # too few
    mixed = [{"cal": 0.92, "won": i % 5 != 0} for i in range(100)]   # 80% hit
    assert cc.proven(mixed) is None                # lower bound ~71%


def test_report_tables_and_tickets():
    rows = []
    for d in range(10):
        date = f"2026-09-{d + 1:02d}"
        for m in range(6):
            rows.append(_row(f"m{d}-{m}", 0.9 + m * 0.01, not (m == 0 and d % 2), date=date))
            rows.append(_row(f"m{d}-{m}", 0.6, False, date=date, market="btts"))   # below 80%: ignored
    rep = cc.report(rows, {})
    assert rep["picks"] == 120
    mk = {c["cell"]: c for c in rep["markets"]}
    assert "football:1x2" in mk and "football:btts" not in mk
    assert mk["football:1x2"]["picks"] == 60 and mk["football:1x2"]["matches"] == 60
    assert rep["leagues"][0]["cell"] == "football:E0"
    t = {x["legs"]: x for x in rep["tickets"]["all"]}
    assert t[5]["days"] == 10 and t[5]["landed"] == 10     # top 5 skip the losing 90% pick
    assert t[10]["days"] == 0                               # never 10 matches in a day
    assert t[3]["said"] < 1


def test_tickets_take_one_pick_per_match():
    rows = [{**_row("a", 0.9, True), "cal": 0.95}, {**_row("a", 0.9, False), "cal": 0.85},
            {**_row("b", 0.9, True), "cal": 0.9}]
    t = cc.tickets(rows, sizes=(2,))
    assert t[0]["days"] == 1 and t[0]["landed"] == 1


def test_print_report_runs(capsys):
    rep = cc.report([_row(f"m{i}", 0.92, True) for i in range(40)], {})
    cc.print_report(rep)
    assert "TICKETS" in capsys.readouterr().out
