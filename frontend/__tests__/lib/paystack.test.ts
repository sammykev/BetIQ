/** @jest-environment node */
import { createHmac } from "crypto";

jest.mock("@clerk/nextjs/server", () => ({ clerkClient: jest.fn() }));
import { paysFor, validSignature } from "@/lib/paystack";
import { payer } from "@/lib/subscription";

describe("validSignature", () => {
  const body = JSON.stringify({ event: "charge.success", data: { reference: "betiq_u_1" } });
  const sign = (s: string) => createHmac("sha512", s).update(body).digest("hex");

  it("accepts Paystack's HMAC-SHA512 of the body", () => {
    expect(validSignature(body, sign("sk_test"), "sk_test")).toBe(true);
  });
  it("rejects a wrong key, an edited body or no signature", () => {
    expect(validSignature(body, sign("other"), "sk_test")).toBe(false);
    expect(validSignature(body + " ", sign("sk_test"), "sk_test")).toBe(false);
    expect(validSignature(body, null, "sk_test")).toBe(false);
    expect(validSignature(body, sign(""), "")).toBe(false);
  });
});

describe("paysFor", () => {
  it("needs a successful NGN payment of at least the plan price", () => {
    expect(paysFor({ status: "success", currency: "NGN", amount: 850000 }, "premium")).toBe(true);
    expect(paysFor({ status: "success", currency: "NGN", amount: 500000 }, "premium")).toBe(false);
    expect(paysFor({ status: "success", currency: "NGN", amount: 500000 }, "lite")).toBe(true);
    expect(paysFor({ status: "failed", currency: "NGN", amount: 850000 }, "premium")).toBe(false);
    expect(paysFor({ status: "success", currency: "USD", amount: 850000 }, "premium")).toBe(false);
  });
});

describe("payer", () => {
  it("reads metadata as an object or a JSON string", () => {
    expect(payer({ metadata: { userId: "u", plan: "lite" } })).toEqual({ userId: "u", plan: "lite" });
    expect(payer({ metadata: '{"userId":"u","plan":"premium"}' })).toEqual({ userId: "u", plan: "premium" });
    expect(payer({ metadata: "not json" })).toEqual({});
    expect(payer({})).toEqual({});
  });
});
