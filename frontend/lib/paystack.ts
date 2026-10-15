// Server only: redeeming a Paystack payment for a plan. Used by /api/subscribe
// (the browser, right after paying) and /api/paystack/webhook (Paystack itself,
// so a payment still counts when the browser never reports it).
import { createHmac, timingSafeEqual } from "crypto";
import { clerkClient } from "@clerk/nextjs/server";
import { type Plan, planKobo } from "@/lib/pricing";
import { MAX_STORED_REFERENCES, renewal } from "@/lib/subscription";

/** Paystack signs each webhook body with HMAC-SHA512 of the secret key. */
export function validSignature(rawBody: string, signature: string | null, secret: string): boolean {
  if (!signature || !secret) return false;
  const expected = Buffer.from(createHmac("sha512", secret).update(rawBody).digest("hex"));
  const given = Buffer.from(signature);
  return expected.length === given.length && timingSafeEqual(expected, given);
}

export type PaystackTx = {
  status?: string; reference?: string; amount?: number | string; currency?: string;
  paid_at?: string; paidAt?: string; metadata?: unknown;
};

/** The transaction as Paystack reports it now, or null when it can't be read. */
export async function verifyTransaction(reference: string): Promise<PaystackTx | null> {
  const res = await fetch(
    `https://api.paystack.co/transaction/verify/${encodeURIComponent(reference)}`,
    { headers: { Authorization: `Bearer ${process.env.PAYSTACK_SECRET_KEY}` } }
  );
  const data = await res.json().catch(() => null);
  if (!data?.status || !data.data) {
    console.error("[paystack] verify failed:", data?.message);
    return null;
  }
  return data.data as PaystackTx;
}

/** Whether what was actually paid covers `plan` (the amount is set in the browser). */
export function paysFor(tx: PaystackTx, plan: Plan): boolean {
  return tx.status === "success" && tx.currency === "NGN" && Number(tx.amount) >= planKobo(plan);
}

export type Redeemed =
  | { ok: true; plan: Plan; expires: string }
  | { ok: false; error: "reference_already_used" };

/** Give `userId` 30 days of `plan` for `reference`, once per reference. */
export async function redeem(userId: string, plan: Plan, reference: string): Promise<Redeemed> {
  const client = await clerkClient();
  const user = await client.users.getUser(userId);
  const used = Array.isArray(user.privateMetadata?.paystack_references)
    ? (user.privateMetadata.paystack_references as string[])
    : [];
  // paystack_reference covers the last payment made before this list existed
  if (used.includes(reference) || user.publicMetadata?.paystack_reference === reference) {
    return { ok: false, error: "reference_already_used" };
  }

  // Renewing the same plan extends from the current expiry, so paying early
  // never loses days already paid for; a new plan starts from today.
  const expiresAt = renewal(user.publicMetadata, plan);

  await client.users.updateUserMetadata(userId, {
    publicMetadata: {
      subscription: plan,
      subscription_expires: expiresAt.toISOString(),
      paystack_reference: reference,
      trial: false,   // paid now (days left of a trial on the same plan carry over)
    },
    privateMetadata: {
      paystack_references: [...used, reference].slice(-MAX_STORED_REFERENCES),
    },
  });
  console.log(`[paystack] User ${userId} subscribed to ${plan}. Expires ${expiresAt.toISOString()}`);
  return { ok: true, plan, expires: expiresAt.toISOString() };
}
