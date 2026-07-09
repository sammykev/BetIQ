"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { PredictionCard } from "@/components/PredictionCard";
import { LeagueTabs } from "@/components/LeagueTabs";
import { triggerRefresh } from "@/lib/api";
import type { Prediction, League } from "@/lib/api";
import {
  RefreshCw, TrendingUp, Shield, AlertTriangle, CalendarDays, Percent, Bell,
  Brain, BarChart3, Bot, Ticket, Globe, ArrowRight, Zap, Target, Layers,
} from "lucide-react";
import { ChatBot } from "@/components/ChatBot";
import { SportCard, type SportPrediction } from "@/components/SportCard";
import { SportModal } from "@/components/SportModal";
import { PaywallModal } from "@/components/PaywallModal";
import { AnnouncementBanner } from "@/components/AnnouncementBanner";
import { AppShell } from "@/components/shell/AppShell";
import { useUser, SignInButton, SignUpButton } from "@clerk/nextjs";
import clsx from "clsx";

const CONFIDENCE_FILTERS = [
  { label: "All", value: 0 },
  { label: "≥ 60%", value: 0.6 },
  { label: "≥ 70%", value: 0.7 },
  { label: "≥ 80%", value: 0.8 },
];

const FEATURES = [
  { icon: Brain,        tint: "text-violet-600 bg-violet-50 dark:bg-violet-500/10",   title: "AI Predictions",  desc: "XGBoost + Elo ratings across 9 leagues, retrained on every refresh." },
  { icon: BarChart3,    tint: "text-sky-600 bg-sky-50 dark:bg-sky-500/10",            title: "Match Analysis",  desc: "xG, 11 betting markets, Elo gauge and correct-score odds per game." },
  { icon: Bot,          tint: "text-brand-600 bg-brand-50 dark:bg-brand-500/10",      title: "AI Assistant",    desc: "Chat to build accumulators and get instant picks." },
  { icon: Ticket,       tint: "text-amber-600 bg-amber-50 dark:bg-amber-500/10",      title: "Booking Codes",   desc: "One-click SportyBet booking code generation." },
  { icon: CalendarDays, tint: "text-rose-600 bg-rose-50 dark:bg-rose-500/10",         title: "History Tracker", desc: "Every past prediction graded — see real accuracy." },
  { icon: Globe,        tint: "text-teal-600 bg-teal-50 dark:bg-teal-500/10",         title: "Live Team News",  desc: "Real-time injury and lineup context via web search." },
];

function WakingUp({ onRetry }: { onRetry: () => void }) {
  const [dots, setDots] = useState(".");
  useEffect(() => {
    const id = setInterval(() => setDots(d => d.length >= 3 ? "." : d + "."), 600);
    // Auto-retry after 20 s
    const retry = setTimeout(onRetry, 20000);
    return () => { clearInterval(id); clearTimeout(retry); };
  }, [onRetry]);
  return (
    <div className="text-center py-20 space-y-4">
      <div className="text-5xl animate-bounce">⚽</div>
      <p className="text-zinc-700 dark:text-zinc-300 font-semibold text-lg">Waking up the server{dots}</p>
      <p className="text-zinc-400 dark:text-zinc-500 text-sm max-w-xs mx-auto">
        The backend spins down when idle. It&apos;ll be ready in about 30 seconds.
      </p>
      <button onClick={onRetry} className="btn-primary">Try now</button>
    </div>
  );
}

