"use client";

import { useUser, SignInButton } from "@clerk/nextjs";
import { X, Crown, Check, Loader2 } from "lucide-react";
import { useState } from "react";

interface Props {
  onClose: () => void;
  onSuccess: () => void;
}

const FEATURES = [
  "Full match analysis modal (xG, Elo, markets)",
  "AI explanation with live injury & team news",
  "AI chatbot — build accumulators by chat",
  "SportyBet booking code generation",
  "Prediction history calendar",
  "All leagues, all confidence filters",
];

const PRICE_KOBO = 500000;
const PRICE_LABEL = "₦5,000 / month";

export function PaywallModal({ onClose, onSuccess }: Props) {
  const { user } = useUser();
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState<string | null>(null);

  const handlePay = async () => {
    if (!user) { setError("Please sign in first."); return; }
    setError(null);

    const paystackKey = process.env.NEXT_PUBLIC_PAYSTACK_PUBLIC_KEY || "";
    if (!paystackKey) { setError("Payment not configured. Contact support."); return; }

    const email = user.emailAddresses[0]?.emailAddress;
    if (!email) { setError("No email on your account."); return; }

    const reference = `betiq_${user.id}_${Date.now()}`;

    // Dynamic import so it never runs during SSR (window would be undefined)
    // @ts-ignore
    const { default: PaystackPop } = await import("@paystack/inline-js");
    const popup = new PaystackPop();
    popup.newTransaction({
      key: paystackKey,
      email,
      amount: PRICE_KOBO,
      currency: "NGN",
      ref: reference,
      metadata: { userId: user.id },
      onSuccess: (transaction: { reference: string }) => {
        setLoading(true);
        fetch("/api/subscribe", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reference: transaction.reference }),
        })
          .then(r => { if (!r.ok) throw new Error(); })
          .then(() => onSuccess())
          .catch(() => setError("Payment received but verification failed. Contact support."))
          .finally(() => setLoading(false));
      },
      onCancel: () => {},
    });
  };

  return (
    <div
      className="fixed inset-0 z-[60] bg-zinc-950/60 backdrop-blur-sm flex items-center justify-center p-4"
      onClick={e => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="w-full max-w-md card !rounded-3xl shadow-pop overflow-hidden animate-scale-in">

        {/* Header */}
        <div className="relative bg-gradient-to-br from-amber-50 to-brand-50 dark:from-amber-500/10 dark:to-brand-500/10 border-b border-zinc-100 dark:border-zinc-800 px-6 py-7 text-center">
          <button onClick={onClose} className="absolute top-4 right-4 p-1.5 hover:bg-zinc-200/60 dark:hover:bg-zinc-800 rounded-lg text-zinc-400 transition-colors">
            <X size={14} />
          </button>
          <span className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-amber-100 dark:bg-amber-500/15 mb-3">
            <Crown size={26} className="text-amber-500" />
          </span>
          <h2 className="text-xl font-black text-zinc-900 dark:text-white">Unlock BetIQ Premium</h2>
          <p className="text-zinc-500 dark:text-zinc-400 text-sm mt-1">Everything you need to bet smarter</p>
          <div className="tnum mt-4 inline-block bg-zinc-900 dark:bg-white text-white dark:text-zinc-900 font-black text-lg px-5 py-1.5 rounded-xl">
            {PRICE_LABEL}
          </div>
        </div>

        {/* Features */}
        <div className="px-6 py-5 space-y-2.5">
          {FEATURES.map(f => (
            <div key={f} className="flex items-start gap-2.5">
              <span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-brand-50 dark:bg-brand-900/30 mt-0.5 shrink-0">
                <Check size={11} className="text-brand-600 dark:text-brand-400" />
              </span>
              <span className="text-sm text-zinc-600 dark:text-zinc-300">{f}</span>
            </div>
          ))}
        </div>

        {/* CTA */}
        <div className="px-6 pb-6 space-y-3">
          {error && <p className="text-rose-500 text-xs text-center font-medium">{error}</p>}

          {!user ? (
            <SignInButton mode="modal">
              <button className="btn-primary w-full !py-3">Sign in to upgrade</button>
            </SignInButton>
          ) : (
            <button
              onClick={handlePay}
              disabled={loading}
              className="btn-primary w-full !py-3 !bg-zinc-900 dark:!bg-white hover:!bg-zinc-800 dark:hover:!bg-zinc-100 !text-white dark:!text-zinc-900"
            >
              {loading ? <><Loader2 size={16} className="animate-spin" /> Verifying…</> : `Pay ${PRICE_LABEL}`}
            </button>
          )}

          <p className="text-center text-[10px] text-zinc-400 dark:text-zinc-600">
            Secured by Paystack · Cancel anytime · NGN only
          </p>
        </div>
      </div>
    </div>
  );
}
