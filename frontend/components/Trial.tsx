"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useUser } from "@clerk/nextjs";
import { Crown, Gift, X } from "lucide-react";
import { refreshAccess, TIER_NAMES } from "@/lib/access";
import { useAuthedFetch } from "@/lib/useAuthedFetch";
import { tierOf, trialDaysLeft, type TrialConfig } from "@/lib/subscription";

// The free trial for new accounts (settings in admin → Users → Free trial):
// started by the backend (/api/trial/start, trial.py) on a new account's
// visit, then a bar with the days left, and a nudge for a week after it ends.

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

function Modal({ children, onClose }: { children: ReactNode; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-[60] bg-ink/80 backdrop-blur-sm flex items-center justify-center p-4"
      onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="card !rounded-3xl max-w-sm w-full p-6 text-center space-y-4 animate-scale-in">{children}</div>
    </div>
  );
}

type TrialAnswer = { started: boolean; tier?: "lite" | "premium"; days?: number; reason?: string; message?: string | null };

/** Starts the trial for a new account (the backend decides: one per email
 *  address), and says so. */
function TrialStarter() {
  const { user, isLoaded } = useUser();
  const authFetch = useAuthedFetch();
  const cfg = useTrialConfig();
  const [answer, setAnswer] = useState<TrialAnswer | null>(null);

  const ask = useCallback(async () => {
    try {
      const r = await authFetch(`${API}/api/trial/start`, { method: "POST" });
      const d: TrialAnswer | null = r.ok ? await r.json() : null;
      if (!d) return;
      if (d.started) {
        await user?.reload().catch(() => {});
        refreshAccess();
      }
      // Said once per account: why there's no trial
      const noted = `betiq:trial-note:${user?.id}`;
      if (!d.started && d.message) {
        if (store.get(noted)) return;
        store.set(noted, "1");
      }
      if (d.started || d.message) setAnswer(d);
    } catch { /* try again next visit */ }
  }, [authFetch, user]);

  useEffect(() => {
    if (!isLoaded || !user || !cfg?.enabled) return;
    const meta = user.publicMetadata as { trial_used?: boolean };
    // Only accounts young enough to still be inside a trial; once per visit
    const young = user.createdAt && Date.now() - new Date(user.createdAt).getTime() < 31 * DAY;
    const key = `betiq:trial-asked:${user.id}`;
    let asked = false;
    try { asked = sessionStorage.getItem(key) === "1"; sessionStorage.setItem(key, "1"); } catch { /* ignore */ }
    if (meta?.trial_used || !young || asked) return;
    ask();
  }, [isLoaded, user, cfg, ask]);

  if (!answer || !user) return null;
  const close = () => setAnswer(null);
  if (answer.started) {
    return (
      <Modal onClose={close}>
        <span className="mx-auto w-14 h-14 rounded-full bg-brand-400/15 text-accent flex items-center justify-center"><Gift size={24} /></span>
        <div>
          <p className="display text-3xl text-n-0">Welcome to BetIQ</p>
          <p className="text-sm text-n-300 mt-2">
            You&apos;ve got <span className="font-bold text-n-0">{answer.days} days of {TIER_NAMES[answer.tier ?? "premium"]}</span> free:
            every feature is open, no card needed. We&apos;ll remind you here before it ends.
          </p>
        </div>
        <button onClick={close} className="btn-primary w-full">Start exploring</button>
      </Modal>
    );
  }
  return (
    <Modal onClose={close}>
      <span className="mx-auto w-14 h-14 rounded-full bg-n-800 text-n-300 flex items-center justify-center"><Gift size={22} /></span>
      <div>
        <p className="display text-2xl text-n-0">No free trial</p>
        <p className="text-sm text-n-300 mt-2">{answer.message}</p>
      </div>
      <button onClick={close} className="btn-secondary w-full">OK</button>
    </Modal>
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
