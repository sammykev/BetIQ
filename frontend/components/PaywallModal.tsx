"use client";

import { useUser, SignInButton } from "@clerk/nextjs";
import { X, Crown, Check, Loader2, Zap } from "lucide-react";
import { useState } from "react";
import clsx from "clsx";
import { PLANS, planKobo, planLabel, type Plan } from "@/lib/pricing";
import { useAccess, refreshAccess, type FeatureId } from "@/lib/access";

interface Props {
  onClose: () => void;
  onSuccess: () => void;
  /** The plan the feature the visitor tried needs: the other one is marked as not including it. */
  need?: Plan;
}

// What each feature gives, as a perk; a plan lists the ones its tier unlocks
// (set per feature in admin → Access, so this follows the switches)
const PERKS: [FeatureId, string][] = [
  ["match_analysis", "Full match analysis: every market, xG, Elo and form"],
  ["ai_preview", "AI match previews with this week's team news"],
  ["code_check", "Check any SportyBet code and make it safer"],
  ["optimizer", "Optimizer: build a slip for the exact odds you want"],
  ["daily_slips", "Daily 10, 15 and 20 odds slips from 85%+ picks"],
  ["ai_chat", "AI assistant that builds slips on request"],
  ["sport.basketball", "Basketball predictions"],
  ["sport.tennis", "Tennis predictions"],
  ["sport.table_tennis", "Table tennis predictions"],
];

export function PaywallModal({ onClose, onSuccess, need }: Props) {
  const { user } = useUser();
  const access = useAccess();
  const [busy, setBusy] = useState<Plan | null>(null);
  const [error, setError] = useState<string | null>(null);

  const perks = (plan: Plan) => PERKS.filter(([f]) => access.shown(f) && access.needs(f) === plan).map(([, t]) => t);

  const pay = async (plan: Plan) => {
    if (!user) { setError("Please sign in first."); return; }
    setError(null);
    const paystackKey = process.env.NEXT_PUBLIC_PAYSTACK_PUBLIC_KEY || "";
    if (!paystackKey) { setError("Payment not configured. Contact support."); return; }
    const email = user.emailAddresses[0]?.emailAddress;
    if (!email) { setError("No email on your account."); return; }

    // Dynamic import so it never runs during SSR (window would be undefined)
    // @ts-ignore
    const { default: PaystackPop } = await import("@paystack/inline-js");
    new PaystackPop().newTransaction({
      key: paystackKey,
      email,
      amount: planKobo(plan),
      currency: "NGN",
      ref: `betiq_${user.id}_${Date.now()}`,
      metadata: { userId: user.id, plan },
      onSuccess: (transaction: { reference: string }) => {
        setBusy(plan);
        fetch("/api/subscribe", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reference: transaction.reference, plan }),
        })
          .then(r => { if (!r.ok) throw new Error(); })
          .then(async () => { await user.reload().catch(() => {}); refreshAccess(); onSuccess(); })
          .catch(() => setError("Payment received but verification failed. Contact support."))
          .finally(() => setBusy(null));
      },
      onCancel: () => {},
    });
  };

  const card = (plan: Plan) => {
    const current = access.tier === plan;
    const below = plan === "lite" && access.tier === "premium";
    const lacks = need === "premium" && plan === "lite";
    const featured = plan === "premium";
    const list = plan === "premium" ? perks("premium") : perks("lite");
    return (
      <div key={plan} className={clsx("rounded-2xl border p-4 flex flex-col gap-3",
        featured ? "border-brand-400/60 bg-brand-400/[0.06]" : "border-n-800 bg-surface-sunken")}>
        <div className="flex items-center justify-between gap-2">
          <p className="font-display font-extrabold text-xl uppercase tracking-wide text-n-0 inline-flex items-center gap-1.5">
            {featured ? <Crown size={16} className="text-amber-500" /> : <Zap size={16} className="text-accent" />}
            {PLANS[plan].name}
          </p>
          {featured && <span className="text-[10px] font-bold uppercase tracking-wide rounded-full bg-brand-400 text-ink px-2 py-0.5">Best value</span>}
        </div>
        <p className="font-display font-extrabold text-3xl text-n-0 tnum leading-none">
          ₦{PLANS[plan].ngn.toLocaleString("en-US")}<span className="text-sm text-n-400 font-sans font-semibold"> / month</span>
        </p>
        <ul className="space-y-2 flex-1">
          {/* Plans stack: Lite has everything free, Premium everything in Lite */}
          <li className="flex items-start gap-2 text-sm text-n-200 font-semibold"><Check size={14} className="text-accent mt-0.5 shrink-0" />
            {plan === "premium" ? "Everything in Lite, plus:" : "Everything free, plus:"}</li>
          {list.map(p => (
            <li key={p} className="flex items-start gap-2 text-sm text-n-200"><Check size={14} className="text-accent mt-0.5 shrink-0" />{p}</li>
          ))}
        </ul>
        {lacks && <p className="text-[11px] text-warn">Doesn&apos;t include what you just opened: that&apos;s in Premium.</p>}
        {!user ? (
          <SignInButton mode="modal">
            <button className={clsx("w-full !py-2.5", featured ? "btn-primary" : "btn-secondary")}>Sign in to subscribe</button>
          </SignInButton>
        ) : below ? (
          <p className="text-xs text-n-400 text-center py-2">You have Premium, which includes Lite.</p>
        ) : (
          <button onClick={() => pay(plan)} disabled={busy !== null}
            className={clsx("w-full !py-2.5", featured ? "btn-primary" : "btn-secondary")}>
            {busy === plan ? <><Loader2 size={15} className="animate-spin" /> Verifying…</>
              : current ? `Renew ${PLANS[plan].name} · ${planLabel(plan)}` : `Get ${PLANS[plan].name}`}
          </button>
        )}
      </div>
    );
  };

  return (
    <div className="fixed inset-0 z-[60] bg-ink/80 backdrop-blur-sm flex items-center justify-center p-4 overflow-y-auto"
      onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="w-full max-w-2xl card !rounded-3xl shadow-pop overflow-hidden animate-scale-in my-auto">
        <div className="relative px-6 pt-6 pb-4 text-center border-b border-n-800">
          <button onClick={onClose} aria-label="Close"
            className="absolute top-4 right-4 p-1.5 hover:bg-n-800 rounded-lg text-n-400 transition-colors"><X size={14} /></button>
          <h2 className="display text-3xl sm:text-4xl text-n-0">Choose your plan</h2>
          <p className="text-n-400 text-sm mt-1">Monthly, paid in naira. Renew any time; days you have left carry over.</p>
        </div>
        <div className="grid sm:grid-cols-2 gap-3 p-4 sm:p-5">
          {card("lite")}
          {card("premium")}
        </div>
        <div className="px-6 pb-5 space-y-2">
          {error && <p className="text-danger text-xs text-center font-medium">{error}</p>}
          <p className="text-center text-[10px] text-n-500">Secured by Paystack · NGN only · 18+ · Bet responsibly</p>
        </div>
      </div>
    </div>
  );
}
