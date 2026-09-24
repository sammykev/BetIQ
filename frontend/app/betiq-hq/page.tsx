"use client";

import { useEffect, useState, useCallback } from "react";
import {
  Shield, ToggleLeft, ToggleRight, Loader2, RefreshCw,
  Unlock, Lock, Trash2, Download, Megaphone,
  TrendingUp, Users, DollarSign, Zap, MessageSquare,
  AlertTriangle, CheckCircle2, BarChart2, Mail, Crown,
  UserPlus, UserMinus, MousePointerClick,
} from "lucide-react";
import clsx from "clsx";
import { TrackRecord } from "@/components/TrackRecord";

const API = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";

/* ───────────── helpers ───────────── */
function fmt(n: number) { return n.toLocaleString("en-NG"); }
function pct(n: number) {
  const color = n >= 60 ? "text-green-400" : n >= 45 ? "text-yellow-400" : "text-red-400";
  return <span className={clsx("font-bold", color)}>{n}%</span>;
}

/* ───────────── mini bar chart ───────────── */
function AccuracyChart({ daily }: { daily: { date: string; won: number; lost: number; accuracy: number | null }[] }) {
  const max = Math.max(...daily.map(d => d.won + d.lost), 1);
  return (
    <div className="flex items-end gap-1 h-20">
      {daily.slice(-21).map((d, i) => {
        const total = d.won + d.lost;
        const h = total === 0 ? 0 : Math.max(4, (total / max) * 80);
        const color = d.accuracy === null ? "bg-slate-700"
          : d.accuracy >= 60 ? "bg-green-500"
          : d.accuracy >= 45 ? "bg-yellow-400" : "bg-red-500";
        return (
          <div key={i} title={`${d.date}: ${d.won}W/${d.lost}L ${d.accuracy ?? "—"}%`}
            className="flex-1 flex flex-col justify-end">
            <div className={clsx("rounded-t transition-all", color)} style={{ height: h }} />
          </div>
        );
      })}
    </div>
  );
}

