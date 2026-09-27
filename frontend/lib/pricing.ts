// Subscription plans — the single source for the paywall, the dashboard
// upgrade button and the server-side Paystack amount check.

export type Plan = "lite" | "premium";
export type Tier = "free" | Plan;

export const PLANS: Record<Plan, { name: string; ngn: number }> = {
  lite: { name: "Lite", ngn: 5000 },
  premium: { name: "Premium", ngn: 8500 },
};

/** Paystack charges in kobo (1 NGN = 100 kobo). */
export const planKobo = (plan: Plan) => PLANS[plan].ngn * 100;

/** "₦5,000 / month" */
export const planLabel = (plan: Plan) => `₦${PLANS[plan].ngn.toLocaleString("en-US")} / month`;

export const isPlan = (v: unknown): v is Plan => v === "lite" || v === "premium";

// Kept for older imports: the Premium price
export const PREMIUM_PRICE_NGN = PLANS.premium.ngn;
export const PREMIUM_PRICE_KOBO = planKobo("premium");
export const PREMIUM_PRICE_LABEL = planLabel("premium");
