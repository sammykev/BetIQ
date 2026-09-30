"""
Codes at the odds shown, with no suspended picks: SportyBet makes a code
even with suspended selections in it, so they're left out and the code
made again; the optimizer checks each pick on SportyBet's match page first.
"""

import asyncio
import time

import booking_slip
import main
import optimizer
import sportybet
from booking_slip import to_sportybet
from tests.unit.test_booking_slip import EVENT, before_kickoff, sel  # noqa: F401 (fixture)

ONE = ("sr:match:111", "1", "1")
OVER = ("sr:match:111", "18", "12")


def book(selections, replies):
    posted = []

    async def fetch(date):
        return [EVENT]

    async def post(chosen):
        posted.append(chosen)
        return replies[min(len(posted), len(replies)) - 1]
    return asyncio.run(to_sportybet(selections, fetch, sportybet.find_event, post)), posted


class TestBooking:
    def test_a_code_with_a_suspended_pick_is_made_again_without_it(self, before_kickoff):
        first = {"code": "DIRTY", "url": "u1", "odds": {ONE: 1.85}, "unavailable": {OVER}}
        clean = {"code": "CLEAN", "url": "u2", "odds": {ONE: 1.85}, "unavailable": set()}
        result, posted = book([sel(code="1"), sel(home="Arsenal", away="Chelsea", market="goals_ou", code="O25")],
                              [first, clean])
        assert result["code"] == "CLEAN" and len(posted) == 2
        assert [x["outcomeId"] for x in posted[1]] == ["1"]            # the suspended one left out
        assert [p["status"] for p in result["picks"]] == ["booked", "unavailable"]
        assert "Suspended" in result["picks"][1]["reason"] and result["total_odds"] == 1.85

    def test_the_booked_price_is_sportybets_and_a_moved_one_is_flagged(self, before_kickoff):
        reply = {"code": "C", "url": "u", "odds": {ONE: 1.95}, "unavailable": set()}
        result, _ = book([sel(code="1", odds=1.85)], [reply])
        p = result["picks"][0]
        assert p["odds"] == 1.95 and p["shown_odds"] == 1.85 and result["price_changes"] == 1
        assert result["total_odds"] == 1.95
        same, _ = book([sel(code="1", odds=1.95)], [reply])
        assert "shown_odds" not in same["picks"][0] and same["price_changes"] == 0

    def test_inactive_or_unpriced_outcomes_in_the_reply_count_as_suspended(self):
        data = {"shareCode": "X", "outcomes": [
            {"eventId": "e1", "marketId": "1", "outcomeId": "1", "odds": "1.80", "isActive": 1},
            {"eventId": "e1", "marketId": "1", "outcomeId": "2", "odds": "3.10", "isActive": 0},
            {"eventId": "e2", "marketId": "18", "outcomeId": "12", "odds": "1.00"},
            {"eventId": "e3", "marketId": "18", "outcomeId": "13"}]}
        odds, unavailable = sportybet._share_prices(data)
        assert odds == {("e1", "1", "1"): 1.8}
        assert unavailable == {("e1", "1", "2"), ("e2", "18", "12")}   # no price given isn't suspension


