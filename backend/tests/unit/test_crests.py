"""Club crests by name from football-data.org's team lists (crests.py)."""

import crests

TEAMS = [
    {"name": "FC Barcelona", "shortName": "Barça", "crest": "https://crests.football-data.org/81.png"},
    {"name": "RCD Espanyol de Barcelona", "shortName": "Espanyol", "crest": "https://crests.football-data.org/80.png"},
    {"name": "SV Werder Bremen", "shortName": "Bremen", "crest": "https://crests.football-data.org/12.png"},
    {"name": "Borussia Dortmund", "shortName": "Dortmund", "crest": "https://crests.football-data.org/4.png"},
    {"name": "Borussia Mönchengladbach", "shortName": "M'gladbach", "crest": "https://crests.football-data.org/18.png"},
    {"name": "Club Atlético de Madrid", "shortName": "Atleti", "crest": "https://crests.football-data.org/78.png"},
    {"name": "Real Madrid CF", "shortName": "Real Madrid", "crest": "https://crests.football-data.org/86.png"},
]


class FakeRedis:
    def __init__(self):
        self.h = {}

    def hset(self, key, mapping):
        self.h.update(mapping)

    def hgetall(self, key):
        return dict(self.h)


def test_names_find_their_own_club_never_one_named_after_the_city():
    r = FakeRedis()
    crests.save(r, crests.entries(TEAMS))
    look = lambda n: (crests.lookup(r, n) or "").rsplit("/", 1)[-1]
    assert look("RCD Espanyol de Barcelona") == "80.png"
    assert look("Espanyol") == "80.png"
    assert look("FC Barcelona") == look("Barcelona") == "81.png"
    assert look("Werder Bremen") == "12.png"
    assert look("Borussia Dortmund") == look("Dortmund") == "4.png"
    assert look("Borussia Monchengladbach") == "18.png"
    assert look("Atletico Madrid") == look("Ath Madrid") == "78.png"     # accents; a training-data name
    assert look("Real Madrid") == "86.png"
    assert crests.lookup(r, "Real Sociedad") is None                      # unknown: no guess
    assert crests.lookup(r, "Madrid") is None                             # two clubs fit: no guess
