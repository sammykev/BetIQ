import { clerkClient } from "@clerk/nextjs/server";
import { NextRequest, NextResponse } from "next/server";
import { isAdminRequest } from "@/lib/serverAdmin";
import { tierOf } from "@/lib/subscription";

export async function GET(req: NextRequest) {
  if (!(await isAdminRequest(req)))
    return NextResponse.json({ error: "forbidden" }, { status: 403 });
  try {
    const client = await clerkClient();
    const { data: users, totalCount } = await client.users.getUserList({ limit: 500 });
    const tiers = users.map(u => tierOf(u.publicMetadata as Record<string, unknown>));
    const premium = tiers.filter(t => t === "premium").length, lite = tiers.filter(t => t === "lite").length;
    return NextResponse.json({ total: totalCount, premium, lite, paid: premium + lite });
  } catch {
    return NextResponse.json({ total: 0, premium: 0, lite: 0, paid: 0 });
  }
}
