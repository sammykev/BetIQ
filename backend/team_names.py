"""
Match live team names to the names the model was trained on.

Fixtures and results come from football-data.org ("Manchester United FC",
"FC Bayern München"); the training CSVs come from football-data.co.uk
("Man United", "Bayern Munich"). National teams come from ESPN and The Odds
API ("USA", "Czechia") and train under the international results CSV's names
("United States", "Czech Republic"). The model keys every team's Elo, form and
head-to-head record by name, so an unmatched name is a brand-new team with
default ratings — its prediction rests on the odds alone.

Resolution, most to least certain:
  1. exact name
  2. ALIASES — clubs whose names differ beyond suffixes and accents
  3. normalised equality — accents, punctuation, "FC"/"AFC"/"CF"-style
     affixes and founding years removed ("1. FC Köln" = "FC Koln")
  4. a known name's words all present in the live name, when exactly one
     known team fits ("Brighton & Hove Albion" → "Brighton",
     "Borussia Dortmund" → "Dortmund")
Anything else stays as-is: merging two different clubs' histories is worse
than leaving a new name unmatched.
"""

import re
import unicodedata
from typing import Dict, Iterable, Optional, Set

# Club-type affixes that carry no identity
_STOPWORDS = {
    "fc", "afc", "cf", "sc", "ac", "as", "ss", "ssc", "us", "acf", "bc", "cfc",
    "rc", "rcd", "cd", "ud", "ca", "sd", "sv", "vfl", "vfb", "tsg", "fsv",
    "ogc", "losc", "aj", "sco", "hsc", "calcio", "club", "de", "del", "la",
    "the", "and", "futbol", "balompie", "football", "e",
    # Eastern European and Greek ones ("FK Shakhtar Donetsk", "PAE AEK", "NK Celje")
    "fk", "pae", "nk", "hnk", "gnk", "pfc",
}

# football-data.org name (normalised) → training CSV name
_ALIAS_SOURCE = {
    # England
    "Manchester United FC": "Man United",
    "Manchester City FC": "Man City",
    "Wolverhampton Wanderers FC": "Wolves",
    "Nottingham Forest FC": "Nott'm Forest",
    "Sheffield Wednesday FC": "Sheffield Weds",
    "Queens Park Rangers FC": "QPR",
    "West Bromwich Albion FC": "West Brom",
    "Peterborough United FC": "Peterboro",
    # Germany
    "FC Bayern München": "Bayern Munich",
    "Eintracht Frankfurt": "Ein Frankfurt",
    "Borussia Mönchengladbach": "M'gladbach",
    "Hamburger SV": "Hamburg",
    "Hertha BSC": "Hertha",
    "SpVgg Greuther Fürth": "Greuther Furth",
    "SV Wehen Wiesbaden": "Wehen",
    # France
    "Paris Saint-Germain FC": "Paris SG",
    "Olympique Lyonnais": "Lyon",
    "Stade Rennais FC 1901": "Rennes",
    "Stade Brestois 29": "Brest",
    "AS Saint-Étienne": "St Etienne",
    "Girondins de Bordeaux": "Bordeaux",
    "Clermont Foot 63": "Clermont",
    # Spain
    "Club Atlético de Madrid": "Ath Madrid",
    "Athletic Club": "Ath Bilbao",
    "RCD Espanyol de Barcelona": "Espanol",
    # Italy
    "FC Internazionale Milano": "Inter",
    # Netherlands
    "NEC": "Nijmegen",
    "PSV": "PSV Eindhoven",
    "AZ": "AZ Alkmaar",
    "Fortuna Sittard": "For Sittard",
    # Portugal
    "Sporting Clube de Portugal": "Sp Lisbon",
    "Sporting CP": "Sp Lisbon",
    "Sporting Clube de Braga": "Sp Braga",
    "SC Braga": "Sp Braga",
    "Vitória SC": "Guimaraes",
    "FC Paços de Ferreira": "Pacos Ferreira",
    "Clube de Futebol Estrela da Amadora": "Estrela",
    # Brazil (football-data.org / ESPN → football-data.co.uk's BRA file)
    "CR Flamengo": "Flamengo RJ",
    "Flamengo": "Flamengo RJ",
    "Botafogo FR": "Botafogo RJ",
    "Botafogo": "Botafogo RJ",
    "Clube Atlético Mineiro": "Atletico-MG",
    "Atlético Mineiro": "Atletico-MG",
    "Atlético-MG": "Atletico-MG",
    "Club Athletico Paranaense": "Athletico-PR",
    "Athletico Paranaense": "Athletico-PR",
    "Athletico-PR": "Athletico-PR",
    "Atlético Clube Goianiense": "Atletico GO",
    "Atlético Goianiense": "Atletico GO",
    "Red Bull Bragantino": "Bragantino",
    "RB Bragantino": "Bragantino",
    "CR Vasco da Gama": "Vasco",
    "Vasco da Gama": "Vasco",
    "Sport Club do Recife": "Sport Recife",
    "Sport Recife": "Sport Recife",
    # National teams: ESPN / The Odds API / FIFA name → international results CSV name
    "USA": "United States",
    "United States of America": "United States",
    "Korea Republic": "South Korea",
    "Republic of Korea": "South Korea",
    "Korea DPR": "North Korea",
    "DPR Korea": "North Korea",
    "Côte d'Ivoire": "Ivory Coast",
    "Congo DR": "DR Congo",
    "Democratic Republic of the Congo": "DR Congo",
    "Czechia": "Czech Republic",
    "Ireland": "Republic of Ireland",
    "Türkiye": "Turkey",
    "Turkiye": "Turkey",
    "Cabo Verde": "Cape Verde",
    "Cape Verde Islands": "Cape Verde",
    "China PR": "China",
    "IR Iran": "Iran",
    "Kyrgyz Republic": "Kyrgyzstan",
    "UAE": "United Arab Emirates",
    "St Kitts and Nevis": "Saint Kitts and Nevis",
    "St Vincent and the Grenadines": "Saint Vincent and the Grenadines",
    "St Lucia": "Saint Lucia",
    "US Virgin Islands": "United States Virgin Islands",
    "Macao": "Macau",
    "Viet Nam": "Vietnam",
    "Lao PDR": "Laos",
    "Swaziland": "Eswatini",
    "East Timor": "Timor-Leste",
    "FYR Macedonia": "North Macedonia",
    "Holland": "Netherlands",
}


