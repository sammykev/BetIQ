import { clerkClient } from "@clerk/nextjs/server";
import { NextRequest, NextResponse } from "next/server";
import { isAdminRequest } from "@/lib/serverAdmin";

// POST — grant premium  |  DELETE — revoke premium
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

    const user = users[0];
    const expires = new Date();
    if (grant) expires.setDate(expires.getDate() + 30);

    await client.users.updateUserMetadata(user.id, {
      publicMetadata: grant
        ? { subscription: "premium", subscription_expires: expires.toISOString() }
        : { subscription: null, subscription_expires: null },
    });

    return NextResponse.json({ ok: true, email, action: grant ? "granted" : "revoked" });
  } catch (err) {
    console.error("[admin/grant]", err);
    return NextResponse.json({ error: "server_error" }, { status: 500 });
  }
}

export async function POST(req: NextRequest) { return handleGrant(req, true); }
export async function DELETE(req: NextRequest) { return handleGrant(req, false); }
