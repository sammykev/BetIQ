"use client";

import { useEffect, useState, useCallback } from "react";
import { PredictionCard } from "@/components/PredictionCard";
import { MatchModal } from "@/components/MatchModal";
import { LeagueTabs } from "@/components/LeagueTabs";
import { fetchPredictions, fetchLeagues, triggerRefresh } from "@/lib/api";
import type { Prediction, League } from "@/lib/api";
import { RefreshCw, TrendingUp, Shield, Info, AlertTriangle, CalendarDays, Percent } from "lucide-react";
import { ChatBot } from "@/components/ChatBot";
import { CalendarView } from "@/components/CalendarView";
import { ValueBets } from "@/components/ValueBets";
import { UserMenu } from "@/components/UserMenu";
import { PaywallModal } from "@/components/PaywallModal";
import { AnnouncementBanner } from "@/components/AnnouncementBanner";
import { useUser, SignInButton, SignUpButton } from "@clerk/nextjs";
import clsx from "clsx";

const CONFIDENCE_FILTERS = [
  { label: "All", value: 0 },
  { label: "≥ 60%", value: 0.6 },
  { label: "≥ 70%", value: 0.7 },
  { label: "≥ 80%", value: 0.8 },
];

const FEATURES = [
  { icon: "⚽", title: "AI Predictions", desc: "XGBoost + Elo ratings across 9 leagues" },
  { icon: "🎯", title: "Match Analysis", desc: "xG, markets, Elo gauge, correct score odds" },
  { icon: "🤖", title: "AI Assistant", desc: "Chat to build accumulators instantly" },
  { icon: "🎟️", title: "SportyBet Codes", desc: "One-click booking code generation" },
  { icon: "📅", title: "History Calendar", desc: "Track past predictions & accuracy" },
  { icon: "🌐", title: "Live Team News", desc: "Real-time injury & lineup context" },
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
      <p className="text-slate-300 font-semibold text-lg">Waking up the server{dots}</p>
      <p className="text-slate-500 text-sm max-w-xs mx-auto">
        The backend spins down when idle. It'll be ready in about 30 seconds.
      </p>
      <button onClick={onRetry}
        className="px-5 py-2 bg-green-500 hover:bg-green-400 text-black font-bold rounded-xl text-sm transition-all">
        Try now
      </button>
    </div>
  );
}

function AuthGate() {
  return (
    <div className="min-h-screen bg-slate-950 flex flex-col">
      {/* Header */}
      <header className="border-b border-slate-800 px-6 py-4 flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <img src="/logo.svg" alt="BetIQ" className="w-9 h-9 rounded-full" />
          <div>
            <h1 className="text-lg font-bold text-white leading-none">BetIQ</h1>
            <p className="text-xs text-slate-500 leading-none mt-0.5">AI Football Predictions</p>
          </div>
        </div>
        <SignInButton mode="modal">
          <button className="text-xs text-slate-400 hover:text-white transition-colors">
            Sign in
          </button>
        </SignInButton>
      </header>

      {/* Hero */}
      <main className="flex-1 flex flex-col items-center justify-center px-4 py-16 text-center space-y-8">
        <div className="space-y-4 max-w-lg">
          <div className="text-6xl">⚽</div>
          <h2 className="text-4xl font-black text-white leading-tight">
            Bet smarter with <span className="text-green-400">AI predictions</span>
          </h2>
          <p className="text-slate-400 text-lg leading-relaxed">
            XGBoost models + Elo ratings across 9 leagues. Chat to pick games,
            get SportyBet booking codes in one tap.
          </p>
        </div>

        {/* CTA buttons */}
        <div className="flex flex-col sm:flex-row gap-3 w-full max-w-xs">
          <SignUpButton mode="modal">
            <button className="flex-1 bg-green-500 hover:bg-green-400 text-black font-bold py-3 px-6 rounded-xl text-sm transition-all hover:scale-105 active:scale-95">
              Create free account
            </button>
          </SignUpButton>
          <SignInButton mode="modal">
            <button className="flex-1 bg-slate-800 hover:bg-slate-700 border border-slate-700 text-white font-semibold py-3 px-6 rounded-xl text-sm transition-all">
              Sign in
            </button>
          </SignInButton>
        </div>

        <p className="text-slate-600 text-xs">
          Free to sign up · No credit card required
        </p>

        {/* Feature grid */}
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3 w-full max-w-2xl pt-4">
          {FEATURES.map(({ icon, title, desc }) => (
            <div key={title} className="bg-slate-900 border border-slate-800 rounded-xl p-4 text-left space-y-1.5">
              <span className="text-2xl">{icon}</span>
              <p className="text-white text-sm font-semibold">{title}</p>
              <p className="text-slate-500 text-xs leading-relaxed">{desc}</p>
            </div>
          ))}
        </div>
      </main>

      {/* Footer */}
      <footer className="border-t border-slate-800 py-4 text-center text-xs text-slate-700">
        BetIQ · AI Football Predictions · For educational use only
      </footer>
    </div>
  );
}

