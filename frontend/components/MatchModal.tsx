"use client";

import { useEffect, useState } from "react";
import { fetchMatchAnalysis, fetchExplanation } from "@/lib/api";
import type { MatchAnalysis, Market, MatchExplanation } from "@/lib/api";
import type { Prediction } from "@/lib/api";
import { X, Star, BarChart2, Clock, Sparkles, ExternalLink } from "lucide-react";
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
  cards: "🟨",
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
    absGap < 30 ? "text-slate-700 dark:text-slate-300" :
    absGap < 80 ? "text-blue-300" :
    absGap < 150 ? "text-yellow-300" :
    absGap < 250 ? "text-orange-300" : "text-red-400";

  return (
    <div className="bg-slate-100/80 dark:bg-slate-800/60 border border-slate-200 dark:border-slate-700/50 rounded-xl p-4 space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-base">🏆</span>
        <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-200">Elo Rating</h3>
        <span className={clsx("ml-auto text-xs font-bold px-2 py-0.5 rounded-full bg-slate-300 dark:bg-slate-700", labelColor)}>
          {elo.label}
        </span>
      </div>

      {/* Team ratings */}
      <div className="grid grid-cols-3 gap-2 text-center">
        <div>
          <p className="text-xs text-slate-500 truncate">{home}</p>
          <p className="text-lg font-black text-slate-900 dark:text-slate-100">{elo.home}</p>
        </div>
        <div className="flex flex-col items-center justify-center">
          <p className="text-[10px] text-slate-500 uppercase tracking-wide">Gap</p>
          <p className={clsx("text-base font-black", labelColor)}>
            {absGap > 0 ? (homeLeads ? "+" : "−") : ""}{absGap}
          </p>
          <p className="text-[10px] text-slate-400 dark:text-slate-600">/ {elo.gap_out_of} pts</p>
        </div>
        <div>
          <p className="text-xs text-slate-500 truncate">{away}</p>
          <p className="text-lg font-black text-slate-900 dark:text-slate-100">{elo.away}</p>
        </div>
      </div>

      {/* Gap bar */}
      <div>
        <div className="flex justify-between text-[10px] text-slate-400 dark:text-slate-600 mb-1">
          <span>0</span>
          <span className="text-slate-500">Elo gap scale (max {elo.gap_out_of} pts = ~91% win prob)</span>
          <span>{elo.gap_out_of}</span>
        </div>
        <div className="h-2 bg-slate-300 dark:bg-slate-700 rounded-full overflow-hidden">
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
      <div className="flex items-start gap-2 bg-slate-200/40 dark:bg-slate-700/30 rounded-lg px-3 py-2">
        <span className="text-yellow-400 mt-0.5 shrink-0">💡</span>
        <p className="text-xs text-slate-700 dark:text-slate-300">
          <span className="font-semibold text-slate-900 dark:text-white">{elo.leading}</span> have a{" "}
          <span className={clsx("font-bold", labelColor)}>{Math.round(elo.implied_win_prob * 100)}%</span>{" "}
          implied win probability from Elo ratings alone.{" "}
          <span className="text-slate-500 dark:text-slate-400">{elo.description}</span>
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
      <div className="flex-1 h-1.5 bg-slate-300 dark:bg-slate-700 rounded-full overflow-hidden">
        <div
          className={clsx("h-full rounded-full transition-all duration-500",
            highlight ? "bg-green-400" : "bg-slate-500")}
          style={{ width: `${Math.round(prob * 100)}%` }}
        />
      </div>
      <span className={clsx("text-xs font-semibold w-9 text-right",
        highlight ? "text-green-300" : "text-slate-500 dark:text-slate-400")}>
        {Math.round(prob * 100)}%
      </span>
    </div>
  );
}

