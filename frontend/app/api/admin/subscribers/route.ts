import { clerkClient } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";

export async function GET() {
  try {
    const client = await clerkClient();
    const { data: users, totalCount } = await client.users.getUserList({ limit: 500 });
    const premium = users.filter(
      (u) => (u.publicMetadata as { subscription?: string })?.subscription === "premium" &&
        new Date((u.publicMetadata as { subscription_expires?: string })?.subscription_expires ?? 0) > new Date()
    );
    return NextResponse.json({ total: totalCount, premium: premium.length });
  } catch {
    return NextResponse.json({ total: 0, premium: 0 });
  }
}
