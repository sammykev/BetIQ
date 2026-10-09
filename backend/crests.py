"""
Club crests by name, from football-data.org's own team lists.

Each tracked competition's clubs (name, short name, crest) are kept in one
Redis hash (KEY), refreshed daily by the server (main._crest_index_refresh)
and topped up from every fixture's crest. A badge request is answered from
it by the exact name, else by whole words (match): every word of a known
name must be in the asked-for one, the known name matching the most words
winning — so "RCD Espanyol de Barcelona" is Espanyol, never Barcelona, and
"Werder Bremen" finds "SV Werder Bremen". No guessing from a hand-kept list
of ids.
"""

import time
from typing import Dict, Iterable, List, Optional, Tuple

import team_names

KEY = "betiq:crests"
MEMO_SECONDS = 600
_memo: Tuple[float, Dict[str, str]] = (0.0, {})


def entries(teams: Iterable[Dict]) -> Dict[str, str]:
    """{name: crest} for each team's full and short name."""
    out: Dict[str, str] = {}
    for t in teams:
        crest = t.get("crest")
        if not crest:
            continue
        for k in ("name", "shortName"):
            n = (t.get(k) or "").strip()
            if n:
                out.setdefault(n, crest)
    return out


def _words(name: str) -> List[str]:
    return team_names.normalise(name).split()


def _hit(k: str, n: str) -> bool:
    return n == k or (len(k) >= 5 and n.startswith(k))


def match(name: str, known: Iterable[str]) -> Optional[str]:
    """The known name `name` stands for, by whole words (None if unclear)."""
    nw = _words(name)
    if not nw:
        return None
    best: Optional[Tuple[int, int, str]] = None     # (-words matched, first position, key)
    wider = []
    for key in known:
        kw = _words(key)
        if not kw:
            continue
        if kw == nw:
            return key
        at = [next((i for i, n in enumerate(nw) if _hit(k, n)), -1) for k in kw]
        if min(at) >= 0:
            cand = (-len(kw), min(at), key)
            if best is None or cand < best:
                best = cand
        elif all(n in kw for n in nw):
            wider.append(key)
    if best:
        return best[2]
    # A short name inside exactly one known name ("Bayern" → "FC Bayern München")
    return wider[0] if len(wider) == 1 else None


def load(r) -> Dict[str, str]:
    """The whole index (kept in memory for MEMO_SECONDS)."""
    global _memo
    at, idx = _memo
    if idx and time.time() - at < MEMO_SECONDS:
        return idx
    try:
        idx = r.hgetall(KEY) or {}
    except Exception:
        idx = {}
    _memo = (time.time(), idx)
    return idx


def lookup(r, name: str) -> Optional[str]:
    """The crest for a club name, or None."""
    if not name or r is None:
        return None
    idx = load(r)
    if not idx:
        return None
    hit = idx.get(name.strip())
    if hit:
        return hit
    key = match(name, idx.keys())
    if not key:
        # A training-data name ("Ath Madrid") under football-data's ("Club Atlético de Madrid")
        alias = _FROM_ALIAS.get(name.strip())
        key = (alias if alias in idx else match(alias, idx.keys())) if alias else None
    return idx.get(key) if key else None


# training CSV name → a football-data.org name (team_names' aliases, reversed)
_FROM_ALIAS: Dict[str, str] = {}
for _fd, _csv in getattr(team_names, "_ALIAS_SOURCE", {}).items():
    _FROM_ALIAS.setdefault(_csv, _fd)


def save(r, found: Dict[str, str]) -> int:
    global _memo
    if not found or r is None:
        return 0
    r.hset(KEY, mapping=found)
    _memo = (0.0, {})
    return len(found)
