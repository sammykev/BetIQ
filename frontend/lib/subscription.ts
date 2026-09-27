// Pure helpers for /api/subscribe — kept out of the route file (Next.js only
// allows HTTP-method exports there) so they can be unit-tested.

const PERIOD_DAYS = 30;
// Kept in private metadata so a reference can't be redeemed twice. Monthly
// payments mean this stays tiny; the cap just bounds it.
export const MAX_STORED_REFERENCES = 100;

/** Paystack returns metadata as an object, or as a JSON string on some clients. */
export function paidByUser(tx: { reference?: string; metadata?: unknown }, userId: string): boolean {
  let meta = tx.metadata;
  if (typeof meta === "string") {
    try { meta = JSON.parse(meta); } catch { meta = null; }
  }
  const metaUser = meta && typeof meta === "object" ? (meta as { userId?: unknown }).userId : undefined;
  if (metaUser !== undefined) return metaUser === userId;
  // Fallback: the paywall also encodes the payer in the reference.
  return typeof tx.reference === "string" && tx.reference.startsWith(`betiq_${userId}_`);
}

// The paywall redeems a payment the moment Paystack confirms it, so a genuine
// redemption is minutes old. Anything older is a replay — notably payments made
// before redeemed references were recorded, which the used-list can't know about.
export const MAX_PAYMENT_AGE_HOURS = 24;

/** When the payment happened: Paystack's paid_at, else the time encoded in our
 *  betiq_<userId>_<ms> reference. Null when neither is usable. */
export function paymentTime(tx: { paid_at?: unknown; paidAt?: unknown; reference?: string }): Date | null {
  for (const v of [tx.paid_at, tx.paidAt]) {
    if (typeof v === "string") {
      const d = new Date(v);
      if (!isNaN(d.getTime())) return d;
    }
  }
  const ms = typeof tx.reference === "string" ? tx.reference.match(/_(\d{13})$/)?.[1] : undefined;
  return ms ? new Date(Number(ms)) : null;
}

/** True when the payment is recent enough to redeem. Unknown age is rejected. */
export function isFreshPayment(
  tx: { paid_at?: unknown; paidAt?: unknown; reference?: string },
  now = new Date(),
): boolean {
  const t = paymentTime(tx);
  if (!t) return false;
  const ageMs = now.getTime() - t.getTime();
  // Small allowance for clock skew between Paystack and this server
  return ageMs > -5 * 60_000 && ageMs <= MAX_PAYMENT_AGE_HOURS * 3_600_000;
}

/** New expiry: +30 days from the current expiry if still active, else from now. */
export function nextExpiry(currentExpiry: unknown, now = new Date()): Date {
  const current = new Date(typeof currentExpiry === "string" ? currentExpiry : 0).getTime();
  const base = new Date(Math.max(now.getTime(), isNaN(current) ? 0 : current));
  base.setDate(base.getDate() + PERIOD_DAYS);
  return base;
}

type Meta = { subscription?: unknown; subscription_expires?: unknown } | null | undefined;

/** A user's tier from their Clerk public metadata: "lite" / "premium" until it expires. */
export function tierOf(meta: Meta, now = new Date()): "free" | "lite" | "premium" {
  const tier = meta?.subscription;
  if (tier !== "lite" && tier !== "premium") return "free";
  const expires = new Date(typeof meta?.subscription_expires === "string" ? meta.subscription_expires : 0);
  return expires.getTime() > now.getTime() ? tier : "free";
}

/** After paying for `plan`: renewing the same plan adds 30 days to what's
 *  left; changing plan starts 30 days of the new one from now. */
export function renewal(meta: Meta, plan: "lite" | "premium", now = new Date()): Date {
  return nextExpiry(tierOf(meta, now) === plan ? meta?.subscription_expires : undefined, now);
}

const TIER_ORDER = ["free", "lite", "premium"];
/** Whether `tier` includes everything `needed` does (unknown values count as free). */
export function tierAtLeast(tier: string, needed: string): boolean {
  const rank = (t: string) => Math.max(0, TIER_ORDER.indexOf(t));
  return rank(tier) >= rank(needed);
}

// ── Free trial for new accounts (backend trial.py decides; admin → Users) ──
export interface TrialConfig {
  enabled: boolean; days: number; tier: "lite" | "premium"; since: string | null;
  require_phone: boolean; per_ip_week: number;
}
type TrialMeta = Meta & { trial?: unknown; trial_used?: unknown };

/** Days left of an active trial (rounded up), or null when not on one. */
export function trialDaysLeft(meta: TrialMeta, now = new Date()): number | null {
  if (meta?.trial !== true || tierOf(meta, now) === "free") return null;
  const ms = new Date(String(meta.subscription_expires)).getTime() - now.getTime();
  return Math.max(1, Math.ceil(ms / 86_400_000));
}
