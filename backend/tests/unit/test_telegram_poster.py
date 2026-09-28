"""
Posting the daily odds to a Telegram channel (telegram_poster.py): the
message (every slip, code and pick), links, errors, and the daily tick.
"""

import asyncio
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

import main
import telegram_poster as tg
from tests.unit.test_user_endpoints import FakeRedis


def pick(home, away, label, odds, time="18:00"):
    return {"home": home, "away": away, "date": "2026-09-28", "time": time, "label": label, "odds": odds, "code": "x"}


DOC = {"date": "2026-09-28", "slips": [
    {"target": 10, "status": "pending", "total_odds": 10.24, "booking": {"code": "ABC123"},
     "picks": [pick("Arsenal", "Chelsea", "Over 1.5 goals", 1.22), pick("PSG", "Lens", "PSG or draw", 1.1, "19:45")]},
    {"target": 15, "status": "none", "picks": []},
    {"target": 20, "status": "pending", "total_odds": 20.3, "booking": None,
     "picks": [pick("Barça <B>", "Real & Co", "Home win", 1.5)]},
]}


class TestCompose:
    def test_every_slip_code_and_pick_in_lagos_time(self):
        text = tg.compose(DOC, "https://predict-withbetiq.vercel.app")
        assert text.startswith("<b>BetIQ Daily Odds · Monday 28 September</b>")
        assert "<b>10x slip · 10.24 odds</b>\nSportyBet code: <code>ABC123</code>" in text
        assert "• Arsenal v Chelsea (19:00): Over 1.5 goals @ 1.22" in text      # 18:00 UTC
        assert "(20:45)" in text and "15x" not in text
        assert "SportyBet code: on the site shortly" in text
        assert "Barça &lt;B&gt; v Real &amp; Co" in text                       # escaped for HTML
        assert "https://predict-withbetiq.vercel.app/daily" in text and "18+" in text

    def test_nothing_to_post(self):
        assert tg.compose({"date": "2026-09-28", "slips": [{"target": 10, "status": "none", "picks": []}]}, "https://x") is None

    def test_too_long_keeps_the_codes(self):
        many = {**DOC, "slips": [{**DOC["slips"][0], "picks": [pick(f"Home{i}" * 5, f"Away{i}" * 5, "Over 1.5 goals", 1.2)
                                                                   for i in range(80)]}]}
        text = tg.compose(many, "https://x")
        assert len(text) <= tg.MAX_LENGTH and "<code>ABC123</code>" in text and "Home1" not in text


def test_links_and_errors():
    assert tg.message_url("@betiqdaily", 42) == "https://t.me/betiqdaily/42"
    assert tg.message_url("-1001234567890", 7) == "https://t.me/c/1234567890/7"
    assert "BotFather" in tg.error_text(401, {"description": "Unauthorized"})
    assert "administrator" in tg.error_text(403, {"description": "Forbidden: bot is not a member of the channel chat"})
    assert "TELEGRAM_CHAT_ID" in tg.error_text(400, {"description": "Bad Request: chat not found"})


@pytest.fixture
def site(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(main, "_get_redis", lambda: fake)
    monkeypatch.setattr(main, "_daily_memory", {})
    monkeypatch.setattr(main, "_post_ready", lambda doc, now: True)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "@betiqdaily")
    for k in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET"):
        monkeypatch.delenv(k, raising=False)
    sent = []

    async def publish(text):
        sent.append(text)
        return {"ok": True, "id": "42", "url": "https://t.me/betiqdaily/42"}
    monkeypatch.setattr(tg, "publish", publish)

    async def identity(request):
        return "secret", None
    monkeypatch.setattr(main, "_admin_identity", identity)
    today = date.today().isoformat()
    fake.kv[main.DAILY_KEY.format(today)] = json.dumps({**DOC, "date": today})
    return fake, sent


def test_posts_to_the_channel_once_when_switched_on(site):
    fake, sent = site
    c = TestClient(main.app)
    asyncio.run(main._daily_tick())
    assert sent == []
    got = c.put("/api/admin/post/telegram", json={"enabled": True}).json()
    assert got["enabled"] and got["configured"] and "<code>ABC123</code>" in got["preview"]
    asyncio.run(main._daily_tick())
    asyncio.run(main._daily_tick())
    assert len(sent) == 1
    assert c.get("/api/admin/post/telegram").json()["today"]["url"] == "https://t.me/betiqdaily/42"


def test_missing_settings(site, monkeypatch):
    monkeypatch.delenv("TELEGRAM_CHAT_ID")
    c = TestClient(main.app)
    assert c.get("/api/admin/post/telegram").json()["missing"] == ["TELEGRAM_CHAT_ID"]
    assert c.post("/api/admin/post/telegram/now", json={}).status_code == 400
