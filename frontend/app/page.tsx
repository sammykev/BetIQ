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
import clsx from "clsx";

const CONFIDENCE_FILTERS = [
  { label: "All", value: 0 },
  { label: "≥ 60%", value: 0.6 },
  { label: "≥ 70%", value: 0.7 },
  { label: "≥ 80%", value: 0.8 },
];

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

  const load = useCallback(async () => {
    try {
      setError(null);
      const [data, lgs] = await Promise.all([
        fetchPredictions(undefined, undefined),
        fetchLeagues(),
      ]);
      setAllPredictions(data.predictions);
      setLastUpdated(data.last_updated);
      setLeagues(lgs);
    } catch (e) {
      setError("Could not reach the prediction server. Make sure the backend is running.");
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

  return (
    <div className="min-h-screen bg-white dark:bg-slate-950">
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
              onClick={() => setShowCalendar(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-200 dark:bg-slate-800 hover:bg-slate-300 dark:hover:bg-slate-700 border border-slate-300 dark:border-slate-700 rounded-lg text-xs text-slate-700 dark:text-slate-300 transition-all"
            >
              <CalendarDays size={12} />
              History
            </button>
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

        {/* Content */}
        {loading ? (
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
        ) : error ? (
          <div className="text-center py-20 space-y-3">
            <AlertTriangle size={40} className="text-red-400 mx-auto" />
            <p className="text-red-300 font-medium">{error}</p>
            <p className="text-slate-500 dark:text-slate-500 text-sm">Check that the backend server is running and NEXT_PUBLIC_API_URL is set correctly.</p>
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
                onClick={() => setSelectedMatch(p)}
              />
            ))}
          </div>
        )}
      </main>

      {/* AI Betting Assistant */}
      <ChatBot predictions={allPredictions} />

      {/* Calendar / History */}
      {showCalendar && <CalendarView onClose={() => setShowCalendar(false)} />}

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
