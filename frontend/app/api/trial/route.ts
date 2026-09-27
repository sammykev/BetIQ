import { auth, clerkClient } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";
import { trialFor, type TrialConfig } from "@/lib/subscription";

// POST — start the free trial for the signed-in account, if it qualifies
// (lib/subscription.ts trialFor). The site calls it on a new account's first
// visit; the settings come from the backend (admin → Users → Free trial).
const API = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";

export async function POST() {
  try {
    const { userId } = await auth();
    if (!userId) return NextResponse.json({ error: "not_authenticated" }, { status: 401 });

    const res = await fetch(`${API}/api/trial`, { cache: "no-store" });
    if (!res.ok) return NextResponse.json({ started: false, reason: "trial_unavailable" });
    const cfg = (await res.json()) as TrialConfig;

    const client = await clerkClient();
    const user = await client.users.getUser(userId);
    const verdict = trialFor(user, cfg);
    if (!("expires" in verdict)) return NextResponse.json({ started: false, reason: verdict.reason });

    await client.users.updateUserMetadata(userId, {
      publicMetadata: {
        subscription: cfg.tier,
        subscription_expires: verdict.expires.toISOString(),
        trial: true,
        trial_used: true,
      },
    });
    console.log(`[trial] ${userId}: ${cfg.tier} until ${verdict.expires.toISOString()}`);
    return NextResponse.json({ started: true, tier: cfg.tier, days: cfg.days, expires: verdict.expires.toISOString() });
  } catch (err) {
    console.error("[trial]", err);
    return NextResponse.json({ error: "server_error" }, { status: 500 });
  }
}
