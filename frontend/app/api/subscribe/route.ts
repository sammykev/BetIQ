import { auth, clerkClient } from "@clerk/nextjs/server";
import { NextRequest, NextResponse } from "next/server";
import { isPlan, planKobo } from "@/lib/pricing";
import { MAX_STORED_REFERENCES, isFreshPayment, paidByUser, renewal } from "@/lib/subscription";

export async function POST(req: NextRequest) {
  try {
    const { userId } = await auth();
    if (!userId) {
      return NextResponse.json({ error: "not_authenticated" }, { status: 401 });
    }

    const body = await req.json();
    const reference = body?.reference;
    // Older clients only sold Premium
    const plan = body?.plan === undefined ? "premium" : body.plan;
    if (!reference) {
      return NextResponse.json({ error: "missing_reference" }, { status: 400 });
    }
    if (!isPlan(plan)) {
      return NextResponse.json({ error: "unknown_plan" }, { status: 400 });
    }

    // Verify the payment with Paystack
    const verify = await fetch(
      `https://api.paystack.co/transaction/verify/${encodeURIComponent(reference)}`,
      { headers: { Authorization: `Bearer ${process.env.PAYSTACK_SECRET_KEY}` } }
    );
    const data = await verify.json();

    if (!data.status || data.data?.status !== "success") {
      console.error("[subscribe] Paystack verify failed:", data.message);
      return NextResponse.json({ error: "payment_not_verified" }, { status: 400 });
    }

    // The amount is set in the browser, so check what was actually paid —
    // otherwise an edited checkout (e.g. ₦1, or the Lite price) would still
    // unlock the plan asked for.
    const paid = data.data;
    if (paid.currency !== "NGN" || Number(paid.amount) < planKobo(plan)) {
      console.error(`[subscribe] Amount mismatch for ${reference}: ${paid.amount} ${paid.currency}`);
      return NextResponse.json({ error: "amount_mismatch" }, { status: 400 });
    }

    // A successful reference must belong to this account and count once —
    // otherwise one payment could unlock (or keep extending) any account.
    if (!paidByUser(paid, userId)) {
      console.error(`[subscribe] Reference ${reference} was not paid by ${userId}`);
      return NextResponse.json({ error: "reference_not_yours" }, { status: 403 });
    }

    // Only redeem recent payments. Old references can't be replayed for free
    // months, including ones paid before the used-reference list existed.
    if (!isFreshPayment(paid)) {
      console.error(`[subscribe] Stale payment ${reference} (paid_at ${paid.paid_at})`);
      return NextResponse.json({ error: "payment_too_old" }, { status: 409 });
    }

    const client = await clerkClient();
    const user = await client.users.getUser(userId);
    const used = Array.isArray(user.privateMetadata?.paystack_references)
      ? (user.privateMetadata.paystack_references as string[])
      : [];
    // paystack_reference covers the last payment made before this list existed
    if (used.includes(reference) || user.publicMetadata?.paystack_reference === reference) {
      return NextResponse.json({ error: "reference_already_used" }, { status: 409 });
    }

    // Renewing the same plan extends from the current expiry, so paying early
    // never loses days already paid for; a new plan starts from today.
    const expiresAt = renewal(user.publicMetadata, plan);

    await client.users.updateUserMetadata(userId, {
      publicMetadata: {
        subscription: plan,
        subscription_expires: expiresAt.toISOString(),
        paystack_reference: reference,
      },
      privateMetadata: {
        paystack_references: [...used, reference].slice(-MAX_STORED_REFERENCES),
      },
    });

    console.log(`[subscribe] User ${userId} subscribed to ${plan}. Expires ${expiresAt.toISOString()}`);
    return NextResponse.json({ success: true, plan, expires: expiresAt.toISOString() });
  } catch (err) {
    console.error("[subscribe]", err);
    return NextResponse.json({ error: "server_error" }, { status: 500 });
  }
}
