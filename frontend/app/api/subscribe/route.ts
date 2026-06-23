import { auth, clerkClient } from "@clerk/nextjs/server";
import { NextRequest, NextResponse } from "next/server";

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

    // Calculate expiry (30 days from now)
    const expiresAt = new Date();
    expiresAt.setDate(expiresAt.getDate() + 30);

    // Update Clerk user metadata
    const client = await clerkClient();
    await client.users.updateUserMetadata(userId, {
      publicMetadata: {
        subscription: "premium",
        subscription_expires: expiresAt.toISOString(),
        paystack_reference: reference,
      },
    });

    console.log(`[subscribe] User ${userId} upgraded to premium. Expires ${expiresAt.toISOString()}`);
    return NextResponse.json({ success: true, expires: expiresAt.toISOString() });
  } catch (err) {
    console.error("[subscribe]", err);
    return NextResponse.json({ error: "server_error" }, { status: 500 });
  }
}
