"""
The free trial for new accounts: who gets one.

A new account (made since the trial was switched on) gets the trial plan
until sign-up + `days`, once. To stop the same person taking trial after
trial with fresh accounts, each trial is recorded against:

- the account's email, canonical (case, and for Gmail the dots and +tags,
  don't make a new address); throwaway-email domains get no trial;
- the device (an id the site keeps in the browser);
- the verified phone number, when the admin requires one;
- and at most `per_ip_week` trials a week from one IP address (mobile
  networks share addresses, so this is a ceiling, not one each).

check() decides from the Clerk user record and what's been recorded;
main.py records the identifiers of each trial it starts.
"""

import hashlib
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

DEFAULTS = {"enabled": False, "days": 7, "tier": "premium", "since": None,
            "require_phone": False, "per_ip_week": 3}
KEEP_DAYS = 400          # how long a trial's email / phone / device stay recorded

# Throwaway-email services (the common ones)
DISPOSABLE = frozenset("""
mailinator.com guerrillamail.com guerrillamail.net guerrillamail.org sharklasers.com grr.la 10minutemail.com
10minutemail.net temp-mail.org tempmail.com tempmail.net temp-mail.io tempmailo.com tempmail.dev yopmail.com
yopmail.net trashmail.com trashmail.de getnada.com nada.email dispostable.com maildrop.cc fakeinbox.com
throwawaymail.com mohmal.com emailondeck.com mailnesia.com mintemail.com tempr.email moakt.com burnermail.io
spamgourmet.com mailcatch.com mytemp.email tempinbox.com getairmail.com discard.email mail.tm mail.gw
emailfake.com fakemail.net inboxkitten.com mailpoof.com linshiyouxiang.net 33mail.com tmpmail.org tmpmail.net
""".split())

# Why a trial wasn't given, as the site says it (None: nothing to say)
MESSAGES = {
    "email_used": "This email address has already had a free trial.",
    "device_used": "A free trial has already been used on this device.",
    "phone_used": "This phone number has already had a free trial.",
    "ip_limit": "Too many free trials have started from your network this week. Try again in a few days.",
    "disposable_email": "Free trials need a regular email address, not a temporary one.",
    "email_unverified": "Verify your email address to start your free trial.",
    "needs_phone": "Verify your phone number to start your free trial.",
}
# Refusals that won't change: the account stops asking
FINAL = {"email_used", "device_used", "phone_used", "disposable_email"}


def canonical_email(email: str) -> str:
    """The address as a mailbox: lower case, no +tag, and for Gmail no dots."""
    local, _, domain = (email or "").strip().lower().partition("@")
    local = local.split("+", 1)[0]
    if domain in ("gmail.com", "googlemail.com"):
        local, domain = local.replace(".", ""), "gmail.com"
    return f"{local}@{domain}"


def canonical_phone(phone: str) -> str:
    return "".join(ch for ch in (phone or "") if ch.isdigit())


def digest(kind: str, value: str) -> str:
    """How an identifier is recorded: hashed, never the email or number itself."""
    return hashlib.sha256(f"betiq-trial:{kind}:{value}".encode()).hexdigest()[:32]


def primary_email(user: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    emails = user.get("email_addresses") or []
    return next((e for e in emails if e.get("id") == user.get("primary_email_address_id")), emails[0] if emails else None)


def verified(item: Optional[Dict[str, Any]]) -> bool:
    return ((item or {}).get("verification") or {}).get("status") == "verified"


def _tier(meta: Dict[str, Any], now: datetime) -> str:
    import auth
    return auth.tier_from_metadata(meta, now.timestamp())


def identifiers(user: Dict[str, Any], device: str) -> Dict[str, str]:
    """kind → digest for what a trial is recorded against."""
    out = {}
    email = primary_email(user)
    if email and email.get("email_address"):
        out["email"] = digest("email", canonical_email(email["email_address"]))
    phones = [canonical_phone(p.get("phone_number", "")) for p in user.get("phone_numbers") or [] if verified(p)]
    if phones and phones[0]:
        out["phone"] = digest("phone", phones[0])
    if device and 8 <= len(device) <= 100:
        out["device"] = digest("device", device)
    return out


def check(user: Dict[str, Any], cfg: Dict[str, Any], device: str, recorded: Callable[[str, str], Optional[str]],
          ip_trials: int, now: Optional[datetime] = None) -> Dict[str, Any]:
    """{"expires": datetime} when this account gets the trial now, else
    {"reason": …}. `recorded(kind, digest)` → the account a trial was already
    given to under that identifier (or None); `ip_trials` → trials started
    from this IP in the last week."""
    now = now or datetime.now(timezone.utc)
    cfg = {**DEFAULTS, **(cfg or {})}
    meta = user.get("public_metadata") or {}
    if not cfg["enabled"]:
        return {"reason": "trial_off"}
    if meta.get("trial_used"):
        return {"reason": "already_used"}
    if _tier(meta, now) != "free":
        return {"reason": "has_plan"}
    try:
        since = datetime.fromisoformat(str(cfg["since"]).replace("Z", "+00:00"))
        created = datetime.fromtimestamp(int(user["created_at"]) / 1000, timezone.utc)
    except (KeyError, TypeError, ValueError):
        return {"reason": "account_too_old"}
    if created < since:
        return {"reason": "account_too_old"}
    expires = datetime.fromtimestamp(created.timestamp() + int(cfg["days"]) * 86400, timezone.utc)
    if expires <= now:
        return {"reason": "window_passed"}

    email = primary_email(user)
    if not email or not verified(email):
        return {"reason": "email_unverified"}
    if canonical_email(email.get("email_address", "")).split("@")[-1] in DISPOSABLE:
        return {"reason": "disposable_email"}
    ids = identifiers(user, device)
    if cfg["require_phone"] and "phone" not in ids:
        return {"reason": "needs_phone"}
    for kind in ("email", "phone", "device"):
        if kind in ids and recorded(kind, ids[kind]) not in (None, user.get("id")):
            return {"reason": f"{kind}_used"}
    if ip_trials >= int(cfg["per_ip_week"]):
        return {"reason": "ip_limit"}
    return {"expires": expires, "ids": ids}


def settings(body: Dict[str, Any], current: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    """The settings after an admin's change (ValueError if bad). Switching
    the trial on dates it, so only accounts made from then get one."""
    cfg = {**DEFAULTS, **(current or {})}
    if "days" in body:
        try:
            days = int(body["days"])
        except (TypeError, ValueError):
            raise ValueError("days must be a number")
        if not 1 <= days <= 30:
            raise ValueError("days must be 1–30")
        cfg["days"] = days
    if "tier" in body:
        if body["tier"] not in ("lite", "premium"):
            raise ValueError("tier must be lite or premium")
        cfg["tier"] = body["tier"]
    if "per_ip_week" in body:
        try:
            n = int(body["per_ip_week"])
        except (TypeError, ValueError):
            raise ValueError("per_ip_week must be a number")
        if not 1 <= n <= 100:
            raise ValueError("per_ip_week must be 1–100")
        cfg["per_ip_week"] = n
    if "require_phone" in body:
        cfg["require_phone"] = bool(body["require_phone"])
    if "enabled" in body:
        enabled = bool(body["enabled"])
        if enabled and not cfg["enabled"]:
            cfg["since"] = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
        cfg["enabled"] = enabled
    return cfg


def public(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return {k: cfg.get(k, DEFAULTS[k]) for k in DEFAULTS}
