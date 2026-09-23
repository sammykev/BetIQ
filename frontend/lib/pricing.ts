// Premium subscription price — the single source for the paywall, the
// dashboard upgrade button and the server-side Paystack amount check.

export const PREMIUM_PRICE_NGN = 3500;

/** Paystack charges in kobo (1 NGN = 100 kobo). */
export const PREMIUM_PRICE_KOBO = PREMIUM_PRICE_NGN * 100;

/** "₦3,500 / month" */
export const PREMIUM_PRICE_LABEL = `₦${PREMIUM_PRICE_NGN.toLocaleString("en-US")} / month`;