function AuthGate() {
  return (
    <div className="min-h-screen bg-[#fafafa] dark:bg-[#09090b] flex flex-col">
      {/* Nav */}
      <header className="px-5 sm:px-8 py-4 flex items-center justify-between max-w-6xl mx-auto w-full">
        <div className="flex items-center gap-2.5">
          <img src="/logo.svg" alt="BetIQ" className="w-9 h-9 rounded-xl" />
          <span className="text-lg font-bold tracking-tight text-zinc-900 dark:text-white">BetIQ</span>
        </div>
        <SignInButton mode="modal">
          <button className="btn-secondary !py-1.5">Sign in</button>
        </SignInButton>
      </header>

      {/* Hero */}
      <main className="flex-1 w-full max-w-6xl mx-auto px-5 sm:px-8">
        <section className="pt-16 pb-14 sm:pt-24 sm:pb-20 text-center">
          <div className="inline-flex items-center gap-1.5 bg-brand-50 dark:bg-brand-900/20 text-brand-700 dark:text-brand-400 border border-brand-100 dark:border-brand-900 text-xs font-semibold px-3 py-1 rounded-full mb-6">
            <Zap size={12} /> Powered by XGBoost + Elo ratings
          </div>
          <h1 className="text-4xl sm:text-6xl font-black tracking-tight text-zinc-900 dark:text-white leading-[1.05] max-w-3xl mx-auto">
            Football predictions,
            <br />
            <span className="text-gradient">engineered by AI.</span>
          </h1>
          <p className="text-zinc-500 dark:text-zinc-400 text-lg sm:text-xl leading-relaxed max-w-xl mx-auto mt-6">
            Machine-learned probabilities across 9 leagues and 11 markets.
            Chat to build your slip, get a booking code in one tap.
          </p>

          <div className="flex flex-col sm:flex-row gap-3 justify-center mt-9">
            <SignUpButton mode="modal">
              <button className="btn-primary !px-7 !py-3 !text-base">
                Create free account <ArrowRight size={16} />
              </button>
            </SignUpButton>
            <SignInButton mode="modal">
              <button className="btn-secondary !px-7 !py-3 !text-base">Sign in</button>
            </SignInButton>
          </div>
          <p className="text-zinc-400 dark:text-zinc-600 text-xs mt-4">
            Free to sign up · No credit card required
          </p>

          {/* Stat strip */}
          <div className="grid grid-cols-3 max-w-lg mx-auto mt-14 divide-x divide-zinc-200 dark:divide-zinc-800">
            {[
              { icon: Layers, value: "9",   label: "Leagues covered" },
              { icon: Target, value: "11+", label: "Betting markets" },
              { icon: Zap,    value: "6h",  label: "Refresh cycle" },
            ].map(({ icon: Icon, value, label }) => (
              <div key={label} className="px-4">
                <p className="tnum text-2xl sm:text-3xl font-black text-zinc-900 dark:text-white">{value}</p>
                <p className="text-[11px] sm:text-xs text-zinc-400 dark:text-zinc-500 font-medium mt-1">{label}</p>
              </div>
            ))}
          </div>
        </section>

        {/* Feature grid */}
        <section className="pb-20">
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {FEATURES.map(({ icon: Icon, tint, title, desc }) => (
              <div key={title} className="card p-5 text-left">
                <span className={clsx("inline-flex items-center justify-center w-10 h-10 rounded-xl mb-3", tint)}>
                  <Icon size={19} />
                </span>
                <p className="text-zinc-900 dark:text-white text-sm font-bold">{title}</p>
                <p className="text-zinc-500 dark:text-zinc-400 text-[13px] leading-relaxed mt-1">{desc}</p>
              </div>
            ))}
          </div>
        </section>
      </main>

      {/* Footer */}
      <footer className="border-t border-zinc-200 dark:border-zinc-800 py-5 text-center text-xs text-zinc-400 dark:text-zinc-600">
        BetIQ · AI Football Predictions · Gamble responsibly · For educational use only
      </footer>
    </div>
  );
}

