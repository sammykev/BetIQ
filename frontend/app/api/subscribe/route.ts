import { auth, clerkClient } from "@clerk/nextjs/server";
import { NextRequest, NextResponse } from "next/server";
import { PREMIUM_PRICE_KOBO } from "@/lib/pricing";
import { MAX_STORED_REFERENCES, nextExpiry, paidByUser } from "@/lib/subscription";

export async function POST(req: NextRequest) {
  try {
    const { userId } = await auth();
    if (!userId) {
      return NextResponse.json({ error: "not_authenticated" }, { status: 401 });
    }

    const { reference } = await req.json();
    if (!reference) {
      return NextResponse.json({ error: "missing_reference" }, { status: 400 });
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
    // otherwise an edited checkout (e.g. ₦1) would still unlock premium.
    const paid = data.data;
    if (paid.currency !== "NGN" || Number(paid.amount) < PREMIUM_PRICE_KOBO) {
      console.error(`[subscribe] Amount mismatch for ${reference}: ${paid.amount} ${paid.currency}`);
      return NextResponse.json({ error: "amount_mismatch" }, { status: 400 });
    }

    // A successful reference must belong to this account and count once —
    // otherwise one payment could unlock (or keep extending) any account.
    if (!paidByUser(paid, userId)) {
      console.error(`[subscribe] Reference ${reference} was not paid by ${userId}`);
      return NextResponse.json({ error: "reference_not_yours" }, { status: 403 });
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

    // Renewals extend from the current expiry when it's still in the future,
    // so paying early never loses days already paid for.
    const expiresAt = nextExpiry(user.publicMetadata?.subscription_expires);

    await client.users.updateUserMetadata(userId, {
      publicMetadata: {
        subscription: "premium",
        subscription_expires: expiresAt.toISOString(),
        paystack_reference: reference,
      },
      privateMetadata: {
        paystack_references: [...used, reference].slice(-MAX_STORED_REFERENCES),
      },
    });

    console.log(`[subscribe] User ${userId} upgraded to premium. Expires ${expiresAt.toISOString()}`);
    return NextResponse.json({ success: true, expires: expiresAt.toISOString() });
  } catch (err) {
    console.error("[subscribe]", err);
    return NextResponse.json({ error: "server_error" }, { status: 500 });
  }
}
