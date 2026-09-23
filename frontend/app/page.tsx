"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { PredictionCard } from "@/components/PredictionCard";
import { LeagueTabs } from "@/components/LeagueTabs";
import { triggerRefresh } from "@/lib/api";
import type { Prediction, League } from "@/lib/api";
import { dayLabel } from "@/lib/matchTime";
import { predKey } from "@/components/SaveButton";
import { useAuthedFetch } from "@/lib/useAuthedFetch";
import {
  RefreshCw, TrendingUp, AlertTriangle, CalendarDays, Percent, Bell,
  Brain, BarChart3, Bot, Ticket, Globe, ArrowRight, SearchX, ChevronDown,
} from "lucide-react";
import { ChatBot } from "@/components/ChatBot";
import { SportCard, type SportPrediction } from "@/components/SportCard";
import { SportModal } from "@/components/SportModal";
import { PaywallModal } from "@/components/PaywallModal";
import { AnnouncementBanner } from "@/components/AnnouncementBanner";
import { AppShell, Wordmark } from "@/components/shell/AppShell";
import { ThemeToggle } from "@/components/ThemeToggle";
import { useUser, SignInButton, SignUpButton } from "@clerk/nextjs";
import clsx from "clsx";

const SPORTS = [
  { key: "football", label: "Football" },
  { key: "basketball", label: "Basketball" },
  { key: "tennis", label: "Tennis" },
  { key: "table-tennis", label: "Table Tennis" },
] as const;
type Sport = (typeof SPORTS)[number]["key"];

type Quick = "all" | "bankers" | "value" | "high";

const CONFIDENCE_FILTERS = [
  { label: "Any confidence", value: 0 },
  { label: "60%+ confidence", value: 0.6 },
  { label: "70%+ confidence", value: 0.7 },
  { label: "80%+ confidence", value: 0.8 },
];

const FEATURES = [
  { icon: Brain,        title: "AI predictions",  desc: "XGBoost + Elo ratings across 9 leagues, retrained on every refresh." },
  { icon: BarChart3,    title: "Match analysis",  desc: "xG, 11 betting markets, an Elo gauge and correct-score odds for every game." },
  { icon: Bot,          title: "AI assistant",    desc: "Chat to build accumulators and get instant picks." },
  { icon: Ticket,       title: "Booking codes",   desc: "One-tap SportyBet booking code generation." },
  { icon: CalendarDays, title: "History tracker", desc: "Every past prediction graded against the real result." },
  { icon: Globe,        title: "Live team news",  desc: "Injury and lineup context pulled from the web before kick-off." },
];

// Static example for the signed-out landing page — rendered with the real card.
const SAMPLE: Prediction = {
  home: "Arsenal", away: "Chelsea", date: new Date().toISOString().slice(0, 10), time: "16:30",
  league: "PL", league_name: "Premier League", flag: "🏴",
  p_home: 0.54, p_draw: 0.24, p_away: 0.22, p_over15: 0.78, p_over25: 0.56,
  tip_1x2: "Arsenal Win", tip_code: "1", tip_goals: "Over 2.5", goals_type: "Banker", goals_confidence: 0.81,
  odds_home: 1.85, odds_draw: 3.6, odds_away: 4.2, value_edge: 0.08, is_value_bet: true,
};

