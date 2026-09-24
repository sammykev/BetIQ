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

export async function isAdminRequest(req: NextRequest): Promise<boolean> {
  if (secretMatches((req.headers.get("x-admin-secret") || "").trim())) return true;
  try {
    const { userId } = await auth();
    return !!userId && ADMIN_USER_IDS.has(userId);
  } catch {
    return false;
  }
}