class TestLiveState:
    PAGE = {"eventId": "sr:match:1", "markets": [
        {"id": "1", "specifier": "", "status": 0, "outcomes": [
            {"id": "1", "odds": "1.90", "isActive": 1}, {"id": "2", "odds": "4.0", "isActive": 0}]},
        {"id": "18", "specifier": "total=2.5", "status": 2, "outcomes": [{"id": "12", "odds": "1.7", "isActive": 1}]}]}

    def ids(self, market, spec, outcome):
        return {"eventId": "sr:match:1", "marketId": market, "specifier": spec, "outcomeId": outcome}

    def test_open_suspended_and_missing(self):
        assert booking_slip.live_state(self.PAGE, self.ids("1", "", "1")) == ("open", 1.9)
        assert booking_slip.live_state(self.PAGE, self.ids("1", "", "2")) == ("suspended", None)
        assert booking_slip.live_state(self.PAGE, self.ids("18", "total=2.5", "12")) == ("suspended", None)
        assert booking_slip.live_state(self.PAGE, self.ids("18", "total=3.5", "12")) == ("missing", None)
        assert booking_slip.live_state(None, self.ids("1", "", "1")) == ("missing", None)

    def test_pick_ids_use_the_picks_own_or_ours(self):
        own = {"sb": {"eventId": "sr:match:9", "marketId": "225", "specifier": "total=160.5", "outcomeId": "12"}}
        assert booking_slip.pick_ids(own, None)["eventId"] == "sr:match:9"
        ours = booking_slip.pick_ids({"market": "1x2", "code": "1"}, "sr:match:1")
        assert ours == {"eventId": "sr:match:1", "marketId": "1", "specifier": "", "outcomeId": "1"}
        assert booking_slip.pick_ids({"market": "1x2", "code": "1"}, None) is None


def opt(home, odds, prob=0.8, market="1x2", code="1"):
    return optimizer.Option(home, f"{home} B", "2026-10-01", "18:00", "L", market, "1X2", code, f"{home} win",
                            prob, odds, "sportybet")


class TestOptimizerCheck:
    def test_suspended_picks_are_dropped_and_prices_taken_from_sportybet(self, monkeypatch):
        """_live_states answers per pick: the solver runs again without the
        suspended one, with SportyBet's current prices."""
        groups = [[opt("A", 1.5)], [opt("C", 1.5)], [opt("E", 1.5, prob=0.7)]]
        calls = []

        async def states(picks, events, market_map):
            calls.append([p["home"] for p in picks])
            out = {}
            for p in picks:
                k = main._pick_key(p)
                out[k] = ("suspended", None) if p["home"] == "A" else ("open", 1.6 if p["home"] == "C" else p["odds"])
            return out
        monkeypatch.setattr(main, "_live_states", states)
        got = asyncio.run(_check_loop(groups, 2.0, 3.0))
        assert [p["home"] for p in got["picks"]] == ["C", "E"]
        assert {p["home"]: p["odds"] for p in got["picks"]}["C"] == 1.6
        assert got["live_check"]["removed"][0]["home"] == "A" and got["live_check"]["repriced"] == 1
        assert len(calls) >= 2

    def test_live_pages_are_read_several_at_a_time_and_reused(self, monkeypatch):
        monkeypatch.setattr(main, "_live_pages", {})
        reads = []

        async def page(eid):
            reads.append(eid)
            await asyncio.sleep(0.1)
            return {"eventId": eid, "markets": []}
        monkeypatch.setattr(sportybet, "event_page", page)
        start = time.monotonic()
        got = asyncio.run(main._live_pages_for([f"e{i}" for i in range(16)]))
        assert len(got) == 16 and time.monotonic() - start < 0.6
        asyncio.run(main._live_pages_for(["e1", "e2"]))
        assert len(reads) == 16                                       # reused within the minute

    def test_a_started_match_is_left_out(self, monkeypatch):
        async def pages(ids):
            return {i: {"eventId": i, "estimateStartTime": int((time.time() - 60) * 1000), "markets": []} for i in ids}
        monkeypatch.setattr(main, "_live_pages_for", pages)
        pick = {"home": "A", "away": "B", "date": "2026-10-01", "market": "1x2", "code": "1"}
        got = asyncio.run(main._live_states([pick], {("A", "B", "2026-10-01"): {"eventId": "sr:match:5"}}, {}))
        assert got[main._pick_key(pick)] == ("started", None)


async def _check_loop(groups, lo, hi):
    """The optimizer endpoint's check loop on given groups (the rest of the
    request, predictions and filters, isn't needed for it)."""
    result, check = await main._solve_checked(groups, lo, hi, (lo * hi) ** 0.5, optimizer.MAX_GAMES, {}, {})
    return {**result, "live_check": check}
