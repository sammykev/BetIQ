"use client";

import { useEffect, useState } from "react";
import { useUser } from "@clerk/nextjs";
import { Crown, Gift, X } from "lucide-react";
import { refreshAccess, TIER_NAMES } from "@/lib/access";
import { tierOf, trialDaysLeft, type TrialConfig } from "@/lib/subscription";

// The free trial for new accounts (settings in admin → Users → Free trial):
// started by /api/trial on a new account's first visit, then a bar with the
// days left, and a nudge for a few days after it ends.

const API = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";
const DAY = 86_400_000;

let configCache: Promise<TrialConfig | null> | null = null;
function loadConfig(): Promise<TrialConfig | null> {
  configCache ??= fetch(`${API}/api/trial`).then(r => (r.ok ? r.json() : null)).catch(() => null);
  return configCache;
}

/** The trial settings (null until loaded, or if they can't be). */
export function useTrialConfig(): TrialConfig | null {
  const [cfg, setCfg] = useState<TrialConfig | null>(null);
  useEffect(() => { loadConfig().then(setCfg); }, []);
  return cfg;
}

const store = {
  get: (k: string) => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k: string, v: string) => { try { localStorage.setItem(k, v); } catch { /* ignore */ } },
};

/** Starts the trial for a new account, once, and says so. */
function TrialStarter() {
  const { user, isLoaded } = useUser();
  const [started, setStarted] = useState<{ tier: "lite" | "premium"; days: number } | null>(null);

  useEffect(() => {
    if (!isLoaded || !user) return;
    const meta = user.publicMetadata as { trial_used?: boolean };
    // Only accounts young enough to still be inside any trial, asked once each
    const young = user.createdAt && Date.now() - new Date(user.createdAt).getTime() < 31 * DAY;
    const key = `betiq:trial-asked:${user.id}`;
    if (meta?.trial_used || !young || store.get(key)) return;
    store.set(key, "1");
    fetch("/api/trial", { method: "POST" })
      .then(r => (r.ok ? r.json() : null))
      .then(async d => {
        if (!d?.started) return;
        await user.reload().catch(() => {});
        refreshAccess();
        setStarted({ tier: d.tier, days: d.days });
      })
      .catch(() => {});
  }, [isLoaded, user]);

  if (!started) return null;
  return (
    <div className="fixed inset-0 z-[60] bg-ink/80 backdrop-blur-sm flex items-center justify-center p-4"
      onClick={e => { if (e.target === e.currentTarget) setStarted(null); }}>
      <div className="card !rounded-3xl max-w-sm w-full p-6 text-center space-y-4 animate-scale-in">
        <span className="mx-auto w-14 h-14 rounded-full bg-brand-400/15 text-accent flex items-center justify-center"><Gift size={24} /></span>
        <div>
          <p className="display text-3xl text-n-0">Welcome to BetIQ</p>
          <p className="text-sm text-n-300 mt-2">
            You&apos;ve got <span className="font-bold text-n-0">{started.days} days of {TIER_NAMES[started.tier]}</span> free:
            every feature is open, no card needed. We&apos;ll remind you here before it ends.
          </p>
        </div>
        <button onClick={() => setStarted(null)} className="btn-primary w-full">Start exploring</button>
      </div>
    </div>
  );
}

/** The trial's days left, or (for a few days after) that it ended. */
function TrialBar({ onPlans }: { onPlans: () => void }) {
  const { user } = useUser();
  const [hidden, setHidden] = useState(true);
  const meta = (user?.publicMetadata ?? {}) as { trial?: boolean; subscription?: string; subscription_expires?: string };
  const left = trialDaysLeft(meta);
  const endedAt = meta.trial === true && tierOf(meta) === "free" ? new Date(String(meta.subscription_expires)).getTime() : NaN;
  const ended = !isNaN(endedAt) && Date.now() - endedAt < 7 * DAY;
  // Dismissed for the day
  const key = `betiq:trial-bar:${new Date().toISOString().slice(0, 10)}`;
  useEffect(() => { setHidden(store.get(key) === "1"); }, [key]);

  if (hidden || (!left && !ended)) return null;
  const tier = meta.subscription === "lite" ? "Lite" : "Premium";
  return (
    <div className={`mb-5 flex items-center gap-3 rounded-xl border px-3.5 py-2.5 text-sm ${
      ended || (left ?? 9) <= 2 ? "border-warn/40 bg-warn/10" : "border-brand-400/40 bg-brand-400/[0.07]"}`}>
      <Crown size={16} className={ended ? "text-warn shrink-0" : "text-amber-500 shrink-0"} />
      <p className="flex-1 min-w-0 text-n-200">
        {ended ? <>Your free trial has ended. Subscribe to keep every feature.</>
          : <><span className="font-semibold text-n-0">{tier} free trial</span> · {left} day{left === 1 ? "" : "s"} left.
            {(left ?? 9) <= 2 ? " Subscribe to keep it." : ""}</>}
      </p>
      <button onClick={onPlans} className="btn-primary !px-3 !py-1.5 !text-xs shrink-0">See plans</button>
      <button onClick={() => { store.set(key, "1"); setHidden(true); }} aria-label="Hide for today"
        className="p-1 rounded-lg text-n-400 hover:text-n-0 hover:bg-n-800 shrink-0"><X size={14} /></button>
    </div>
  );
}

/** Everything the trial needs on every page: the starter and the bar. */
export function Trial({ onPlans }: { onPlans: () => void }) {
  return (
    <>
      <TrialStarter />
      <TrialBar onPlans={onPlans} />
    </>
  );
}
