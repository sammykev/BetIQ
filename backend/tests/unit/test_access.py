"""
Feature switches (access.py): on / testers / off, the tier each feature
needs, accounts given a feature, and the API enforcing them (main.py).
"""

import json

import pytest
from fastapi.testclient import TestClient

import access
import auth
import main
from tests.unit.test_user_endpoints import FakeRedis


def feat(state="on", tier="free", allow=()):
    return {"state": state, "tier": tier, "allow": [{"id": a, "label": a} for a in allow]}


class TestRules:
    def test_defaults_and_bad_saved_values(self):
        got = access.merged({"optimizer": {"state": "sideways", "tier": "gold", "allow": ["x", {"id": "u1"}]}})
        assert set(got) == set(access.DEFAULTS)
        assert got["optimizer"] == {"state": "on", "tier": "premium", "allow": [{"id": "u1"}]}
        assert got["sport.tennis"]["state"] == "testers"     # not ready: testers only to start with
        assert got["bet_slip"]["tier"] == "free"

    def test_states(self):
        on, testers, off = feat(), feat("testers", allow=["t1"]), feat("off", allow=["t1"])
        assert access.visible(on, None, False)
        assert not access.visible(testers, "u1", False) and access.visible(testers, "t1", False)
        assert access.visible(testers, None, True)            # admins see what testers see
        assert not access.visible(off, "t1", False) and not access.visible(off, None, True)

    def test_tiers(self):
        lite = feat(tier="lite")
        assert not access.allowed(lite, "u1", False, "free", True)
        assert access.allowed(lite, "u1", False, "lite", True)
        assert access.allowed(lite, "u1", False, "premium", True)
        assert not access.allowed(feat(tier="premium"), "u1", False, "lite", True)
        assert access.allowed(lite, "u1", False, "free", False)   # paywall off: everyone
        # Given the feature: no tier needed
        assert access.allowed(feat(tier="premium", allow=["u1"]), "u1", False, "free", True)

    def test_validate(self):
        assert access.validate({"state": "off", "junk": 1}) == {"state": "off"}
        with pytest.raises(ValueError):
            access.validate({"tier": "gold"})
        got = access.validate({"allow": [{"id": "u1", "label": "a@b.c"}, {"id": "u1"}, {"id": ""}, "x"]})
        assert got == {"allow": [{"id": "u1", "label": "a@b.c"}]}

    def test_tier_from_metadata(self):
        later, earlier = "2999-01-01T00:00:00Z", "2000-01-01T00:00:00Z"
        assert auth.tier_from_metadata({"subscription": "lite", "subscription_expires": later}) == "lite"
        assert auth.tier_from_metadata({"subscription": "premium", "subscription_expires": later}) == "premium"
        assert auth.tier_from_metadata({"subscription": "premium", "subscription_expires": earlier}) == "free"
        assert auth.tier_from_metadata({"subscription": "gold", "subscription_expires": later}) == "free"
        assert auth.tier_at_least("premium", "lite") and not auth.tier_at_least("lite", "premium")


@pytest.fixture
def api(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(main, "_get_redis", lambda: fake)
    monkeypatch.setattr(main, "_features_cache", [0.0, None])
    monkeypatch.setattr(main, "_paywall_enabled", lambda: True)
    monkeypatch.setattr(auth, "premium_enforced", lambda: True)
    who = {"via": None, "uid": None, "tier": "free"}

    async def identity(request):
        return who["via"], who["uid"]

    async def tier(uid):
        return who["tier"]
    monkeypatch.setattr(main, "_admin_identity", identity)
    monkeypatch.setattr(auth, "user_tier", tier)
    return fake, who


class TestApi:
    def test_features_for_a_visitor(self, api):
        _, who = api
        got = TestClient(main.app).get("/api/features").json()
        assert got["features"]["sport.tennis"]["visible"] is False
        assert got["features"]["optimizer"] == {"visible": True, "tier": "premium", "granted": False}
        who["via"] = "clerk"
        got = TestClient(main.app).get("/api/features").json()
        assert got["admin"] and got["features"]["sport.tennis"]["visible"]

    def test_admin_switches_and_testers(self, api):
        fake, who = api
        c = TestClient(main.app)
        who["via"] = "secret"
        assert c.put("/api/admin/features/sport.tennis", json={"allow": [{"id": "t1", "label": "me@x.com"}]}).status_code == 200
        assert c.put("/api/admin/features/nope", json={"state": "on"}).status_code == 404
        assert c.put("/api/admin/features/optimizer", json={"tier": "gold"}).status_code == 400
        listed = {f["id"]: f for f in c.get("/api/admin/features").json()["features"]}
        assert listed["sport.tennis"]["allow"] == [{"id": "t1", "label": "me@x.com"}]
        assert "betiq:config:features" in fake.kv
        # A tester sees tennis; anyone else gets feature_off
        who.update(via=None, uid="t1")
        assert c.get("/api/features").json()["features"]["sport.tennis"]["visible"]
        who["uid"] = "u2"
        r = c.get("/api/sports/tennis/leagues")
        assert (r.status_code, r.json()["detail"]) == (404, "feature_off")
        # Non-admins can't change switches
        assert c.put("/api/admin/features/optimizer", json={"state": "off"}).status_code == 403

    def test_switched_off_and_tiers_are_enforced(self, api):
        fake, who = api
        c = TestClient(main.app)
        who["uid"] = "u1"
        r = c.post("/api/optimizer/code", json={"code": "ABC123"})
        assert (r.status_code, r.json()["detail"]) == (402, "lite_required")
        who["tier"] = "lite"
        assert c.post("/api/optimizer", json={"min_odds": 2, "max_odds": 5}).json()["detail"] == "premium_required"
        # Switched off: gone for everyone, whatever the tier
        feats = access.merged(None)
        feats["optimizer"]["state"] = "off"
        fake.kv["betiq:config:features"] = json.dumps(feats)
        main._features_cache[:] = [0.0, None]
        who["tier"] = "premium"
        r = c.post("/api/optimizer", json={"min_odds": 2, "max_odds": 5})
        assert (r.status_code, r.json()["detail"]) == (404, "feature_off")
