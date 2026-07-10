"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { fetchMatchAnalysis, fetchExplanation } from "@/lib/api";
import type { MatchAnalysis, Market, MatchExplanation, Prediction } from "@/lib/api";
import { TeamBadge } from "@/components/PredictionCard";
import { AppShell } from "@/components/shell/AppShell";
import {
  ArrowLeft, Star, Clock, Sparkles, ExternalLink, Loader2, Copy, Check, Ticket,
  Trophy, Gauge, Radio, Search,
} from "lucide-react";
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

// ------------------------------------------------------------------ //
// Elo Gauge
// ------------------------------------------------------------------ //
function EloGauge({ elo, home, away }: { elo: MatchAnalysis["elo"]; home: string; away: string }) {
  const absGap = Math.abs(elo.gap);
  const cappedGap = Math.min(absGap, elo.gap_out_of);
  const pct = (cappedGap / elo.gap_out_of) * 100;
  const homeLeads = elo.gap >= 0;

  const labelColor =
    absGap < 30 ? "text-zinc-500 dark:text-zinc-400" :
    absGap < 80 ? "text-sky-600 dark:text-sky-400" :
    absGap < 150 ? "text-amber-600 dark:text-amber-400" :
    absGap < 250 ? "text-orange-600 dark:text-orange-400" : "text-rose-600 dark:text-rose-400";

  const barColor =
    absGap < 30 ? "bg-zinc-300 dark:bg-zinc-600" :
    absGap < 80 ? "bg-sky-500" :
    absGap < 150 ? "bg-amber-500" :
    absGap < 250 ? "bg-orange-500" : "bg-rose-500";

  return (
    <div className="card p-4 space-y-3">
      <div className="flex items-center gap-2">
        <Trophy size={15} className="text-amber-500" />
        <h3 className="text-sm font-bold text-zinc-800 dark:text-zinc-200">Elo Rating</h3>
        <span className={clsx("ml-auto text-xs font-bold px-2.5 py-0.5 rounded-full bg-zinc-100 dark:bg-zinc-800", labelColor)}>
          {elo.label}
        </span>
      </div>

      {/* Team ratings */}
      <div className="grid grid-cols-3 gap-2 text-center">
        <div>
          <p className="text-xs text-zinc-400 dark:text-zinc-500 truncate">{home}</p>
          <p className="tnum text-lg font-black text-zinc-900 dark:text-zinc-100">{elo.home}</p>
        </div>
        <div className="flex flex-col items-center justify-center">
          <p className="text-[10px] text-zinc-400 dark:text-zinc-500 uppercase tracking-wide">Gap</p>
          <p className={clsx("tnum text-base font-black", labelColor)}>
            {absGap > 0 ? (homeLeads ? "+" : "−") : ""}{absGap}
          </p>
        </div>
        <div>
          <p className="text-xs text-zinc-400 dark:text-zinc-500 truncate">{away}</p>
          <p className="tnum text-lg font-black text-zinc-900 dark:text-zinc-100">{elo.away}</p>
        </div>
      </div>

      {/* Gap bar */}
      <div>
        <div className="h-2 bg-zinc-100 dark:bg-zinc-800 rounded-full overflow-hidden">
          <div className={clsx("h-full rounded-full transition-all duration-700", barColor)} style={{ width: `${pct}%` }} />
        </div>
        <p className="text-[10px] text-zinc-400 dark:text-zinc-600 mt-1 text-center">
          Elo gap scale · {elo.gap_out_of} pts ≈ 91% win probability
        </p>
      </div>

      {/* Implied prob */}
      <div className="bg-zinc-50 dark:bg-zinc-800/60 rounded-xl px-3 py-2.5">
        <p className="text-xs text-zinc-600 dark:text-zinc-300 leading-relaxed">
          <span className="font-semibold text-zinc-900 dark:text-white">{elo.leading}</span> have a{" "}
          <span className={clsx("tnum font-bold", labelColor)}>{Math.round(elo.implied_win_prob * 100)}%</span>{" "}
          implied win probability from Elo alone.{" "}
          <span className="text-zinc-400 dark:text-zinc-500">{elo.description}</span>
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
      <div className="flex-1 h-1.5 bg-zinc-100 dark:bg-zinc-800 rounded-full overflow-hidden">
        <div
          className={clsx("h-full rounded-full transition-all duration-500",
            highlight ? "bg-brand-500" : "bg-zinc-300 dark:bg-zinc-600")}
          style={{ width: `${Math.round(prob * 100)}%` }}
        />
      </div>
      <span className={clsx("tnum text-xs font-semibold w-9 text-right",
        highlight ? "text-brand-600 dark:text-brand-400" : "text-zinc-400 dark:text-zinc-500")}>
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
    <div className="card p-4 space-y-3">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-bold text-zinc-800 dark:text-zinc-200">{market.name}</h3>
        {sbMarket && (
          <span className="ml-auto inline-flex items-center gap-1 text-[10px] text-brand-600 dark:text-brand-400 font-semibold">
            <Radio size={10} /> Live odds
          </span>
        )}
      </div>

      <div className="space-y-1.5">
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
                "space-y-1 rounded-lg px-2 py-1.5 transition-all border",
                canSelect ? "cursor-pointer" : "",
                isSelected
                  ? "bg-brand-50 dark:bg-brand-900/20 border-brand-300 dark:border-brand-700"
                  : canSelect
                    ? "hover:bg-zinc-50 dark:hover:bg-zinc-800/60 border-transparent"
                    : "border-transparent"
              )}
            >
              <div className="flex items-center gap-1.5">
                <span className={clsx("text-xs truncate flex-1",
                  isSelected ? "text-brand-700 dark:text-brand-400 font-semibold" : "text-zinc-600 dark:text-zinc-300")}>
                  {opt.label}
                </span>
                {(sbOut?.odds || (opt as any).odds) && (
                  <span className={clsx("tnum text-xs font-bold shrink-0 px-2 py-0.5 rounded-md",
                    isSelected
                      ? "bg-brand-600 text-white"
                      : "bg-zinc-100 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-200")}>
                    {sbOut?.odds || (opt as any).odds}
                  </span>
                )}
                {opt.code === recommendedCode && !isSelected && (
                  <span className="flex items-center gap-0.5 bg-brand-50 dark:bg-brand-900/30 text-brand-700 dark:text-brand-400 text-[9px] font-bold px-1.5 py-0.5 rounded-full border border-brand-200 dark:border-brand-800 shrink-0 uppercase tracking-wide">
                    <Star size={8} /> Top pick
                  </span>
                )}
                {isSelected && <Check size={13} className="text-brand-600 dark:text-brand-400 shrink-0" />}
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
      <div className="card p-4 space-y-2 animate-pulse">
        <div className="h-3 bg-zinc-100 dark:bg-zinc-800 rounded-full w-1/3" />
        <div className="h-3 bg-zinc-100 dark:bg-zinc-800 rounded-full w-full" />
        <div className="h-3 bg-zinc-100 dark:bg-zinc-800 rounded-full w-5/6" />
      </div>
    );
  }

  if (!explanation.explanation) return null;

  const hasWebSearch = !!explanation.model?.startsWith("compound-beta") && explanation.sources.length > 0;

  return (
    <div className="card p-4 space-y-3">
      <div className="flex items-center gap-2">
        <Sparkles size={14} className="text-violet-500 shrink-0" />
        <span className="text-sm font-bold text-zinc-800 dark:text-zinc-200">AI Analysis</span>
        <span className="ml-auto text-[10px] px-2 py-0.5 rounded-full font-medium bg-sky-50 dark:bg-sky-500/10 text-sky-700 dark:text-sky-400 border border-sky-200 dark:border-sky-500/25">
          {hasWebSearch ? "Live web search" : "Stats only"}
        </span>
      </div>
      <p className="text-sm text-zinc-600 dark:text-zinc-300 leading-relaxed">
        {explanation.explanation}
      </p>
      {explanation.sources.length > 0 && (
        <div className="pt-2 border-t border-zinc-100 dark:border-zinc-800 flex flex-wrap gap-3">
          {explanation.sources.slice(0, 3).map((src, i) => {
            const domain = (() => { try { return new URL(src).hostname.replace("www.", ""); } catch { return src; } })();
            return (
              <a key={i} href={src} target="_blank" rel="noopener noreferrer"
                className="flex items-center gap-1 text-[11px] text-zinc-400 hover:text-sky-600 dark:hover:text-sky-400 transition-colors">
                <ExternalLink size={10} />{domain}
              </a>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ //
// Page
// ------------------------------------------------------------------ //
function MatchContent() {
  const router = useRouter();
  const params = useSearchParams();
  const home = params.get("home") ?? "";
  const away = params.get("away") ?? "";
  const date = params.get("date") ?? "";

  const [prediction, setPrediction] = useState<Prediction | null>(null);
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
    if (!home || !away) return;

    fetchMatchAnalysis(home, away)
      .then(setAnalysis)
      .catch(() => setError(true))
      .finally(() => setLoadingAnalysis(false));

    fetchExplanation(home, away)
      .then(setExplanation)
      .catch(() => setExplanation({ explanation: null, sources: [], model: null, error: "failed" }));

    // Find the prediction entry for header probabilities
    fetch(`${API}/api/predictions?limit=500`)
      .then(r => r.json())
      .then(d => {
        const preds: Prediction[] = d.predictions ?? [];
        const found = preds.find(p =>
          p.home === home && p.away === away && (!date || p.date === date)
        );
        if (found) setPrediction(found);
      })
      .catch(() => {});

    // Fetch live SportyBet event for real odds + market IDs
    fetch(`${API}/api/sportybet-event?home=${encodeURIComponent(home)}&away=${encodeURIComponent(away)}&date=${date}`)
      .then(r => r.json())
      .then(d => { if (d.found) setSbEvent(d); })
      .catch(() => {});
  }, [home, away, date]);

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
  const rec = analysis?.recommended;

  if (!home || !away) {
    return (
      <div className="text-center py-20 space-y-3">
        <p className="text-zinc-500 dark:text-zinc-400 font-medium">No match selected.</p>
        <button onClick={() => router.push("/")} className="btn-secondary">
          <ArrowLeft size={14} /> Back to predictions
        </button>
      </div>
    );
  }

  return (
    <div className={clsx("space-y-4 animate-fade-in", slip.length > 0 && "pb-36")}>
      {/* Back */}
      <button
        onClick={() => router.back()}
        className="inline-flex items-center gap-1.5 text-sm font-medium text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 transition-colors"
      >
        <ArrowLeft size={15} /> Back
      </button>

      {/* Match header */}
      <div className="card p-5 sm:p-6">
        <div className="flex items-center gap-2 text-xs text-zinc-400 dark:text-zinc-500 mb-4">
          {prediction?.flag && <span>{prediction.flag}</span>}
          <span className="font-semibold uppercase tracking-wide">{prediction?.league_name ?? "Match analysis"}</span>
          <span>·</span>
          <span className="flex items-center gap-1 tnum">
            <Clock size={11} /> {date || prediction?.date}
            {prediction?.time && prediction.time !== "TBD" ? ` · ${prediction.time}` : ""}
          </span>
        </div>

        <div className="flex items-center gap-4 sm:gap-8">
          <div className="flex-1 flex flex-col items-center text-center gap-2">
            <TeamBadge name={home} size={52} />
            <div>
              <p className="text-base sm:text-lg font-bold text-zinc-900 dark:text-white leading-tight">{home}</p>
              <p className="text-[11px] text-zinc-400 dark:text-zinc-500">Home</p>
            </div>
          </div>
          <div className="text-zinc-300 dark:text-zinc-600 font-black text-lg">vs</div>
          <div className="flex-1 flex flex-col items-center text-center gap-2">
            <TeamBadge name={away} size={52} />
            <div>
              <p className="text-base sm:text-lg font-bold text-zinc-900 dark:text-white leading-tight">{away}</p>
              <p className="text-[11px] text-zinc-400 dark:text-zinc-500">Away</p>
            </div>
          </div>
        </div>

        {/* 1X2 quick stats */}
        {prediction && (
          <div className="grid grid-cols-3 gap-2 mt-5">
            {[
              { label: "Home win", value: Math.round(prediction.p_home * 100), tint: "text-brand-600 dark:text-brand-400" },
              { label: "Draw",     value: Math.round(prediction.p_draw * 100), tint: "text-zinc-600 dark:text-zinc-300" },
              { label: "Away win", value: Math.round(prediction.p_away * 100), tint: "text-sky-600 dark:text-sky-400" },
            ].map(({ label, value, tint }) => (
              <div key={label} className="bg-zinc-50 dark:bg-zinc-800/60 rounded-xl px-3 py-2.5 text-center">
                <p className="text-[11px] text-zinc-400 dark:text-zinc-500 font-medium">{label}</p>
                <p className={clsx("tnum text-lg font-black", tint)}>{value}%</p>
              </div>
            ))}
          </div>
        )}
      </div>

      {loadingAnalysis && (
        <div className="space-y-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="card h-28 animate-pulse !bg-zinc-100 dark:!bg-zinc-900" />
          ))}
        </div>
      )}
      {error && (
        <p className="text-center py-8 text-zinc-400 dark:text-zinc-500">
          Could not load analysis. Is the backend running?
        </p>
      )}
      {analysis && (
        <>
          {/* Best Pick banner */}
          {rec && (
            <div className="card !border-brand-200 dark:!border-brand-800 !bg-brand-50/60 dark:!bg-brand-900/15 p-4 flex items-center gap-4">
              <div className="w-10 h-10 bg-brand-100 dark:bg-brand-900/40 rounded-full flex items-center justify-center shrink-0">
                <Star size={18} className="text-brand-600 dark:text-brand-400" />
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-[10px] text-brand-700 dark:text-brand-400 font-bold uppercase tracking-wider mb-0.5">Best pick</p>
                <p className="text-zinc-900 dark:text-white font-bold text-base truncate">{rec.label}</p>
                <p className="text-xs text-zinc-500 dark:text-zinc-400">{rec.market}</p>
              </div>
              <p className="tnum text-2xl font-black text-brand-600 dark:text-brand-400 shrink-0">{Math.round(rec.prob * 100)}%</p>
            </div>
          )}

          {/* AI Explanation */}
          <AIExplanation explanation={explanation} />

          {/* Web search model adjustment badge */}
          {analysis.web_adjustment_reason && (
            <div className="card !border-orange-200 dark:!border-orange-500/25 !bg-orange-50/60 dark:!bg-orange-500/10 px-4 py-3 flex items-start gap-2.5">
              <Search size={14} className="text-orange-500 mt-0.5 shrink-0" />
              <div>
                <p className="text-xs text-orange-700 dark:text-orange-300 font-semibold">Web search adjusted this prediction</p>
                <p className="text-xs text-orange-600/80 dark:text-orange-200/70 mt-0.5">{analysis.web_adjustment_reason}</p>
                {analysis.web_adjustment_flags && analysis.web_adjustment_flags.length > 0 && (
                  <div className="flex gap-1 flex-wrap mt-1.5">
                    {analysis.web_adjustment_flags.map(f => (
                      <span key={f} className="text-[10px] bg-orange-100 dark:bg-orange-500/20 text-orange-700 dark:text-orange-300 px-1.5 py-0.5 rounded-md">
                        {f.replace(/_/g, " ")}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Live odds badge */}
          {analysis.live_odds_fetched && (
            <div className="card !border-sky-200 dark:!border-sky-500/25 !bg-sky-50/60 dark:!bg-sky-500/10 px-4 py-2.5 flex items-center gap-2">
              <Radio size={14} className="text-sky-500" />
              <p className="text-xs text-sky-700 dark:text-sky-300 font-medium">
                Live odds fetched from {analysis.odds_bookie || "market"} — model recalibrated in real time
              </p>
            </div>
          )}

          {/* Team form */}
          {analysis.team_form && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {[
                { team: home, form: analysis.team_form.home },
                { team: away, form: analysis.team_form.away },
              ].map(({ team, form }) => (
                <div key={team} className="card px-4 py-3.5 space-y-2.5">
                  <div className="flex items-center gap-2">
                    <TeamBadge name={team} size={22} />
                    <p className="text-xs font-bold text-zinc-700 dark:text-zinc-300 truncate">{team}</p>
                  </div>
                  {form?.available ? (
                    <>
                      <div className="flex gap-1">
                        {(form.form || "").split("").map((r, i) => (
                          <span key={i} className={clsx(
                            "text-[10px] font-black w-6 h-6 rounded-md flex items-center justify-center",
                            r === "W" ? "bg-brand-100 dark:bg-brand-500/20 text-brand-700 dark:text-brand-400" :
                            r === "D" ? "bg-amber-100 dark:bg-amber-500/20 text-amber-700 dark:text-amber-400" :
                                        "bg-rose-100 dark:bg-rose-500/20 text-rose-700 dark:text-rose-400"
                          )}>{r}</span>
                        ))}
                      </div>
                      <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] text-zinc-400 dark:text-zinc-500">
                        {form.goals_scored != null && (
                          <span>Scored <span className="tnum text-zinc-800 dark:text-zinc-200 font-semibold">{form.goals_scored}/g</span></span>
                        )}
                        {form.goals_conceded != null && (
                          <span>Conceded <span className="tnum text-zinc-800 dark:text-zinc-200 font-semibold">{form.goals_conceded}/g</span></span>
                        )}
                        {form.xg_for != null && (
                          <span>xG for <span className="tnum text-sky-600 dark:text-sky-400 font-semibold">{form.xg_for}</span></span>
                        )}
                        {form.xg_against != null && (
                          <span>xG against <span className="tnum text-orange-600 dark:text-orange-400 font-semibold">{form.xg_against}</span></span>
                        )}
                        <span>Elo <span className="tnum text-zinc-800 dark:text-zinc-200 font-semibold">{form.elo}</span></span>
                        {(form.games ?? 0) > 0 && <span className="tnum">{form.wins}W {form.draws}D {form.losses}L</span>}
                      </div>
                    </>
                  ) : (
                    <p className="text-[11px] text-zinc-400 dark:text-zinc-600">Form data not yet available</p>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* xG row */}
          <div className="grid grid-cols-2 gap-3">
            {[
              { label: `${home} xG`, value: analysis.xg_home },
              { label: `${away} xG`, value: analysis.xg_away },
            ].map(({ label, value }) => (
              <div key={label} className="card px-4 py-3.5 flex items-center gap-3">
                <span className="inline-flex items-center justify-center w-9 h-9 rounded-xl bg-violet-50 dark:bg-violet-500/10 text-violet-600 dark:text-violet-400 shrink-0">
                  <Gauge size={17} />
                </span>
                <div className="min-w-0">
                  <p className="text-[11px] text-zinc-400 dark:text-zinc-500 font-medium truncate">{label}</p>
                  <p className="tnum text-lg font-black text-zinc-900 dark:text-zinc-100 leading-tight">{value.toFixed(2)}</p>
                </div>
              </div>
            ))}
          </div>

          {/* Elo gauge */}
          <EloGauge elo={analysis.elo} home={home} away={away} />

          {/* SportyBet hint */}
          {sbEvent && (
            <div className="card !border-brand-200 dark:!border-brand-800 !bg-brand-50/60 dark:!bg-brand-900/15 px-4 py-2.5 flex items-center gap-2">
              <Ticket size={14} className="text-brand-600 dark:text-brand-400" />
              <p className="text-xs text-brand-700 dark:text-brand-300 font-medium">
                Live SportyBet odds loaded — tap any outcome to add it to your bet slip
              </p>
            </div>
          )}

          {/* Markets grid */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {analysis.markets.map((m) => {
              // Match our analysis market to SportyBet market
              const sbM = sbEvent?.markets.find(sb => {
                const sid = sb.id;
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

      {/* ── Floating Bet Slip ── */}
      {slip.length > 0 && (
        <div className="fixed bottom-16 lg:bottom-4 inset-x-3 lg:left-[calc(15rem+1rem)] lg:right-4 z-40 max-w-3xl mx-auto card !rounded-2xl shadow-pop p-4 space-y-3 animate-slide-up">
          {/* Selections */}
          <div className="space-y-1.5 max-h-28 overflow-y-auto">
            {slip.map((s, i) => (
              <div key={i} className="flex items-center justify-between gap-2 text-xs">
                <span className="text-zinc-400 dark:text-zinc-500 truncate flex-1">
                  {s.marketName}{s.specifier ? ` (${s.specifier})` : ""}
                </span>
                <span className="text-zinc-900 dark:text-white font-semibold shrink-0">{s.outcomeName}</span>
                <span className="tnum text-brand-600 dark:text-brand-400 font-black shrink-0">{s.odds}</span>
                <button onClick={() => setSlip(prev => prev.filter((_, j) => j !== i))}
                  className="text-zinc-300 dark:text-zinc-600 hover:text-rose-500 transition-colors shrink-0 text-base leading-none ml-1">×</button>
              </div>
            ))}
          </div>

          {/* Combined odds + actions */}
          <div className="flex items-center gap-3 pt-1 border-t border-zinc-100 dark:border-zinc-800">
            <div className="flex-1">
              <p className="text-[10px] text-zinc-400 dark:text-zinc-500 font-medium">Combined odds</p>
              <p className="tnum text-lg font-black text-brand-600 dark:text-brand-400">
                {slip.reduce((acc, s) => {
                  const o = parseFloat(s.odds);
                  return isNaN(o) ? acc : +(acc * o).toFixed(2);
                }, 1)}x
              </p>
            </div>

            <button onClick={() => { setSlip([]); setBookingCode(null); }}
              className="text-xs text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300 transition-colors px-2 font-medium">
              Clear
            </button>

            {bookingCode ? (
              <div className="flex items-center gap-2 bg-brand-50 dark:bg-brand-900/20 border border-brand-200 dark:border-brand-800 rounded-xl px-3 py-2">
                <span className="text-zinc-900 dark:text-white font-black tracking-widest text-sm font-mono">{bookingCode}</span>
                <button onClick={copyCode} className="btn-primary !px-2.5 !py-1.5 !text-xs">
                  {codeCopied ? <Check size={11} /> : <Copy size={11} />}
                  {codeCopied ? "Copied!" : "Copy"}
                </button>
              </div>
            ) : (
              <button onClick={generateCode} disabled={generating} className="btn-primary">
                {generating ? <Loader2 size={14} className="animate-spin" /> : <Ticket size={14} />}
                {generating ? "Generating…" : `Get code (${slip.length})`}
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default function MatchPage() {
  return (
    <AppShell>
      <Suspense
        fallback={
          <div className="flex items-center justify-center py-24">
            <div className="w-8 h-8 border-2 border-brand-600 border-t-transparent rounded-full animate-spin" />
          </div>
        }
      >
        <MatchContent />
      </Suspense>
    </AppShell>
  );
}
