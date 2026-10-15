"""
The weekly league drift check (league_review.py): verdicts per league, a
failing league kept until it recovers, what is left out in flag vs auto
mode, the admin's settings, and the admin alert.
"""

import asyncio

import pytest

import league_review as lr
import market_accuracy
import telegram_poster as tg


@pytest.fixture(autouse=True)
def settled(monkeypatch):
    # Each entry carries its settled picks directly: [(market, code, prob, won)]
    monkeypatch.setattr(market_accuracy, "settle", lambda e: e.get("_s") or [])


def entries(league, n, prob, won_every, name=None):
    """n matches in `league`, one pick each at `prob`, won on every
    `won_every`-th match (0 = never)."""
    return [{"league": league, "league_name": name or league,
             "_s": [("1x2", "1", prob, bool(won_every) and i % won_every == 0)]} for i in range(n)]


def by(result):
    return {x["league"]: x for x in result["leagues"]}


class TestReview:
    def test_a_league_well_short_fails_and_one_on_target_is_ok(self):
        bad = entries("PL", 40, 0.7, 3, "Premier League")      # 35% vs 70%
        good = entries("LL", 40, 0.5, 2)                        # 50% vs 50%
        res = lr.review(bad + good + [{"league": "PL", "_s": []}, {"_s": [("1x2", "1", 0.5, True)]}])
        assert res["matches"] == 80
        assert by(res)["PL"]["status"] == "pause" and by(res)["PL"]["failing"]
        assert by(res)["LL"]["status"] == "ok" and not by(res)["LL"]["failing"]
        assert res["failing"] == ["PL"] and res["newly_failing"] == ["PL"] and res["recovered"] == []
        assert res["leagues"][0]["league"] == "PL"
        assert "came in 35% vs 70% said, over 40 matches" == by(res)["PL"]["why"]

    def test_too_few_matches_cant_fail(self):
        res = lr.review(entries("SA", 8, 0.8, 0))
        assert by(res)["SA"]["status"] == "few" and res["failing"] == []

    def test_failing_stays_until_it_reviews_ok(self):
        thin = lr.review(entries("PL", 5, 0.7, 0), failing_before=["PL", "BL"])
        assert set(thin["failing"]) == {"PL", "BL"} and thin["newly_failing"] == []
        assert by(thin)["BL"]["why"] == "no settled picks"
        back = lr.review(entries("PL", 40, 0.5, 2), failing_before=["PL"])
        assert back["failing"] == [] and back["recovered"] == ["PL"]


class TestBlocked:
    STATE = {"latest": {"failing": ["PL", "BL"]}, "overrides": {"BL": "on", "LL": "off"}}

    def test_flag_mode_only_leaves_out_pinned_off(self):
        assert lr.blocked({**self.STATE, "mode": "flag"}) == {"LL"}
        assert lr.blocked({"latest": {"failing": ["PL"]}}) == set()     # flag is the default

    def test_auto_mode_pauses_failing_unless_pinned_on(self):
        assert lr.blocked({**self.STATE, "mode": "auto"}) == {"PL", "LL"}


class TestSettings:
    def test_valid_update(self):
        got = lr.settings({"mode": "auto", "overrides": {"PL": "off", "LL": "auto", "SA": None}}, {})
        assert got == {"mode": "auto", "overrides": {"PL": "off"}}
        assert lr.settings({}, {"mode": "auto", "overrides": {"X": "on"}}) == {"mode": "auto", "overrides": {"X": "on"}}

    @pytest.mark.parametrize("body", [{"mode": "off"}, {"overrides": []}, {"overrides": {"PL": "maybe"}},
                                      {"overrides": {" ": "off"}}, {"overrides": {"x" * 61: "off"}}])
    def test_bad_updates_refused(self, body):
        with pytest.raises(ValueError):
            lr.settings(body, {})


class TestAlerts:
    def test_message_only_when_something_changed(self):
        res = lr.review(entries("PL", 40, 0.7, 3, "Premier League"))
        text = lr.message(res, "flag")
        assert text.startswith("BetIQ league drift check") and "Premier League: came in 35%" in text
        assert "Flag mode: still being picked" in text
        assert "Paused" in lr.message(res, "auto")
        assert lr.message(lr.review(entries("LL", 40, 0.5, 2)), "flag") is None
        back = lr.review(entries("PL", 40, 0.5, 2, "Premier League"), failing_before=["PL"])
        assert lr.message(back, "auto") == "BetIQ league drift check\nRecovered: Premier League"

    def test_remember_keeps_the_latest_and_a_short_history(self):
        state = {}
        res = lr.review(entries("PL", 40, 0.7, 3))
        for i in range(lr.HISTORY + 3):
            state = lr.remember(state, res, f"2026-10-{i + 1:02d}")
        assert state["latest"]["at"] == f"2026-10-{lr.HISTORY + 3:02d}"
        assert len(state["history"]) == lr.HISTORY and state["history"][0]["failing"] == ["PL"]

    def test_banner_for_a_fresh_check_with_failing_leagues(self):
        state = lr.remember({}, lr.review(entries("PL", 40, 0.7, 3, "Premier League")), "x")
        banner = lr.alert(state, 1)
        assert banner["title"] == "League drift: 1 league falling short" and "Premier League" in banner["detail"]
        assert "still being picked" in banner["detail"]
        assert "paused" in lr.alert({**state, "mode": "auto"}, 1)["detail"]
        assert lr.alert(state, 8) is None and lr.alert(state, None) is None and lr.alert({}, 1) is None


class TestNotifyAdmin:
    def test_needs_its_own_chat(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "@public")
        monkeypatch.delenv("TELEGRAM_ADMIN_CHAT_ID", raising=False)
        sent = []

        async def publish(text, chat=None):
            sent.append((text, chat))
            return {"ok": True}

        monkeypatch.setattr(tg, "publish", publish)
        assert asyncio.run(tg.notify_admin("x"))["ok"] is False and sent == []
        monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "4242")
        assert asyncio.run(tg.notify_admin("a <b> & c"))["ok"]
        assert sent == [("a &lt;b&gt; &amp; c", "4242")]
