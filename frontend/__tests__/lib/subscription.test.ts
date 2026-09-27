import { isFreshPayment, nextExpiry, paidByUser, paymentTime, renewal, tierAtLeast, tierOf } from "@/lib/subscription";
import { planKobo, planLabel } from "@/lib/pricing";

describe("pricing", () => {
  it("charges Lite ₦5,000 and Premium ₦8,500 a month, in kobo", () => {
    expect(planKobo("lite")).toBe(500000);
    expect(planKobo("premium")).toBe(850000);
    expect(planLabel("lite")).toBe("₦5,000 / month");
    expect(planLabel("premium")).toBe("₦8,500 / month");
  });
});

describe("paidByUser", () => {
  it("accepts a payment whose metadata names this user", () => {
    expect(paidByUser({ reference: "x", metadata: { userId: "user_a" } }, "user_a")).toBe(true);
  });

  it("rejects another user's payment", () => {
    expect(paidByUser({ reference: "betiq_user_b_1", metadata: { userId: "user_b" } }, "user_a")).toBe(false);
  });

  it("parses metadata sent as a JSON string", () => {
    expect(paidByUser({ metadata: JSON.stringify({ userId: "user_a" }) }, "user_a")).toBe(true);
    expect(paidByUser({ metadata: JSON.stringify({ userId: "user_b" }) }, "user_a")).toBe(false);
  });

  it("falls back to the payer encoded in the reference", () => {
    expect(paidByUser({ reference: "betiq_user_a_1700000000000" }, "user_a")).toBe(true);
    expect(paidByUser({ reference: "betiq_user_b_1700000000000" }, "user_a")).toBe(false);
  });

  it("does not let a reference prefix-match a longer user id", () => {
    expect(paidByUser({ reference: "betiq_user_ab_1" }, "user_a")).toBe(false);
  });
});

describe("nextExpiry", () => {
  const now = new Date("2026-09-23T12:00:00Z");
  const days = (a: Date, b: Date) => Math.round((a.getTime() - b.getTime()) / 86_400_000);

  it("gives 30 days from now for a new subscriber", () => {
    expect(days(nextExpiry(undefined, now), now)).toBe(30);
  });

  it("gives 30 days from now when the old subscription has lapsed", () => {
    expect(days(nextExpiry("2026-08-01T00:00:00Z", now), now)).toBe(30);
  });

  it("extends from the current expiry when renewing early", () => {
    const current = new Date("2026-10-03T12:00:00Z"); // 10 days left
    expect(days(nextExpiry(current.toISOString(), now), current)).toBe(30);
    expect(days(nextExpiry(current.toISOString(), now), now)).toBe(40);
  });

  it("ignores an unparseable expiry", () => {
    expect(days(nextExpiry("not a date", now), now)).toBe(30);
  });
});

describe("isFreshPayment", () => {
  const now = new Date("2026-09-23T12:00:00Z");
  const hoursAgo = (h: number) => new Date(now.getTime() - h * 3_600_000).toISOString();

  it("accepts a payment made minutes ago", () => {
    expect(isFreshPayment({ paid_at: hoursAgo(0.1) }, now)).toBe(true);
  });

  it("rejects a payment older than 24 hours (replayed old reference)", () => {
    expect(isFreshPayment({ paid_at: hoursAgo(25) }, now)).toBe(false);
    expect(isFreshPayment({ paid_at: "2026-01-15T09:00:00Z" }, now)).toBe(false);
  });

  it("falls back to the timestamp in our reference", () => {
    const fresh = `betiq_user_a_${now.getTime() - 60_000}`;
    const stale = `betiq_user_a_${now.getTime() - 48 * 3_600_000}`;
    expect(isFreshPayment({ reference: fresh }, now)).toBe(true);
    expect(isFreshPayment({ reference: stale }, now)).toBe(false);
  });

  it("rejects when the payment time can't be determined", () => {
    expect(isFreshPayment({ reference: "custom-ref" }, now)).toBe(false);
    expect(paymentTime({ paid_at: "garbage" })).toBeNull();
  });

  it("rejects timestamps far in the future", () => {
    expect(isFreshPayment({ paid_at: new Date(now.getTime() + 3_600_000).toISOString() }, now)).toBe(false);
  });
});

describe("tiers", () => {
  const now = new Date("2026-09-27T12:00:00Z");
  const later = "2026-10-10T00:00:00Z";
  it("reads lite and premium until they expire", () => {
    expect(tierOf({ subscription: "lite", subscription_expires: later }, now)).toBe("lite");
    expect(tierOf({ subscription: "premium", subscription_expires: later }, now)).toBe("premium");
    expect(tierOf({ subscription: "premium", subscription_expires: "2026-09-01T00:00:00Z" }, now)).toBe("free");
    expect(tierOf({ subscription: "gold", subscription_expires: later }, now)).toBe("free");
    expect(tierOf(undefined, now)).toBe("free");
  });
  it("extends the same plan and restarts a different one", () => {
    const days = (d: Date) => Math.round((d.getTime() - now.getTime()) / 86_400_000);
    expect(days(renewal({ subscription: "lite", subscription_expires: later }, "lite", now))).toBe(43);
    expect(days(renewal({ subscription: "lite", subscription_expires: later }, "premium", now))).toBe(30);
    expect(days(renewal(undefined, "premium", now))).toBe(30);
  });
});

describe("plans stack", () => {
  it("gives Lite everything free and Premium everything in Lite", () => {
    const tiers = ["free", "lite", "premium"];
    for (const has of tiers) for (const needs of tiers)
      expect(tierAtLeast(has, needs)).toBe(tiers.indexOf(has) >= tiers.indexOf(needs));
    expect(tierAtLeast("gold", "lite")).toBe(false);   // unknown counts as free
  });
});
