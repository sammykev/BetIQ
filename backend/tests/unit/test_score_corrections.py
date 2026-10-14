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