def normalise(name: str) -> str:
    """Lowercase ASCII words, without affixes, years or punctuation."""
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9 ]+", " ", s.lower().replace("'", ""))
    words = [w for w in s.split() if w not in _STOPWORDS and not w.isdigit()]
    return " ".join(words)


ALIASES: Dict[str, str] = {normalise(k): v for k, v in _ALIAS_SOURCE.items()}
# Names in the Champions League CSVs (openfootball-style) → training names
UCL_ALIASES = {
    "Atleti": "Ath Madrid",
    "Paris": "Paris SG",
    "Frankfurt": "Ein Frankfurt",
    "Sporting CP": "Sp Lisbon",
}

# Irregular names are matched by alias only, never by word containment
# ("Inter Miami" must not become "Inter").
_ALIAS_ONLY = set(ALIASES.values())


class TeamResolver:
    """Resolves names against a set of known (trained-on) team names."""

    def __init__(self, known: Iterable[str] = (), aliases: Optional[Dict[str, str]] = None):
        """`aliases` adds source-specific names on top of ALIASES — e.g. the
        UCL CSVs' "Paris", which must not apply to live names ("Paris FC")."""
        self._aliases = {**ALIASES, **{normalise(k): v for k, v in (aliases or {}).items()}}
        self._known: Set[str] = set()
        self._by_norm: Dict[str, Set[str]] = {}
        self._cache: Dict[str, str] = {}
        self._misses: Set[str] = set()
        for name in known:
            self.add(name)

    def add(self, name: str) -> None:
        if not name or name in self._known:
            return
        self._known.add(name)
        self._by_norm.setdefault(normalise(name), set()).add(name)
        # A newly known team may be what an earlier miss should have matched
        if self._misses:
            for miss in self._misses:
                self._cache.pop(miss, None)
            self._misses.clear()

    def resolve(self, name: str) -> str:
        if not name or name in self._known:
            return name
        hit = self._cache.get(name)
        if hit is None:
            hit = self._match(name)
            self._cache[name] = hit or name
            if hit is None:
                self._misses.add(name)
        return hit or name

    def _match(self, name: str) -> Optional[str]:
        norm = normalise(name)
        if not norm:
            return None
        alias = self._aliases.get(norm)
        if alias and alias in self._known:
            return alias
        same = self._by_norm.get(norm)
        if same and len(same) == 1:
            return next(iter(same))

        words = set(norm.split())
        candidates = set()
        for known_norm, names in self._by_norm.items():
            k_words = set(known_norm.split())
            if not k_words or len(names) != 1 or names & _ALIAS_ONLY:
                continue
            # Only this direction: "Paris FC" must not become "Paris SG"
            if k_words <= words and max(len(w) for w in k_words) >= 4:
                candidates |= names
        return next(iter(candidates)) if len(candidates) == 1 else None

    def resolve_frame(self, df, cols=("HomeTeam", "AwayTeam")):
        """A copy of a results DataFrame with team columns resolved."""
        out = df.copy()
        for c in cols:
            if c in out.columns:
                out[c] = out[c].map(lambda n: self.resolve(n) if isinstance(n, str) else n)
        return out

    def mapping(self) -> Dict[str, str]:
        """Names that resolved to a different known name."""
        return {k: v for k, v in self._cache.items() if k != v}
