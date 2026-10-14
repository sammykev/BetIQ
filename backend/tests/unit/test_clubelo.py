"""ClubElo ratings fetched by the server (clubelo.py)."""

from datetime import date

import clubelo

CSV = """Rank,Club,Country,Level,Elo,From,To
1,Real Madrid,ESP,1,2010.53,2026-10-10,2026-10-16
None,Pafos,CYP,1,1555.2,2026-10-10,2026-10-16
"""


class Resp:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class Client:
    def __init__(self):
        self.urls = []

    def get(self, url, timeout=None):
        self.urls.append(url)
        return Resp(CSV)


class R(dict):
    def set(self, k, v):
        self[k] = v

    def get(self, k):
        return dict.get(self, k)

    def hkeys(self, k):
        return list(dict.get(self, k, {}).keys())

    def hset(self, k, f, v):
        self.setdefault(k, {})[f] = v

    def hgetall(self, k):
        return dict.get(self, k, {})


def test_parse_and_refresh_with_backfill():
    assert clubelo.parse(CSV)[0] == {"Real Madrid": 2010.5, "Pafos": 1555.2}
    r, c = R(), Client()
    st = clubelo.refresh(r, c, today=date(2024, 9, 4))
    assert st["clubs"] == 2 and st["history_added"] == 10 and st["history_missing"] == 0    # Mondays since 1 July
    assert clubelo.latest(r)["Pafos"] == 1555.2
    assert set(clubelo.history(r)) == set(clubelo.weeks(date(2024, 9, 4)))
    assert clubelo.refresh(r, Client(), today=date(2024, 9, 4))["history_added"] == 0
