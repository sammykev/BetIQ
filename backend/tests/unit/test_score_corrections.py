"""Wrong finals: SportyBet's youth / simulated games never land on the real
match, and ESPN's final corrects a weaker source's (and the tickets on it)."""

from datetime import datetime, timezone

import matchday
import results_feed
import tickets


def _ev(home, away, tournament, score="2:3", hour=12):
    ts = int(datetime(2026, 10, 13, hour, tzinfo=timezone.utc).timestamp() * 1000)
    return {"eventId": "sr:match:1", "homeTeamName": home, "awayTeamName": away, "estimateStartTime": ts,
            "status": 4, "setScore": score, "matchStatus": "Ended", "_tournament": tournament}


def test_youth_women_and_simulated_games_are_skipped():
    assert results_feed.parse_sportybet(_ev("Arsenal FC", "LOSC Lille", "International Youth · UEFA Youth League")) is None
    assert results_feed.parse_sportybet(_ev("Villarreal SRL", "Napoli SRL", "Simulated Reality League · UCL SRL")) is None
    assert results_feed.parse_sportybet(_ev("Villarreal CF", "SSC Napoli U20", "Friendly")) is None
    assert results_feed.parse_sportybet(_ev("Arsenal (W)", "Lyon (W)", "UEFA Women's Champions League")) is None
    got = results_feed.parse_sportybet(_ev("Arsenal", "Lille", "International Clubs · UEFA Champions League", "3:0", 19))
    assert got and (got["hg"], got["ag"], got["ko"]) == (3, 0, "2026-10-13T19:00+00:00")
    assert results_feed.parse_sportybet(_ev("Brazil", "Argentina", "International · FIFA World Cup Qualification", "1:0"))


def test_a_result_hours_away_from_kickoff_is_another_match():
    entries = {"k": {"home": "Arsenal FC", "away": "Lille OSC", "date": "2026-10-13", "time": "19:00"}}
    youth = {"date": "2026-10-13", "home": "Arsenal FC", "away": "LOSC Lille", "status": "finished", "hg": 2, "ag": 3,
             "ko": "2026-10-13T12:00+00:00"}
    real = {**youth, "hg": 3, "ag": 0, "ko": "2026-10-13T19:00+00:00"}
    assert matchday.match_results(entries, [youth]) == []
    assert matchday.match_results(entries, [youth, real]) == [("k", real)]
    assert matchday.match_results(entries, [{k: v for k, v in youth.items() if k != "ko"}])   # no time: date only


def _entry(score, source):
    return {"home": "Arsenal FC", "away": "Lille OSC", "date": "2026-10-13", "time": "19:00", "pred": {},
            "result": {"status": "finished", "hg": score[0], "ag": score[1], "source": source}}


def test_espn_corrects_a_sportybet_final_but_not_the_other_way():
    e = _entry((2, 3), "sportybet")
    espn = {"status": "finished", "hg": 3, "ag": 0, "source": "espn"}
    assert matchday.apply_result(e, espn)
    assert (e["result"]["hg"], e["result"]["ag"], e["result"]["source"]) == (3, 0, "espn")
    assert e["result"]["corrected"]["was"] == [2, 3] and e["result"]["corrected"]["from"] == "sportybet"
    assert not matchday.apply_result(e, espn)                                   # once
    e2 = _entry((3, 0), "espn")
    matchday.apply_result(e2, {"status": "finished", "hg": 2, "ag": 3, "source": "sportybet"})
    assert (e2["result"]["hg"], e2["result"]["ag"]) == (3, 0)


def test_weaker_finals_are_rechecked_for_a_while():
    now = datetime(2026, 10, 14, 5, tzinfo=timezone.utc)
    assert matchday.needs_result(_entry((2, 3), "sportybet"), now)
    assert not matchday.needs_result(_entry((2, 3), "sportybet"), now, recheck=False)
    assert not matchday.needs_result(_entry((3, 0), "espn"), now)
    assert not matchday.needs_result(_entry((2, 3), "sportybet"), datetime(2026, 10, 16, tzinfo=timezone.utc))


def test_tickets_on_a_corrected_match_are_graded_again():
    leg = {"market": "1x2", "code": "1", "home": "Arsenal FC", "away": "Lille OSC", "date": "2026-10-13"}
    t = {"status": "pending", "legs": [leg]}
    res = {"status": "finished", "hg": 2, "ag": 3, "source": "sportybet"}
    tickets.settle(t, lambda l: res)
    assert t["status"] == "lost" and leg["graded_on"] == "2-3"
    res = {"status": "finished", "hg": 3, "ag": 0, "source": "espn", "corrected": {"was": [2, 3]}}
    assert not tickets.settle(t, lambda l: res)                       # only with recheck
    assert tickets.settle(t, lambda l: res, recheck=True)
    assert t["status"] == "won" and leg["regraded"] == {"was": "lost", "score": "3-0"}
    assert not tickets.settle(t, lambda l: res, recheck=True)         # once


