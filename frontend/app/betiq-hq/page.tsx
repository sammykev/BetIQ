"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import clsx from "clsx";
import { useAuth } from "@clerk/nextjs";
import {
  AlertTriangle, Banknote, CheckCircle2, Cpu, Gauge, Globe2, LineChart as LineIcon,
  Loader2, Megaphone, RefreshCw, Shield, Ticket, ToggleRight, Users,
} from "lucide-react";
import { useAuthedFetch } from "@/lib/useAuthedFetch";
import { ThemeToggle } from "@/components/ThemeToggle";
import { API, AdminContext, type AdminApi, inputClass } from "@/components/admin/ui";
import { OverviewSection } from "@/components/admin/sections/Overview";
import { RevenueSection } from "@/components/admin/sections/Revenue";
import { UsersSection } from "@/components/admin/sections/Users";
import { AccessSection } from "@/components/admin/sections/Access";
import { PredictionsSection } from "@/components/admin/sections/Predictions";
import { SportyBetSection } from "@/components/admin/sections/SportyBet";
import { MessagingSection } from "@/components/admin/sections/Messaging";
import { SystemSection } from "@/components/admin/sections/System";
import { SecuritySection } from "@/components/admin/sections/Security";

// The map's world outline is ~100 KB: load the traffic tab only when opened
const TrafficSection = dynamic(() => import("@/components/admin/sections/Traffic").then(m => m.TrafficSection), {
  loading: () => <div className="card p-5"><div className="skeleton h-3 w-2/3" /></div>,
});

const TABS = [
  { id: "overview", label: "Overview", icon: Gauge },
  { id: "traffic", label: "Traffic", icon: Globe2 },
  { id: "revenue", label: "Revenue", icon: Banknote },
  { id: "users", label: "Users", icon: Users },
  { id: "access", label: "Access", icon: ToggleRight },
  { id: "predictions", label: "Predictions", icon: LineIcon },
  { id: "sportybet", label: "SportyBet", icon: Ticket },
  { id: "messaging", label: "Messaging", icon: Megaphone },
  { id: "system", label: "System", icon: Cpu },
  { id: "security", label: "Security", icon: Shield },
] as const;
type TabId = (typeof TABS)[number]["id"];

