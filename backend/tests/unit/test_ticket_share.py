"""Sharing a ticket by a public link: /t/<id>."""

import json

from fastapi.testclient import TestClient

import main
from tests.unit.test_ticket_delete import as_user, enforced, redis, ticket  # noqa: F401


def test_share_link_shows_the_ticket_without_the_account(redis, enforced):
    uid = "user_alice"
    redis.set(f"betiq:user:{uid}:tickets", json.dumps([ticket("AAA"), ticket("BBB", "won")]))
    c = TestClient(main.app)
    sid = c.post("/api/user/tickets/share", json={"code": "BBB"}, headers=as_user(uid)).json()["id"]
    # The same ticket keeps its link
    assert c.post("/api/user/tickets/share", json={"code": "BBB"}, headers=as_user(uid)).json()["id"] == sid

    r = c.get(f"/api/share/{sid}")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == "BBB" and body["status"] == "won"
    assert body["legs"][0]["home"] == "A"
    assert uid not in r.text and "source" not in body


def test_share_needs_the_owner_and_an_existing_ticket(redis, enforced):
    redis.set("betiq:user:user_bob:tickets", json.dumps([ticket("BOB1")]))
    c = TestClient(main.app)
    assert c.post("/api/user/tickets/share", json={"code": "BOB1"}).status_code == 401
    # Alice can't share Bob's code: it isn't among her tickets
    assert c.post("/api/user/tickets/share", json={"code": "BOB1"}, headers=as_user("user_alice")).status_code == 404


def test_unknown_or_removed_share_is_404(redis, enforced):
    uid = "user_alice"
    redis.set(f"betiq:user:{uid}:tickets", json.dumps([ticket("AAA")]))
    c = TestClient(main.app)
    assert c.get("/api/share/nope1234").status_code == 404
    assert c.get("/api/share/bad!id").status_code == 404
    sid = c.post("/api/user/tickets/share", json={"code": "AAA"}, headers=as_user(uid)).json()["id"]
    c.delete("/api/user/tickets?code=AAA", headers=as_user(uid))
    assert c.get(f"/api/share/{sid}").status_code == 404
