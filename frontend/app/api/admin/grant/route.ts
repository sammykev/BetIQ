import { clerkClient } from "@clerk/nextjs/server";
import { NextRequest, NextResponse } from "next/server";
import { isAdminRequest } from "@/lib/serverAdmin";
import { isPlan } from "@/lib/pricing";

// POST — grant a plan ({email, tier?: "lite" | "premium", days?})  |  DELETE — revoke it
async function handleGrant(req: NextRequest, grant: boolean) {
  try {
    if (!(await isAdminRequest(req)))
      return NextResponse.json({ error: "forbidden" }, { status: 403 });
    const body = await req.json();

    const email = (body.email || "").toLowerCase().trim();
    if (!email) return NextResponse.json({ error: "missing_email" }, { status: 400 });

    const client = await clerkClient();
    const { data: users } = await client.users.getUserList({ emailAddress: [email] });
    if (!users.length) return NextResponse.json({ error: "user_not_found" }, { status: 404 });

    const tier = body.tier ?? "premium";
    if (grant && !isPlan(tier)) return NextResponse.json({ error: "unknown_tier" }, { status: 400 });
    const days = Math.round(Number(body.days ?? 30));
    if (grant && !(days >= 1 && days <= 366)) return NextResponse.json({ error: "days_out_of_range" }, { status: 400 });

    const user = users[0];
    const expires = new Date();
    if (grant) expires.setDate(expires.getDate() + days);

    await client.users.updateUserMetadata(user.id, {
      publicMetadata: grant
        ? { subscription: tier, subscription_expires: expires.toISOString(), trial: false }
        : { subscription: null, subscription_expires: null, trial: false },
    });

    return NextResponse.json({ ok: true, email, action: grant ? "granted" : "revoked", tier: grant ? tier : null });
  } catch (err) {
    console.error("[admin/grant]", err);
    return NextResponse.json({ error: "server_error" }, { status: 500 });
  }
}

export async function POST(req: NextRequest) { return handleGrant(req, true); }
export async function DELETE(req: NextRequest) { return handleGrant(req, false); }
