"""
The shared model: stored in chunks in Redis, loaded by the API server instead
of training, and published by whichever side trained it.
"""

import json
import time

import numpy as np
import pytest

import main
import model_store
from predictor import MODEL_CACHE_VERSION, LeaguePredictor
from tests.unit.test_model_upgrades import season


class FakeRedis:
    def __init__(self):
        self.data = {}

    def set(self, key, value, ex=None):
        self.data[key] = value if isinstance(value, bytes) else str(value).encode()

    def get(self, key):
        return self.data.get(key)

    def delete(self, *keys):
        for k in keys:
            self.data.pop(k, None)


@pytest.fixture
def redis(monkeypatch):
    r = FakeRedis()
    monkeypatch.setattr(model_store, "_client", lambda url=None: r)
    return r


@pytest.fixture(scope="module")
def trained():
    m = LeaguePredictor()
    m.train(season())
    return m


class TestStore:
    def test_round_trip_in_chunks(self, redis, monkeypatch):
        monkeypatch.setattr(model_store, "CHUNK", 10)
        blob = bytes(range(256)) * 3
        meta = model_store.publish(blob, 7, {"source": "test"})
        assert meta["chunks"] == 77 and meta["source"] == "test"
        got, got_meta = model_store.fetch(7)
        assert got == blob and got_meta["sha256"] == meta["sha256"]

    def test_republishing_removes_the_old_chunks(self, redis):
        model_store.publish(b"first", 7)
        model_store.publish(b"second", 7)
        assert model_store.fetch(7)[0] == b"second"
        assert sum(k.startswith("betiq:model:v7:") and not k.endswith("meta") for k in redis.data) == 1

    def test_too_old_is_ignored(self, redis):
        model_store.publish(b"x", 7)
        meta = json.loads(redis.data["betiq:model:v7:meta"])
        meta["trained_at"] = time.time() - 48 * 3600
        redis.set("betiq:model:v7:meta", json.dumps(meta))
        assert model_store.fetch(7, max_age_hours=36) is None

    def test_corrupted_or_missing_chunks_are_ignored(self, redis):
        model_store.publish(b"model", 7)
        gen = json.loads(redis.data["betiq:model:v7:meta"])["gen"]
        redis.set(f"betiq:model:v7:{gen}:0", b"tampered")
        assert model_store.fetch(7) is None
        redis.delete(f"betiq:model:v7:{gen}:0")
        assert model_store.fetch(7) is None

    def test_other_versions_are_separate(self, redis):
        model_store.publish(b"old model", 4)
        assert model_store.fetch(5) is None

    def test_without_redis(self, monkeypatch):
        monkeypatch.setattr(model_store, "_client", lambda url=None: None)
        assert model_store.fetch(7) is None and model_store.describe(7) is None
        with pytest.raises(RuntimeError):
            model_store.publish(b"x", 7)


def test_model_survives_bytes(trained):
    loaded = LeaguePredictor.from_bytes(trained.to_bytes())
    for pair in (("Arsenal", "Chelsea"), ("Leeds", "Hull")):
        assert loaded.predict_match(*pair) == trained.predict_match(*pair)


class TestServerModel:
    @pytest.fixture(autouse=True)
    def no_disk_cache(self, monkeypatch):
        monkeypatch.setattr(LeaguePredictor, "load_cache", classmethod(lambda cls, m: None))
        monkeypatch.setattr(LeaguePredictor, "save_cache", lambda self, m: None)

    def test_loads_the_shared_model_instead_of_training(self, redis, trained, monkeypatch):
        model_store.publish(trained.to_bytes(), MODEL_CACHE_VERSION, {"source": "github-actions"})
        monkeypatch.setattr(main, "_train_new", lambda c: pytest.fail("should not train"))
        p = main._load_or_train(season(), 0.0)
        assert p.predict_match("Arsenal", "Chelsea") == trained.predict_match("Arsenal", "Chelsea")

    def test_trains_and_shares_when_nothing_is_published(self, redis, trained, monkeypatch):
        monkeypatch.setattr(main, "_train_new", lambda c: trained)
        assert main._load_or_train(season(), 0.0) is trained
        _, meta = model_store.fetch(MODEL_CACHE_VERSION)
        assert meta["source"] == "api-server"

    def test_trains_when_redis_is_down(self, monkeypatch, trained):
        def boom(url=None):
            raise ConnectionError("down")
        monkeypatch.setattr(model_store, "_client", boom)
        monkeypatch.setattr(main, "_train_new", lambda c: trained)
        assert main._load_or_train(season(), 0.0) is trained


def test_training_script_skips_without_redis(monkeypatch, capsys):
    import train_model
    monkeypatch.delenv("UPSTASH_REDIS_URL", raising=False)
    assert train_model.main() == 0
    assert "Skipping" in capsys.readouterr().out


def test_admin_data_status_reports_the_shared_model(redis, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(main, "ADMIN_SECRET", "s3cret")
    assert TestClient(main.app).get("/api/admin/data-status", headers={"X-Admin-Secret": "s3cret"}).json()["shared_model"] is None
    model_store.publish(b"model", MODEL_CACHE_VERSION, {"source": "github-actions", "rows": 38012})
    shared = TestClient(main.app).get("/api/admin/data-status", headers={"X-Admin-Secret": "s3cret"}).json()["shared_model"]
    assert (shared["source"], shared["rows"], shared["size"]) == ("github-actions", 38012, 5)
    assert shared["trained_at"].endswith("+00:00") and shared["max_age_hours"] == model_store.MAX_AGE_HOURS
