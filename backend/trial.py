"""
The free trial for new accounts: who gets one.

A new account (made since the trial was switched on) gets the trial plan
until sign-up + `days`, once. So that fresh accounts don't mean fresh trials,
each trial is recorded against the account's email as a mailbox: case, and
for Gmail the dots and +tags, don't make a new address. The email must be
verified, and throwaway-email domains get no trial.

check() decides from the Clerk user record and what's been recorded;
main.py records the email of each trial it starts (hashed).
"""

import hashlib
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

DEFAULTS = {"enabled": False, "days": 7, "tier": "premium", "since": None}
KEEP_DAYS = 400          # how long a trial's email stays recorded

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
    "disposable_email": "Free trials need a regular email address, not a temporary one.",
    "email_unverified": "Verify your email address to start your free trial.",
}
# Refusals that won't change: the account stops asking
FINAL = {"email_used", "disposable_email"}


def canonical_email(email: str) -> str:
    """The address as a mailbox: lower case, no +tag, and for Gmail no dots."""
    local, _, domain = (email or "").strip().lower().partition("@")
    local = local.split("+", 1)[0]
    if domain in ("gmail.com", "googlemail.com"):
        local, domain = local.replace(".", ""), "gmail.com"
    return f"{local}@{domain}"


def digest(kind: str, value: str) -> str:
    """How an email is recorded: hashed, never the address itself."""
    return hashlib.sha256(f"betiq-trial:{kind}:{value}".encode()).hexdigest()[:32]


def primary_email(user: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    emails = user.get("email_addresses") or []
    return next((e for e in emails if e.get("id") == user.get("primary_email_address_id")), emails[0] if emails else None)


def verified(item: Optional[Dict[str, Any]]) -> bool:
    return ((item or {}).get("verification") or {}).get("status") == "verified"


def _tier(meta: Dict[str, Any], now: datetime) -> str:
    import auth
    return auth.tier_from_metadata(meta, now.timestamp())


def check(user: Dict[str, Any], cfg: Dict[str, Any], recorded: Callable[[str], Optional[str]],
          now: Optional[datetime] = None) -> Dict[str, Any]:
    """{"expires": datetime, "email": digest} when this account gets the
    trial now, else {"reason": …}. `recorded(digest)` → the account a trial
    was already given to under that email (or None)."""
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
    if not email or not email.get("email_address") or not verified(email):
        return {"reason": "email_unverified"}
    mailbox = canonical_email(email["email_address"])
    if mailbox.split("@")[-1] in DISPOSABLE:
        return {"reason": "disposable_email"}
    key = digest("email", mailbox)
    if recorded(key) not in (None, user.get("id")):
        return {"reason": "email_used"}
    return {"expires": expires, "email": key}


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
    if "enabled" in body:
        enabled = bool(body["enabled"])
        if enabled and not cfg["enabled"]:
            cfg["since"] = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
        cfg["enabled"] = enabled
    return cfg


def public(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return {k: cfg.get(k, DEFAULTS[k]) for k in DEFAULTS}
