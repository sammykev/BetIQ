from datetime import datetime, timezone

import pytest

import racket_markets as rkm
import racket_matchday as rmd
import racket_predictions as rp
import table_tennis_model as ttm
import tennis_model as tm

TABLE = {"186": ("rk_winner", "winner"), "188": ("rk_set_handicap", "set_handicap"),
         "187": ("rk_games_handicap", "games_handicap"), "189": ("rk_total_games", "total_games"),
         "199": ("rk_correct_score", "correct_score"), "202": ("rk_set_winner", "s_winner"),
         "204": ("rk_set_total", "s_total")}


@pytest.fixture(autouse=True)
def markets(monkeypatch):
    monkeypatch.setitem(rkm.MARKETS, "tennis", TABLE)
    monkeypatch.setitem(rkm.MARKETS, "table_tennis", TABLE)


def _o(oid, odds, desc=""):
    return {"id": oid, "odds": str(odds), "isActive": 1, "desc": desc}


EV = {"eventId": "sr:match:1", "homeTeamName": "Sinner, Jannik", "awayTeamName": "Alcaraz, Carlos",
      "estimateStartTime": int(datetime(2026, 10, 1, 12, tzinfo=timezone.utc).timestamp() * 1000),
      "_tournament": "ATP · ATP Beijing, China Men Singles",
      "markets": [
          {"id": "186", "outcomes": [_o("4", 1.8), _o("5", 2.0)]},
          {"id": "188", "specifier": "hcp=-1.5", "outcomes": [_o("1714", 2.6), _o("1715", 1.45)]},
          {"id": "187", "specifier": "hcp=-2.5", "outcomes": [_o("1714", 2.1), _o("1715", 1.7)]},
          {"id": "189", "specifier": "total=22.5", "outcomes": [_o("12", 1.9), _o("13", 1.9)]},
          {"id": "189", "specifier": "total=20.5", "outcomes": [_o("12", 1.5), _o("13", 2.5)]},
          {"id": "199", "outcomes": [_o("a", 3.0, "2:0"), _o("b", 4.0, "2:1"), _o("c", 4.5, "0:2"), _o("d", 5, "1:2")]},
          {"id": "202", "specifier": "setnr=1", "outcomes": [_o("4", 1.8), _o("5", 1.95)]},
          {"id": "204", "specifier": "setnr=2|total=9.5", "outcomes": [_o("12", 1.9), _o("13", 1.85)]},
          {"id": "999", "outcomes": [_o("1", 1.5)]},
      ]}


def test_offers_read_every_line_with_ids():
    offs = rkm.offers(EV, "tennis")
    codes = {(o["market"], o["code"]) for o in offs}
    assert ("rk_winner", "1") in codes and ("rk_winner", "2") in codes
    assert ("rk_set_handicap", "H-1.5") in codes and ("rk_set_handicap", "A+1.5") in codes
    assert ("rk_total_games", "O22.5") in codes and ("rk_total_games", "U20.5") in codes
    assert ("rk_correct_score", "2:1") in codes
    assert ("rk_s1_winner", "1") in codes and ("rk_s2_total", "O9.5") in codes
    o = next(o for o in offs if o["market"] == "rk_set_handicap" and o["code"] == "A+1.5")
    assert o["sb"] == {"eventId": "sr:match:1", "marketId": "188", "specifier": "hcp=-1.5", "outcomeId": "1715"}
    assert rkm.main_lines(offs) == {"winner": (1.8, 2.0), "total": (22.5, 1.9, 1.9)}


def test_labels_and_names_in_each_sport():
    offs = {(o["market"], o["code"]): o for o in rkm.offers(EV, "tennis")}
    assert rkm.label(offs[("rk_set_handicap", "H-1.5")], "Sinner", "Alcaraz") == "Sinner -1.5 sets"
    assert rkm.label(offs[("rk_total_games", "O22.5")], "Sinner", "Alcaraz") == "Over 22.5 games"
    assert rkm.label(offs[("rk_s2_total", "U9.5")], "Sinner", "Alcaraz") == "2nd set: Under 9.5 games"
    assert rkm.label(offs[("rk_total_games", "O22.5")], "A", "B", "table_tennis") == "Over 22.5 points"
    assert rkm.market_name("rk_s1_winner") == "1st Set Winner"
    assert rkm.market_name("rk_s2_total", "table_tennis") == "2nd Game Total Points"
    assert rkm.market_name("rk_games_handicap", "table_tennis") == "Points Handicap"


def test_probabilities_are_consistent():
    md = tm.match_dist(0.66, 0.62, 3)
    offs = rkm.offers(EV, "tennis")
    by = {(o["market"], o["code"]): rkm.probability(md, o) for o in offs}
    assert by[("rk_winner", "1")] + by[("rk_winner", "2")] == pytest.approx(1.0)
    assert by[("rk_set_handicap", "H-1.5")] == pytest.approx(md.sets[(2, 0)])
    assert by[("rk_set_handicap", "H-1.5")] + by[("rk_set_handicap", "A+1.5")] == pytest.approx(1.0)
    assert by[("rk_total_games", "O22.5")] + by[("rk_total_games", "U22.5")] == pytest.approx(1.0)
    assert by[("rk_correct_score", "2:1")] == pytest.approx(md.sets[(2, 1)])
    assert by[("rk_s1_winner", "1")] == pytest.approx(md.p_first_set())
    # Table tennis distributions answer the same questions
    tmd = ttm.match_dist(0.53)
    assert rkm.probability(tmd, {"kind": "total_games", "code": "O75.5"}) == pytest.approx(tmd.p_total_over(75.5))


