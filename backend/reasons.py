"""
"Why this pick": a few short, plain reasons shown under each pick.

They are written from templates, not by a language model, so they only
say what the numbers say: our chance, how the price compares with it, and
for football what the model expects (goals, corners, cards, shots) or how
far apart the two teams' ratings are.

`context` runs when the pick is made (it needs the prediction); `why`
runs when the slip is put together, after the price may have moved.
"""

from typing import Any, Dict, List, Optional

MAX_REASONS = 3

# A price this far above / below what our chance is worth is called out
GOOD_PRICE = 0.05
POOR_PRICE = -0.03

# Markets whose reason is the expected count of something (set_pieces / shots means)
_MEAN_OF = {
    "corners_ou": ("corners", "corners"), "home_corners_ou": ("corners_home", "corners"),
    "away_corners_ou": ("corners_away", "corners"), "cards_ou": ("bookings", "bookings"),
    "shots_ou": ("shots", "shots"), "sot_ou": ("sot", "shots on target"),
    "home_shots_ou": ("shots_home", "shots"), "away_shots_ou": ("shots_away", "shots"),
    "home_sot_ou": ("sot_home", "shots on target"), "away_sot_ou": ("sot_away", "shots on target"),
}
_GOALS = ("goals_ou", "btts", "home_goals_ou", "away_goals_ou", "clean_sheet", "win_to_nil", "dc_goals")
_RESULT = ("1x2", "double_chance", "handicap", "win_to_nil", "dc_goals")


def _num(x: Any) -> Optional[float]:
    return float(x) if isinstance(x, (int, float)) else None


def _elo(gap: Any, home: str, away: str) -> Dict[str, Any]:
    """The rating summary from the bare home-minus-away Elo gap (same bands as predict_match_full)."""
    g = _num(gap)
    if g is None:
        return {}
    a = abs(g)
    label = ("Evenly Matched" if a < 30 else "Slight Edge" if a < 80 else "Clear Advantage" if a < 150
             else "Strong Favourite" if a < 250 else "Heavy Favourite")
    return {"label": label, "leading": home if g >= 0 else away, "gap": g}


def context(pred: Dict[str, Any], market: str) -> List[str]:
    """Football reasons from the prediction itself (at most two)."""
    out: List[str] = []
    home, away = pred.get("home") or "Home", pred.get("away") or "Away"
    xh, xa = _num(pred.get("xg_home")), _num(pred.get("xg_away"))
    if market in _GOALS and xh is not None and xa is not None:
        out.append(f"Expected goals: {home} {xh:.1f}, {away} {xa:.1f} ({xh + xa:.1f} in total)")
    if market in _MEAN_OF:
        stat, name = _MEAN_OF[market]
        mean = _num(((pred.get("set_pieces") or {}).get(stat) or {}).get("mean"))
        if mean is not None:
            who = f"{home} " if stat.endswith("_home") else f"{away} " if stat.endswith("_away") else ""
            out.append(f"We expect {who}about {mean:.1f} {name}")
    elo = pred.get("elo") if isinstance(pred.get("elo"), dict) else _elo(pred.get("elo_gap"), home, away)
    if market in _RESULT and elo.get("label") and elo.get("leading"):
        gap = _num(elo.get("gap"))
        if elo["label"] == "Evenly Matched":
            out.append("The two teams' ratings are close")
        else:
            out.append(f"{elo['label']}: {elo['leading']} rated {abs(gap or 0):.0f} points higher")
    return out[:2]


def price(prob: float, odds: Optional[float], source: Optional[str]) -> Optional[str]:
    """How the price compares with our chance (real prices only)."""
    if not odds or odds <= 1 or source not in ("sportybet", "bookmaker"):
        return None
    edge = prob * odds - 1
    where = "SportyBet" if source == "sportybet" else "The market"
    implied = round(100 / odds)
    if edge >= GOOD_PRICE:
        return f"{where}'s price implies {implied}%, below our chance: good value"
    if edge <= POOR_PRICE:
        return f"{where}'s price implies {implied}%, a little above our chance"
    return f"{where}'s price implies {implied}%, about what we think"


def why(prob: float, odds: Optional[float], source: Optional[str], extra: Optional[List[str]] = None) -> List[str]:
    """Up to three reasons: our chance, the price, then the football context."""
    out = [f"Our model gives it {round(prob * 100)}%"]
    p = price(prob, odds, source)
    if p:
        out.append(p)
    out += list(extra or [])
    return out[:MAX_REASONS]
