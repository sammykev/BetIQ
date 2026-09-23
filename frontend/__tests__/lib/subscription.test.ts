import { nextExpiry, paidByUser } from "@/lib/subscription";
import { PREMIUM_PRICE_KOBO, PREMIUM_PRICE_LABEL } from "@/lib/pricing";

describe("pricing", () => {
  it("charges ₦3,500 a month in kobo", () => {
    expect(PREMIUM_PRICE_KOBO).toBe(350000);
    expect(PREMIUM_PRICE_LABEL).toBe("₦3,500 / month");
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
