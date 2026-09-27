"use client";

import { useEffect, useState } from "react";
import { useUser } from "@clerk/nextjs";
import { useAuthedFetch } from "@/lib/useAuthedFetch";

const API = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";

/**
 * Whether this visitor gets Premium features: an unexpired premium
 * subscription, an admin, or everyone while the paywall is switched off.
 * The backend checks the same (require_premium); this only decides what to
 * show. `ready` is false until all of that is known.
 */
export function usePremium(): { ready: boolean; signedIn: boolean; premium: boolean } {
  const { user, isLoaded } = useUser();
  const authFetch = useAuthedFetch();
  const [paywall, setPaywall] = useState<boolean | null>(null);
  const [admin, setAdmin] = useState<boolean | null>(null);

  const meta = user?.publicMetadata as { subscription?: string; subscription_expires?: string } | undefined;
  const subscribed = meta?.subscription === "premium" && new Date(meta?.subscription_expires ?? 0) > new Date();

  useEffect(() => {
    fetch(`${API}/api/config/paywall`).then(r => r.json())
      .then(d => setPaywall(d?.enabled !== false)).catch(() => setPaywall(true));
  }, []);

  // Admins aren't marked in Clerk; ask the backend (only when it matters)
  const needAdminCheck = isLoaded && !!user && !subscribed && paywall === true;
  useEffect(() => {
    if (!needAdminCheck) return;
    authFetch(`${API}/api/admin/whoami`).then(r => r.json())
      .then(d => setAdmin(!!d?.admin)).catch(() => setAdmin(false));
  }, [needAdminCheck, authFetch]);

  const ready = isLoaded && paywall !== null && (!needAdminCheck || admin !== null);
  return { ready, signedIn: !!user, premium: paywall === false || subscribed || admin === true };
}
