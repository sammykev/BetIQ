"use client";

import { useEffect, useState } from "react";
import { fetchMatchAnalysis } from "@/lib/api";
import type { MatchAnalysis, Market } from "@/lib/api";
import type { Prediction } from "@/lib/api";
import { X, Star, BarChart2, Clock } from "lucide-react";
import clsx from "clsx";

interface Props {
  prediction: Prediction;
  onClose: () => void;
}

const MARKET_ICONS: Record<string, string> = {
  "1x2": "⚽",
  double_chance: "🛡️",
  btts: "🎯",
  goals_ou: "📊",
  asian_handicap: "⚖️",
  half_time: "⏱️",
  correct_score: "🔢",
};

// ------------------------------------------------------------------ //
// Elo Gauge
// ------------------------------------------------------------------ //
function EloGauge({ elo, home, away }: { elo: MatchAnalysis["elo"]; home: string; away: string }) {
  const absGap = Math.abs(elo.gap);
  const cappedGap = Math.min(absGap, elo.gap_out_of);
  const pct = (cappedGap / elo.gap_out_of) * 100;
  const homeLeads = elo.gap >= 0;

  const labelColor =
    absGap < 30 ? "text-slate-300" :
    absGap < 80 ? "text-blue-300" :
    absGap < 150 ? "text-yellow-300" :
    absGap < 250 ? "text-orange-300" : "text-red-400";

  return (
    <div className="bg-slate-800/60 border border-slate-700/50 rounded-xl p-4 space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-base">🏆</span>
        <h3 className="text-sm font-semibold text-slate-200">Elo Rating</h3>
        <span className={clsx("ml-auto text-xs font-bold px-2 py-0.5 rounded-full bg-slate-700", labelColor)}>
          {elo.label}
        </span>
      </div>

      {/* Team ratings */}
      <div className="grid grid-cols-3 gap-2 text-center">
        <div>
          <p className="text-xs text-slate-500 truncate">{home}</p>
          <p className="text-lg font-black text-slate-100">{elo.home}</p>
        </div>
        <div className="flex flex-col items-center justify-center">
          <p className="text-[10px] text-slate-500 uppercase tracking-wide">Gap</p>
          <p className={clsx("text-base font-black", labelColor)}>
            {absGap > 0 ? (homeLeads ? "+" : "−") : ""}{absGap}
          </p>
          <p className="text-[10px] text-slate-600">/ {elo.gap_out_of} pts</p>
        </div>
        <div>
          <p className="text-xs text-slate-500 truncate">{away}</p>
          <p className="text-lg font-black text-slate-100">{elo.away}</p>
        </div>
      </div>

      {/* Gap bar */}
      <div>
        <div className="flex justify-between text-[10px] text-slate-600 mb-1">
          <span>0</span>
          <span className="text-slate-500">Elo gap scale (max {elo.gap_out_of} pts = ~91% win prob)</span>
          <span>{elo.gap_out_of}</span>
        </div>
        <div className="h-2 bg-slate-700 rounded-full overflow-hidden">
          <div
            className={clsx("h-full rounded-full transition-all duration-700",
              absGap < 30 ? "bg-slate-400" :
              absGap < 80 ? "bg-blue-400" :
              absGap < 150 ? "bg-yellow-400" :
              absGap < 250 ? "bg-orange-400" : "bg-red-400"
            )}
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>

      {/* Implied prob */}
      <div className="flex items-start gap-2 bg-slate-700/30 rounded-lg px-3 py-2">
        <span className="text-yellow-400 mt-0.5 shrink-0">💡</span>
        <p className="text-xs text-slate-300">
          <span className="font-semibold text-white">{elo.leading}</span> have a{" "}
          <span className={clsx("font-bold", labelColor)}>{Math.round(elo.implied_win_prob * 100)}%</span>{" "}
          implied win probability from Elo ratings alone.{" "}
          <span className="text-slate-400">{elo.description}</span>
        </p>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ //
// Market block
// ------------------------------------------------------------------ //
function ProbBar({ prob, highlight }: { prob: number; highlight: boolean }) {
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 bg-slate-700 rounded-full overflow-hidden">
        <div
          className={clsx("h-full rounded-full transition-all duration-500",
            highlight ? "bg-green-400" : "bg-slate-500")}
          style={{ width: `${Math.round(prob * 100)}%` }}
        />
      </div>
      <span className={clsx("text-xs font-semibold w-9 text-right",
        highlight ? "text-green-300" : "text-slate-400")}>
        {Math.round(prob * 100)}%
      </span>
    </div>
  );
}

function MarketBlock({ market, recommendedCode }: { market: Market; recommendedCode?: string }) {
  const best = [...market.options].sort((a, b) => b.prob - a.prob)[0];
  return (
    <div className="bg-slate-800/60 border border-slate-700/50 rounded-xl p-4 space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-base">{MARKET_ICONS[market.id] || "📌"}</span>
        <h3 className="text-sm font-semibold text-slate-200">{market.name}</h3>
      </div>
      <div className="space-y-2">
        {market.options.map((opt) => (
          <div key={opt.code} className="space-y-1">
            <div className="flex items-center gap-1.5">
              <span className="text-xs text-slate-300 truncate flex-1">{opt.label}</span>
              {opt.code === recommendedCode && (
                <span className="flex items-center gap-0.5 bg-green-500/20 text-green-300 text-[10px] font-bold px-1.5 py-0.5 rounded-full border border-green-500/30 shrink-0">
                  <Star size={8} /> TOP PICK
                </span>
              )}
            </div>
            <ProbBar prob={opt.prob} highlight={opt.code === best.code} />
          </div>
        ))}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ //
// H2H Tab
// ------------------------------------------------------------------ //
function H2HTab({ h2h, home, away }: { h2h: H2HData; home: string; away: string }) {
  if (!h2h.summary || h2h.meetings.length === 0) {
    const fromApi = h2h.source === "api";
    return (
      <div className="text-center py-12 text-slate-500 space-y-2">
        <p className="text-3xl">🗂️</p>
        <p className="text-slate-300 font-medium">No recent meetings found</p>
        {fromApi ? (
          <>
            <p className="text-sm text-slate-500">
              Live data checked — these teams have no recorded head-to-head
              meetings in this competition.
            </p>
            <p className="text-xs text-slate-600 mt-1">
              This is common for international friendlies or new competition
              groupings. The prediction is based on Elo ratings and form instead.
            </p>
          </>
        ) : (
          <>
            <p className="text-sm text-slate-500">
              These teams may not have met in our recorded competitions.
            </p>
            <p className="text-xs text-slate-600">
              The prediction is based on Elo ratings and team form.
            </p>
          </>
        )}
      </div>
    );
  }

  const s = h2h.summary;
  const homeWinPct = Math.round((s.home_wins / s.total) * 100);
  const drawPct   = Math.round((s.draws   / s.total) * 100);
  const awayWinPct = Math.round((s.away_wins / s.total) * 100);
  const bttsPct = Math.round((s.btts_count / s.total) * 100);

  return (
    <div className="space-y-4">
      {/* Summary cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        {[
          { label: `${home} Wins`, value: s.home_wins, pct: homeWinPct, color: "text-blue-400" },
          { label: "Draws",        value: s.draws,     pct: drawPct,    color: "text-slate-300" },
          { label: `${away} Wins`, value: s.away_wins, pct: awayWinPct, color: "text-purple-400" },
          { label: "BTTS",         value: `${s.btts_count}/${s.total}`, pct: bttsPct, color: "text-yellow-400" },
        ].map(({ label, value, pct, color }) => (
          <div key={label} className="bg-slate-800/60 border border-slate-700/50 rounded-xl p-3 text-center">
            <p className="text-xs text-slate-500 truncate">{label}</p>
            <p className={clsx("text-xl font-black", color)}>{value}</p>
            <p className="text-xs text-slate-600">{pct}%</p>
          </div>
        ))}
      </div>

      {/* Avg goals */}
      <div className="flex gap-3">
        {[
          { label: `Avg Goals / Game`, value: s.avg_goals },
          { label: `${home} Goals Total`, value: s.home_goals },
          { label: `${away} Goals Total`, value: s.away_goals },
        ].map(({ label, value }) => (
          <div key={label} className="flex-1 bg-slate-800/60 border border-slate-700/50 rounded-xl p-3 text-center">
            <p className="text-xs text-slate-500 truncate">{label}</p>
            <p className="text-lg font-black text-slate-100">{value}</p>
          </div>
        ))}
      </div>

      {/* Win bar */}
      <div className="space-y-1">
        <p className="text-xs text-slate-500">Head-to-head record ({s.total} games)</p>
        <div className="flex h-3 rounded-full overflow-hidden gap-0.5">
          <div className="bg-blue-500 transition-all" style={{ width: `${homeWinPct}%` }} title={`${home} wins`} />
          <div className="bg-slate-500 transition-all" style={{ width: `${drawPct}%` }} title="Draws" />
          <div className="bg-purple-500 transition-all" style={{ width: `${awayWinPct}%` }} title={`${away} wins`} />
        </div>
        <div className="flex justify-between text-[10px] text-slate-500">
          <span className="text-blue-400">{home} {homeWinPct}%</span>
          <span>Draw {drawPct}%</span>
          <span className="text-purple-400">{awayWinPct}% {away}</span>
        </div>
      </div>

      {/* Meeting list */}
      <div className="space-y-2">
        <p className="text-xs font-semibold text-slate-400 uppercase tracking-wide">Recent Meetings</p>
        {h2h.meetings.map((m, i) => {
          const homeWon = m.winner === m.home_team;
          const awayWon = m.winner === m.away_team;
          return (
            <div key={i} className="flex items-center gap-3 bg-slate-800/50 rounded-lg px-3 py-2.5">
              <span className="text-xs text-slate-500 w-20 shrink-0">{m.date}</span>
              <div className="flex-1 flex items-center gap-2 min-w-0">
                <span className={clsx("text-xs font-medium truncate flex-1 text-right",
                  homeWon ? "text-white" : "text-slate-400")}>{m.home_team}</span>
                <span className={clsx(
                  "text-sm font-black px-2 py-0.5 rounded font-mono shrink-0",
                  m.result === "D" ? "bg-slate-700 text-slate-200" :
                  (m.winner === m.home_team ? "bg-blue-500/20 text-blue-200" : "bg-purple-500/20 text-purple-200")
                )}>{m.score}</span>
                <span className={clsx("text-xs font-medium truncate flex-1",
                  awayWon ? "text-white" : "text-slate-400")}>{m.away_team}</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ //
// Main Modal
// ------------------------------------------------------------------ //
export function MatchModal({ prediction: p, onClose }: Props) {
  const [analysis, setAnalysis] = useState<MatchAnalysis | null>(null);
  const [loadingAnalysis, setLoadingAnalysis] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    fetchMatchAnalysis(p.home, p.away)
      .then(setAnalysis)
      .catch(() => setError(true))
      .finally(() => setLoadingAnalysis(false));
  }, [p.home, p.away]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  const rec = analysis?.recommended;

  return (
    <div
      className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-start justify-center p-4 overflow-y-auto"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="w-full max-w-2xl my-8 bg-slate-900 border border-slate-700 rounded-2xl overflow-hidden shadow-2xl">

        {/* Header */}
        <div className="bg-gradient-to-r from-slate-800 to-slate-900 border-b border-slate-700 p-5">
          <div className="flex items-start justify-between gap-4">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-3 flex-wrap">
                <span>{p.flag}</span>
                <span className="text-xs text-slate-400 font-medium">{p.league_name}</span>
                <span className="text-slate-600">·</span>
                <span className="text-xs text-slate-500 flex items-center gap-1">
                  <Clock size={10} /> {p.date}{p.time !== "TBD" ? ` · ${p.time}` : ""}
                </span>
              </div>
              <div className="flex items-center gap-4">
                <div className="flex-1 text-center">
                  <p className="text-lg font-bold text-white leading-tight">{p.home}</p>
                  <p className="text-xs text-slate-500 mt-0.5">Home</p>
                </div>
                <div className="text-slate-500 font-bold">vs</div>
                <div className="flex-1 text-center">
                  <p className="text-lg font-bold text-white leading-tight">{p.away}</p>
                  <p className="text-xs text-slate-500 mt-0.5">Away</p>
                </div>
              </div>
            </div>
            <button onClick={onClose} className="p-2 hover:bg-slate-700 rounded-lg transition-colors shrink-0">
              <X size={16} className="text-slate-400" />
            </button>
          </div>

          {/* 1X2 quick stats */}
          <div className="grid grid-cols-3 gap-2 mt-4">
            {[
              { label: "Home Win", value: `${Math.round(p.p_home * 100)}%`, color: "text-blue-400" },
              { label: "Draw",     value: `${Math.round(p.p_draw * 100)}%`, color: "text-slate-300" },
              { label: "Away Win", value: `${Math.round(p.p_away * 100)}%`, color: "text-purple-400" },
            ].map(({ label, value, color }) => (
              <div key={label} className="bg-slate-700/40 rounded-lg px-3 py-2 text-center">
                <p className="text-xs text-slate-500">{label}</p>
                <p className={clsx("text-sm font-bold", color)}>{value}</p>
              </div>
            ))}
          </div>
        </div>

        {/* Tabs — H2H temporarily hidden while being improved */}
        <div className="flex border-b border-slate-800">
          <div className="flex-1 flex items-center justify-center gap-1.5 py-3 text-xs font-medium text-green-400 border-b-2 border-green-500 bg-green-500/5">
            <BarChart2 size={13} /> Analysis &amp; Markets
          </div>
        </div>

        {/* Body */}
        <div className="p-5 space-y-4">
          {loadingAnalysis && (
            <div className="space-y-3">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="bg-slate-800 rounded-xl h-28 animate-pulse" />
              ))}
            </div>
          )}
          {error && (
            <p className="text-center py-8 text-slate-500">Could not load analysis. Is the backend running?</p>
          )}
          {analysis && (
            <>
              {/* Best Pick banner */}
              {rec && (
                <div className="bg-green-500/10 border border-green-500/30 rounded-xl p-4 flex items-center gap-4">
                  <div className="w-10 h-10 bg-green-500/20 rounded-full flex items-center justify-center shrink-0">
                    <Star size={18} className="text-green-400" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-xs text-green-400 font-semibold uppercase tracking-wide mb-0.5">Best Pick</p>
                    <p className="text-white font-bold text-base truncate">{rec.label}</p>
                    <p className="text-xs text-slate-400">{rec.market} · {Math.round(rec.prob * 100)}% confidence</p>
                  </div>
                  <p className="text-2xl font-black text-green-400 shrink-0">{Math.round(rec.prob * 100)}%</p>
                </div>
              )}

              {/* xG row */}
              <div className="grid grid-cols-2 gap-3">
                {[
                  { label: `${p.home} xG`, value: analysis.xg_home },
                  { label: `${p.away} xG`, value: analysis.xg_away },
                ].map(({ label, value }) => (
                  <div key={label} className="bg-slate-800/60 border border-slate-700/50 rounded-xl px-4 py-3">
                    <p className="text-xs text-slate-500">{label}</p>
                    <p className="text-lg font-bold text-slate-100">{value.toFixed(2)}</p>
                    <p className="text-xs text-slate-600">Expected goals</p>
                  </div>
                ))}
              </div>

              {/* Elo gauge */}
              <EloGauge elo={analysis.elo} home={p.home} away={p.away} />

              {/* Market blocks grid */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {analysis.markets.map((m) => (
                  <MarketBlock
                    key={m.id}
                    market={m}
                    recommendedCode={rec?.market_id === m.id ? rec.code : undefined}
                  />
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