export default function HomePage() {
  const [allPredictions, setAllPredictions] = useState<Prediction[]>([]);
  const [leagues, setLeagues] = useState<League[]>([]);
  const [selectedLeague, setSelectedLeague] = useState("ALL");
  const [minConf, setMinConf] = useState(0);
  const [sortBy, setSortBy] = useState<"date" | "confidence">("date");
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedMatch, setSelectedMatch] = useState<Prediction | null>(null);
  const [showCalendar, setShowCalendar] = useState(false);
  const [bankersOnly, setBankersOnly] = useState(false);
  const [activeView, setActiveView] = useState<"picks" | "value">("picks");
  const [showPaywall, setShowPaywall] = useState(false);
  const [savedKeys, setSavedKeys] = useState<Set<string>>(new Set());

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

  // Keep Render backend alive — ping every 10 minutes
  useEffect(() => {
    const API = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";
    const ping = () => fetch(`${API}/api/health`).catch(() => {});
    ping(); // immediate ping on load
    const id = setInterval(ping, 10 * 60 * 1000);
    return () => clearInterval(id);
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

  const sorted = [...predictions].sort((a, b) =>
    sortBy === "date"
      ? a.date.localeCompare(b.date) || a.time.localeCompare(b.time)
      : b.goals_confidence - a.goals_confidence
  );

  const fmt = (iso: string | null) => {
    if (!iso) return "Never";
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  };

  // Auth gate — all hooks above must run first (React rules)
  if (!isLoaded) {
    return (
      <div className="min-h-screen bg-slate-950 flex items-center justify-center">
        <div className="w-8 h-8 border-2 border-green-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (!user) return <AuthGate />;

  if (maintenanceMode) {
    return (
      <div className="min-h-screen bg-slate-950 flex flex-col items-center justify-center gap-4 text-center p-6">
        <img src="/logo.svg" alt="BetIQ" className="w-16 h-16 rounded-full opacity-60" />
        <h1 className="text-2xl font-black text-white">Back soon</h1>
        <p className="text-slate-400 max-w-sm">BetIQ is undergoing scheduled maintenance. We'll be back shortly with fresh predictions.</p>
        <p className="text-slate-600 text-sm">⚽ Thanks for your patience</p>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-white dark:bg-slate-950">
      {/* Announcement banner — sticky above header */}
      <AnnouncementBanner text={siteBanner} />

      {/* Header */}
      <header className="border-b border-slate-200 dark:border-slate-800 bg-white/80 dark:bg-slate-950/80 backdrop-blur sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-4 py-3 flex items-center justify-between gap-4">
          <div className="flex items-center gap-2.5">
            <img src="/logo.svg" alt="BetIQ" className="w-9 h-9 rounded-full" />
            <div>
              <h1 className="text-lg font-bold text-slate-900 dark:text-white tracking-tight leading-none">BetIQ</h1>
              <p className="text-xs text-slate-500 dark:text-slate-500 leading-none mt-0.5">AI Football Predictions</p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            {lastUpdated && (
              <span className="text-xs text-slate-500 hidden sm:block">
                Updated {fmt(lastUpdated)}
              </span>
            )}
            <button
              onClick={() => isPremium ? setShowCalendar(true) : setShowPaywall(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-200 dark:bg-slate-800 hover:bg-slate-300 dark:hover:bg-slate-700 border border-slate-300 dark:border-slate-700 rounded-lg text-xs text-slate-700 dark:text-slate-300 transition-all"
            >
              <CalendarDays size={12} />
              History
            </button>
            <UserMenu onUpgrade={() => setShowPaywall(true)} />
            <button
              onClick={handleRefresh}
              disabled={refreshing}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-200 dark:bg-slate-800 hover:bg-slate-300 dark:hover:bg-slate-700 border border-slate-300 dark:border-slate-700 rounded-lg text-xs text-slate-700 dark:text-slate-300 transition-all disabled:opacity-50"
            >
              <RefreshCw size={12} className={clsx(refreshing && "animate-spin")} />
              Refresh
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 py-6 space-y-6">
        {/* Stats bar */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {[
            { icon: <TrendingUp size={16} />, label: "Total Picks", value: predictions.length, color: "text-blue-400", onClick: undefined, active: false },
            { icon: <Shield size={16} />, label: "Bankers", value: bankers.length, color: "text-green-400", onClick: () => setBankersOnly(b => !b), active: bankersOnly },
            { icon: <TrendingUp size={16} />, label: "High Confidence", value: highConf.length, color: "text-yellow-400", onClick: undefined, active: false },
            { icon: <Info size={16} />, label: "Leagues", value: Object.keys(counts).length, color: "text-purple-400", onClick: undefined, active: false },
          ].map(({ icon, label, value, color, onClick, active }) => (
            <div
              key={label}
              onClick={onClick}
              className={clsx(
                "bg-slate-50 dark:bg-slate-900 border rounded-xl px-4 py-3 flex items-center gap-3 transition-all",
                onClick ? "cursor-pointer hover:scale-[1.02]" : "",
                active
                  ? "border-green-500/60 bg-green-500/10 dark:bg-green-500/10 ring-1 ring-green-500/40"
                  : "border-slate-200 dark:border-slate-800"
              )}
            >
              <span className={color}>{icon}</span>
              <div>
                <p className="text-xs text-slate-500">{label}{active ? " — active" : ""}</p>
                <p className="text-xl font-bold text-slate-900 dark:text-slate-100">{value}</p>
              </div>
            </div>
          ))}
        </div>

        {/* View toggle: Picks / Value Bets */}
        <div className="flex gap-2 bg-slate-100 dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl p-1 w-fit">
          <button
            onClick={() => setActiveView("picks")}
            className={clsx(
              "flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-sm font-semibold transition-all",
              activeView === "picks"
                ? "bg-white dark:bg-slate-800 text-slate-900 dark:text-white shadow-sm"
                : "text-slate-500 hover:text-slate-700 dark:hover:text-slate-300"
            )}
          >
            <Shield size={13} /> Picks
          </button>
          <button
            onClick={() => setActiveView("value")}
            className={clsx(
              "flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-sm font-semibold transition-all",
              activeView === "value"
                ? "bg-white dark:bg-slate-800 text-green-400 shadow-sm"
                : "text-slate-500 hover:text-slate-700 dark:hover:text-slate-300"
            )}
          >
            <TrendingUp size={13} /> Value Bets
          </button>
        </div>

        {/* Disclaimer */}
        <div className="flex items-start gap-2.5 bg-yellow-500/5 border border-yellow-500/20 rounded-xl px-4 py-3">
          <AlertTriangle size={14} className="text-yellow-500 mt-0.5 shrink-0" />
          <p className="text-xs text-yellow-200/70">
            Gamble responsibly. Past performance does not guarantee future results.
          </p>
        </div>

        {/* Filters */}
        <div className="space-y-3">
          <LeagueTabs
            leagues={leagues}
            selected={selectedLeague}
            onSelect={setSelectedLeague}
            counts={counts}
          />

          <div className="flex items-center gap-3 flex-wrap">
            <div className="flex gap-2">
              {CONFIDENCE_FILTERS.map(({ label, value }) => (
                <button
                  key={label}
                  onClick={() => setMinConf(value)}
                  className={clsx(
                    "px-3 py-1 rounded-lg text-xs font-medium border transition-all",
                    minConf === value
                      ? "bg-green-500/20 text-green-300 border-green-500/40"
                      : "bg-slate-200 dark:bg-slate-800 text-slate-500 dark:text-slate-400 border-slate-300 dark:border-slate-700 hover:text-slate-800 dark:hover:text-slate-200"
                  )}
                >
                  {label}
                </button>
              ))}
            </div>

            <div className="flex items-center gap-1 bg-slate-200 dark:bg-slate-800 border border-slate-300 dark:border-slate-700 rounded-lg p-0.5">
              <button
                onClick={() => setSortBy("date")}
                className={clsx(
                  "flex items-center gap-1.5 px-3 py-1 rounded-md text-xs font-medium transition-all",
                  sortBy === "date"
                    ? "bg-slate-300 dark:bg-slate-600 text-slate-900 dark:text-white"
                    : "text-slate-500 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200"
                )}
              >
                <CalendarDays size={11} />
                Soonest first
              </button>
              <button
                onClick={() => setSortBy("confidence")}
                className={clsx(
                  "flex items-center gap-1.5 px-3 py-1 rounded-md text-xs font-medium transition-all",
                  sortBy === "confidence"
                    ? "bg-slate-300 dark:bg-slate-600 text-slate-900 dark:text-white"
                    : "text-slate-500 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200"
                )}
              >
                <Percent size={11} />
                Best confidence
              </button>
            </div>
          </div>
        </div>

        {/* Value Bets view */}
        {activeView === "value" && (
          <ValueBets onMatchClick={(home, away) => {
            const found = allPredictions.find(p => p.home === home && p.away === away);
            if (found) setSelectedMatch(found);
          }} />
        )}

        {/* Picks view */}
        {activeView === "picks" && (loading ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className="bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl p-4 space-y-4 animate-pulse">
                <div className="h-3 bg-slate-200 dark:bg-slate-800 rounded w-1/2" />
                <div className="space-y-2">
                  <div className="h-5 bg-slate-200 dark:bg-slate-800 rounded w-3/4 mx-auto" />
                  <div className="h-3 bg-slate-200 dark:bg-slate-800 rounded w-1/4 mx-auto" />
                  <div className="h-5 bg-slate-200 dark:bg-slate-800 rounded w-3/4 mx-auto" />
                </div>
                <div className="grid grid-cols-3 gap-2">
                  {[0, 1, 2].map((j) => <div key={j} className="h-10 bg-slate-200 dark:bg-slate-800 rounded-lg" />)}
                </div>
                <div className="h-3 bg-slate-200 dark:bg-slate-800 rounded" />
                <div className="h-3 bg-slate-200 dark:bg-slate-800 rounded w-3/4" />
              </div>
            ))}
          </div>
        ) : error === "__waking__" ? (
          <WakingUp onRetry={() => { setLoading(true); load(); }} />
        ) : error ? (
          <div className="text-center py-20 space-y-3">
            <AlertTriangle size={40} className="text-red-400 mx-auto" />
            <p className="text-red-300 font-medium">Could not reach the prediction server.</p>
            <button onClick={() => { setLoading(true); load(); }}
              className="px-4 py-2 bg-slate-800 text-slate-300 rounded-lg text-sm hover:bg-slate-700 transition-all">
              Retry
            </button>
          </div>
        ) : predictions.length === 0 ? (
          <div className="text-center py-20 space-y-3">
            <span className="text-5xl">📭</span>
            <p className="text-slate-500 dark:text-slate-400">No predictions available for the selected filters.</p>
            <p className="text-slate-400 dark:text-slate-600 text-sm">Try widening your confidence filter or selecting All Leagues.</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {sorted.map((p, i) => (
              <PredictionCard
                key={`${p.home}-${p.away}-${p.date}-${i}`}
                prediction={p}
                savedKeys={savedKeys}
                onClick={() => {
                  const API_B = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";
                  fetch(`${API_B}/api/track/match`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ home: p.home, away: p.away }) }).catch(() => {});
                  isPremium ? setSelectedMatch(p) : setShowPaywall(true);
                }}
              />
            ))}
          </div>
        ))}
      </main>

      {/* AI Betting Assistant — premium only */}
      {isPremium && <ChatBot predictions={allPredictions} />}

      {/* Calendar / History */}
      {showCalendar && <CalendarView onClose={() => setShowCalendar(false)} />}

      {/* Paywall */}
      {showPaywall && (
        <PaywallModal
          onClose={() => setShowPaywall(false)}
          onSuccess={() => { setShowPaywall(false); window.location.reload(); }}
        />
      )}

      {/* Match Analysis Modal */}
      {selectedMatch && (
        <MatchModal
          prediction={selectedMatch}
          onClose={() => setSelectedMatch(null)}
        />
      )}

      {/* Footer */}
      <footer className="border-t border-slate-200 dark:border-slate-800 mt-12 py-6 text-center text-xs text-slate-400 dark:text-slate-600">
        <p>BetIQ · Powered by XGBoost + Elo Ratings · football-data.org</p>
        <p className="mt-1">9 leagues · Updated every 6 hours · For educational use only</p>
      </footer>
    </div>
  );
}
