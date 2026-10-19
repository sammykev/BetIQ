"""
SportyBet-only leagues are a gzip'd blob: the link job reads them with the
binary Redis client, not the app's text one (UnicodeDecodeError: byte 0x8b).
"""

import collect_sb_leagues
import main
import model_store
from jobs import sportybet_links


class TextRedis:
    def get(self, key):
        raise UnicodeDecodeError("utf-8", b"\x1f\x8b", 1, 2, "invalid start byte")


class BinaryRedis:
    def __init__(self):
        self.kv = {}

    def get(self, key):
        return self.kv.get(key)

    def set(self, key, value, **kw):
        self.kv[key] = value


def test_publish_reads_the_leagues_with_the_binary_client(monkeypatch):
    binary = BinaryRedis()
    collect_sb_leagues.save(binary, collect_sb_leagues.empty())
    monkeypatch.setattr(main, "_get_redis", lambda: TextRedis())
    monkeypatch.setattr(model_store, "_client", lambda url=None: binary)
    assert sportybet_links._publish_sb_leagues([]) == []
