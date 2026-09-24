import { clerkClient } from "@clerk/nextjs/server";
import { NextRequest, NextResponse } from "next/server";
import { isAdminRequest } from "@/lib/serverAdmin";

export async function GET(req: NextRequest) {
  if (!(await isAdminRequest(req)))
    return NextResponse.json({ error: "forbidden" }, { status: 403 });

  try {
    const client = await clerkClient();
    const { data: users, totalCount } = await client.users.getUserList({ limit: 500, orderBy: "-created_at" });
    const now = new Date();

    const mapped = users.map(u => {
      const meta = u.publicMetadata as { subscription?: string; subscription_expires?: string };
      const expires = meta?.subscription_expires ? new Date(meta.subscription_expires) : null;
      const isPremium = meta?.subscription === "premium" && expires && expires > now;
      const daysLeft = expires && isPremium ? Math.ceil((expires.getTime() - now.getTime()) / 86400000) : null;
      return {
        id: u.id,
        email: u.emailAddresses[0]?.emailAddress ?? "",
        name: `${u.firstName ?? ""} ${u.lastName ?? ""}`.trim() || "—",
        created: u.createdAt,
        premium: isPremium,
        expires: expires?.toISOString() ?? null,
        days_left: daysLeft,
      };
    });

    // Growth: sign-ups per day for last 30 days
    const growth: Record<string, number> = {};
    for (const u of users) {
      const d = new Date(u.createdAt).toISOString().slice(0, 10);
      growth[d] = (growth[d] || 0) + 1;
    }

    const expiring = mapped.filter(u => u.days_left !== null && u.days_left <= 7);

    return NextResponse.json({ users: mapped.slice(0, 100), total: totalCount, growth, expiring });
  } catch (err) {
    console.error("[admin/users]", err);
    return NextResponse.json({ error: "server_error" }, { status: 500 });
  }
}
