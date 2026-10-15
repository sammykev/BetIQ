// Paystack's charge.success webhook: grants the plan when the browser never
// reported the payment (tab closed, network dropped, verification hiccup).
// Set the URL in Paystack Dashboard → Settings → API Keys & Webhooks:
//   https://<site>/api/paystack/webhook
import { NextRequest, NextResponse } from "next/server";
import { isPlan } from "@/lib/pricing";
import { WEBHOOK_GRACE_MS, WEBHOOK_MAX_AGE_HOURS, payer, paymentTime } from "@/lib/subscription";
import { paysFor, redeem, validSignature, verifyTransaction } from "@/lib/paystack";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  const raw = await req.text();
  if (!validSignature(raw, req.headers.get("x-paystack-signature"), process.env.PAYSTACK_SECRET_KEY || "")) {
    return NextResponse.json({ error: "bad_signature" }, { status: 401 });
  }

  let event: { event?: string; data?: { reference?: string } };
  try { event = JSON.parse(raw); } catch { return NextResponse.json({ error: "bad_body" }, { status: 400 }); }
  // Anything but a successful charge is acknowledged and ignored
  if (event.event !== "charge.success" || !event.data?.reference) {
    return NextResponse.json({ ok: true, ignored: true });
  }

  try {
    const reference = event.data.reference;
    // Re-read the transaction from Paystack rather than trusting the body alone
    const tx = await verifyTransaction(reference);
    if (!tx) return NextResponse.json({ error: "verify_failed" }, { status: 502 });   // Paystack retries

    const { userId, plan } = payer(tx);
    if (!userId || !isPlan(plan) || !reference.startsWith(`betiq_${userId}_`)) {
      return NextResponse.json({ ok: true, ignored: "not_a_subscription" });
    }
    if (!paysFor(tx, plan)) {
      console.error(`[paystack-webhook] Amount mismatch for ${reference}: ${tx.amount} ${tx.currency}`);
      return NextResponse.json({ ok: true, ignored: "amount_mismatch" });
    }

    const at = paymentTime(tx);
    const age = at ? Date.now() - at.getTime() : NaN;
    if (!(age <= WEBHOOK_MAX_AGE_HOURS * 3_600_000)) {
      return NextResponse.json({ ok: true, ignored: "too_old" });
    }
    // Leave a brand-new payment to the browser; ask Paystack to come back
    if (age < WEBHOOK_GRACE_MS) {
      return NextResponse.json({ retry: "later" }, { status: 503 });
    }

    const done = await redeem(userId, plan, reference);
    if (done.ok) console.log(`[paystack-webhook] Redeemed ${reference} the browser didn't report`);
    return NextResponse.json({ ok: true, redeemed: done.ok });
  } catch (err) {
    console.error("[paystack-webhook]", err);
    return NextResponse.json({ error: "server_error" }, { status: 500 });
  }
}
