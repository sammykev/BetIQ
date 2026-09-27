"use client";

import { useState, type ReactNode } from "react";
import Link from "next/link";
import { SignInButton } from "@clerk/nextjs";
import { Check, Loader2, Lock, EyeOff } from "lucide-react";
import { PaywallModal } from "@/components/PaywallModal";
import { useAccess, tierAtLeast, TIER_NAMES, type FeatureId } from "@/lib/access";
import type { Plan } from "@/lib/pricing";

/** A switched-off page or feature: what a visitor sees if they land on it. */
export function Unavailable({ title = "Not available" }: { title?: string }) {
  return (
    <section className="card p-8 text-center space-y-3 max-w-xl mx-auto">
      <span className="mx-auto w-12 h-12 rounded-full bg-n-800 text-n-400 flex items-center justify-center"><EyeOff size={20} /></span>
      <p className="font-display font-extrabold text-2xl uppercase tracking-wide text-n-0">{title}</p>
      <p className="text-sm text-n-400">This isn&apos;t available right now. Check back soon.</p>
      <Link href="/" className="btn-secondary inline-flex">Back to predictions</Link>
    </section>
  );
}

/** A feature the visitor's tier doesn't include: what it does, and the upgrade. */
export function Locked({ feature, title, perks }: { feature: FeatureId; title: string; perks: string[] }) {
  const { signedIn, needs, tier: mine } = useAccess();
  const [paywall, setPaywall] = useState(false);
  const tier = needs(feature);
  const plan = (tier === "free" ? "lite" : tier) as Plan;
  // Their plan covers it, but the server refused: don't sell them what they have
  if (signedIn && mine !== "free" && tierAtLeast(mine, tier)) {
    return (
      <section className="card p-6 sm:p-8 text-center space-y-3 max-w-xl mx-auto">
        <span className="mx-auto w-12 h-12 rounded-full bg-warn/15 text-warn flex items-center justify-center"><Lock size={20} /></span>
        <p className="font-display font-extrabold text-2xl uppercase tracking-wide text-n-0">{title}</p>
        <p className="text-sm text-n-400">Your {TIER_NAMES[mine]} plan includes this, but we couldn&apos;t confirm it just now.
          Try again in a minute; if it keeps happening, contact support and we&apos;ll sort it out.</p>
        <button onClick={() => window.location.reload()} className="btn-secondary">Try again</button>
      </section>
    );
  }
  return (
    <section className="card p-6 sm:p-8 text-center space-y-5 max-w-xl mx-auto">
      <span className="mx-auto w-12 h-12 rounded-full bg-brand-400/15 text-accent flex items-center justify-center"><Lock size={20} /></span>
      <div>
        <p className="font-display font-extrabold text-2xl uppercase tracking-wide text-n-0">{title}</p>
        <p className="text-sm text-n-400 mt-1">Part of BetIQ {TIER_NAMES[tier]}{tier === "lite" ? " and Premium" : ""}.</p>
      </div>
      {perks.length > 0 && (
        <ul className="space-y-2 text-left text-sm text-n-200 max-w-sm mx-auto">
          {perks.map(p => <li key={p} className="flex gap-2"><Check size={16} className="text-accent shrink-0 mt-0.5" />{p}</li>)}
        </ul>
      )}
      {signedIn ? (
        <button onClick={() => setPaywall(true)} className="btn-primary">See plans</button>
      ) : (
        <SignInButton mode="modal"><button className="btn-primary">Sign in to subscribe</button></SignInButton>
      )}
      {paywall && <PaywallModal need={plan} onClose={() => setPaywall(false)} onSuccess={() => setPaywall(false)} />}
    </section>
  );
}

/**
 * Renders children when the visitor may use `feature`; otherwise the
 * switched-off card, or the locked card with the upgrade. `locked` forces the
 * locked card (the server refused: 401/402).
 */
export function FeatureGate({ feature, title, perks = [], locked = false, children }: {
  feature: FeatureId; title: string; perks?: string[]; locked?: boolean; children: ReactNode;
}) {
  const { ready, shown, can } = useAccess();
  if (!ready) {
    return <div className="card flex items-center justify-center gap-2 py-16 text-sm text-n-400"><Loader2 size={15} className="animate-spin" /> Loading…</div>;
  }
  if (!shown(feature)) return <Unavailable title={title} />;
  if (!can(feature) || locked) return <Locked feature={feature} title={title} perks={perks} />;
  return <>{children}</>;
}
