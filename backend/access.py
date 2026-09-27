"""
Feature switches: every page, tab and feature the admin can turn on or off,
the subscription tier it needs, and the accounts given access to it.

Each feature has a state:
  on       everyone sees it; using it needs `tier` (free / lite / premium)
  testers  only the accounts in `allow` (and admins) see it: for features
           that aren't ready yet
  off      nobody sees it

Accounts in `allow` get the feature whatever their tier, in either on or
testers state. The admin page edits this (main.py /api/admin/features);
the site reads it per visitor from /api/features and the API enforces it.
"""

from typing import Any, Dict, List, Optional

import auth

STATES = ("on", "testers", "off")
MAX_ALLOWED = 200   # accounts per feature

# id, label, group, what it covers, default state, default tier
REGISTRY: List[Dict[str, str]] = [
    {"id": "daily_slips", "label": "Daily odds", "group": "Pages",
     "about": "Daily 10, 15 and 20 odds slips from picks the model rates 85%+",
     "state": "on", "tier": "premium"},
    {"id": "optimizer", "label": "Optimizer: build a slip", "group": "Pages",
     "about": "Build a slip for a target odds", "state": "on", "tier": "premium"},
    {"id": "code_check", "label": "Optimizer: check a SportyBet code", "group": "Pages",
     "about": "Rate a pasted booking code and improve it", "state": "on", "tier": "lite"},
    {"id": "record", "label": "Record", "group": "Pages",
     "about": "The public track record page", "state": "on", "tier": "free"},
    {"id": "dashboard", "label": "Dashboard", "group": "Pages",
     "about": "Each account's tickets and stats", "state": "on", "tier": "free"},
    {"id": "sport.basketball", "label": "Basketball", "group": "Sports",
     "about": "Basketball predictions", "state": "testers", "tier": "free"},
    {"id": "sport.tennis", "label": "Tennis", "group": "Sports",
     "about": "Tennis predictions from SportyBet's listings", "state": "testers", "tier": "free"},
    {"id": "sport.table_tennis", "label": "Table tennis", "group": "Sports",
     "about": "Table tennis predictions from SportyBet's listings", "state": "testers", "tier": "free"},
    {"id": "match_analysis", "label": "Full match analysis", "group": "Features",
     "about": "The match page: every market, xG, Elo, form", "state": "on", "tier": "lite"},
    {"id": "ai_preview", "label": "AI match preview", "group": "Features",
     "about": "The written preview with this week's team news", "state": "on", "tier": "lite"},
    {"id": "ai_chat", "label": "AI betting assistant", "group": "Features",
     "about": "The chat that builds slips on request", "state": "on", "tier": "premium"},
    {"id": "bet_slip", "label": "Bet slip", "group": "Features",
     "about": "Adding picks to a slip and booking it on SportyBet", "state": "on", "tier": "free"},
    {"id": "live_stats", "label": "Live stats", "group": "Features",
     "about": "Live stats and timelines on scores, match pages and tickets", "state": "on", "tier": "free"},
]
DEFAULTS = {f["id"]: f for f in REGISTRY}

# URL sport names (/api/sports/{sport}) to feature ids; football is always on
SPORT_FEATURES = {"basketball": "sport.basketball", "tennis": "sport.tennis",
                  "table-tennis": "sport.table_tennis", "table_tennis": "sport.table_tennis"}


def merged(saved: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Every feature's settings: the saved ones over the defaults. Unknown or
    malformed saved values fall back to the default."""
    saved = saved if isinstance(saved, dict) else {}
    out = {}
    for fid, d in DEFAULTS.items():
        s = saved.get(fid) if isinstance(saved.get(fid), dict) else {}
        state = s.get("state") if s.get("state") in STATES else d["state"]
        tier = s.get("tier") if s.get("tier") in auth.TIERS else d["tier"]
        allow = [a for a in (s.get("allow") or []) if isinstance(a, dict) and isinstance(a.get("id"), str)]
        out[fid] = {"state": state, "tier": tier, "allow": allow[:MAX_ALLOWED]}
    return out


def validate(change: Dict[str, Any]) -> Dict[str, Any]:
    """An admin's edit to one feature: only known fields with valid values."""
    out: Dict[str, Any] = {}
    if "state" in change:
        if change["state"] not in STATES:
            raise ValueError(f"state must be one of {', '.join(STATES)}")
        out["state"] = change["state"]
    if "tier" in change:
        if change["tier"] not in auth.TIERS:
            raise ValueError(f"tier must be one of {', '.join(auth.TIERS)}")
        out["tier"] = change["tier"]
    if "allow" in change:
        allow = change["allow"]
        if not isinstance(allow, list) or len(allow) > MAX_ALLOWED:
            raise ValueError(f"allow must be a list of at most {MAX_ALLOWED} accounts")
        clean, seen = [], set()
        for a in allow:
            uid = str((a or {}).get("id", "")).strip() if isinstance(a, dict) else ""
            if not uid or len(uid) > 64 or uid in seen:
                continue
            seen.add(uid)
            clean.append({"id": uid, "label": str(a.get("label") or uid)[:120]})
        out["allow"] = clean
    return out


def granted(f: Dict[str, Any], uid: Optional[str], admin: bool) -> bool:
    """Given this feature by name (or an admin)."""
    return admin or bool(uid and any(a["id"] == uid for a in f["allow"]))


def visible(f: Dict[str, Any], uid: Optional[str], admin: bool) -> bool:
    """Whether this visitor sees the feature at all (tier aside)."""
    if f["state"] == "off":
        return False
    if f["state"] == "testers":
        return granted(f, uid, admin)
    return True


def allowed(f: Dict[str, Any], uid: Optional[str], admin: bool, tier: str, paywall_on: bool) -> bool:
    """Whether this visitor may use it: sees it, and has the tier (or was
    given it, or the paywall is off)."""
    if not visible(f, uid, admin):
        return False
    return not paywall_on or granted(f, uid, admin) or auth.tier_at_least(tier, f["tier"])


def for_visitor(features: Dict[str, Dict[str, Any]], uid: Optional[str], admin: bool) -> Dict[str, Dict[str, Any]]:
    """What the site needs per feature: shown at all, the tier it needs, and
    whether this account was given it (then no tier is needed)."""
    return {fid: {"visible": visible(f, uid, admin), "tier": f["tier"], "granted": granted(f, uid, admin)}
            for fid, f in features.items()}
