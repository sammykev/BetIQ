"use client";

import { useEffect, useState } from "react";
import { X, Star, Loader2, TrendingUp } from "lucide-react";
import clsx from "clsx";
import { getSportAssets } from "@/lib/sportsAssets";
import type { SportPrediction } from "./SportCard";

const API = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";

interface Outcome {
  name:    string;
  label:   string;
  odds:    number;
  implied: number;
  point:   number | null;
}

interface Market {
  id:       string;
  name:     string;
  icon:     string;
  outcomes: Outcome[];
}

interface EventDetail {
  home:      string;
  away:      string;
  date:      string;
  time:      string;
  sport:     string;
  markets:   Market[];
  best_pick: { label: string; odds: number; confidence: number } | null;
}

function OddsBar({ outcome, isBest }: { outcome: Outcome; isBest: boolean }) {
  const pct = Math.round(outcome.implied * 100);
  return (
    <div className={clsx(
      "flex items-center gap-3 px-3 py-2.5 rounded-xl transition-all",
      isBest
        ? "bg-green-500/15 border border-green-500/30"
        : "bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700"
    )}>
      <div className="flex-1 min-w-0">
        <p className={clsx("text-sm font-semibold truncate",
          isBest ? "text-green-400" : "text-slate-800 dark:text-slate-200")}>
          {outcome.label}
        </p>
        <div className="flex items-center gap-2 mt-1">
          <div className="flex-1 h-1 bg-slate-300 dark:bg-slate-700 rounded-full overflow-hidden">
            <div className={clsx("h-full rounded-full", isBest ? "bg-green-400" : "bg-slate-500")}
              style={{ width: `${pct}%` }} />
          </div>
          <span className="text-[11px] text-slate-500 dark:text-slate-400 shrink-0">{pct}%</span>
        </div>
      </div>
      <div className="text-right shrink-0">
        <span className={clsx("text-lg font-black",
          isBest ? "text-green-400" : "text-slate-700 dark:text-slate-200")}>
          {outcome.odds}
        </span>
        {isBest && (
          <p className="text-[9px] text-green-400/70 font-semibold">BEST ODDS</p>
        )}
      </div>
    </div>
  );
}

interface Props {
  prediction: SportPrediction;
  onClose: () => void;
}

