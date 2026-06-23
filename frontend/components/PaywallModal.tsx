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
      className="fixed inset-0 z-[60] bg-black/85 backdrop-blur-sm flex items-center justify-center p-4"
      onClick={e => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="w-full max-w-md bg-slate-900 border border-slate-700 rounded-2xl shadow-2xl overflow-hidden">

        {/* Header */}
        <div className="relative bg-gradient-to-br from-yellow-500/20 to-green-500/10 border-b border-slate-700 px-6 py-6 text-center">
          <button onClick={onClose} className="absolute top-4 right-4 p-1.5 hover:bg-slate-700 rounded-lg text-slate-400">
            <X size={14} />
          </button>
          <Crown size={32} className="text-yellow-400 mx-auto mb-2" />
          <h2 className="text-xl font-black text-white">Unlock BetIQ Premium</h2>
          <p className="text-slate-400 text-sm mt-1">Everything you need to bet smarter</p>
          <div className="mt-3 inline-block bg-yellow-500 text-black font-black text-lg px-4 py-1 rounded-xl">
            {PRICE_LABEL}
          </div>
        </div>

        {/* Features */}
        <div className="px-6 py-5 space-y-2.5">
          {FEATURES.map(f => (
            <div key={f} className="flex items-start gap-2.5">
              <Check size={14} className="text-green-400 mt-0.5 shrink-0" />
              <span className="text-sm text-slate-300">{f}</span>
            </div>
          ))}
        </div>

        {/* CTA */}
        <div className="px-6 pb-6 space-y-3">
          {error && <p className="text-red-400 text-xs text-center">{error}</p>}

          {!user ? (
            <SignInButton mode="modal">
              <button className="w-full py-3 bg-green-500 hover:bg-green-400 text-black font-bold rounded-xl transition-all text-sm">
                Sign in to upgrade
              </button>
            </SignInButton>
          ) : (
            <button
              onClick={handlePay}
              disabled={loading}
              className="w-full py-3 bg-yellow-500 hover:bg-yellow-400 disabled:opacity-60 text-black font-bold rounded-xl transition-all text-sm flex items-center justify-center gap-2"
            >
              {loading ? <><Loader2 size={16} className="animate-spin" /> Verifying…</> : `Pay ${PRICE_LABEL}`}
            </button>
          )}

          <p className="text-center text-[10px] text-slate-600">
            Secured by Paystack · Cancel anytime · NGN only
          </p>
        </div>
      </div>
    </div>
  );
}
