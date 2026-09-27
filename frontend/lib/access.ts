"use client";

import { useEffect, useState } from "react";
import { useUser } from "@clerk/nextjs";
import { useAuthedFetch } from "@/lib/useAuthedFetch";
import { tierAtLeast, tierOf } from "@/lib/subscription";
export { tierAtLeast };
import type { Tier } from "@/lib/pricing";

// Feature switches (backend access.py, set in admin → Access): whether this
// visitor sees each page/feature and whether their tier unlocks it. The
// backend enforces the same rules; this only decides what to show.

const API = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";

export type FeatureId =
  | "daily_slips" | "optimizer" | "code_check" | "record" | "dashboard"
  | "sport.basketball" | "sport.tennis" | "sport.table_tennis"
  | "match_analysis" | "ai_preview" | "ai_chat" | "bet_slip" | "live_stats";

interface FeatureState { visible: boolean; tier: Tier; granted: boolean }
interface FeaturesResponse { paywall: boolean; admin: boolean; signed_in: boolean; features: Record<string, FeatureState> }

// Until the switches load: what the backend starts with (so nothing flashes
// in that the admin has switched off by default)
const DEFAULTS: Record<FeatureId, FeatureState> = {
  daily_slips: { visible: true, tier: "premium", granted: false },
  optimizer: { visible: true, tier: "premium", granted: false },
  code_check: { visible: true, tier: "lite", granted: false },
  record: { visible: true, tier: "free", granted: false },
  dashboard: { visible: true, tier: "free", granted: false },
  "sport.basketball": { visible: false, tier: "free", granted: false },
  "sport.tennis": { visible: false, tier: "free", granted: false },
  "sport.table_tennis": { visible: false, tier: "free", granted: false },
  match_analysis: { visible: true, tier: "lite", granted: false },
  ai_preview: { visible: true, tier: "lite", granted: false },
  ai_chat: { visible: true, tier: "premium", granted: false },
  bet_slip: { visible: true, tier: "free", granted: false },
  live_stats: { visible: true, tier: "free", granted: false },
};


export const TIER_NAMES: Record<Tier, string> = { free: "Free", lite: "Lite", premium: "Premium" };

// One request per signed-in state, shared by every component on the page
const store: { key: string | null; data: FeaturesResponse | null; pending: Promise<FeaturesResponse | null> | null } =
  { key: null, data: null, pending: null };
const listeners = new Set<() => void>();

/** Reload the switches (after the admin changes them, or after paying). */
export function refreshAccess() {
  store.key = null; store.data = null; store.pending = null;
  listeners.forEach(l => l());
}

export interface Access {
  /** The switches and the visitor's tier are known. */
  ready: boolean;
  signedIn: boolean;
  tier: Tier;
  admin: boolean;
  /** The paywall switch in admin: off gives everyone every tier. */
  paywall: boolean;
  /** Shown to this visitor at all (switched on, or they're a tester). */
  shown: (f: FeatureId) => boolean;
  /** Shown and unlocked by their tier (or given to them). */
  can: (f: FeatureId) => boolean;
  /** The tier a feature needs. */
  needs: (f: FeatureId) => Tier;
}

export function useAccess(): Access {
  const { user, isLoaded } = useUser();
  const authFetch = useAuthedFetch();
  const key = isLoaded ? (user?.id ?? "anon") : null;
  const [, rerender] = useState(0);

  useEffect(() => {
    const l = () => rerender(n => n + 1);
    listeners.add(l);
    return () => { listeners.delete(l); };
  }, []);

  useEffect(() => {
    if (!key) return;
    if (store.key === key && (store.data || store.pending)) return;
    store.key = key; store.data = null;
    const p = authFetch(`${API}/api/features`).then(r => (r.ok ? r.json() : null)).catch(() => null);
    store.pending = p;
    p.then(d => {
      if (store.pending !== p) return;
      store.data = d ?? { paywall: true, admin: false, signed_in: !!user, features: {} };
      store.pending = null;
      listeners.forEach(l => l());
    });
  }, [key, authFetch, user]);

  const data = store.key === key ? store.data : null;
  const tier = tierOf(user?.publicMetadata as Record<string, unknown> | undefined);
  const state = (f: FeatureId): FeatureState => data?.features?.[f] ?? DEFAULTS[f];
  const paywall = data?.paywall !== false;
  return {
    ready: !!key && !!data,
    signedIn: !!user,
    tier,
    admin: !!data?.admin,
    paywall,
    shown: f => state(f).visible,
    can: f => { const s = state(f); return s.visible && (!paywall || s.granted || tierAtLeast(tier, s.tier)); },
    needs: f => state(f).tier,
  };
}