export function SportModal({ prediction: p, onClose }: Props) {
  const [detail, setDetail]   = useState<EventDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState(false);

  const assets = getSportAssets(p.home, p.away, p.sport);
  const imgSize = assets.isPlayerFace ? "cover" : "60%";

  useEffect(() => {
    const params = new URLSearchParams({ home: p.home, away: p.away, date: p.date });
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 20000);  // 20s timeout
    fetch(`${API}/api/sports/${p.sport}/event?${params}`, { signal: ctrl.signal })
      .then(r => { if (!r.ok) throw new Error(r.status.toString()); return r.json(); })
      .then(d => setDetail(d))
      .catch(() => setError(true))
      .finally(() => { setLoading(false); clearTimeout(timer); });
  }, [p.home, p.away, p.date, p.sport]);

  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  const sportLabel: Record<string, string> = {
    basketball: "Basketball", tennis: "Lawn Tennis", table_tennis: "Table Tennis",
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-start justify-center p-4 overflow-y-auto"
      onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="w-full max-w-2xl my-8 bg-slate-50 dark:bg-slate-900 border border-slate-300 dark:border-slate-700 rounded-2xl overflow-hidden shadow-2xl">

        {/* Hero header with bleeding image */}
        <div className="relative h-40 overflow-hidden">
          <div className="absolute inset-0 flex">
            <div className="flex-1" style={{ background: assets.homeColor }} />
            <div className="flex-1" style={{ background: assets.awayColor }} />
          </div>
          {assets.homeImage && (
            <div className="absolute inset-0" style={{
              backgroundImage: `url(${assets.homeImage})`,
              backgroundSize: imgSize,
              backgroundPosition: assets.isPlayerFace ? "25% top" : "30% center",
              backgroundRepeat: "no-repeat",
              maskImage: "linear-gradient(to right,black 0%,black 25%,transparent 70%)",
              WebkitMaskImage: "linear-gradient(to right,black 0%,black 25%,transparent 70%)",
            }} />
          )}
          {assets.awayImage && (
            <div className="absolute inset-0" style={{
              backgroundImage: `url(${assets.awayImage})`,
              backgroundSize: imgSize,
              backgroundPosition: assets.isPlayerFace ? "75% top" : "70% center",
              backgroundRepeat: "no-repeat",
              maskImage: "linear-gradient(to left,black 0%,black 25%,transparent 70%)",
              WebkitMaskImage: "linear-gradient(to left,black 0%,black 25%,transparent 70%)",
            }} />
          )}
          <div className="absolute inset-0 bg-black/50" />

          {/* Header content */}
          <div className="absolute inset-0 flex flex-col justify-between p-5">
            <div className="flex items-start justify-between">
              <div>
                <p className="text-[11px] font-semibold text-white/70">
                  {p.flag} {p.league_name} · {sportLabel[p.sport] || p.sport}
                  {p.surface ? ` · ${p.surface}` : ""}
                </p>
                <p className="text-[11px] text-white/50 mt-0.5">{p.date}{p.time && p.time !== "TBD" ? ` · ${p.time}` : ""}</p>
              </div>
              <button onClick={onClose}
                className="p-2 hover:bg-white/10 rounded-lg transition-colors">
                <X size={16} className="text-white/70" />
              </button>
            </div>

            <div className="flex items-end justify-between gap-4">
              <div>
                <p className="text-xl font-black text-white drop-shadow-lg">{p.home}</p>
                <p className="text-[10px] text-white/50 mt-0.5">
                  {Math.round(p.p_home * 100)}% win prob
                </p>
              </div>
              <span className="text-white/40 text-sm font-bold mb-1">vs</span>
              <div className="text-right">
                <p className="text-xl font-black text-white drop-shadow-lg">{p.away}</p>
                <p className="text-[10px] text-white/50 mt-0.5">
                  {Math.round(p.p_away * 100)}% win prob
                </p>
              </div>
            </div>
          </div>
        </div>

        {/* Body */}
        <div className="p-5 space-y-5">

          {/* Best pick banner */}
          {(detail?.best_pick || p.tip_1x2) && (
            <div className="bg-green-500/10 border border-green-500/30 rounded-xl p-4 flex items-center gap-4">
              <div className="w-10 h-10 bg-green-500/20 rounded-full flex items-center justify-center shrink-0">
                <Star size={18} className="text-green-400" />
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-xs text-green-400 font-semibold uppercase tracking-wide mb-0.5">Best Pick</p>
                <p className="text-slate-900 dark:text-white font-bold text-base">
                  {detail?.best_pick?.label || p.tip_1x2}
                </p>
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  {Math.round((detail?.best_pick?.confidence || p.goals_confidence) * 100)}% confidence
                </p>
              </div>
              {(detail?.best_pick?.odds) && (
                <p className="text-2xl font-black text-green-400 shrink-0">{detail.best_pick.odds}</p>
              )}
            </div>
          )}

          {/* Markets */}
          {loading ? (
            <div className="flex items-center justify-center gap-2 py-10 text-slate-500">
              <Loader2 size={18} className="animate-spin" />
              <span className="text-sm">Loading live markets…</span>
            </div>
          ) : error ? (
            <div className="text-center py-10 space-y-3">
              <TrendingUp size={28} className="text-slate-500 mx-auto opacity-40" />
              <p className="text-slate-500 text-sm">Live markets unavailable for this match.</p>
              <p className="text-slate-600 text-xs">
                This can happen when no active tournament is running for {p.sport === "tennis" ? "tennis" : p.sport === "table_tennis" ? "table tennis" : "this sport"}.
                The Odds API only covers major tournaments when they're in progress.
              </p>
              {/* Show what we DO have from the prediction card */}
              <div className="mt-4 bg-slate-800/50 border border-slate-700 rounded-xl p-4 text-left space-y-2">
                <p className="text-xs text-slate-400 font-semibold uppercase tracking-wide">Our Model Prediction</p>
                <div className="flex justify-between text-sm">
                  <span className="text-white font-semibold">{p.home}</span>
                  <span className="text-green-400 font-black">{Math.round(p.p_home * 100)}%</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-white font-semibold">{p.away}</span>
                  <span className="text-green-400 font-black">{Math.round(p.p_away * 100)}%</span>
                </div>
                <div className="pt-2 border-t border-slate-700">
                  <p className="text-xs text-slate-400">Best pick: <span className="text-white font-bold">{p.tip_1x2}</span></p>
                  <p className="text-xs text-slate-400">Confidence: <span className="text-green-400 font-bold">{Math.round(p.goals_confidence * 100)}%</span></p>
                  {p.odds_home && <p className="text-xs text-slate-400 mt-1">
                    Odds: <span className="text-white">{p.home} {p.odds_home}</span> · <span className="text-white">{p.away} {p.odds_away}</span>
                  </p>}
                </div>
              </div>
            </div>
          ) : detail?.markets.map(market => {
            const bestOdds = Math.max(...market.outcomes.map(o => o.implied));
            return (
              <div key={market.id} className="space-y-2">
                <div className="flex items-center gap-2">
                  <span className="text-base">{market.icon}</span>
                  <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-200">{market.name}</h3>
                  <span className="ml-auto text-[10px] text-slate-400 dark:text-slate-600">Best available odds</span>
                </div>
                <div className="space-y-2">
                  {market.outcomes.map(o => (
                    <OddsBar key={o.name} outcome={o} isBest={o.implied === bestOdds} />
                  ))}
                </div>
              </div>
            );
          })}

          <p className="text-center text-[10px] text-slate-400 dark:text-slate-600 pt-2">
            Odds sourced from The Odds API · Best available across EU bookmakers · Always verify before betting
          </p>
        </div>
      </div>
    </div>
  );
}