def test_daily_slips_are_graded_again_and_a_wrong_cut_is_marked():
    import daily_slips
    from datetime import date, timedelta
    day = (date.today() - timedelta(days=1)).isoformat()      # recent: within tickets.EXPIRE_DAYS
    pick = lambda h, a: {"home": h, "away": a, "date": day, "market": "1x2", "code": "1", "status": "pending"}
    cut = {"target": 10, "status": "pending", "picks": [pick("Arsenal FC", "Lille OSC"), pick("Inter", "Brugge")],
           "booking": {"code": "OLD1"}}
    results = {"Arsenal FC": {"status": "finished", "hg": 2, "ag": 3, "source": "sportybet"}, "Inter": None}
    daily_slips.grade(cut, lambda p: results[p["home"]])
    assert cut["status"] == "lost" and cut["picks"][0]["graded_on"] == "2-3"
    new = {"target": 10, "status": "pending", "picks": [pick("Leipzig", "PSV")],
           "replaces": {"code": "OLD1", "cut_by": "Arsenal FC v Lille OSC"}}
    doc = {"slips": [new], "cut": [cut]}
    results = {"Arsenal FC": {"status": "finished", "hg": 3, "ag": 0, "source": "espn", "corrected": {"was": [2, 3]}},
               "Inter": {"status": "finished", "hg": 2, "ag": 0, "source": "espn"}, "Leipzig": None}
    moved = daily_slips.recheck_day(doc, lambda p: results[p["home"]])
    assert moved == [("10", "lost", "won")]
    assert cut["cut_in_error"] and cut["picks"][0]["regraded"] == {"was": "lost", "score": "3-0"}
    assert new["replaces"]["in_error"] and new["replaces"]["cut_by"] is None and new["status"] == "pending"
    assert daily_slips.recheck_day(doc, lambda p: results[p["home"]]) == []      # once


def test_slip_reliability_keeps_bands_with_enough_legs():
    import main
    rep = {"legs_by_prob_given": {"70–80%": {"settled": 690, "win_rate": 0.674, "avg_prob_given": 0.737},
                                  "<50%": {"settled": 4, "win_rate": 0.75, "avg_prob_given": 0.336}}}
    got = main.slip_reliability(rep)
    assert got == [{"lo": 0.7, "hi": 0.8, "said": 0.737, "won": 0.674, "settled": 690}]
    assert main.slip_reliability(None) == []


def test_a_correction_takes_the_trusted_sources_stats():
    e = _entry((2, 3), "sportybet")
    e["result"]["corners"] = [9, 9]                       # the youth game's
    matchday.apply_result(e, {"status": "finished", "hg": 3, "ag": 0, "source": "espn", "corners": [7, 2],
                              "shots": [20, 5]})
    assert e["result"]["corners"] == [7, 2] and e["result"]["shots"] == [20, 5]


def test_a_cut_slips_other_picks_are_graded_and_its_status_stays_lost():
    import daily_slips
    pick = lambda h: {"home": h, "away": "X", "date": "2026-10-13", "market": "1x2", "code": "1", "status": "pending"}
    s = {"target": 5, "status": "lost", "picks": [{**pick("A"), "status": "lost"}, pick("B")]}
    assert daily_slips.grade(s, lambda p: {"status": "finished", "hg": 1, "ag": 0})
    assert s["picks"][1]["status"] == "won" and s["status"] == "lost"


def test_a_leg_no_source_settles_in_a_week_is_unknown():
    import daily_slips
    old = {"home": "A", "away": "B", "date": "2026-09-01", "time": "18:00", "market": "corners_ou", "code": "O95",
           "status": "pending"}
    t = {"status": "pending", "legs": [dict(old), {**old, "home": "C", "market": "1x2", "code": "1"}]}
    results = {"A": {"status": "finished", "hg": 1, "ag": 0}, "C": {"status": "finished", "hg": 1, "ag": 0}}
    tickets.settle(t, lambda l: results[l["home"]])                # no corners came in
    assert [l["status"] for l in t["legs"]] == ["unknown", "won"] and t["status"] == "open"
    s = {"target": 5, "status": "pending", "picks": [dict(old)]}
    daily_slips.grade(s, lambda p: results[p["home"]])
    assert s["picks"][0]["status"] == "unknown" and s["status"] == "open"
