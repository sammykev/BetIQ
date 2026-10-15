import { auth } from "@clerk/nextjs/server";
import { NextRequest, NextResponse } from "next/server";
import { isPlan } from "@/lib/pricing";
import { isFreshPayment, paidByUser } from "@/lib/subscription";
import { paysFor, redeem, verifyTransaction } from "@/lib/paystack";

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
    const paid = await verifyTransaction(reference);
    if (!paid || paid.status !== "success") {
      return NextResponse.json({ error: "payment_not_verified" }, { status: 400 });
    }

    // The amount is set in the browser, so check what was actually paid —
    // otherwise an edited checkout (e.g. ₦1, or the Lite price) would still
    // unlock the plan asked for.
    if (!paysFor(paid, plan)) {
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

    const done = await redeem(userId, plan, reference);
    if ("error" in done) {
      // The webhook may have redeemed it first: the plan is already on the account
      return NextResponse.json({ error: done.error }, { status: 409 });
    }
    return NextResponse.json({ success: true, plan: done.plan, expires: done.expires });
  } catch (err) {
    console.error("[subscribe]", err);
    return NextResponse.json({ error: "server_error" }, { status: 500 });
  }
}