function ago(iso: string): string {
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (isNaN(mins)) return "recently";
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

/** Pulsing accent dot — "the model is live". */
function LiveDot() {
  return (
    <span className="relative flex h-2 w-2 shrink-0">
      <span className="absolute inline-flex h-full w-full rounded-full bg-accent opacity-60 animate-ping" />
      <span className="relative inline-flex h-2 w-2 rounded-full bg-accent" />
    </span>
  );
}

function StatePanel({ icon, title, body, action }: {
  icon: React.ReactNode; title: string; body?: string; action?: React.ReactNode;
}) {
  return (
    <div className="card border-dashed text-center px-6 py-16 space-y-3">
      <div className="mx-auto w-12 h-12 rounded-2xl bg-n-800/70 flex items-center justify-center text-n-400">{icon}</div>
      <p className="font-display font-bold text-xl uppercase tracking-wide text-n-0">{title}</p>
      {body && <p className="text-sm text-n-400 max-w-sm mx-auto">{body}</p>}
      {action && <div className="pt-2">{action}</div>}
    </div>
  );
}

function WakingUp({ onRetry }: { onRetry: () => void }) {
  useEffect(() => {
    // Auto-retry after 20 s
    const retry = setTimeout(onRetry, 20000);
    return () => clearTimeout(retry);
  }, [onRetry]);
  return (
    <StatePanel
      icon={<RefreshCw size={20} className="animate-spin" />}
      title="Warming up the model"
      body="The prediction server sleeps when idle and takes about 30 seconds to wake. This page retries on its own."
      action={<button onClick={onRetry} className="btn-primary">Try now</button>}
    />
  );
}

function SkeletonCard() {
  return (
    <div className="card overflow-hidden">
      <div className="flex justify-between px-4 py-3 border-b border-n-800/80">
        <div className="skeleton h-3 w-28" />
        <div className="skeleton h-3 w-20" />
      </div>
      <div className="p-4 space-y-3">
        {[0, 1].map(i => (
          <div key={i} className="flex items-center gap-3">
            <div className="skeleton w-[30px] h-[30px] shrink-0" />
            <div className="skeleton h-4 flex-1" />
            <div className="skeleton h-7 w-12 !rounded-lg" />
          </div>
        ))}
        <div className="skeleton h-1 w-full" />
        <div className="skeleton h-[68px] w-full !rounded-xl" />
      </div>
    </div>
  );
}

function AuthGate() {
  return (
    <div className="min-h-screen flex flex-col overflow-x-clip">
      {/* Nav */}
      <header className="px-5 sm:px-8 h-16 flex items-center justify-between max-w-6xl mx-auto w-full">
        <div className="flex items-center gap-2.5">
          <img src="/logo.svg" alt="" className="w-8 h-8 rounded-lg ring-1 ring-n-800" />
          <Wordmark className="text-[26px]" />
        </div>
        <div className="flex items-center gap-2">
          <ThemeToggle />
          <SignInButton mode="modal">
            <button className="btn-secondary !py-1.5">Sign in</button>
          </SignInButton>
        </div>
      </header>

      <main className="flex-1 w-full max-w-6xl mx-auto px-5 sm:px-8">
        {/* Hero */}
        <section className="grid lg:grid-cols-[1.3fr_1fr] gap-12 lg:gap-14 items-center pt-10 pb-16 sm:pt-16 lg:pt-20 lg:pb-24">
          <div>
            <p className="inline-flex items-center gap-2 text-xs font-semibold text-n-300 bg-surface border border-n-800 rounded-full pl-2.5 pr-3 py-1.5">
              <LiveDot /> XGBoost + Elo model · 9 leagues · updated every 6h
            </p>
            <h1 className="display text-[52px] sm:text-7xl lg:text-[76px] text-n-0 mt-6">
              Football predictions,
              <br />
              <span className="text-accent sm:whitespace-nowrap">engineered by AI.</span>
            </h1>
            <p className="text-n-400 text-lg leading-relaxed max-w-lg mt-6">
              Machine-learned probabilities across 9 leagues and 11 markets.
              Chat to build your slip and get a SportyBet booking code in one tap.
            </p>
            <div className="flex flex-col sm:flex-row gap-3 mt-8">
              <SignUpButton mode="modal">
                <button className="btn-primary !px-7 !py-3.5 !text-base">
                  Create free account <ArrowRight size={16} />
                </button>
              </SignUpButton>
              <SignInButton mode="modal">
                <button className="btn-secondary !px-7 !py-3.5 !text-base">Sign in</button>
              </SignInButton>
            </div>
            <p className="text-n-500 text-xs mt-4">Free to sign up · No card required · 18+ only</p>
          </div>

          {/* Product preview: the real prediction card */}
          <div className="relative max-w-md w-full mx-auto lg:mx-0">
            <div className="absolute -inset-8 bg-brand-400/10 blur-3xl rounded-full" aria-hidden="true" />
            <div className="relative pointer-events-none select-none">
              <PredictionCard prediction={SAMPLE} />
            </div>
            <p className="relative text-center text-[11px] text-n-500 mt-3">Example prediction card</p>
          </div>
        </section>

        {/* Stat strip */}
        <section className="grid grid-cols-3 border-y border-n-900 divide-x divide-n-900">
          {[
            { value: "9", label: "Leagues covered" },
            { value: "11+", label: "Betting markets" },
            { value: "6h", label: "Refresh cycle" },
          ].map(({ value, label }) => (
            <div key={label} className="py-6 sm:py-8 text-center">
              <p className="font-display font-extrabold text-4xl sm:text-5xl text-n-0 tnum">{value}</p>
              <p className="eyebrow mt-1.5">{label}</p>
            </div>
          ))}
        </section>

        {/* Feature grid */}
        <section className="py-16 sm:py-20">
          <h2 className="display text-4xl sm:text-5xl text-n-0">Everything on one slip</h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 mt-8">
            {FEATURES.map(({ icon: Icon, title, desc }) => (
              <div key={title} className="card p-5">
                <span className="inline-flex items-center justify-center w-10 h-10 rounded-xl bg-brand-400/10 text-accent ring-1 ring-brand-400/20">
                  <Icon size={19} />
                </span>
                <p className="font-display font-bold text-xl uppercase tracking-wide text-n-0 mt-4">{title}</p>
                <p className="text-n-400 text-sm leading-relaxed mt-1">{desc}</p>
              </div>
            ))}
          </div>
        </section>
      </main>

      <footer className="border-t border-n-900 py-6 text-center text-xs text-n-500">
        BetIQ · AI football predictions · 18+ · Gamble responsibly · For educational use only
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
  const [quick, setQuick] = useState<Quick>("all");
  const [activeSport, setActiveSport] = useState<Sport>("football");
  const [sportPreds, setSportPreds] = useState<SportPrediction[]>([]);
  const [sportLoading, setSportLoading] = useState(false);
  const [selectedSportMatch, setSelectedSportMatch] = useState<SportPrediction | null>(null);
  const [showPaywall, setShowPaywall] = useState(false);
  const [savedKeys, setSavedKeys] = useState<Set<string>>(new Set());
  const [pushEnabled, setPushEnabled] = useState(false);
  const [pushSupported, setPushSupported] = useState(false);

  const { user, isLoaded } = useUser();
  const authFetch = useAuthedFetch();
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
      setAllPredictions(Array.isArray(data?.predictions) ? data.predictions : []);
      setLastUpdated(data?.last_updated ?? null);
      setLeagues(Array.isArray(lgs) ? lgs : []);
      setPaywallActive(pw?.enabled !== false);
      setMaintenanceMode(maint?.enabled === true);
      setSiteBanner(ban?.banner || "");
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

  // The user's saved picks, so each card's star shows the right state
  const userId = user?.id;
  useEffect(() => {
    if (!userId) return;
    const API = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";
    authFetch(`${API}/api/user/saves?uid=${encodeURIComponent(userId)}`)
      .then(r => (r.ok ? r.json() : []))
      .then((d: unknown) => {
        if (Array.isArray(d)) setSavedKeys(new Set(d.map(predKey)));
      })
      .catch(() => {});
  }, [userId, authFetch]);

  const onSaveToggle = useCallback((key: string, saved: boolean) => {
    setSavedKeys(prev => {
      const next = new Set(prev);
      if (saved) next.add(key); else next.delete(key);
      return next;
    });
  }, []);

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

  // Counts per league for tab badges (valid predictions only)
  const counts: Record<string, number> = {};
  for (const p of validPredictions) {
    counts[p.league] = (counts[p.league] || 0) + 1;
  }

  // League + confidence narrow the pool; the quick chips then slice it
  const pool = validPredictions
    .filter((p) => selectedLeague === "ALL" || p.league === selectedLeague)
    .filter((p) => minConf === 0 || p.goals_confidence >= minConf);

  const QUICK: { key: Quick; label: string; test: (p: Prediction) => boolean }[] = [
    { key: "all",     label: "All picks",       test: () => true },
    { key: "bankers", label: "Bankers",         test: (p) => p.goals_type === "Banker" },
    { key: "value",   label: "Value bets",      test: (p) => !!p.is_value_bet },
    { key: "high",    label: "75%+ confidence", test: (p) => p.goals_confidence >= 0.75 },
  ];
  const predictions = pool.filter(QUICK.find(q => q.key === quick)!.test);

  const sorted = [...predictions].sort((a, b) => {
    if (sortBy === "value") {
      const va = a.value_edge ?? -1;
      const vb = b.value_edge ?? -1;
      return vb - va;
    }
    if (sortBy === "confidence") return b.goals_confidence - a.goals_confidence;
    return a.date.localeCompare(b.date) || a.time.localeCompare(b.time);
  });

  // Sorted by kick-off → group under "Today", "Tomorrow", "Sat 26 Sep"…
  const groups: { label: string; items: Prediction[] }[] = [];
  if (sortBy === "date") {
    for (const p of sorted) {
      const label = dayLabel(p.date);
      const last = groups[groups.length - 1];
      if (last && last.label === label) last.items.push(p);
      else groups.push({ label, items: [p] });
    }
  } else {
    groups.push({ label: "", items: sorted });
  }

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

  const retry = () => { setLoading(true); load(); };

  // Auth gate — all hooks above must run first (React rules)
  if (!isLoaded) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="w-8 h-8 border-2 border-brand-400 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (!user) return <AuthGate />;

  if (maintenanceMode) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center gap-4 text-center p-6">
        <Wordmark className="text-4xl" />
        <h1 className="display text-5xl text-n-0 mt-4">Back soon</h1>
        <p className="text-n-400 max-w-sm">
          BetIQ is undergoing scheduled maintenance. We&apos;ll be back shortly with fresh predictions.
        </p>
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
            "hidden sm:inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold border transition-all",
            pushEnabled
              ? "bg-brand-400/10 border-brand-400/40 text-accent"
              : "bg-surface border-n-800 text-n-400 hover:text-n-0 hover:border-n-700"
          )}
        >
          <Bell size={12} />
          {pushEnabled ? "Alerts on" : "Alerts"}
        </button>
      )}
      <button
        onClick={handleRefresh}
        disabled={refreshing}
        className="btn-secondary !px-3 !py-1.5 !text-xs !rounded-lg"
      >
        <RefreshCw size={12} className={clsx(refreshing && "animate-spin")} />
        <span className="hidden sm:inline">Refresh</span>
      </button>
    </>
  );

  const sportSummary = activeSport !== "football" && !sportLoading && sportPreds.length > 0
    ? `${sportPreds.length} picks · ${sportPreds.filter(p => p.goals_confidence >= 0.65).length} high confidence · ${new Set(sportPreds.map(p => p.league_name)).size} competitions`
    : null;

  return (
    <AppShell
      banner={<AnnouncementBanner text={siteBanner} />}
      actions={headerActions}
      onUpgrade={() => setShowPaywall(true)}
    >
      <div className="space-y-5">
        {/* Page heading */}
        <div>
          <p className="flex items-center gap-2 text-xs font-medium text-n-400">
            <LiveDot />
            {lastUpdated ? <>Model live · updated {ago(lastUpdated)}</> : "AI picks across 9 leagues"}
          </p>
          <h1 className="display text-5xl sm:text-6xl text-n-0 mt-2">Predictions</h1>
        </div>

        {/* Sport tabs */}
        <div role="tablist" aria-label="Sport" className="flex gap-6 border-b border-n-800 overflow-x-auto -mx-4 px-4 sm:mx-0 sm:px-0">
          {SPORTS.map(({ key, label }) => {
            const active = activeSport === key;
            return (
              <button
                key={key}
                role="tab"
                aria-selected={active}
                onClick={() => setActiveSport(key)}
                className={clsx(
                  "relative shrink-0 pb-3 font-display font-bold text-[17px] uppercase tracking-[0.06em] transition-colors",
                  active ? "text-n-0" : "text-n-500 hover:text-n-300"
                )}
              >
                {label}
                {active && <span className="absolute left-0 right-0 -bottom-px h-[2px] bg-accent rounded-full" />}
              </button>
            );
          })}
        </div>

        {/* Other sports */}
        {activeSport !== "football" && (
          <div className="space-y-4">
            {sportSummary && <p className="text-sm text-n-400">{sportSummary}</p>}
            {sportLoading ? (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                {Array.from({ length: 6 }).map((_, i) => <SkeletonCard key={i} />)}
              </div>
            ) : sportPreds.length === 0 ? (
              <StatePanel
                icon={<SearchX size={20} />}
                title={`No ${SPORTS.find(s => s.key === activeSport)?.label.toLowerCase()} picks yet`}
                body="The model publishes picks once odds and fixtures are confirmed. Check back closer to game day."
              />
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
            {/* Quick views */}
            <div className="flex gap-2 overflow-x-auto -mx-4 px-4 sm:mx-0 sm:px-0 pb-0.5">
              {QUICK.map(({ key, label, test }) => {
                const n = pool.filter(test).length;
                const active = quick === key;
                return (
                  <button
                    key={key}
                    onClick={() => setQuick(key)}
                    aria-pressed={active}
                    className={clsx("chip shrink-0", active ? "chip-active" : "chip-idle")}
                  >
                    {label}
                    <span className={clsx("tnum text-[11px] font-bold", active ? "text-ink/60" : "text-n-500")}>{n}</span>
                  </button>
                );
              })}
            </div>

            <LeagueTabs
              leagues={leagues}
              selected={selectedLeague}
              onSelect={setSelectedLeague}
              counts={counts}
            />

            <div className="flex items-center gap-2 justify-between flex-wrap">
              {/* Minimum confidence */}
              <label className="relative inline-flex items-center">
                <span className="sr-only">Minimum confidence</span>
                <select
                  value={minConf}
                  onChange={e => setMinConf(Number(e.target.value))}
                  className="appearance-none bg-surface border border-n-800 hover:border-n-700 text-n-300 text-xs font-semibold rounded-lg pl-3 pr-7 py-2 outline-none cursor-pointer transition-colors"
                >
                  {CONFIDENCE_FILTERS.map(({ label, value }) => (
                    <option key={value} value={value}>{label}</option>
                  ))}
                </select>
                <ChevronDown size={13} className="absolute right-2.5 text-n-500 pointer-events-none" />
              </label>

              {/* Sort */}
              <div className="flex items-center gap-0.5 bg-surface border border-n-800 rounded-lg p-0.5" role="group" aria-label="Sort by">
                {([
                  { key: "date",       label: "Kick-off",   icon: CalendarDays },
                  { key: "confidence", label: "Confidence", icon: Percent },
                  { key: "value",      label: "Value",      icon: TrendingUp },
                ] as const).map(({ key, label, icon: Icon }) => (
                  <button
                    key={key}
                    onClick={() => setSortBy(key)}
                    aria-pressed={sortBy === key}
                    className={clsx(
                      "flex items-center gap-1.5 px-2 sm:px-2.5 py-1.5 rounded-md text-xs font-semibold transition-all",
                      sortBy === key
                        ? "bg-n-800 text-n-0"
                        : "text-n-500 hover:text-n-200"
                    )}
                  >
                    <Icon size={12} className={clsx("hidden sm:block", sortBy === key && "text-accent")} />
                    {label}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* Picks */}
          {loading ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              {Array.from({ length: 9 }).map((_, i) => <SkeletonCard key={i} />)}
            </div>
          ) : error === "__waking__" ? (
            <WakingUp onRetry={retry} />
          ) : error ? (
            <StatePanel
              icon={<AlertTriangle size={20} className="text-danger" />}
              title="Can't reach the model"
              body="The prediction server didn't respond. Check your connection and try again."
              action={<button onClick={retry} className="btn-secondary">Retry</button>}
            />
          ) : predictions.length === 0 ? (
            <StatePanel
              icon={<SearchX size={20} />}
              title="No picks match"
              body="Try a lower confidence filter, another view, or All Leagues."
              action={
                <button
                  onClick={() => { setQuick("all"); setMinConf(0); setSelectedLeague("ALL"); }}
                  className="btn-secondary"
                >
                  Reset filters
                </button>
              }
            />
          ) : (
            <div className="space-y-8">
              {groups.map(({ label, items }) => (
                <section key={label || "all"} className="space-y-3">
                  {label && (
                    <div className="flex items-baseline gap-3">
                      <h2 className="font-display font-extrabold text-2xl uppercase tracking-wide text-n-0">{label}</h2>
                      <span className="text-xs font-semibold text-n-500 tnum">{items.length} {items.length === 1 ? "match" : "matches"}</span>
                      <span className="flex-1 h-px bg-n-800 self-center" />
                    </div>
                  )}
                  <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                    {items.map((p, i) => (
                      <PredictionCard
                        key={`${p.home}-${p.away}-${p.date}-${i}`}
                        prediction={p}
                        savedKeys={savedKeys}
                        onSaveToggle={onSaveToggle}
                        showDay={!label}
                        onClick={() => openMatch(p)}
                      />
                    ))}
                  </div>
                </section>
              ))}
            </div>
          )}
        </>}

        {/* Footer note */}
        <p className="text-center text-xs text-n-500 pt-8">
          Powered by XGBoost + Elo ratings · football-data.org · Updated every 6 hours · 18+ · Gamble responsibly
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