export default function HomePage() {
  const router = useRouter();
  const [allPredictions, setAllPredictions] = useState<Prediction[]>([]);
  const [leagues, setLeagues] = useState<League[]>([]);
  const [selectedLeague, setSelectedLeague] = useState("ALL");
  const [minConf, setMinConf] = useState(0);
  const [sortBy, setSortBy] = useState<"date" | "confidence" | "value">("date");
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [bankersOnly, setBankersOnly] = useState(false);
  const [activeSport, setActiveSport] = useState<"football" | "basketball" | "tennis" | "table-tennis">("football");
  const [sportPreds, setSportPreds] = useState<SportPrediction[]>([]);
  const [sportLoading, setSportLoading] = useState(false);
  const [selectedSportMatch, setSelectedSportMatch] = useState<SportPrediction | null>(null);
  const [showPaywall, setShowPaywall] = useState(false);
  const [savedKeys] = useState<Set<string>>(new Set());
  const [pushEnabled, setPushEnabled] = useState(false);
  const [pushSupported, setPushSupported] = useState(false);

  const { user, isLoaded } = useUser();
  const [paywallActive, setPaywallActive] = useState(true);
  const [maintenanceMode, setMaintenanceMode] = useState(false);
  const [siteBanner, setSiteBanner] = useState("");

  const hasSubscription =
    (user?.publicMetadata as { subscription?: string; subscription_expires?: string })
      ?.subscription === "premium" &&
    new Date(
      (user?.publicMetadata as { subscription_expires?: string })?.subscription_expires ?? 0
    ) > new Date();

  // If admin has disabled the paywall, everyone gets full access
  const isPremium = !paywallActive || hasSubscription;

  const load = useCallback(async () => {
    const API = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";

    // Abort after 15 s so the page never hangs indefinitely
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 15000);

    try {
      setError(null);
      const [data, lgs, pw, maint, ban] = await Promise.all([
        fetch(`${API}/api/predictions?limit=500`, { signal: ctrl.signal }).then(r => r.json()),
        fetch(`${API}/api/leagues`,               { signal: ctrl.signal }).then(r => r.json()).catch(() => []),
        fetch(`${API}/api/config/paywall`).then(r => r.json()).catch(() => ({ enabled: true })),
        fetch(`${API}/api/config/maintenance`).then(r => r.json()).catch(() => ({ enabled: false })),
        fetch(`${API}/api/admin/banner`).then(r => r.json()).catch(() => ({ banner: null })),
      ]);
      clearTimeout(timer);
      setAllPredictions(data.predictions ?? []);
      setLastUpdated(data.last_updated ?? null);
      setLeagues(lgs);
      setPaywallActive(pw.enabled);
      setMaintenanceMode(maint.enabled);
      setSiteBanner(ban.banner || "");
    } catch (e: any) {
      clearTimeout(timer);
      if (e?.name === "AbortError") {
        setError("__waking__");   // special code — show friendly waking-up message
      } else {
        setError("Could not reach the prediction server.");
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    load();
  }, [load]);

  // Load sport predictions when sport tab changes (non-football)
  useEffect(() => {
    if (activeSport === "football") return;
    setSportLoading(true);
    setSportPreds([]);
    const API = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";
    fetch(`${API}/api/sports/${activeSport}`)
      .then(r => r.ok ? r.json() : [])
      .then(d => setSportPreds(Array.isArray(d) ? d : []))
      .catch(() => setSportPreds([]))
      .finally(() => setSportLoading(false));
  }, [activeSport]);

  // Keep Render backend alive — ping every 10 minutes
  useEffect(() => {
    const API = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";
    const ping = () => fetch(`${API}/api/health`).catch(() => {});
    ping(); // immediate ping on load
    const id = setInterval(ping, 10 * 60 * 1000);
    return () => clearInterval(id);
  }, []);

  // Check push notification support
  useEffect(() => {
    if ("serviceWorker" in navigator && "PushManager" in window) {
      setPushSupported(true);
      navigator.serviceWorker.ready.then(reg => {
        reg.pushManager.getSubscription().then(sub => {
          setPushEnabled(!!sub);
        });
      });
    }
  }, []);

  async function handleRefresh() {
    setRefreshing(true);
    try {
      await triggerRefresh();
      await new Promise((r) => setTimeout(r, 3000));
      await load();
    } finally {
      setRefreshing(false);
    }
  }

  async function handlePushToggle() {
    if (!pushSupported) return;
    const API = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";
    try {
      const reg = await navigator.serviceWorker.ready;
      if (pushEnabled) {
        const sub = await reg.pushManager.getSubscription();
        if (sub) {
          await fetch(`${API}/api/push/subscribe`, {
            method: "DELETE",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ subscription: sub.toJSON() }),
          });
          await sub.unsubscribe();
        }
        setPushEnabled(false);
      } else {
        const keyRes = await fetch(`${API}/api/push/public-key`);
        const { public_key } = await keyRes.json();
        if (!public_key) { alert("Push notifications not configured on server."); return; }
        const sub = await reg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: public_key,
        });
        await fetch(`${API}/api/push/subscribe`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ subscription: sub.toJSON() }),
        });
        setPushEnabled(true);
      }
    } catch (e) {
      console.error("Push toggle error:", e);
    }
  }

  const _SKIP = new Set(["tbd", "tba", "to be announced", "", "none"]);
  const validPredictions = allPredictions.filter(
    (p) => p.home?.trim() && p.away?.trim() &&
           !_SKIP.has(p.home.trim().toLowerCase()) &&
           !_SKIP.has(p.away.trim().toLowerCase())
  );

  // Client-side filtering
  const predictions = validPredictions
    .filter((p) => selectedLeague === "ALL" || p.league === selectedLeague)
    .filter((p) => minConf === 0 || p.goals_confidence >= minConf)
    .filter((p) => !bankersOnly || p.goals_type === "Banker");

  // Counts per league for tab badges (valid predictions only)
  const counts: Record<string, number> = {};
  for (const p of validPredictions) {
    counts[p.league] = (counts[p.league] || 0) + 1;
  }

  const bankers = predictions.filter((p) => p.goals_type === "Banker");
  const highConf = predictions.filter((p) => p.goals_confidence >= 0.75);

  const sorted = [...predictions].sort((a, b) => {
    if (sortBy === "value") {
      const va = a.value_edge ?? -1;
      const vb = b.value_edge ?? -1;
      return vb - va;
    }
    if (sortBy === "confidence") return b.goals_confidence - a.goals_confidence;
    return a.date.localeCompare(b.date) || a.time.localeCompare(b.time);
  });

  const fmt = (iso: string | null) => {
    if (!iso) return "Never";
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  };

  const openMatch = (p: Prediction) => {
    const API_B = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";
    fetch(`${API_B}/api/track/match`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ home: p.home, away: p.away }),
    }).catch(() => {});
    if (!isPremium) { setShowPaywall(true); return; }
    const q = new URLSearchParams({ home: p.home, away: p.away, date: p.date });
    router.push(`/match?${q}`);
  };

  // Auth gate — all hooks above must run first (React rules)
  if (!isLoaded) {
    return (
      <div className="min-h-screen bg-[#fafafa] dark:bg-[#09090b] flex items-center justify-center">
        <div className="w-8 h-8 border-2 border-brand-600 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (!user) return <AuthGate />;

  if (maintenanceMode) {
    return (
      <div className="min-h-screen bg-[#fafafa] dark:bg-[#09090b] flex flex-col items-center justify-center gap-4 text-center p-6">
        <img src="/logo.svg" alt="BetIQ" className="w-16 h-16 rounded-2xl opacity-60" />
        <h1 className="text-2xl font-black text-zinc-900 dark:text-white">Back soon</h1>
        <p className="text-zinc-500 dark:text-zinc-400 max-w-sm">
          BetIQ is undergoing scheduled maintenance. We&apos;ll be back shortly with fresh predictions.
        </p>
        <p className="text-zinc-400 dark:text-zinc-600 text-sm">⚽ Thanks for your patience</p>
      </div>
    );
  }

  const headerActions = (
    <>
      {pushSupported && (
        <button
          onClick={handlePushToggle}
          title={pushEnabled ? "Disable value bet notifications" : "Get notified for value bets"}
          className={clsx(
            "hidden sm:inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-all",
            pushEnabled
              ? "bg-brand-50 dark:bg-brand-900/20 border-brand-200 dark:border-brand-800 text-brand-700 dark:text-brand-400"
              : "bg-white dark:bg-zinc-900 border-zinc-200 dark:border-zinc-800 text-zinc-500 dark:text-zinc-400 hover:text-zinc-800 dark:hover:text-zinc-200"
          )}
        >
          <Bell size={12} />
          {pushEnabled ? "Alerts on" : "Alerts"}
        </button>
      )}
      <button
        onClick={handleRefresh}
        disabled={refreshing}
        className="btn-secondary !px-3 !py-1.5 !text-xs"
      >
        <RefreshCw size={12} className={clsx(refreshing && "animate-spin")} />
        <span className="hidden sm:inline">Refresh</span>
      </button>
    </>
  );

  return (
    <AppShell
      banner={<AnnouncementBanner text={siteBanner} />}
      actions={headerActions}
      onUpgrade={() => setShowPaywall(true)}
    >
      <div className="space-y-6">
        {/* Page heading */}
        <div className="flex items-end justify-between gap-4 flex-wrap">
          <div>
            <h1 className="text-2xl sm:text-3xl font-black tracking-tight text-zinc-900 dark:text-white">
              Predictions
            </h1>
            <p className="text-sm text-zinc-400 dark:text-zinc-500 mt-1">
              {lastUpdated ? `Model last updated at ${fmt(lastUpdated)}` : "AI picks across 9 leagues"}
            </p>
          </div>

          {/* Sport selector */}
          <div className="flex gap-2 flex-wrap">
            {([
              { key: "football",     label: "⚽ Football"     },
              { key: "basketball",   label: "🏀 Basketball"   },
              { key: "tennis",       label: "🎾 Tennis"       },
              { key: "table-tennis", label: "🏓 Table Tennis" },
            ] as const).map(({ key, label }) => (
              <button
                key={key}
                onClick={() => setActiveSport(key)}
                className={clsx("chip", activeSport === key ? "chip-active" : "chip-idle")}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        {/* Stats row */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {(activeSport === "football" ? [
            { icon: TrendingUp, label: "Total picks",     value: predictions.length, tint: "text-sky-600 bg-sky-50 dark:bg-sky-500/10", onClick: undefined as (() => void) | undefined, active: false },
            { icon: Shield,     label: "Bankers",         value: bankers.length,     tint: "text-brand-600 bg-brand-50 dark:bg-brand-500/10", onClick: () => setBankersOnly(b => !b), active: bankersOnly },
            { icon: Percent,    label: "High confidence", value: highConf.length,    tint: "text-amber-600 bg-amber-50 dark:bg-amber-500/10", onClick: undefined, active: false },
            { icon: TrendingUp, label: "Value bets",      value: validPredictions.filter(p => p.is_value_bet).length, tint: "text-violet-600 bg-violet-50 dark:bg-violet-500/10", onClick: () => setSortBy("value"), active: sortBy === "value" },
          ] : [
            { icon: TrendingUp, label: "Total picks",       value: sportPreds.length, tint: "text-sky-600 bg-sky-50 dark:bg-sky-500/10", onClick: undefined, active: false },
            { icon: Shield,     label: "High conf (≥65%)",  value: sportPreds.filter(p => p.goals_confidence >= 0.65).length, tint: "text-brand-600 bg-brand-50 dark:bg-brand-500/10", onClick: undefined, active: false },
            { icon: Percent,    label: "Very high (≥75%)",  value: sportPreds.filter(p => p.goals_confidence >= 0.75).length, tint: "text-amber-600 bg-amber-50 dark:bg-amber-500/10", onClick: undefined, active: false },
            { icon: Globe,      label: "Competitions",      value: new Set(sportPreds.map(p => p.league_name)).size, tint: "text-violet-600 bg-violet-50 dark:bg-violet-500/10", onClick: undefined, active: false },
          ]).map(({ icon: Icon, label, value, tint, onClick, active }) => (
            <button
              key={label}
              onClick={onClick}
              disabled={!onClick}
              className={clsx(
                "card px-4 py-3.5 flex items-center gap-3 text-left transition-all",
                onClick && "hover:shadow-card-hover hover:-translate-y-0.5 cursor-pointer",
                active && "ring-2 ring-brand-500/50 border-brand-300 dark:border-brand-700"
              )}
            >
              <span className={clsx("inline-flex items-center justify-center w-9 h-9 rounded-xl shrink-0", tint)}>
                <Icon size={17} />
              </span>
              <span className="min-w-0">
                <span className="block text-[11px] text-zinc-400 dark:text-zinc-500 font-medium truncate">
                  {label}{active ? " · on" : ""}
                </span>
                <span className="tnum block text-xl font-black text-zinc-900 dark:text-white leading-tight">
                  {value}
                </span>
              </span>
            </button>
          ))}
        </div>

        {/* Sport predictions (non-football) */}
        {activeSport !== "football" && (
          <div className="space-y-4">
            {sportLoading ? (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                {Array.from({ length: 6 }).map((_, i) => (
                  <div key={i} className="card h-48 animate-pulse !bg-zinc-100 dark:!bg-zinc-900" />
                ))}
              </div>
            ) : sportPreds.length === 0 ? (
              <div className="text-center py-16 text-zinc-400 dark:text-zinc-500 space-y-2">
                <p className="text-3xl">{activeSport === "basketball" ? "🏀" : activeSport === "tennis" ? "🎾" : "🏓"}</p>
                <p className="text-sm font-medium">No {activeSport} predictions right now.</p>
              </div>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                {sportPreds.map((p, i) => (
                  <SportCard key={i} prediction={p} onClick={() => setSelectedSportMatch(p)} />
                ))}
              </div>
            )}
          </div>
        )}

        {activeSport === "football" && <>
          {/* Filters */}
          <div className="space-y-3">
            <LeagueTabs
              leagues={leagues}
              selected={selectedLeague}
              onSelect={setSelectedLeague}
              counts={counts}
            />

            <div className="flex items-center gap-3 flex-wrap">
              <div className="flex gap-1.5">
                {CONFIDENCE_FILTERS.map(({ label, value }) => (
                  <button
                    key={label}
                    onClick={() => setMinConf(value)}
                    className={clsx("chip !text-xs !px-3 !py-1", minConf === value ? "chip-active" : "chip-idle")}
                  >
                    {label}
                  </button>
                ))}
              </div>

              <div className="flex items-center gap-0.5 bg-zinc-100 dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 rounded-xl p-0.5 ml-auto">
                {([
                  { key: "date",       label: "Soonest",    icon: CalendarDays },
                  { key: "confidence", label: "Confidence", icon: Percent },
                  { key: "value",      label: "Value",      icon: TrendingUp },
                ] as const).map(({ key, label, icon: Icon }) => (
                  <button
                    key={key}
                    onClick={() => setSortBy(key)}
                    className={clsx(
                      "flex items-center gap-1.5 px-3 py-1.5 rounded-[10px] text-xs font-semibold transition-all",
                      sortBy === key
                        ? "bg-white dark:bg-zinc-800 text-zinc-900 dark:text-white shadow-sm"
                        : "text-zinc-400 dark:text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300"
                    )}
                  >
                    <Icon size={11} />
                    {label}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* Picks grid */}
          {loading ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              {Array.from({ length: 9 }).map((_, i) => (
                <div key={i} className="card p-4 space-y-4 animate-pulse">
                  <div className="h-3 bg-zinc-100 dark:bg-zinc-800 rounded-full w-1/2" />
                  <div className="space-y-2.5">
                    <div className="flex items-center gap-2">
                      <div className="w-7 h-7 bg-zinc-100 dark:bg-zinc-800 rounded-full" />
                      <div className="h-4 bg-zinc-100 dark:bg-zinc-800 rounded-full flex-1" />
                    </div>
                    <div className="flex items-center gap-2">
                      <div className="w-7 h-7 bg-zinc-100 dark:bg-zinc-800 rounded-full" />
                      <div className="h-4 bg-zinc-100 dark:bg-zinc-800 rounded-full flex-1" />
                    </div>
                  </div>
                  <div className="h-1.5 bg-zinc-100 dark:bg-zinc-800 rounded-full" />
                  <div className="h-14 bg-zinc-50 dark:bg-zinc-800/60 rounded-xl" />
                </div>
              ))}
            </div>
          ) : error === "__waking__" ? (
            <WakingUp onRetry={() => { setLoading(true); load(); }} />
          ) : error ? (
            <div className="text-center py-20 space-y-3">
              <AlertTriangle size={40} className="text-rose-400 mx-auto" />
              <p className="text-zinc-700 dark:text-zinc-300 font-medium">Could not reach the prediction server.</p>
              <button onClick={() => { setLoading(true); load(); }} className="btn-secondary">
                Retry
              </button>
            </div>
          ) : predictions.length === 0 ? (
            <div className="text-center py-20 space-y-3">
              <span className="text-5xl">📭</span>
              <p className="text-zinc-500 dark:text-zinc-400 font-medium">No predictions match the selected filters.</p>
              <p className="text-zinc-400 dark:text-zinc-600 text-sm">Try widening your confidence filter or selecting All Leagues.</p>
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              {sorted.map((p, i) => (
                <PredictionCard
                  key={`${p.home}-${p.away}-${p.date}-${i}`}
                  prediction={p}
                  savedKeys={savedKeys}
                  onClick={() => openMatch(p)}
                />
              ))}
            </div>
          )}
        </>}

        {/* Footer note */}
        <p className="text-center text-xs text-zinc-400 dark:text-zinc-600 pt-6">
          Powered by XGBoost + Elo ratings · football-data.org · Updated every 6 hours · Gamble responsibly
        </p>
      </div>

      {/* Sport analysis modal */}
      {selectedSportMatch && (
        <SportModal prediction={selectedSportMatch} onClose={() => setSelectedSportMatch(null)} />
      )}

      {/* AI Betting Assistant — premium only */}
      {isPremium && <ChatBot predictions={allPredictions} />}

      {/* Paywall */}
      {showPaywall && (
        <PaywallModal
          onClose={() => setShowPaywall(false)}
          onSuccess={() => { setShowPaywall(false); window.location.reload(); }}
        />
      )}
    </AppShell>
  );
}
