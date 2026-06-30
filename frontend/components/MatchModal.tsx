"use client";

import { useEffect, useState } from "react";
import { fetchMatchAnalysis, fetchExplanation } from "@/lib/api";
import type { MatchAnalysis, Market, MatchExplanation } from "@/lib/api";
import type { Prediction } from "@/lib/api";
import { X, Star, BarChart2, Clock, Sparkles, ExternalLink, ShoppingCart, Loader2, Copy, Check, Ticket } from "lucide-react";
import clsx from "clsx";

const API = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";

interface SbOutcome { id: string; desc: string; odds: string; }
interface SbMarket  { id: string; name: string; specifier: string; outcomes: SbOutcome[]; }
interface SbEvent   { found: boolean; eventId: string; gameId: string; homeTeam: string; awayTeam: string; markets: SbMarket[]; }

interface SlipItem {
  matchId:      string;
  marketId:     string;
  marketName:   string;
  specifier:    string;
  outcomeId:    string;
  outcomeName:  string;
  homeTeamName: string;
  awayTeamName: string;
  odds:         string;
  status:       number;
}

interface Props {
  prediction: Prediction;
  onClose: () => void;
}

const MARKET_ICONS: Record<string, string> = {
  "1x2":         "⚽",
  double_chance: "🛡️",
  btts:          "🎯",
  goals_ou:      "📊",
  asian_handicap:"⚖️",
  half_time:     "⏱️",
  correct_score: "🔢",
  cards:         "🟨",
  win_to_nil:    "🔒",
  clean_sheet:   "🧤",
  result_btts:   "🎯⚽",
  draw_no_bet:   "🚫",
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

function MarketBlock({
  market, recommendedCode, sbMarket, onSelect, selectedOutcomeId,
}: {
  market: Market;
  recommendedCode?: string;
  sbMarket?: SbMarket;
  onSelect?: (item: Omit<SlipItem, "matchId" | "homeTeamName" | "awayTeamName">) => void;
  selectedOutcomeId?: string;
}) {
  const best = [...market.options].sort((a, b) => b.prob - a.prob)[0];

  // Map our option labels to SportyBet outcome descs for odds lookup
  const sbOddsFor = (optLabel: string): SbOutcome | undefined => {
    if (!sbMarket) return undefined;
    const label = optLabel.toLowerCase();
    return sbMarket.outcomes.find(o => {
      const d = o.desc.toLowerCase();
      return d === label || d.startsWith(label.split(" ")[0]);
    });
  };

  return (
    <div className="bg-slate-100/80 dark:bg-slate-800/60 border border-slate-200 dark:border-slate-700/50 rounded-xl p-4 space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-base">{MARKET_ICONS[market.id] || "📌"}</span>
        <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-200">{market.name}</h3>
        {sbMarket && <span className="ml-auto text-[10px] text-green-400 font-semibold">Live odds ✓</span>}
      </div>

      <div className="space-y-2">
        {market.options.map((opt) => {
          const sbOut = sbOddsFor(opt.label);
          const isSelected = sbOut ? selectedOutcomeId === sbOut.id : false;
          const canSelect = !!sbOut && !!onSelect;

          return (
            <div key={opt.code}
              onClick={() => {
                if (!canSelect || !sbMarket || !sbOut) return;
                onSelect({ marketId: sbMarket.id, marketName: sbMarket.name,
                           specifier: sbMarket.specifier, outcomeId: sbOut.id,
                           outcomeName: sbOut.desc, odds: sbOut.odds, status: 0 });
              }}
              className={clsx(
                "space-y-1 rounded-lg px-2 py-1.5 transition-all",
                canSelect ? "cursor-pointer" : "",
                isSelected
                  ? "bg-green-500/15 border border-green-500/40 ring-1 ring-green-500/30"
                  : canSelect ? "hover:bg-slate-200/60 dark:hover:bg-slate-700/40 border border-transparent" : ""
              )}
            >
              <div className="flex items-center gap-1.5">
                <span className={clsx("text-xs truncate flex-1",
                  isSelected ? "text-green-400 font-semibold" : "text-slate-700 dark:text-slate-300")}>
                  {opt.label}
                </span>
                {(sbOut?.odds || (opt as any).odds) && (
                  <span className={clsx("text-xs font-black shrink-0 px-2 py-0.5 rounded-lg",
                    isSelected
                      ? "bg-green-500 text-black"
                      : "bg-slate-300 dark:bg-slate-700 text-slate-800 dark:text-slate-200")}>
                    {sbOut?.odds || (opt as any).odds}
                  </span>
                )}
                {opt.code === recommendedCode && !isSelected && (
                  <span className="flex items-center gap-0.5 bg-green-500/20 text-green-300 text-[10px] font-bold px-1.5 py-0.5 rounded-full border border-green-500/30 shrink-0">
                    <Star size={8} /> TOP PICK
                  </span>
                )}
                {isSelected && <span className="text-green-400 text-xs font-bold shrink-0">✓ Added</span>}
              </div>
              <ProbBar prob={opt.prob} highlight={opt.code === best.code} />
            </div>
          );
        })}
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
        <span className="ml-auto text-[10px] px-2 py-0.5 rounded-full font-medium border
          border-blue-500/25 bg-blue-500/15 text-blue-400">
          {hasWebSearch ? "🌐 Live web search" : "📊 Stats only"}
        </span>
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
  const [sbEvent, setSbEvent] = useState<SbEvent | null>(null);
  const [slip, setSlip] = useState<SlipItem[]>([]);
  const [generating, setGenerating] = useState(false);
  const [bookingCode, setBookingCode] = useState<string | null>(null);
  const [codeCopied, setCodeCopied] = useState(false);

  useEffect(() => {
    fetchMatchAnalysis(p.home, p.away)
      .then(setAnalysis)
      .catch(() => setError(true))
      .finally(() => setLoadingAnalysis(false));

    fetchExplanation(p.home, p.away)
      .then(setExplanation)
      .catch(() => setExplanation({ explanation: null, sources: [], model: null, error: "failed" }));

    // Fetch live SportyBet event for real odds + market IDs
    fetch(`${API}/api/sportybet-event?home=${encodeURIComponent(p.home)}&away=${encodeURIComponent(p.away)}&date=${p.date}`)
      .then(r => r.json())
      .then(d => { if (d.found) setSbEvent(d); })
      .catch(() => {});
  }, [p.home, p.away, p.date]);

  const addToSlip = (matchId: string, homeTeam: string, awayTeam: string) =>
    (item: Omit<SlipItem, "matchId" | "homeTeamName" | "awayTeamName">) => {
      setSlip(prev => {
        // One selection per market — replace if same market already in slip
        const filtered = prev.filter(s => !(s.matchId === matchId && s.marketId === item.marketId));
        const existing = prev.find(s => s.matchId === matchId && s.marketId === item.marketId && s.outcomeId === item.outcomeId);
        if (existing) return filtered; // deselect if same outcome clicked again
        return [...filtered, { ...item, matchId, homeTeamName: homeTeam, awayTeamName: awayTeam }];
      });
      setBookingCode(null);
    };

  const generateCode = async () => {
    if (!slip.length) return;
    setGenerating(true);
    setBookingCode(null);
    try {
      const res = await fetch(`${API}/api/booking`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ selections: slip }),
      });
      const data = await res.json();
      setBookingCode(data.code || null);
    } catch {
      setBookingCode(null);
    } finally {
      setGenerating(false);
    }
  };

  const copyCode = () => {
    if (!bookingCode) return;
    navigator.clipboard.writeText(bookingCode);
    setCodeCopied(true);
    setTimeout(() => setCodeCopied(false), 2000);
  };

  const matchId = sbEvent?.eventId || "";

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

        {/* Body — extra bottom padding when slip is open */}
        <div className={clsx("p-5 space-y-4", slip.length > 0 && "pb-40")}>
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

              {/* Web search model adjustment badge */}
              {analysis.web_adjustment_reason && (
                <div className="flex items-start gap-2 bg-orange-500/10 border border-orange-500/25 rounded-xl px-3 py-2.5">
                  <span className="text-orange-400 text-sm shrink-0">🔍</span>
                  <div>
                    <p className="text-xs text-orange-300 font-semibold">Web search adjusted this prediction</p>
                    <p className="text-xs text-orange-200/70 mt-0.5">{analysis.web_adjustment_reason}</p>
                    {analysis.web_adjustment_flags && analysis.web_adjustment_flags.length > 0 && (
                      <div className="flex gap-1 flex-wrap mt-1">
                        {analysis.web_adjustment_flags.map(f => (
                          <span key={f} className="text-[10px] bg-orange-500/20 text-orange-300 px-1.5 py-0.5 rounded">
                            {f.replace(/_/g,' ')}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Live odds badge */}
              {analysis.live_odds_fetched && (
                <div className="flex items-center gap-2 bg-blue-500/10 border border-blue-500/25 rounded-xl px-3 py-2">
                  <span className="text-blue-400 text-sm">📡</span>
                  <p className="text-xs text-blue-300 font-medium">
                    Live odds fetched now from {analysis.odds_bookie || "market"} — model recalibrated in real time
                  </p>
                </div>
              )}

              {/* Team form */}
              {analysis.team_form && (
                <div className="grid grid-cols-2 gap-3">
                  {[
                    { team: p.home, form: analysis.team_form.home },
                    { team: p.away, form: analysis.team_form.away },
                  ].map(({ team, form }) => (
                    <div key={team} className="bg-slate-100/80 dark:bg-slate-800/60 border border-slate-200 dark:border-slate-700/50 rounded-xl px-4 py-3 space-y-2">
                      <p className="text-xs text-slate-500 font-semibold truncate">{team}</p>
                      {form?.available ? (
                        <>
                          <div className="flex gap-1">
                            {(form.form || "").split("").map((r, i) => (
                              <span key={i} className={clsx(
                                "text-[10px] font-black w-5 h-5 rounded flex items-center justify-center",
                                r === "W" ? "bg-green-500/20 text-green-400" :
                                r === "D" ? "bg-yellow-500/20 text-yellow-400" :
                                            "bg-red-500/20 text-red-400"
                              )}>{r}</span>
                            ))}
                          </div>
                          <div className="grid grid-cols-2 gap-x-3 text-[10px] text-slate-500">
                            {form.goals_scored != null && (
                              <span>⚽ Scored: <span className="text-white font-semibold">{form.goals_scored}/g</span></span>
                            )}
                            {form.goals_conceded != null && (
                              <span>🧤 Conceded: <span className="text-white font-semibold">{form.goals_conceded}/g</span></span>
                            )}
                            <span>Elo: <span className="text-white font-semibold">{form.elo}</span></span>
                            {form.games > 0 && <span>{form.wins}W {form.draws}D {form.losses}L</span>}
                          </div>
                          {form.games > 0
                            ? <p className="text-[9px] text-slate-600">Last {form.games} games · Updated every 3h</p>
                            : <p className="text-[9px] text-slate-500">Match stats loading — Elo rating shown</p>
                          }
                        </>
                      ) : (
                        <p className="text-[10px] text-slate-600">Form data not yet available</p>
                      )}
                    </div>
                  ))}
                </div>
              )}

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
              {sbEvent && (
                <div className="flex items-center gap-2 bg-green-500/10 border border-green-500/25 rounded-xl px-3 py-2">
                  <span className="text-green-400 text-sm">🎯</span>
                  <p className="text-xs text-green-300 font-medium">
                    Live SportyBet odds loaded — click any outcome to add to your bet slip
                  </p>
                </div>
              )}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {analysis.markets.map((m) => {
                  // Match our analysis market to SportyBet market
                  const sbM = sbEvent?.markets.find(sb => {
                    const sid = sb.id;
                    const mn = sb.name.toLowerCase();
                    if (m.id === "1x2" && sid === "1") return true;
                    if (m.id === "btts" && sid === "29") return true;
                    if (m.id === "double_chance" && sid === "10") return true;
                    if (m.id === "goals_ou" && sid === "18" && sb.specifier === "total=2.5") return true;
                    return false;
                  });
                  const selectedId = slip.find(s => s.marketId === sbM?.id && s.matchId === matchId)?.outcomeId;
                  return (
                    <MarketBlock
                      key={m.id}
                      market={m}
                      recommendedCode={rec?.market_id === m.id ? rec.code : undefined}
                      sbMarket={sbM}
                      onSelect={sbEvent ? addToSlip(matchId, sbEvent.homeTeam, sbEvent.awayTeam) : undefined}
                      selectedOutcomeId={selectedId}
                    />
                  );
                })}
              </div>
            </>
          )}
        </div>

        {/* ── Sticky Bet Slip ── */}
        {slip.length > 0 && (
          <div className="sticky bottom-0 bg-slate-900/95 border-t border-slate-700 backdrop-blur-md p-4 space-y-3">
            {/* Selections */}
            <div className="space-y-1.5 max-h-28 overflow-y-auto">
              {slip.map((s, i) => (
                <div key={i} className="flex items-center justify-between gap-2 text-xs">
                  <span className="text-slate-400 truncate flex-1">
                    {s.marketName}{s.specifier ? ` (${s.specifier})` : ""}
                  </span>
                  <span className="text-white font-semibold shrink-0">{s.outcomeName}</span>
                  <span className="text-green-400 font-black shrink-0">{s.odds}</span>
                  <button onClick={() => setSlip(prev => prev.filter((_, j) => j !== i))}
                    className="text-slate-600 hover:text-red-400 transition-colors shrink-0 text-base leading-none ml-1">×</button>
                </div>
              ))}
            </div>

            {/* Combined odds + actions */}
            <div className="flex items-center gap-3">
              <div className="flex-1">
                <p className="text-[10px] text-slate-500">Combined odds</p>
                <p className="text-lg font-black text-green-400">
                  {slip.reduce((acc, s) => {
                    const o = parseFloat(s.odds);
                    return isNaN(o) ? acc : +(acc * o).toFixed(2);
                  }, 1)}x
                </p>
              </div>

              <button onClick={() => { setSlip([]); setBookingCode(null); }}
                className="text-xs text-slate-500 hover:text-slate-300 transition-colors px-2">
                Clear
              </button>

              {bookingCode ? (
                <div className="flex items-center gap-2 bg-green-500/15 border border-green-500/30 rounded-xl px-3 py-2">
                  <span className="text-white font-black tracking-widest text-sm font-mono">{bookingCode}</span>
                  <button onClick={copyCode}
                    className="flex items-center gap-1 bg-green-500 hover:bg-green-400 text-black text-xs font-bold px-2.5 py-1.5 rounded-lg transition-colors">
                    {codeCopied ? <Check size={11} /> : <Copy size={11} />}
                    {codeCopied ? "Copied!" : "Copy"}
                  </button>
                </div>
              ) : (
                <button onClick={generateCode} disabled={generating}
                  className="flex items-center gap-2 bg-green-500 hover:bg-green-400 disabled:opacity-60 text-black font-bold text-sm px-4 py-2.5 rounded-xl transition-colors">
                  {generating ? <Loader2 size={14} className="animate-spin" /> : <Ticket size={14} />}
                  {generating ? "Generating…" : `Get Code (${slip.length})`}
                </button>
              )}
            </div>

            <p className="text-[10px] text-slate-600 text-center">
              {bookingCode
                ? "Paste this code on SportyBet to load your bet slip"
                : "Uses live SportyBet market IDs for accurate booking codes"}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
