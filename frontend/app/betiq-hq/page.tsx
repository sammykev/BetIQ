"use client";

import { useEffect, useState } from "react";
import { Shield, ToggleLeft, ToggleRight, Loader2, RefreshCw, Unlock, Lock } from "lucide-react";
import clsx from "clsx";

const API = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";

interface Stats {
  predictions: number;
  model_ready: boolean;
  last_updated: string | null;
}

export default function AdminPage() {
  const [secret, setSecret]           = useState("");
  const [authed, setAuthed]           = useState(false);
  const [authError, setAuthError]     = useState(false);
  const [authLoading, setAuthLoading] = useState(false);
  const [paywallEnabled, setPaywallEnabled] = useState<boolean | null>(null);
  const [toggling, setToggling]       = useState(false);
  const [stats, setStats]             = useState<Stats | null>(null);

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

  const handleAuth = async () => {
    if (!secret.trim()) return;
    setAuthLoading(true);
    setAuthError(false);
    try {
      const res = await fetch(`${API}/api/config/paywall`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ secret, enabled: true }),
      });
      if (res.ok) {
        setAuthed(true);
        const data = await res.json();
        setPaywallEnabled(data.enabled);
      } else {
        setAuthError(true);
      }
    } catch {
      setAuthError(true);
    } finally {
      setAuthLoading(false);
    }
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
    } catch {/**/} finally {
      setToggling(false);
    }
  };

  /* ── Secret gate ── */
  if (!authed) {
    return (
      <div className="min-h-screen bg-slate-950 flex items-center justify-center p-4">
        <div className="w-full max-w-sm bg-slate-900 border border-slate-700 rounded-2xl p-6 space-y-4">
          <div className="flex items-center gap-3">
            <img src="/logo.svg" alt="" className="w-9 h-9 rounded-full" />
            <div>
              <h1 className="text-white font-bold">BetIQ Control Centre</h1>
              <p className="text-slate-500 text-xs">Authorised access only</p>
            </div>
          </div>

          <input
            type="password"
            placeholder="Admin secret"
            value={secret}
            onChange={e => setSecret(e.target.value)}
            onKeyDown={e => e.key === "Enter" && handleAuth()}
            className="w-full bg-slate-800 border border-slate-700 text-white rounded-xl px-4 py-2.5 text-sm outline-none focus:border-green-500 transition-colors"
          />

          {authError && (
            <p className="text-red-400 text-xs">Incorrect secret — check your Render env var.</p>
          )}

          <button
            onClick={handleAuth}
            disabled={authLoading || !secret.trim()}
            className="w-full bg-green-500 hover:bg-green-400 disabled:opacity-50 text-black font-bold py-2.5 rounded-xl text-sm transition-all flex items-center justify-center gap-2"
          >
            {authLoading ? <Loader2 size={14} className="animate-spin" /> : <Shield size={14} />}
            {authLoading ? "Checking…" : "Enter"}
          </button>
        </div>
      </div>
    );
  }

  /* ── Dashboard ── */
  return (
    <div className="min-h-screen bg-slate-950 p-6 space-y-6 max-w-2xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <img src="/logo.svg" alt="BetIQ" className="w-9 h-9 rounded-full" />
          <div>
            <h1 className="text-white font-bold text-lg">BetIQ Control Centre</h1>
            <p className="text-slate-500 text-xs">System management</p>
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

        <div className="flex items-center justify-between gap-4">
          <div>
            <p className="text-slate-300 text-sm font-medium">
              {paywallEnabled ? "Paywall is ON" : "Paywall is OFF"}
            </p>
            <p className="text-slate-500 text-xs mt-0.5">
              {paywallEnabled
                ? "Free users see predictions only. Premium required for analysis, AI & chatbot."
                : "All features open to everyone — no subscription required."}
            </p>
          </div>

          <button
            onClick={toggle}
            disabled={toggling || paywallEnabled === null}
            className="shrink-0 disabled:opacity-50 transition-all hover:scale-105"
          >
            {toggling ? (
              <Loader2 size={40} className="text-green-400 animate-spin" />
            ) : paywallEnabled ? (
              <ToggleRight size={56} className="text-green-400" />
            ) : (
              <ToggleLeft size={56} className="text-slate-600" />
            )}
          </button>
        </div>

        <div className={clsx(
          "flex items-center gap-2 px-4 py-3 rounded-xl text-sm font-medium border",
          paywallEnabled
            ? "bg-yellow-500/10 text-yellow-400 border-yellow-500/20"
            : "bg-green-500/10 text-green-400 border-green-500/20"
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
            <h2 className="text-white font-semibold">Backend Status</h2>
            <button
              onClick={() => fetch(`${API}/api/health`).then(r => r.json()).then(setStats)}
              className="p-1.5 hover:bg-slate-800 rounded-lg text-slate-500 transition-colors"
            >
              <RefreshCw size={13} />
            </button>
          </div>

          <div className="grid grid-cols-3 gap-3">
            {[
              { label: "Predictions", value: stats.predictions, color: "text-green-400" },
              { label: "Model ready", value: stats.model_ready ? "Yes" : "No", color: stats.model_ready ? "text-green-400" : "text-red-400" },
              { label: "Last updated", value: stats.last_updated ? new Date(stats.last_updated).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "Never", color: "text-slate-300" },
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
        BetIQ Admin · Changes apply instantly via Redis
      </p>
    </div>
  );
}
