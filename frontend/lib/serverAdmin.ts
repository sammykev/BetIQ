import { auth } from "@clerk/nextjs/server";
import { timingSafeEqual } from "crypto";
import type { NextRequest } from "next/server";

// Admin check for the site's own /api/admin/* routes, mirroring the backend
// (backend/main.py require_admin): a signed-in Clerk user listed in
// ADMIN_USER_IDS, or ADMIN_SECRET in the X-Admin-Secret header. Never a
// secret in the URL or body: URLs land in logs and browser history.

const ADMIN_USER_IDS = new Set(
  (process.env.ADMIN_USER_IDS || "").split(",").map(s => s.trim()).filter(Boolean),
);

function secretMatches(given: string): boolean {
  const expected = process.env.ADMIN_SECRET || "";
  if (!expected || !given) return false;
  const a = Buffer.from(given);
  const b = Buffer.from(expected);
  return a.length === b.length && timingSafeEqual(a, b);
}

// Wrong secrets per IP (this server instance): 10 in 15 minutes locks the
// IP out for the rest of the window, as on the backend.
const MAX_FAILURES = 10;
const WINDOW_MS = 15 * 60_000;
const failures = new Map<string, number[]>();

function clientIp(req: NextRequest): string {
  return req.headers.get("x-real-ip") || (req.headers.get("x-forwarded-for") || "").split(",")[0].trim() || "unknown";
}

function recentFailures(ip: string): number[] {
  const now = Date.now();
  const hits = (failures.get(ip) ?? []).filter(t => now - t < WINDOW_MS);
  failures.set(ip, hits);
  if (failures.size > 10_000) failures.clear();
  return hits;
}

export async function isAdminRequest(req: NextRequest): Promise<boolean> {
  const given = (req.headers.get("x-admin-secret") || "").trim();
  if (given) {
    const ip = clientIp(req);
    const hits = recentFailures(ip);
    if (hits.length < MAX_FAILURES) {
      if (secretMatches(given)) return true;
      hits.push(Date.now());
    }
  }
  // Signed-in admins get in whatever the header says
  try {
    const { userId } = await auth();
    return !!userId && ADMIN_USER_IDS.has(userId);
  } catch {
    return false;
  }
}
