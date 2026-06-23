"use client";

import { useUser } from "@clerk/nextjs";
import { useEffect, useState } from "react";
import { Shield, ToggleLeft, ToggleRight, Loader2, RefreshCw, Users, Lock, Unlock } from "lucide-react";
import clsx from "clsx";

const API = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";
const ADMIN_EMAIL = process.env.NEXT_PUBLIC_ADMIN_EMAIL || "";

interface Stats {
  predictions: number;
  model_ready: boolean;
  last_updated: string | null;
}

export default function AdminPage() {
  const { user, isLoaded } = useUser();
  const [paywallEnabled, setPaywallEnabled] = useState<boolean | null>(null);
  const [toggling, setToggling]   = useState(false);
  const [stats, setStats]         = useState<Stats | null>(null);
  const [secret, setSecret]       = useState("");
  const [authed, setAuthed]       = useState(false);
  const [authError, setAuthError] = useState(false);

  const isAdmin = isLoaded && user?.emailAddresses.some(
    (e) => e.emailAddress === ADMIN_EMAIL
  );

  useEffect(() => {
    if (!authed) return;
    Promise.all([
      fetch(`${API}/api/config/paywall`).then(r => r.json()),
      fetch(`${API}/api/health`).then(r => r.json()),
    ]).then(([pw, health]) => {
      setPaywallEnabled(pw.enabled);
      setStats(health);
    }).catch(() => {});
  }, [authed]);

  const handleAuth = () => {
    if (!secret.trim()) return;
    // Verify by attempting a no-op toggle with the secret
    fetch(`${API}/api/config/paywall`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ secret, enabled: paywallEnabled ?? true }),
    }).then(r => {
      if (r.ok) { setAuthed(true); setAuthError(false); }
      else setAuthError(true);
    }).catch(() => setAuthError(true));
  };

  const toggle = async () => {
    if (paywallEnabled === null || toggling) return;
    setToggling(true);
    const next = !paywallEnabled;
    try {
      const res = await fetch(`${API}/api/config/paywall`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ secret, enabled: next }),
      });
      if (res.ok) setPaywallEnabled(next);
    } catch {/* silently fail */} finally {
      setToggling(false);
    }
  };

  // Not loaded yet
  if (!isLoaded) {
    return (
      <div className="min-h-screen bg-slate-950 flex items-center justify-center">
        <Loader2 size={24} className="text-green-400 animate-spin" />
      </div>
    );
  }

  // Not admin
  if (!isAdmin) {
    return (
      <div className="min-h-screen bg-slate-950 flex flex-col items-center justify-center gap-3 text-slate-500">
        <Shield size={40} />
        <p className="text-lg font-semibold">Admin access only</p>
        <a href="/" className="text-green-400 text-sm hover:underline">← Back to BetIQ</a>
      </div>
    );
  }

  // Admin secret gate
  if (!authed) {
    return (
      <div className="min-h-screen bg-slate-950 flex items-center justify-center p-4">
        <div className="w-full max-w-sm bg-slate-900 border border-slate-700 rounded-2xl p-6 space-y-4">
          <div className="flex items-center gap-2">
            <Shield size={20} className="text-green-400" />
            <h1 className="text-white font-bold">Admin Login</h1>
          </div>
          <input
            type="password"
            placeholder="Admin secret"
            value={secret}
            onChange={e => setSecret(e.target.value)}
            onKeyDown={e => e.key === "Enter" && handleAuth()}
            className="w-full bg-slate-800 border border-slate-700 text-white rounded-xl px-4 py-2.5 text-sm outline-none focus:border-green-500"
          />
          {authError && <p className="text-red-400 text-xs">Incorrect secret.</p>}
          <button
            onClick={handleAuth}
            className="w-full bg-green-500 hover:bg-green-400 text-black font-bold py-2.5 rounded-xl text-sm transition-all"
          >
            Enter
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-950 p-6 space-y-6 max-w-2xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <img src="/logo.svg" alt="BetIQ" className="w-9 h-9 rounded-full" />
          <div>
            <h1 className="text-white font-bold text-lg">BetIQ Admin</h1>
            <p className="text-slate-500 text-xs">{user?.emailAddresses[0]?.emailAddress}</p>
          </div>
        </div>
        <a href="/" className="text-xs text-slate-500 hover:text-green-400 transition-colors">
          ← Back to site
        </a>
      </div>

      {/* Paywall toggle */}
      <div className="bg-slate-900 border border-slate-700 rounded-2xl p-6 space-y-4">
        <div className="flex items-center gap-2">
          <Lock size={16} className="text-yellow-400" />
          <h2 className="text-white font-semibold">Paywall</h2>
        </div>

        <div className="flex items-center justify-between">
          <div>
            <p className="text-slate-300 text-sm font-medium">
              {paywallEnabled ? "Paywall is ON" : "Paywall is OFF"}
            </p>
            <p className="text-slate-500 text-xs mt-0.5">
              {paywallEnabled
                ? "Free users see predictions only. Premium required for analysis."
                : "All features are open to everyone — no subscription required."}
            </p>
          </div>

          <button
            onClick={toggle}
            disabled={toggling || paywallEnabled === null}
            className="shrink-0 disabled:opacity-50 transition-all"
          >
            {toggling ? (
              <Loader2 size={36} className="text-green-400 animate-spin" />
            ) : paywallEnabled ? (
              <ToggleRight size={48} className="text-green-400" />
            ) : (
              <ToggleLeft size={48} className="text-slate-600" />
            )}
          </button>
        </div>

        <div className={clsx(
          "flex items-center gap-2 px-4 py-3 rounded-xl text-sm font-medium",
          paywallEnabled
            ? "bg-yellow-500/10 text-yellow-400 border border-yellow-500/20"
            : "bg-green-500/10 text-green-400 border border-green-500/20"
        )}>
          {paywallEnabled ? <Lock size={14} /> : <Unlock size={14} />}
          {paywallEnabled
            ? "Users must subscribe at ₦1,500/month to access premium features."
            : "Paywall disabled — all features are free for all users right now."}
        </div>
      </div>

      {/* Server stats */}
      {stats && (
        <div className="bg-slate-900 border border-slate-700 rounded-2xl p-6 space-y-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Users size={16} className="text-blue-400" />
              <h2 className="text-white font-semibold">Backend Status</h2>
            </div>
            <button
              onClick={() => fetch(`${API}/api/health`).then(r => r.json()).then(setStats)}
              className="p-1.5 hover:bg-slate-800 rounded-lg text-slate-500"
            >
              <RefreshCw size={13} />
            </button>
          </div>

          <div className="grid grid-cols-3 gap-3">
            {[
              { label: "Predictions", value: stats.predictions, color: "text-green-400" },
              { label: "Model ready", value: stats.model_ready ? "Yes" : "No", color: stats.model_ready ? "text-green-400" : "text-red-400" },
              { label: "Last updated", value: stats.last_updated ? new Date(stats.last_updated).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"}) : "Never", color: "text-slate-300" },
            ].map(({ label, value, color }) => (
              <div key={label} className="bg-slate-800 rounded-xl px-4 py-3">
                <p className="text-xs text-slate-500">{label}</p>
                <p className={clsx("text-lg font-bold mt-0.5", color)}>{value}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      <p className="text-center text-slate-700 text-xs">
        BetIQ Admin · Changes apply instantly site-wide via Redis
      </p>
    </div>
  );
}