export default function AdminPage() {
  // Admin access (backend require_admin): your Clerk sign-in if your user id
  // is in ADMIN_USER_IDS, else the admin secret — kept in memory only and
  // sent as the X-Admin-Secret header, never in a URL or request body.
  const { isLoaded: clerkLoaded } = useAuth();
  const authedFetch = useAuthedFetch();
  const [secret, setSecret] = useState("");
  const [authed, setAuthed] = useState(false);
  const [authLoading, setAuthLoading] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);
  const [whoami, setWhoami] = useState<{ user_id: string | null; via?: string } | null>(null);
  const [tab, setTab] = useState<TabId>("overview");
  const [reloadKey, setReloadKey] = useState(0);
  const [toast, setToast] = useState<{ type: "ok" | "err"; text: string } | null>(null);

  const adminFetch = useCallback((url: string, init: RequestInit = {}) => {
    const headers = new Headers(init.headers);
    if (secret.trim()) headers.set("X-Admin-Secret", secret.trim());
    return authedFetch(url, { ...init, headers });
  }, [authedFetch, secret]);

  const api: AdminApi = useMemo(() => ({
    adminFetch,
    get: async (path) => {
      try {
        const r = await adminFetch(`${API}${path}`);
        return r.ok ? await r.json() : null;
      } catch { return null; }
    },
    post: async (path, body = {}) => {
      const r = await adminFetch(`${API}${path}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error((await r.text()) || `HTTP ${r.status}`);
      return r.json();
    },
    flash: (type, text) => { setToast({ type, text }); setTimeout(() => setToast(null), 3500); },
  }), [adminFetch]);

  // Problems worth a red banner (backend /api/admin/alerts): the database
  // missing, a setting's name broken in .env, live scores stalled
  const [alerts, setAlerts] = useState<{ level: "danger" | "warn"; title: string; detail: string }[]>([]);
  useEffect(() => {
    if (!authed) return;
    const load = () => api.get("/api/admin/alerts").then(d => setAlerts(d?.alerts ?? []));
    load();
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, [authed, api, reloadKey]);

  // The open tab lives in the URL (#traffic), so reloading keeps it
  useEffect(() => {
    const fromHash = () => {
      const h = window.location.hash.slice(1);
      if (TABS.some(t => t.id === h)) setTab(h as TabId);
    };
    fromHash();
    window.addEventListener("hashchange", fromHash);
    return () => window.removeEventListener("hashchange", fromHash);
  }, []);
  const goTo = useCallback((id: string) => {
    if (TABS.some(t => t.id === id)) { setTab(id as TabId); history.replaceState(null, "", `#${id}`); window.scrollTo({ top: 0 }); }
  }, []);

  const checkAdmin = useCallback(async (withSecret: boolean) => {
    setAuthLoading(true); setAuthError(null);
    try {
      const r = await adminFetch(`${API}/api/admin/whoami`);
      if (r.status === 429) { setAuthError("Too many wrong secrets from here — wait 15 minutes."); return; }
      const d = await r.json();
      setWhoami(d);
      if (d.admin) setAuthed(true);
      else if (withSecret) setAuthError("That secret isn't right.");
    } catch { if (withSecret) setAuthError("Couldn't reach the server — it may be waking up. Try again."); }
    finally { setAuthLoading(false); }
  }, [adminFetch]);

  useEffect(() => {
    if (clerkLoaded) checkAdmin(false);
    // Only once Clerk is ready; later checks come from the form
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clerkLoaded]);

  if (!authed) {
    return (
      <div className="min-h-screen bg-canvas flex items-center justify-center p-4">
        <form className="card w-full max-w-sm p-6 space-y-4" onSubmit={e => { e.preventDefault(); checkAdmin(true); }}>
          <div className="flex items-center gap-3">
            <img src="/logo.svg" alt="" className="w-9 h-9 rounded-xl" />
            <div>
              <h1 className="font-display font-extrabold text-xl uppercase text-n-0">Control Centre</h1>
              <p className="text-xs text-n-400">Admins only</p>
            </div>
          </div>
          {whoami?.user_id ? (
            <div className="text-xs text-n-300 rounded-xl bg-surface-sunken p-3 space-y-1">
              <p>You&apos;re signed in, but this account isn&apos;t an admin. To sign in with it instead of the secret, add
                its user id to <span className="text-n-0 font-semibold">ADMIN_USER_IDS</span> on Render and Vercel:</p>
              <p className="font-mono text-accent break-all select-all">{whoami.user_id}</p>
            </div>
          ) : (
            <p className="text-xs text-n-400">Sign in to the site with an admin account to skip the secret.</p>
          )}
          <input type="password" autoComplete="current-password" placeholder="Admin secret" value={secret}
            onChange={e => setSecret(e.target.value)} className={inputClass} />
          {authError && <p className="text-xs text-danger">{authError}</p>}
          <button type="submit" disabled={authLoading || !secret.trim()}
            className="w-full inline-flex items-center justify-center gap-2 rounded-xl bg-brand-400 hover:bg-brand-300 text-ink font-bold py-2.5 text-sm disabled:opacity-50">
            {authLoading ? <Loader2 size={14} className="animate-spin" /> : <Shield size={14} />}
            {authLoading ? "Checking…" : "Enter"}
          </button>
        </form>
      </div>
    );
  }

  const section = (() => {
    switch (tab) {
      case "traffic": return <TrafficSection />;
      case "revenue": return <RevenueSection />;
      case "users": return <UsersSection />;
      case "access": return <AccessSection />;
      case "predictions": return <PredictionsSection />;
      case "sportybet": return <SportyBetSection />;
      case "messaging": return <MessagingSection />;
      case "system": return <SystemSection />;
      case "security": return <SecuritySection />;
      default: return <OverviewSection goTo={goTo} />;
    }
  })();

  return (
    <AdminContext.Provider value={api}>
      <div className="min-h-screen bg-canvas">
        <header className="sticky top-0 z-30 border-b border-n-800 bg-canvas/90 backdrop-blur">
          <div className="max-w-6xl mx-auto px-4 pt-3 flex items-center gap-3">
            <img src="/logo.svg" alt="" className="w-8 h-8 rounded-lg" />
            <div className="min-w-0">
              <h1 className="font-display font-extrabold text-lg uppercase leading-none text-n-0">Control Centre</h1>
              <p className="text-[11px] text-n-400 truncate">
                {whoami?.via === "clerk" ? "Signed in as admin" : "Admin secret"}<span className="hidden sm:inline"> · changes apply instantly</span>
              </p>
            </div>
            <div className="ml-auto flex items-center gap-1">
              <button onClick={() => setReloadKey(k => k + 1)} title="Reload this tab"
                className="p-2 rounded-lg text-n-400 hover:text-n-0 hover:bg-surface-sunken"><RefreshCw size={15} /></button>
              <ThemeToggle />
              <a href="/" className="text-xs text-n-400 hover:text-accent px-2 whitespace-nowrap">← Site</a>
            </div>
          </div>
          <nav className="max-w-6xl mx-auto px-2 sm:px-4 mt-2 flex gap-1 overflow-x-auto" aria-label="Admin sections">
            {TABS.map(t => (
              <button key={t.id} onClick={() => goTo(t.id)} aria-current={tab === t.id ? "page" : undefined}
                className={clsx("inline-flex items-center gap-1.5 px-3 py-2 text-sm font-semibold border-b-2 whitespace-nowrap transition-colors",
                  tab === t.id ? "border-accent text-n-0" : "border-transparent text-n-400 hover:text-n-0")}>
                <t.icon size={14} /> {t.label}
              </button>
            ))}
          </nav>
        </header>

        {alerts.length > 0 && (
          <div className="max-w-6xl mx-auto px-4 pt-4 space-y-2" role="alert">
            {alerts.map(a => (
              <div key={a.title} className={clsx("rounded-xl border px-4 py-3 flex gap-3",
                a.level === "danger" ? "border-danger/50 bg-danger/10" : "border-warn/40 bg-warn/10")}>
                <AlertTriangle size={17} className={clsx("shrink-0 mt-0.5", a.level === "danger" ? "text-danger" : "text-warn")} />
                <div className="min-w-0">
                  <p className={clsx("text-sm font-bold", a.level === "danger" ? "text-danger" : "text-warn")}>{a.title}</p>
                  <p className="text-xs text-n-200 mt-0.5 break-words">{a.detail}</p>
                </div>
              </div>
            ))}
          </div>
        )}

        <main className="max-w-6xl mx-auto px-4 py-5" key={`${tab}-${reloadKey}`}>
          {section}
        </main>

        {toast && (
          <div role="status" className={clsx("fixed bottom-4 right-4 z-50 flex items-center gap-2 rounded-xl px-4 py-3 text-sm font-semibold shadow-card max-w-[90vw]",
            toast.type === "ok" ? "bg-brand-400 text-ink" : "bg-danger text-white")}>
            {toast.type === "ok" ? <CheckCircle2 size={15} /> : <AlertTriangle size={15} />}
            {toast.text}
          </div>
        )}
      </div>
    </AdminContext.Provider>
  );
}