function MarketBlock({ market, recommendedCode }: { market: Market; recommendedCode?: string }) {
  const best = [...market.options].sort((a, b) => b.prob - a.prob)[0];
  return (
    <div className="bg-slate-100/80 dark:bg-slate-800/60 border border-slate-200 dark:border-slate-700/50 rounded-xl p-4 space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-base">{MARKET_ICONS[market.id] || "📌"}</span>
        <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-200">{market.name}</h3>
      </div>
      <div className="space-y-2">
        {market.options.map((opt) => (
          <div key={opt.code} className="space-y-1">
            <div className="flex items-center gap-1.5">
              <span className="text-xs text-slate-700 dark:text-slate-300 truncate flex-1">{opt.label}</span>
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
// AI Explanation block
// ------------------------------------------------------------------ //
function AIExplanation({ explanation }: { explanation: MatchExplanation | null }) {
  if (!explanation) {
    return (
      <div className="bg-slate-100/60 dark:bg-slate-800/40 border border-slate-200 dark:border-slate-700/50 rounded-xl p-4 space-y-2 animate-pulse">
        <div className="h-3 bg-slate-200 dark:bg-slate-700 rounded w-1/3" />
        <div className="h-3 bg-slate-200 dark:bg-slate-700 rounded w-full" />
        <div className="h-3 bg-slate-200 dark:bg-slate-700 rounded w-5/6" />
        <div className="h-3 bg-slate-200 dark:bg-slate-700 rounded w-4/6" />
      </div>
    );
  }

  if (!explanation.explanation) return null;

  const hasWebSearch = explanation.model === "compound-beta" && explanation.sources.length > 0;

  return (
    <div className="bg-slate-100/60 dark:bg-slate-800/40 border border-slate-200 dark:border-slate-700/50 rounded-xl p-4 space-y-3">
      <div className="flex items-center gap-2">
        <Sparkles size={14} className="text-purple-400 shrink-0" />
        <span className="text-xs font-semibold text-slate-700 dark:text-slate-300">AI Analysis</span>
        {hasWebSearch && (
          <span className="ml-auto text-[10px] bg-blue-500/15 text-blue-400 border border-blue-500/25 px-2 py-0.5 rounded-full font-medium">
            🌐 Live web search
          </span>
        )}
      </div>
      <p className="text-sm text-slate-700 dark:text-slate-300 leading-relaxed">
        {explanation.explanation}
      </p>
      {explanation.sources.length > 0 && (
        <div className="pt-1 border-t border-slate-200 dark:border-slate-700/50 flex flex-wrap gap-2">
          {explanation.sources.slice(0, 3).map((src, i) => {
            const domain = (() => { try { return new URL(src).hostname.replace("www.", ""); } catch { return src; } })();
            return (
              <a key={i} href={src} target="_blank" rel="noopener noreferrer"
                className="flex items-center gap-1 text-[10px] text-slate-500 hover:text-blue-400 transition-colors">
                <ExternalLink size={9} />{domain}
              </a>
            );
          })}
        </div>
      )}
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
  const [explanation, setExplanation] = useState<MatchExplanation | null>(null);

  useEffect(() => {
    fetchMatchAnalysis(p.home, p.away)
      .then(setAnalysis)
      .catch(() => setError(true))
      .finally(() => setLoadingAnalysis(false));

    // Fetch AI explanation + qualitative context in parallel
    fetchExplanation(p.home, p.away)
      .then(setExplanation)
      .catch(() => setExplanation({ explanation: null, sources: [], model: null, error: "failed" }));
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
      <div className="w-full max-w-2xl my-8 bg-slate-50 dark:bg-slate-900 border border-slate-300 dark:border-slate-700 rounded-2xl overflow-hidden shadow-2xl">

        {/* Header */}
        <div className="bg-gradient-to-r from-slate-200 dark:from-slate-800 to-slate-50 dark:to-slate-900 border-b border-slate-300 dark:border-slate-700 p-5">
          <div className="flex items-start justify-between gap-4">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-3 flex-wrap">
                <span>{p.flag}</span>
                <span className="text-xs text-slate-500 dark:text-slate-400 font-medium">{p.league_name}</span>
                <span className="text-slate-400 dark:text-slate-600">·</span>
                <span className="text-xs text-slate-500 flex items-center gap-1">
                  <Clock size={10} /> {p.date}{p.time !== "TBD" ? ` · ${p.time}` : ""}
                </span>
              </div>
              <div className="flex items-center gap-4">
                <div className="flex-1 text-center">
                  <p className="text-lg font-bold text-slate-900 dark:text-white leading-tight">{p.home}</p>
                  <p className="text-xs text-slate-500 mt-0.5">Home</p>
                </div>
                <div className="text-slate-500 font-bold">vs</div>
                <div className="flex-1 text-center">
                  <p className="text-lg font-bold text-slate-900 dark:text-white leading-tight">{p.away}</p>
                  <p className="text-xs text-slate-500 mt-0.5">Away</p>
                </div>
              </div>
            </div>
            <button onClick={onClose} className="p-2 hover:bg-slate-300 dark:hover:bg-slate-700 rounded-lg transition-colors shrink-0">
              <X size={16} className="text-slate-500 dark:text-slate-400" />
            </button>
          </div>

          {/* 1X2 quick stats */}
          <div className="grid grid-cols-3 gap-2 mt-4">
            {[
              { label: "Home Win", value: `${Math.round(p.p_home * 100)}%`, color: "text-blue-400" },
              { label: "Draw",     value: `${Math.round(p.p_draw * 100)}%`, color: "text-slate-700 dark:text-slate-300" },
              { label: "Away Win", value: `${Math.round(p.p_away * 100)}%`, color: "text-purple-400" },
            ].map(({ label, value, color }) => (
              <div key={label} className="bg-slate-200/60 dark:bg-slate-700/40 rounded-lg px-3 py-2 text-center">
                <p className="text-xs text-slate-500">{label}</p>
                <p className={clsx("text-sm font-bold", color)}>{value}</p>
              </div>
            ))}
          </div>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-slate-200 dark:border-slate-800">
          <div className="flex-1 flex items-center justify-center gap-1.5 py-3 text-xs font-medium text-green-400 border-b-2 border-green-500 bg-green-500/5">
            <BarChart2 size={13} /> Analysis &amp; Markets
          </div>
        </div>

        {/* Body */}
        <div className="p-5 space-y-4">
          {loadingAnalysis && (
            <div className="space-y-3">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="bg-slate-200 dark:bg-slate-800 rounded-xl h-28 animate-pulse" />
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
                    <p className="text-slate-900 dark:text-white font-bold text-base truncate">{rec.label}</p>
                    <p className="text-xs text-slate-500 dark:text-slate-400">{rec.market} · {Math.round(rec.prob * 100)}% confidence</p>
                  </div>
                  <p className="text-2xl font-black text-green-400 shrink-0">{Math.round(rec.prob * 100)}%</p>
                </div>
              )}

              {/* AI Explanation */}
              <AIExplanation explanation={explanation} />

              {/* xG row */}
              <div className="grid grid-cols-2 gap-3">
                {[
                  { label: `${p.home} xG`, value: analysis.xg_home },
                  { label: `${p.away} xG`, value: analysis.xg_away },
                ].map(({ label, value }) => (
                  <div key={label} className="bg-slate-100/80 dark:bg-slate-800/60 border border-slate-200 dark:border-slate-700/50 rounded-xl px-4 py-3">
                    <p className="text-xs text-slate-500">{label}</p>
                    <p className="text-lg font-bold text-slate-900 dark:text-slate-100">{value.toFixed(2)}</p>
                    <p className="text-xs text-slate-400 dark:text-slate-600">Expected goals</p>
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
