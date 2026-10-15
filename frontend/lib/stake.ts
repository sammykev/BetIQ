// Stake sizing: a quarter of the Kelly stake, capped at 3% of the bankroll.
// Kelly is the share of a bankroll that grows it fastest when the chance is
// right; a quarter of it gives up a little growth for far smaller swings, and
// the cap allows for the model's chance being too high.

export const KELLY_FRACTION = 0.25;
export const MAX_SHARE = 0.03;
const KEY = "betiq:bankroll";
const EVENT = "betiq:bankroll";

/** Full Kelly share for a bet at `odds` (decimal) won with chance `p`; 0 with no edge. */
export function kelly(p: number, odds: number): number {
  if (!(p > 0 && p < 1) || !(odds > 1)) return 0;
  return Math.max(0, (p * odds - 1) / (odds - 1));
}

/** The share of the bankroll to stake: quarter Kelly, at most MAX_SHARE. */
export function stakeShare(p: number, odds: number): number {
  return Math.min(MAX_SHARE, kelly(p, odds) * KELLY_FRACTION);
}

/** The stake in whole naira (rounded down to ₦10 above ₦100). */
export function stakeAmount(bankroll: number, p: number, odds: number): number {
  const raw = bankroll * stakeShare(p, odds);
  return raw >= 100 ? Math.floor(raw / 10) * 10 : Math.floor(raw);
}

export const naira = (n: number) => `₦${Math.round(n).toLocaleString("en-US")}`;

export function readBankroll(): number | null {
  try {
    const v = Number(localStorage.getItem(KEY));
    return v > 0 ? v : null;
  } catch { return null; }
}

export function saveBankroll(v: number | null) {
  try {
    if (v && v > 0) localStorage.setItem(KEY, String(Math.round(v)));
    else localStorage.removeItem(KEY);
  } catch { /* private window: kept for this page only */ }
  try { window.dispatchEvent(new CustomEvent(EVENT, { detail: v })); } catch { /* ignore */ }
}

/** Calls `fn` with the bankroll whenever it's changed on this page or another tab. */
export function onBankroll(fn: (v: number | null) => void): () => void {
  const local = (e: Event) => fn((e as CustomEvent).detail ?? null);
  const other = (e: StorageEvent) => { if (e.key === KEY) fn(readBankroll()); };
  window.addEventListener(EVENT, local);
  window.addEventListener("storage", other);
  return () => { window.removeEventListener(EVENT, local); window.removeEventListener("storage", other); };
}
