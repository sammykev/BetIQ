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

/** New expiry: +30 days from the current expiry if still active, else from now. */
export function nextExpiry(currentExpiry: unknown, now = new Date()): Date {
  const current = new Date(typeof currentExpiry === "string" ? currentExpiry : 0).getTime();
  const base = new Date(Math.max(now.getTime(), isNaN(current) ? 0 : current));
  base.setDate(base.getDate() + PERIOD_DAYS);
  return base;
}