def test_settle():
    res = {"status": "finished", "sets": [2, 1], "games": [[6, 4], [3, 6], [7, 6]], "ret": False}
    assert rkm.settle("rk_winner", "1", res) == "won"
    assert rkm.settle("rk_set_handicap", "H-1.5", res) == "lost"
    assert rkm.settle("rk_set_handicap", "A+1.5", res) == "won"
    assert rkm.settle("rk_games_handicap", "H-2.5", res) == "lost"        # 16-16
    assert rkm.settle("rk_games_handicap", "A+0", res) == "void"
    assert rkm.settle("rk_total_games", "O31.5", res) == "won"
    assert rkm.settle("rk_total_sets", "O2.5", res) == "won"
    assert rkm.settle("rk_correct_score", "2:1", res) == "won"
    assert rkm.settle("rk_odd_even", "EVEN", res) == "won"
    assert rkm.settle("rk_away_set", "Y", res) == "won"
    assert rkm.settle("rk_s2_winner", "2", res) == "won"
    assert rkm.settle("rk_s3_total", "O12.5", res) == "won"
    assert rkm.settle("rk_s3_handicap", "A+1.5", res) == "won"
    assert rkm.settle("rk_winner", "1", {**res, "ret": True}) == "void"
    assert rkm.settle("rk_winner", "1", None) == "pending"
    # Set scores missing: games markets can't be settled
    assert rkm.settle("rk_total_games", "O20.5", {**res, "games": []}) == "void"


def test_predict_prices_every_line():
    p = rp.predict(EV, "tennis", None)                  # no ratings: the market's level
    assert p["sport"] == "tennis" and p["sportybet_event_id"] == "sr:match:1"
    assert p["p_home"] == pytest.approx((1 / 1.8) / (1 / 1.8 + 1 / 2.0), abs=0.002)
    assert p["tip_code"] == "1" and p["model"] == "market"
    assert p["total_line"] == 22.5 and p["tip_goals"].endswith("games")
    assert all(0.5 <= x["prob"] <= 0.985 or x["market"] == "rk_winner" for x in p["rk_markets"])
    assert all(x["sb"]["eventId"] == "sr:match:1" for x in p["rk_markets"])
    # A rated favourite moves the level
    a = tm.Player("Sinner, Jannik", "ATP", 2100, 300)
    b = tm.Player("Alcaraz, Carlos", "ATP", 1900, 300)
    model = {"players": {f"ATP|{tm.name_key(a.name)}": a, f"ATP|{tm.name_key(b.name)}": b}, "avg": tm.DEFAULT_SPW}
    q = rp.predict(EV, "tennis", model)
    assert q["p_home"] > p["p_home"] and q["rated"]


def test_best_of():
    offs = rkm.offers(EV, "tennis")
    assert rp.tennis_best_of("ATP · Wimbledon Men Singles", []) == 5
    assert rp.tennis_best_of("WTA · Wimbledon Women Singles", []) == 3
    assert rp.tennis_best_of("ATP · ATP Beijing", offs) == 3


def test_matchday_flow():
    p = rp.predict(EV, "tennis", None)
    day = {}
    now = datetime(2026, 10, 1, 9, tzinfo=timezone.utc)
    assert rmd.merge_predictions(day, [p], now)
    e = day["sr:match:1"]
    assert e["pred"]["tip_code"] == "1" and e["pred"]["best"]
    live = rmd.parse_live({"eventId": "sr:match:1", "matchStatus": "2nd set", "setScore": "1:0",
                           "gameScore": ["6:4", "2:3"], "pointScore": "15:30", "status": 1})
    assert live == {"id": "sr:match:1", "score": [1, 0], "periods": [[6, 4], [2, 3]], "minute": "S2 · 15:30"}
    assert rmd.apply_live(e, live) and e["result"]["status"] == "live"
    assert rmd.apply_result(e, {"sets": [2, 0], "games": [[6, 4], [6, 3]], "ret": False})
    assert e["grades"]["tip"]["verdict"] == "won"
    assert e["grades"]["games"]["verdict"] == "lost" if e["pred"]["tip_goals"].startswith("Over") else "won"
    s = rmd.day_summary(day.values())
    assert s["finished"] == 1 and s["tip"] == [1, 0]
    pub = rmd.public(e)
    assert pub["score"] == [2, 0] and pub["periods"] == [[6, 4], [6, 3]] and pub["status"] == "finished"
    assert rmd.key("tennis", "2026-10-01") == "betiq:tennis:md:2026-10-01"


def test_calibration_maps_move_the_lines():
    base = rp.predict(EV, "tennis", None)
    model = {"players": {}, "avg": tm.DEFAULT_SPW, "calibration": {"total_games": [[0.55, 0.6], [0.65, 0.72]]}}
    cal = rp.predict(EV, "tennis", model)
    b = {(x["market"], x["code"]): x["prob"] for x in base["rk_markets"]}
    c = {(x["market"], x["code"]): x["prob"] for x in cal["rk_markets"]}
    moved = [k for k in b if k in c and k[0] == "rk_total_games" and abs(b[k] - c[k]) > 1e-3]
    assert moved
    assert all(abs(b[k] - c[k]) < 1e-9 for k in b if k in c and k[0] == "rk_winner")