/* ───────────── main page ───────────── */
export default function AdminPage() {
  const [secret, setSecret]       = useState("");
  const [authed, setAuthed]       = useState(false);
  const [authLoading, setAuthLoading] = useState(false);
  const [authError, setAuthError] = useState(false);

  // Config state
  const [paywallEnabled, setPaywallEnabled]   = useState<boolean | null>(null);
  const [maintenance, setMaintenance]         = useState<boolean | null>(null);
  const [banner, setBanner]                   = useState("");
  const [bannerInput, setBannerInput]         = useState("");
  const [featured, setFeatured]               = useState<string[]>([]);

  // Data state
  const [stats, setStats]       = useState<any>(null);
  const [revenue, setRevenue]   = useState<any>(null);
  const [subs, setSubs]         = useState<{ total: number; premium: number } | null>(null);
  const [health, setHealth]     = useState<any>(null);
  const [users, setUsers]       = useState<any>(null);
  const [popular, setPopular]   = useState<any>(null);
  const [dataStatus, setDataStatus] = useState<any>(null);
  const [sbCheck, setSbCheck] = useState<any>(null);
  const [sbChecking, setSbChecking] = useState(false);
  const [intlCheck, setIntlCheck] = useState<any>(null);
  const [intlChecking, setIntlChecking] = useState(false);
  // Grant/Revoke
  const [grantEmail, setGrantEmail]   = useState("");
  const [grantAction, setGrantAction] = useState<"grant"|"revoke">("grant");
  const [grantMsg, setGrantMsg]       = useState<string | null>(null);
  const [grantLoading, setGrantLoading] = useState(false);
  // Email blast
  const [blastSubject, setBlastSubject] = useState("");
  const [blastMsg,     setBlastMsg]     = useState("");
  const [blastResult,  setBlastResult]  = useState<string | null>(null);
  const [blastLoading, setBlastLoading] = useState(false);

  // Action state
  const [toggling, setToggling] = useState<string | null>(null);
  const [msg, setMsg]           = useState<{ type: "ok" | "err"; text: string } | null>(null);

  const flash = (type: "ok" | "err", text: string) => {
    setMsg({ type, text });
    setTimeout(() => setMsg(null), 3000);
  };

  const post = useCallback(async (path: string, body: object) => {
    const r = await fetch(`${API}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ secret, ...body }),
    });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  }, [secret]);

  const loadAll = useCallback(async () => {
    const [pw, maint, ban, feat, h] = await Promise.all([
      fetch(`${API}/api/config/paywall`).then(r => r.json()),
      fetch(`${API}/api/config/maintenance`).then(r => r.json()),
      fetch(`${API}/api/admin/banner`).then(r => r.json()),
      fetch(`${API}/api/admin/featured`).then(r => r.json()),
      fetch(`${API}/api/health`).then(r => r.json()),
    ]);
    setPaywallEnabled(pw.enabled);
    setMaintenance(maint.enabled);
    setBanner(ban.banner || "");
    setBannerInput(ban.banner || "");
    setFeatured(feat.featured?.map((p: any) => `${p.home} vs ${p.away}`) ?? []);
    setHealth(h);

    // Load heavy data in background
    fetch(`${API}/api/admin/stats?secret=${encodeURIComponent(secret)}`).then(r => r.json()).then(setStats).catch(() => {});
    fetch(`${API}/api/admin/revenue?secret=${encodeURIComponent(secret)}`).then(r => r.json()).then(setRevenue).catch(() => {});
    fetch(`${API}/api/admin/popular?secret=${encodeURIComponent(secret)}`).then(r => r.json()).then(setPopular).catch(() => {});
    fetch(`${API}/api/admin/data-status?secret=${encodeURIComponent(secret)}`).then(r => r.ok ? r.json() : null).then(setDataStatus).catch(() => {});
    fetch("/api/admin/subscribers").then(r => r.json()).then(setSubs).catch(() => {});
    fetch(`/api/admin/users?secret=${encodeURIComponent(secret)}`).then(r => r.json()).then(setUsers).catch(() => {});
  }, [secret]);

  useEffect(() => { if (authed) loadAll(); }, [authed, loadAll]);

  const handleAuth = async () => {
    setAuthLoading(true); setAuthError(false);
    try {
      const r = await fetch(`${API}/api/config/paywall`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ secret, enabled: true }),
      });
      if (r.ok) { setPaywallEnabled((await r.json()).enabled); setAuthed(true); }
      else setAuthError(true);
    } catch { setAuthError(true); }
    finally { setAuthLoading(false); }
  };

  const toggle = async (key: "paywall" | "maintenance") => {
    setToggling(key);
    try {
      if (key === "paywall") {
        const d = await post("/api/config/paywall", { enabled: !paywallEnabled });
        setPaywallEnabled(d.enabled);
        flash("ok", `Paywall ${d.enabled ? "enabled" : "disabled"}`);
      } else {
        const d = await post("/api/config/maintenance", { enabled: !maintenance });
        setMaintenance(d.enabled);
        flash("ok", `Maintenance mode ${d.enabled ? "ON" : "OFF"}`);
      }
    } catch { flash("err", "Toggle failed"); }
    finally { setToggling(null); }
  };

  const refreshPipeline = async () => {
    setToggling("refresh");
    try {
      await fetch(`${API}/api/refresh`, { method: "POST" });
      flash("ok", "Pipeline refresh triggered — takes ~3 min");
    } catch { flash("err", "Refresh failed"); }
    finally { setToggling(null); }
  };

  const clearCache = async () => {
    setToggling("cache");
    try {
      await post("/api/admin/clear-cache", {});
      flash("ok", "Redis cache cleared");
    } catch { flash("err", "Clear failed"); }
    finally { setToggling(null); }
  };

  const publishBanner = async () => {
    try {
      const d = await post("/api/admin/banner", { text: bannerInput });
      setBanner(d.banner || "");
      flash("ok", d.banner ? "Banner published" : "Banner cleared");
    } catch { flash("err", "Banner update failed"); }
  };

  const handleGrant = async () => {
    if (!grantEmail) return;
    setGrantLoading(true); setGrantMsg(null);
    try {
      const res = await fetch("/api/admin/grant", {
        method: grantAction === "grant" ? "POST" : "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ secret, email: grantEmail }),
      });
      const d = await res.json();
      setGrantMsg(d.ok ? `✅ ${d.action} for ${d.email}` : `❌ ${d.error}`);
      if (d.ok) setGrantEmail("");
    } catch { setGrantMsg("❌ Request failed"); }
    finally { setGrantLoading(false); }
  };

  const handleBlast = async () => {
    if (!blastSubject || !blastMsg) return;
    setBlastLoading(true); setBlastResult(null);
    try {
      const res = await fetch("/api/admin/blast", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ secret, subject: blastSubject, message: blastMsg }),
      });
      const d = await res.json();
      setBlastResult(d.error ? `❌ ${d.error}` : `✅ Sent to ${d.sent} users (${d.total_premium} premium total)`);
    } catch { setBlastResult("❌ Send failed"); }
    finally { setBlastLoading(false); }
  };

  const downloadCSV = () => {
    const url = `${API}/api/predictions?limit=500`;
    fetch(url).then(r => r.json()).then(data => {
      const rows = data.predictions || [];
      const header = "home,away,date,time,league,tip_1x2,tip_goals,goals_confidence\n";
      const csv = header + rows.map((p: any) =>
        [p.home, p.away, p.date, p.time, p.league_name, p.tip_1x2, p.tip_goals, p.goals_confidence].join(",")
      ).join("\n");
      const a = document.createElement("a");
      a.href = "data:text/csv;charset=utf-8," + encodeURIComponent(csv);
      a.download = `betiq-predictions-${new Date().toISOString().slice(0,10)}.csv`;
      a.click();
    });
  };

  /* ── Auth gate ── */
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
          <input type="password" placeholder="Admin secret" value={secret}
            onChange={e => setSecret(e.target.value)}
            onKeyDown={e => e.key === "Enter" && handleAuth()}
            className="w-full bg-slate-800 border border-slate-700 text-white rounded-xl px-4 py-2.5 text-sm outline-none focus:border-green-500 transition-colors" />
          {authError && <p className="text-red-400 text-xs">Incorrect secret.</p>}
          <button onClick={handleAuth} disabled={authLoading || !secret.trim()}
            className="w-full bg-green-500 hover:bg-green-400 disabled:opacity-50 text-black font-bold py-2.5 rounded-xl text-sm transition-all flex items-center justify-center gap-2">
            {authLoading ? <Loader2 size={14} className="animate-spin" /> : <Shield size={14} />}
            {authLoading ? "Checking…" : "Enter"}
          </button>
        </div>
      </div>
    );
  }

  /* ── Dashboard ── */
  return (
    <div className="min-h-screen bg-slate-950 p-4 md:p-6 space-y-5 max-w-5xl mx-auto">

      {/* Flash message */}
      {msg && (
        <div className={clsx(
          "fixed top-4 right-4 z-50 flex items-center gap-2 px-4 py-3 rounded-xl text-sm font-semibold shadow-xl",
          msg.type === "ok" ? "bg-green-500 text-black" : "bg-red-500 text-white"
        )}>
          {msg.type === "ok" ? <CheckCircle2 size={14} /> : <AlertTriangle size={14} />}
          {msg.text}
        </div>
      )}

      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <img src="/logo.svg" alt="" className="w-9 h-9 rounded-full" />
          <div>
            <h1 className="text-white font-bold text-lg">BetIQ Control Centre</h1>
            <p className="text-slate-500 text-xs">System management</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={loadAll} className="p-1.5 hover:bg-slate-800 rounded-lg text-slate-500">
            <RefreshCw size={13} />
          </button>
          <a href="/" className="text-xs text-slate-500 hover:text-green-400 transition-colors">← Site</a>
        </div>
      </div>

      {/* ── Quick Controls ── */}
      <section className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {/* Paywall */}
        <div className={clsx("bg-slate-900 border rounded-xl p-4 flex flex-col gap-2 cursor-pointer transition-all hover:border-slate-600",
          paywallEnabled ? "border-yellow-500/30" : "border-slate-700")}
          onClick={() => toggle("paywall")}>
          <div className="flex items-center justify-between">
            {paywallEnabled ? <Lock size={14} className="text-yellow-400" /> : <Unlock size={14} className="text-green-400" />}
            {toggling === "paywall"
              ? <Loader2 size={20} className="animate-spin text-slate-500" />
              : paywallEnabled ? <ToggleRight size={28} className="text-yellow-400" /> : <ToggleLeft size={28} className="text-slate-600" />}
          </div>
          <p className="text-white text-xs font-semibold">Paywall</p>
          <p className="text-slate-500 text-[10px]">{paywallEnabled ? "Premium required" : "All access"}</p>
        </div>

        {/* Maintenance */}
        <div className={clsx("bg-slate-900 border rounded-xl p-4 flex flex-col gap-2 cursor-pointer transition-all hover:border-slate-600",
          maintenance ? "border-orange-500/30" : "border-slate-700")}
          onClick={() => toggle("maintenance")}>
          <div className="flex items-center justify-between">
            <AlertTriangle size={14} className={maintenance ? "text-orange-400" : "text-slate-500"} />
            {toggling === "maintenance"
              ? <Loader2 size={20} className="animate-spin text-slate-500" />
              : maintenance ? <ToggleRight size={28} className="text-orange-400" /> : <ToggleLeft size={28} className="text-slate-600" />}
          </div>
          <p className="text-white text-xs font-semibold">Maintenance</p>
          <p className="text-slate-500 text-[10px]">{maintenance ? "Site paused" : "Site live"}</p>
        </div>

        {/* Refresh predictions */}
        <button onClick={refreshPipeline} disabled={toggling === "refresh"}
          className="bg-slate-900 border border-slate-700 hover:border-blue-500/40 rounded-xl p-4 flex flex-col gap-2 text-left transition-all disabled:opacity-50">
          <RefreshCw size={14} className={clsx("text-blue-400", toggling === "refresh" && "animate-spin")} />
          <p className="text-white text-xs font-semibold">Refresh Predictions</p>
          <p className="text-slate-500 text-[10px]">Trigger pipeline now</p>
        </button>

        {/* Clear cache */}
        <button onClick={clearCache} disabled={toggling === "cache"}
          className="bg-slate-900 border border-slate-700 hover:border-red-500/40 rounded-xl p-4 flex flex-col gap-2 text-left transition-all disabled:opacity-50">
          <Trash2 size={14} className="text-red-400" />
          <p className="text-white text-xs font-semibold">Clear Cache</p>
          <p className="text-slate-500 text-[10px]">Wipe Redis predictions</p>
        </button>
      </section>

      {/* ── Announcement Banner ── */}
      <section className="bg-slate-900 border border-slate-700 rounded-xl p-5 space-y-3">
        <div className="flex items-center gap-2">
          <Megaphone size={15} className="text-blue-400" />
          <h2 className="text-white font-semibold text-sm">Announcement Banner</h2>
          {banner && <span className="ml-auto text-[10px] bg-blue-500/15 text-blue-400 border border-blue-500/25 px-2 py-0.5 rounded-full">Live</span>}
        </div>
        {banner && (
          <div className="bg-blue-500/10 border border-blue-500/20 rounded-lg px-3 py-2 text-blue-300 text-xs">
            📢 {banner}
          </div>
        )}
        <div className="flex gap-2">
          <input value={bannerInput} onChange={e => setBannerInput(e.target.value)}
            placeholder='e.g. "🔥 EPL fixtures just dropped — check your picks!"'
            className="flex-1 bg-slate-800 border border-slate-700 text-white rounded-lg px-3 py-2 text-sm outline-none focus:border-blue-500 transition-colors" />
          <button onClick={publishBanner}
            className="bg-blue-500 hover:bg-blue-400 text-white text-xs font-bold px-4 py-2 rounded-lg transition-all">
            {bannerInput.trim() ? "Publish" : "Clear"}
          </button>
        </div>
        <p className="text-slate-600 text-[10px]">Appears as a blue bar at the top of the site. Leave empty and click Clear to remove it.</p>
      </section>

      {/* ── Download ── */}
      <section className="bg-slate-900 border border-slate-700 rounded-xl p-5 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Download size={15} className="text-green-400" />
          <div>
            <p className="text-white font-semibold text-sm">Export Predictions</p>
            <p className="text-slate-500 text-xs">Download current predictions as CSV</p>
          </div>
        </div>
        <button onClick={downloadCSV}
          className="bg-green-500/10 hover:bg-green-500/20 border border-green-500/30 text-green-400 text-xs font-bold px-4 py-2 rounded-lg transition-all">
          Download CSV
        </button>
      </section>

      {/* ── Revenue + Subscribers ── */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Revenue */}
        <section className="bg-slate-900 border border-slate-700 rounded-xl p-5 space-y-4">
          <div className="flex items-center gap-2">
            <DollarSign size={15} className="text-green-400" />
            <h2 className="text-white font-semibold text-sm">Revenue</h2>
          </div>
          {!revenue ? (
            <div className="space-y-2 animate-pulse">
              {[1,2,3].map(i => <div key={i} className="h-3 bg-slate-800 rounded" />)}
            </div>
          ) : revenue.error ? (
            <p className="text-slate-500 text-xs">{revenue.error === "no_paystack_key" ? "PAYSTACK_SECRET_KEY not set in Render." : revenue.error}</p>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-3">
                <div className="bg-slate-800 rounded-lg px-3 py-2.5">
                  <p className="text-slate-500 text-[10px]">Total Revenue</p>
                  <p className="text-green-400 font-black text-xl">₦{fmt(revenue.total_revenue)}</p>
                </div>
                <div className="bg-slate-800 rounded-lg px-3 py-2.5">
                  <p className="text-slate-500 text-[10px]">Transactions</p>
                  <p className="text-white font-black text-xl">{revenue.transaction_count}</p>
                </div>
              </div>
              <div className="space-y-1">
                <p className="text-slate-500 text-[10px] font-medium uppercase tracking-wide">Recent payments</p>
                {(revenue.recent || []).slice(0, 5).map((t: any, i: number) => (
                  <div key={i} className="flex items-center justify-between text-xs">
                    <span className="text-slate-400 truncate flex-1">{t.email}</span>
                    <span className="text-green-400 font-semibold ml-2">₦{fmt(t.amount)}</span>
                    <span className="text-slate-600 ml-2">{t.date}</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </section>

        {/* Subscribers */}
        <section className="bg-slate-900 border border-slate-700 rounded-xl p-5 space-y-4">
          <div className="flex items-center gap-2">
            <Users size={15} className="text-purple-400" />
            <h2 className="text-white font-semibold text-sm">Subscribers</h2>
          </div>
          {!subs ? (
            <div className="space-y-2 animate-pulse">
              {[1,2].map(i => <div key={i} className="h-12 bg-slate-800 rounded-lg" />)}
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-3">
              <div className="bg-slate-800 rounded-lg px-3 py-2.5">
                <p className="text-slate-500 text-[10px]">Total Users</p>
                <p className="text-white font-black text-2xl">{fmt(subs.total)}</p>
              </div>
              <div className="bg-slate-800 rounded-lg px-3 py-2.5">
                <p className="text-slate-500 text-[10px]">Premium Active</p>
                <p className="text-yellow-400 font-black text-2xl">{fmt(subs.premium)}</p>
              </div>
              <div className="bg-slate-800 rounded-lg px-3 py-2.5 col-span-2">
                <p className="text-slate-500 text-[10px]">Conversion Rate</p>
                <p className="text-white font-black text-xl">
                  {subs.total > 0 ? Math.round(subs.premium / subs.total * 100) : 0}%
                </p>
              </div>
            </div>
          )}
        </section>
      </div>

      {/* ── Prediction Accuracy ── */}
      <section className="bg-slate-900 border border-slate-700 rounded-xl p-5 space-y-4">
        <div className="flex items-center gap-2">
          <TrendingUp size={15} className="text-blue-400" />
          <h2 className="text-white font-semibold text-sm">Prediction Accuracy (30 days)</h2>
        </div>
        {!stats ? (
          <div className="h-32 bg-slate-800 rounded-lg animate-pulse" />
        ) : stats.error ? (
          <p className="text-slate-500 text-xs">{stats.error}</p>
        ) : (
          <>
            {/* Overall */}
            <div className="grid grid-cols-4 gap-3">
              {[
                { label: "Accuracy", value: pct(stats.overall.accuracy), sub: `${stats.overall.won + stats.overall.lost} settled` },
                { label: "Won", value: <span className="text-green-400 font-bold text-xl">{stats.overall.won}</span>, sub: "predictions" },
                { label: "Lost", value: <span className="text-red-400 font-bold text-xl">{stats.overall.lost}</span>, sub: "predictions" },
                { label: "Pending", value: <span className="text-slate-400 font-bold text-xl">{stats.overall.pending}</span>, sub: "awaiting" },
              ].map(({ label, value, sub }) => (
                <div key={label} className="bg-slate-800 rounded-lg px-3 py-2.5">
                  <p className="text-slate-500 text-[10px]">{label}</p>
                  <div className="text-xl mt-0.5">{value}</div>
                  <p className="text-slate-600 text-[10px]">{sub}</p>
                </div>
              ))}
            </div>

            {/* Daily chart */}
            <div className="space-y-1">
              <p className="text-slate-500 text-[10px] uppercase tracking-wide">Daily accuracy (last 21 days)</p>
              <AccuracyChart daily={stats.daily} />
              <div className="flex gap-4 text-[10px] text-slate-600">
                <span className="flex items-center gap-1"><span className="w-2 h-2 bg-green-500 rounded-sm inline-block"/>≥60%</span>
                <span className="flex items-center gap-1"><span className="w-2 h-2 bg-yellow-400 rounded-sm inline-block"/>45-60%</span>
                <span className="flex items-center gap-1"><span className="w-2 h-2 bg-red-500 rounded-sm inline-block"/>&lt;45%</span>
              </div>
            </div>

            {/* By league */}
            <div className="space-y-1">
              <p className="text-slate-500 text-[10px] uppercase tracking-wide">By league</p>
              {Object.entries(stats.by_league as Record<string, any>)
                .sort((a, b) => b[1].accuracy - a[1].accuracy)
                .map(([code, s]: [string, any]) => (
                  <div key={code} className="flex items-center gap-3 text-xs">
                    <span className="text-slate-300 w-32 truncate">{s.flag} {s.name}</span>
                    <div className="flex-1 h-1.5 bg-slate-800 rounded-full overflow-hidden">
                      <div className={clsx("h-full rounded-full", s.accuracy >= 60 ? "bg-green-500" : s.accuracy >= 45 ? "bg-yellow-400" : "bg-red-500")}
                        style={{ width: `${s.accuracy}%` }} />
                    </div>
                    {pct(s.accuracy)}
                    <span className="text-slate-600 w-12 text-right">{s.total} games</span>
                  </div>
                ))}
            </div>

            {/* By tip type */}
            <div className="space-y-1">
              <p className="text-slate-500 text-[10px] uppercase tracking-wide">By tip type</p>
              <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
                {Object.entries(stats.by_tip as Record<string, any>).map(([tip, s]: [string, any]) => (
                  <div key={tip} className="bg-slate-800 rounded-lg px-3 py-2">
                    <p className="text-slate-400 text-[10px]">{tip}</p>
                    <div className="flex items-center justify-between">
                      {pct(s.accuracy)}
                      <span className="text-slate-600 text-[10px]">{s.won}W/{s.lost}L</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </>
        )}
      </section>

      {/* ── SportyBet booking check ── */}
      <section className="bg-slate-900 border border-slate-700 rounded-xl p-5 space-y-3">
        <div className="flex items-center gap-2">
          <Zap size={15} className="text-green-400" />
          <h2 className="text-white font-semibold text-sm">SportyBet booking</h2>
          <button
            onClick={async () => {
              setSbChecking(true); setSbCheck(null);
              try {
                const r = await fetch(`${API}/api/admin/sportybet-check?secret=${encodeURIComponent(secret)}`);
                setSbCheck(r.ok ? await r.json() : { ok: false, steps: [{ step: "Request", ok: false, detail: `HTTP ${r.status}` }] });
              } catch (e) {
                setSbCheck({ ok: false, steps: [{ step: "Request", ok: false, detail: String(e) }] });
              } finally { setSbChecking(false); }
            }}
            disabled={sbChecking}
            className="ml-auto flex items-center gap-1.5 bg-green-500 hover:bg-green-400 disabled:opacity-50 text-black font-bold px-3 py-1.5 rounded-lg text-xs"
          >
            {sbChecking ? <Loader2 size={12} className="animate-spin" /> : <Zap size={12} />}
            {sbChecking ? "Testing…" : "Test live booking"}
          </button>
        </div>
        <p className="text-slate-500 text-xs">Books a real one-pick code from the server — codes only load a betslip, nothing is staked.</p>
        {sbCheck && (
          <div className="space-y-1.5">
            <p className={clsx("text-sm font-bold", sbCheck.ok ? "text-green-400" : "text-red-400")}>
              {sbCheck.ok ? "Booking works" : "Booking is failing"}
              {sbCheck.impersonate && <span className="text-slate-500 font-normal text-xs ml-2">({sbCheck.impersonate}, {sbCheck.country})</span>}
            </p>
            {(sbCheck.steps ?? []).map((st: any) => (
              <div key={st.step} className="flex items-start gap-2 text-xs">
                {st.ok ? <CheckCircle2 size={13} className="text-green-400 mt-px shrink-0" /> : <AlertTriangle size={13} className="text-red-400 mt-px shrink-0" />}
                <span className="text-slate-300 font-semibold shrink-0">{st.step}</span>
                <span className="text-slate-400 break-words">{st.detail}{st.ms != null && <span className="text-slate-600"> · {st.ms} ms</span>}</span>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* ── International fixtures ── */}
      <section className="bg-slate-900 border border-slate-700 rounded-xl p-5 space-y-3">
        <div className="flex items-center gap-2">
          <RefreshCw size={15} className="text-sky-400" />
          <h2 className="text-white font-semibold text-sm">International fixtures</h2>
          <button
            onClick={async () => {
              setIntlChecking(true); setIntlCheck(null);
              try {
                const r = await fetch(`${API}/api/admin/international-check?secret=${encodeURIComponent(secret)}`);
                setIntlCheck(r.ok ? await r.json() : { error: `HTTP ${r.status}` });
              } catch (e) {
                setIntlCheck({ error: String(e) });
              } finally { setIntlChecking(false); }
            }}
            disabled={intlChecking}
            className="ml-auto flex items-center gap-1.5 bg-sky-500 hover:bg-sky-400 disabled:opacity-50 text-black font-bold px-3 py-1.5 rounded-lg text-xs"
          >
            {intlChecking ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
            {intlChecking ? "Fetching…" : "Fetch now"}
          </button>
        </div>
        <p className="text-slate-500 text-xs">Asks ESPN and The Odds API for this window&apos;s national-team matches, shows what each returned, and publishes them.</p>
        {intlCheck?.error && <p className="text-xs text-red-400">{intlCheck.error}</p>}
        {intlCheck && !intlCheck.error && (
          <div className="text-xs space-y-2">
            <p className={clsx("font-bold", intlCheck.fixtures > 0 ? "text-green-400" : "text-red-400")}>
              {intlCheck.fixtures} fixtures found · {intlCheck.published} published
              {!intlCheck.model_ready && " (model still training; they publish when it's ready)"}
            </p>
            {Object.keys(intlCheck.by_competition ?? {}).length > 0 && (
              <p className="text-slate-300">
                {Object.entries(intlCheck.by_competition as Record<string, number>).map(([c, n]) => `${c}: ${n}`).join(" · ")}
              </p>
            )}
            {([["espn", "ESPN"], ["sofascore", "SofaScore"], ["odds_api", "The Odds API"]] as const).map(([key, label]) => (
              <p key={key} className="text-slate-400">
                {label} — {Object.entries((intlCheck.sources?.[key] ?? {}) as Record<string, number>)
                  .map(([k, n]) => `${k} ${n}`).join(" · ") || (key === "odds_api" ? "nothing (or no ODDS_API_KEY)" : "nothing")}
              </p>
            ))}
            {intlCheck.errors?.length > 0 && (
              <p className="text-red-400 break-words">
                Errors: {(intlCheck.errors as string[]).map(e => e.length > 90 ? e.slice(0, 90) + "…" : e).join(" · ")}
              </p>
            )}
          </div>
        )}
      </section>

      {/* ── Training data ── */}
      {dataStatus?.leagues && (
        <section className="bg-slate-900 border border-slate-700 rounded-xl p-5 space-y-4">
          <div className="flex items-center gap-2">
            <RefreshCw size={15} className="text-sky-400" />
            <h2 className="text-white font-semibold text-sm">Training data</h2>
            <span className="text-slate-500 text-xs ml-auto">
              {dataStatus.last_sync?.at
                ? `Synced ${new Date(dataStatus.last_sync.at).toLocaleString()}`
                : "Not synced since the last restart"}
            </span>
          </div>
          <div className="grid grid-cols-3 sm:grid-cols-5 gap-2">
            {Object.entries(dataStatus.leagues as Record<string, { latest_match: string }>).map(([div, l]) => {
              const days = Math.floor((Date.now() - new Date(l.latest_match).getTime()) / 86400000);
              return (
                <div key={div} className="bg-slate-800 rounded-lg px-3 py-2">
                  <p className="text-slate-400 text-[10px] font-bold">{div}</p>
                  <p className={clsx("text-xs font-semibold", days > 14 ? "text-amber-400" : "text-white")}>{l.latest_match}</p>
                </div>
              );
            })}
          </div>
          {dataStatus.last_sync?.report?.failed?.length > 0 && (
            <p className="text-xs text-red-400">Sync failed: {dataStatus.last_sync.report.failed.join(" · ")}</p>
          )}
          {dataStatus.international?.at && (
            <div className="text-xs text-slate-400 space-y-1">
              <p>
                <span className="text-slate-300 font-semibold">Internationals:</span>{" "}
                {dataStatus.international.fixtures} upcoming fixtures · ESPN{" "}
                {Object.values(dataStatus.international.sources?.espn ?? {}).reduce((a: number, n) => a + (n as number), 0)}
                {" "}· SofaScore{" "}
                {Object.values(dataStatus.international.sources?.sofascore ?? {}).reduce((a: number, n) => a + (n as number), 0)}
                {" "}· The Odds API{" "}
                {Object.values(dataStatus.international.sources?.odds_api ?? {}).reduce((a: number, n) => a + (n as number), 0)}
              </p>
              {dataStatus.international.errors?.length > 0 && (
                <p className="text-slate-500">Source errors: {dataStatus.international.errors.join(" · ")}</p>
              )}
            </div>
          )}
          <div className="text-xs text-slate-400 space-y-1">
            <p>
              {dataStatus.teams_checked} upcoming teams checked · {Object.keys(dataStatus.renamed ?? {}).length} matched
              to a different training name · {dataStatus.thin_history?.length ?? 0} with under 5 known matches
            </p>
            {dataStatus.thin_history?.length > 0 && (
              <p className="text-slate-500">
                Little or no history (new club, or a name to add to backend/team_names.py):{" "}
                {dataStatus.thin_history.map((t: any) => `${t.team} (${t.matches})`).join(", ")}
              </p>
            )}
          </div>
        </section>
      )}

      {/* ── Model backtest ── */}
      <TrackRecord secret={secret} />

      {/* ── AI & Chatbot ── */}
      <section className="bg-slate-900 border border-slate-700 rounded-xl p-5 space-y-4">
        <div className="flex items-center gap-2">
          <Zap size={15} className="text-purple-400" />
          <h2 className="text-white font-semibold text-sm">AI & Chatbot Usage</h2>
        </div>
        {!stats ? (
          <div className="h-16 bg-slate-800 rounded-lg animate-pulse" />
        ) : (
          <>
            <div className="grid grid-cols-3 gap-3">
              <div className="bg-slate-800 rounded-lg px-3 py-2.5">
                <p className="text-slate-500 text-[10px]">AI Explanations Today</p>
                <p className="text-purple-400 font-black text-2xl">{stats.ai?.explain_today ?? 0}</p>
              </div>
              <div className="bg-slate-800 rounded-lg px-3 py-2.5">
                <p className="text-slate-500 text-[10px]">Total Explanations</p>
                <p className="text-white font-black text-2xl">{fmt(stats.ai?.explain_total ?? 0)}</p>
              </div>
              <div className="bg-slate-800 rounded-lg px-3 py-2.5">
                <p className="text-slate-500 text-[10px]">Recent Chat Queries</p>
                <p className="text-white font-black text-2xl">{stats.ai?.recent_queries?.length ?? 0}</p>
              </div>
            </div>
            {(stats.ai?.recent_queries || []).length > 0 && (
              <div className="space-y-1">
                <p className="text-slate-500 text-[10px] uppercase tracking-wide">Recent chat queries</p>
                {(stats.ai.recent_queries as string[]).slice(0, 8).map((q, i) => (
                  <div key={i} className="flex items-center gap-2 text-xs">
                    <MessageSquare size={10} className="text-slate-600 shrink-0" />
                    <span className="text-slate-400 truncate">{q}</span>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </section>

      {/* ── Backend Status ── */}
      {health && (
        <section className="bg-slate-900 border border-slate-700 rounded-xl p-5 space-y-3">
          <div className="flex items-center gap-2">
            <BarChart2 size={15} className="text-blue-400" />
            <h2 className="text-white font-semibold text-sm">Backend Status</h2>
          </div>
          <div className="grid grid-cols-3 gap-3">
            {[
              { label: "Predictions", value: health.predictions, color: "text-green-400" },
              { label: "Model Ready", value: health.model_ready ? "Yes" : "No", color: health.model_ready ? "text-green-400" : "text-red-400" },
              { label: "Last Updated", value: health.last_updated ? new Date(health.last_updated).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "Never", color: "text-slate-300" },
            ].map(({ label, value, color }) => (
              <div key={label} className="bg-slate-800 rounded-lg px-3 py-2.5">
                <p className="text-slate-500 text-[10px]">{label}</p>
                <p className={clsx("font-bold text-lg mt-0.5", color)}>{value}</p>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* ── Grant / Revoke Premium ── */}
      <section className="bg-slate-900 border border-slate-700 rounded-xl p-5 space-y-4">
        <div className="flex items-center gap-2">
          <Crown size={15} className="text-yellow-400" />
          <h2 className="text-white font-semibold text-sm">Grant / Revoke Premium</h2>
        </div>
        <div className="flex gap-2">
          {(["grant","revoke"] as const).map(a => (
            <button key={a} onClick={() => setGrantAction(a)}
              className={clsx("flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all",
                grantAction === a
                  ? a === "grant" ? "bg-green-500/20 text-green-400 border border-green-500/30" : "bg-red-500/20 text-red-400 border border-red-500/30"
                  : "bg-slate-800 text-slate-400 border border-slate-700")}>
              {a === "grant" ? <UserPlus size={12}/> : <UserMinus size={12}/>} {a}
            </button>
          ))}
        </div>
        <div className="flex gap-2">
          <input value={grantEmail} onChange={e => setGrantEmail(e.target.value)} placeholder="user@email.com"
            className="flex-1 bg-slate-800 border border-slate-700 text-white rounded-lg px-3 py-2 text-sm outline-none focus:border-yellow-500 transition-colors" />
          <button onClick={handleGrant} disabled={grantLoading || !grantEmail}
            className="bg-yellow-500 hover:bg-yellow-400 disabled:opacity-50 text-black font-bold px-4 py-2 rounded-lg text-sm transition-all flex items-center gap-1">
            {grantLoading ? <Loader2 size={13} className="animate-spin"/> : <CheckCircle2 size={13}/>} Apply
          </button>
        </div>
        {grantMsg && <p className="text-sm">{grantMsg}</p>}
      </section>

      {/* ── Email Blast ── */}
      <section className="bg-slate-900 border border-slate-700 rounded-xl p-5 space-y-3">
        <div className="flex items-center gap-2">
          <Mail size={15} className="text-blue-400" />
          <h2 className="text-white font-semibold text-sm">Email Blast</h2>
          <span className="text-[10px] text-slate-500 ml-auto">All active premium users</span>
        </div>
        <input value={blastSubject} onChange={e => setBlastSubject(e.target.value)} placeholder="Subject line"
          className="w-full bg-slate-800 border border-slate-700 text-white rounded-lg px-3 py-2 text-sm outline-none focus:border-blue-500 transition-colors" />
        <textarea value={blastMsg} onChange={e => setBlastMsg(e.target.value)} placeholder="Message body…" rows={3}
          className="w-full bg-slate-800 border border-slate-700 text-white rounded-lg px-3 py-2 text-sm outline-none focus:border-blue-500 transition-colors resize-none" />
        {blastResult && <p className="text-sm">{blastResult}</p>}
        <button onClick={handleBlast} disabled={blastLoading || !blastSubject || !blastMsg}
          className="w-full bg-blue-500 hover:bg-blue-400 disabled:opacity-50 text-white font-bold py-2.5 rounded-lg text-sm transition-all flex items-center justify-center gap-2">
          {blastLoading ? <Loader2 size={14} className="animate-spin"/> : <Mail size={14}/>}
          {blastLoading ? "Sending…" : "Send Email Blast"}
        </button>
        <p className="text-slate-600 text-[10px]">Requires RESEND_API_KEY in Vercel · Free tier: 100 emails/day</p>
      </section>

      {/* ── Expiring Subscriptions ── */}
      {users?.expiring?.length > 0 && (
        <section className="bg-slate-900 border border-yellow-500/20 rounded-xl p-5 space-y-3">
          <div className="flex items-center gap-2">
            <AlertTriangle size={15} className="text-yellow-400" />
            <h2 className="text-white font-semibold text-sm">Expiring in 7 Days ({users.expiring.length})</h2>
          </div>
          {users.expiring.map((u: any) => (
            <div key={u.id} className="flex items-center justify-between text-xs">
              <span className="text-slate-300 truncate flex-1">{u.email}</span>
              <span className="text-yellow-400 font-semibold ml-2">{u.days_left}d left</span>
            </div>
          ))}
        </section>
      )}

      {/* ── User Growth + Popular ── */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {users?.growth && (
          <section className="bg-slate-900 border border-slate-700 rounded-xl p-5 space-y-3">
            <div className="flex items-center gap-2">
              <Users size={15} className="text-purple-400" />
              <h2 className="text-white font-semibold text-sm">Sign-up Trend</h2>
            </div>
            {Object.entries(users.growth as Record<string,number>)
              .sort(([a],[b]) => b.localeCompare(a)).slice(0,7)
              .map(([date, count]) => (
                <div key={date} className="flex items-center gap-3 text-xs">
                  <span className="text-slate-500 w-24">{date}</span>
                  <div className="flex-1 h-1.5 bg-slate-800 rounded-full overflow-hidden">
                    <div className="h-full bg-purple-500 rounded-full" style={{ width: `${Math.min(100, (count as number)*10)}%` }}/>
                  </div>
                  <span className="text-slate-300 w-4 text-right">{count as number}</span>
                </div>
              ))}
          </section>
        )}

        {popular && (
          <section className="bg-slate-900 border border-slate-700 rounded-xl p-5 space-y-3">
            <div className="flex items-center gap-2">
              <MousePointerClick size={15} className="text-blue-400" />
              <h2 className="text-white font-semibold text-sm">Most Clicked</h2>
            </div>
            {popular.matches?.length > 0 && (
              <div className="space-y-1">
                <p className="text-slate-500 text-[10px] uppercase tracking-wide">Matches</p>
                {popular.matches.slice(0,5).map((m: any) => (
                  <div key={m.name} className="flex justify-between text-xs">
                    <span className="text-slate-300 truncate flex-1">{m.name}</span>
                    <span className="text-blue-400 font-semibold ml-2">{m.clicks}</span>
                  </div>
                ))}
              </div>
            )}
            {popular.leagues?.length > 0 && (
              <div className="space-y-1 pt-2 border-t border-slate-800">
                <p className="text-slate-500 text-[10px] uppercase tracking-wide">Leagues</p>
                {popular.leagues.slice(0,5).map((l: any) => (
                  <div key={l.code} className="flex justify-between text-xs">
                    <span className="text-slate-300">{l.code}</span>
                    <span className="text-blue-400 font-semibold">{l.clicks}</span>
                  </div>
                ))}
              </div>
            )}
          </section>
        )}
      </div>

      <p className="text-center text-slate-700 text-xs pb-4">
        BetIQ Control Centre · Changes apply instantly via Redis
      </p>
    </div>
  );
}
