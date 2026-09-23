"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { fetchMatchAnalysis, fetchExplanation } from "@/lib/api";
import type { MatchAnalysis, Market, MatchExplanation, Prediction, TeamForm } from "@/lib/api";
import { kickoff } from "@/lib/matchTime";
import { TeamBadge } from "@/components/PredictionCard";
import { CompetitionBadge } from "@/components/CompetitionBadge";
import { AppShell } from "@/components/shell/AppShell";
import { useBetSlip } from "@/lib/useBetSlip";
import { bookableOnSportybet, isSelected } from "@/lib/slip";
import {
  ArrowLeft, Sparkles, ExternalLink, Check, Ticket, Radio, Search,
} from "lucide-react";
import clsx from "clsx";

const API = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";

interface SbOutcome { id: string; desc: string; odds: string; }
interface SbMarket  { id: string; name: string; specifier: string; outcomes: SbOutcome[]; }
interface SbEvent   { found: boolean; eventId: string; gameId: string; homeTeam: string; awayTeam: string; markets: SbMarket[]; }


function PanelTitle({ children, right }: { children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2 mb-3">
      <h2 className="font-display font-bold text-lg uppercase tracking-[0.06em] text-n-0">{children}</h2>
      {right && <div className="ml-auto">{right}</div>}
    </div>
  );
}

// ------------------------------------------------------------------ //
// Tale of the tape — Elo, xG and form, side by side
// ------------------------------------------------------------------ //
function FormChips({ form }: { form?: string }) {
  if (!form) return <span className="text-n-600">—</span>;
  return (
    <span className="inline-flex gap-0.5">
      {form.split("").map((r, i) => (
        <span key={i} className={clsx(
          "font-display font-bold text-[11px] w-[18px] h-[18px] rounded flex items-center justify-center",
          r === "W" ? "bg-brand-400 text-ink" : r === "D" ? "bg-zinc-500 text-white" : "bg-rose-500 text-white"
        )}>{r}</span>
      ))}
    </span>
  );
}

/** One comparison row. `better` says which side the stat favours (higher or lower is better). */
function TapeRow({ label, home, away, better = "higher", format = (v: number) => v.toFixed(2) }: {
  label: string; home?: number | null; away?: number | null;
  better?: "higher" | "lower"; format?: (v: number) => string;
}) {
  if (home == null && away == null) return null;
  const h = home ?? 0, a = away ?? 0;
  const homeWins = home != null && away != null && (better === "higher" ? h > a : h < a);
  const awayWins = home != null && away != null && (better === "higher" ? a > h : a < h);
  const total = Math.abs(h) + Math.abs(a) || 1;
  return (
    <div className="py-2.5">
      <div className="flex items-center justify-between text-sm">
        <span className={clsx("font-display font-bold text-xl tnum", homeWins ? "text-n-0" : "text-n-500")}>
          {home != null ? format(home) : "—"}
        </span>
        <span className="eyebrow">{label}</span>
        <span className={clsx("font-display font-bold text-xl tnum", awayWins ? "text-n-0" : "text-n-500")}>
          {away != null ? format(away) : "—"}
        </span>
      </div>
      <div className="flex gap-1 mt-1.5">
        <div className="flex-1 flex justify-end h-1 bg-n-800 rounded-full overflow-hidden">
          <div className={homeWins ? "bg-accent" : "bg-n-600"} style={{ width: `${(Math.abs(h) / total) * 100}%` }} />
        </div>
        <div className="flex-1 h-1 bg-n-800 rounded-full overflow-hidden">
          <div className={clsx("h-full", awayWins ? "bg-accent" : "bg-n-600")} style={{ width: `${(Math.abs(a) / total) * 100}%` }} />
        </div>
      </div>
    </div>
  );
}

function TaleOfTheTape({ analysis, home, away }: { analysis: MatchAnalysis; home: string; away: string }) {
  const elo = analysis.elo;
  const hf: TeamForm | undefined = analysis.team_form?.home;
  const af: TeamForm | undefined = analysis.team_form?.away;
  const int = (v: number) => String(Math.round(v));

  return (
    <div className="card p-4">
      <PanelTitle
        right={<span className="text-[11px] font-bold uppercase tracking-wider text-accent bg-brand-400/10 border border-brand-400/25 rounded-md px-2 py-0.5">{elo.label}</span>}
      >
        Tale of the tape
      </PanelTitle>

      <div className="flex items-center justify-between gap-2 pb-2 border-b border-n-800">
        <span className="flex items-center gap-2 min-w-0"><TeamBadge name={home} size={22} /><span className="text-xs font-bold text-n-0 truncate">{home}</span></span>
        <span className="flex items-center gap-2 min-w-0 flex-row-reverse"><TeamBadge name={away} size={22} /><span className="text-xs font-bold text-n-0 truncate">{away}</span></span>
      </div>

      <div className="divide-y divide-n-800/70">
        <TapeRow label="Elo rating" home={elo.home} away={elo.away} format={int} />
        <TapeRow label="Model xG" home={analysis.xg_home} away={analysis.xg_away} />
        {hf?.available && af?.available && <>
          <TapeRow label="Goals / game" home={hf.goals_scored} away={af.goals_scored} />
          <TapeRow label="Conceded / game" home={hf.goals_conceded} away={af.goals_conceded} better="lower" />
          <TapeRow label="xG for" home={hf.xg_for} away={af.xg_for} />
          <TapeRow label="xG against" home={hf.xg_against} away={af.xg_against} better="lower" />
          <div className="flex items-center justify-between py-3">
            <FormChips form={hf.form} />
            <span className="eyebrow">Last {Math.max(hf.games ?? 0, af.games ?? 0) || 5}</span>
            <FormChips form={af.form} />
          </div>
        </>}
      </div>

      <p className="text-xs text-n-400 leading-relaxed bg-n-800/40 rounded-lg px-3 py-2.5 mt-1">
        <span className="font-semibold text-n-0">{elo.leading}</span> hold a{" "}
        <span className="font-mono text-accent">{Math.abs(elo.gap)}</span>-point Elo edge, worth a{" "}
        <span className="font-mono text-accent">{Math.round(elo.implied_win_prob * 100)}%</span> win chance on ratings alone.{" "}
        <span className="text-n-500">{elo.description}</span>
      </p>
    </div>
  );
}

// ------------------------------------------------------------------ //
// Market block
// ------------------------------------------------------------------ //
function MarketBlock({
  market, recommendedCode, sbMarket, onToggle, isInSlip,
}: {
  market: Market;
  recommendedCode?: string;
  sbMarket?: SbMarket;
  /** Adds/removes an option on the bet slip; absent when the match date is unknown. */
  onToggle?: (opt: Market["options"][number]) => void;
  isInSlip: (code: string) => boolean;
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
    <div className="card p-4">
      <PanelTitle
        right={sbMarket && (
          <span className="inline-flex items-center gap-1 text-[10px] text-accent font-bold uppercase tracking-wider">
            <Radio size={10} /> Live odds
          </span>
        )}
      >
        {market.name}
      </PanelTitle>

      <div className="space-y-1">
        {market.options.map((opt) => {
          const sbOut = sbOddsFor(opt.label);
          const isSelected = isInSlip(opt.code);
          const canSelect = !!onToggle;
          const autoBook = bookableOnSportybet({ market: market.id, code: opt.code });
          const isBest = opt.code === best.code;
          const odds = sbOut?.odds || (opt as any).odds;
          const pct = Math.round(opt.prob * 100);

          return (
            <div key={opt.code}
              role={canSelect ? "button" : undefined}
              tabIndex={canSelect ? 0 : undefined}
              aria-pressed={canSelect ? isSelected : undefined}
              aria-label={canSelect ? `${isSelected ? "Remove" : "Add"} ${market.name}: ${opt.label} ${isSelected ? "from" : "to"} slip` : undefined}
              title={canSelect && !autoBook ? "Not included in SportyBet booking codes" : undefined}
              onClick={() => onToggle?.(opt)}
              onKeyDown={e => { if (canSelect && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); onToggle?.(opt); } }}
              className={clsx(
                "rounded-lg px-2.5 py-2 transition-all border",
                canSelect && "cursor-pointer",
                isSelected
                  ? "bg-brand-400/10 border-brand-400/50"
                  : canSelect ? "hover:bg-n-800/60 border-transparent" : "border-transparent"
              )}
            >
              <div className="flex items-center gap-2">
                <span className={clsx("text-[13px] truncate flex-1", isBest || isSelected ? "text-n-0 font-semibold" : "text-n-400")}>
                  {opt.label}
                </span>
                {opt.code === recommendedCode && !isSelected && (
                  <span className="font-display font-bold text-[10px] uppercase tracking-wider text-ink bg-brand-400 rounded px-1.5 py-px shrink-0">
                    Top pick
                  </span>
                )}
                {odds && (
                  <span className={clsx("font-mono text-xs font-bold shrink-0 px-2 py-0.5 rounded-md",
                    isSelected ? "bg-brand-400 text-ink" : "bg-n-800 text-n-200")}>
                    {odds}
                  </span>
                )}
                {isSelected && <Check size={13} className="text-accent shrink-0" />}
                <span className={clsx("font-display font-bold text-lg w-11 text-right tnum shrink-0", isBest ? "text-accent" : "text-n-500")}>
                  {pct}%
                </span>
              </div>
              <div className="h-1 bg-n-800 rounded-full overflow-hidden mt-1.5">
                <div className={clsx("h-full rounded-full transition-all duration-500", isBest ? "bg-accent" : "bg-n-600")} style={{ width: `${pct}%` }} />
              </div>
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
      <div className="card p-4 space-y-3">
        <div className="skeleton h-4 w-1/3" />
        <div className="skeleton h-3 w-full" />
        <div className="skeleton h-3 w-11/12" />
        <div className="skeleton h-3 w-4/6" />
      </div>
    );
  }

  if (!explanation.explanation) return null;

  // "compound-beta+" is the pre-Sep-2026 tag, still present on cached explanations.
  const hasWebSearch = /^(web-search|compound-beta)\+/.test(explanation.model ?? "") && explanation.sources.length > 0;

  return (
    <div className="card p-4 sm:p-5">
      <PanelTitle
        right={
          <span className={clsx(
            "inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider rounded-md px-2 py-0.5 border",
            hasWebSearch ? "text-info bg-sky-400/10 border-sky-400/25" : "text-n-400 bg-n-800 border-n-700"
          )}>
            {hasWebSearch ? <><Radio size={10} /> Live web search</> : "Stats only"}
          </span>
        }
      >
        <span className="inline-flex items-center gap-2"><Sparkles size={15} className="text-accent" /> AI analysis</span>
      </PanelTitle>
      <p className="text-[15px] text-n-300 leading-relaxed">{explanation.explanation}</p>
      {explanation.sources.length > 0 && (
        <div className="pt-3 mt-3 border-t border-n-800 flex flex-wrap gap-x-4 gap-y-1.5">
          {explanation.sources.slice(0, 3).map((src, i) => {
            const domain = (() => { try { return new URL(src).hostname.replace("www.", ""); } catch { return src; } })();
            return (
              <a key={i} href={src} target="_blank" rel="noopener noreferrer"
                className="flex items-center gap-1 font-mono text-[11px] text-n-500 hover:text-info transition-colors">
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
  const slip = useBetSlip();

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
        const preds: Prediction[] = Array.isArray(d?.predictions) ? d.predictions : [];
        const found = preds.find(p =>
          p.home === home && p.away === away && (!date || p.date === date)
        );
        if (found) setPrediction(found);
      })
      .catch(() => {});

    // Fetch live SportyBet event for real odds + market IDs
    fetch(`${API}/api/sportybet-event?home=${encodeURIComponent(home)}&away=${encodeURIComponent(away)}&date=${date}`)
      .then(r => r.json())
      .then(d => { if (d?.found) setSbEvent(d); })
      .catch(() => {});
  }, [home, away, date]);

  const matchDate = date || prediction?.date || "";
  const toggleOption = (market: Market) => (opt: Market["options"][number]) =>
    slip.toggle({
      home, away, date: matchDate, time: prediction?.time, league: prediction?.league_name,
      market: market.id, marketName: market.name, code: opt.code, label: opt.label, prob: opt.prob,
    });
  const inSlip = (market: Market) => (code: string) =>
    !!matchDate && isSelected(slip.items, { home, away, date: matchDate, market: market.id, code });

  const rec = analysis?.recommended;

  if (!home || !away) {
    return (
      <div className="card border-dashed text-center py-16 space-y-4">
        <p className="font-display font-bold text-xl uppercase text-n-0">No match selected</p>
        <button onClick={() => router.push("/")} className="btn-secondary">
          <ArrowLeft size={14} /> Back to predictions
        </button>
      </div>
    );
  }

  const probs = prediction ? [
    { key: "1", label: "Home", value: Math.round(prediction.p_home * 100) },
    { key: "X", label: "Draw", value: Math.round(prediction.p_draw * 100) },
    { key: "2", label: "Away", value: Math.round(prediction.p_away * 100) },
  ] : [];
  const favKey = probs.length ? [...probs].sort((a, b) => b.value - a.value)[0].key : null;
  const kickoffText = kickoff(date || prediction?.date || "", prediction?.time);

  return (
    <div className="space-y-5 animate-fade-in">
      <button
        onClick={() => router.back()}
        className="inline-flex items-center gap-1.5 text-sm font-semibold text-n-400 hover:text-n-0 transition-colors"
      >
        <ArrowLeft size={15} /> Back
      </button>

      {/* ── Scoreboard hero ── */}
      <section className="card relative overflow-hidden">
        <div className="absolute inset-x-0 top-0 h-40 bg-gradient-to-b from-brand-400/[0.07] to-transparent pointer-events-none" aria-hidden="true" />
        <div className="relative flex items-center justify-between gap-2 px-4 sm:px-6 py-3 border-b border-n-800/80">
          <span className="flex items-center gap-2 min-w-0">
            {prediction?.flag && <CompetitionBadge name={prediction.league_name} fallbackEmoji={prediction.flag} size={14} />}
            <span className="eyebrow truncate">{prediction?.league_name ?? "Match analysis"}</span>
          </span>
          <span className="font-mono text-[11px] text-n-200 uppercase shrink-0">{kickoffText}</span>
        </div>

        <div className="relative grid grid-cols-[1fr_auto_1fr] items-center gap-3 sm:gap-6 px-4 sm:px-8 pt-6 pb-5">
          {[{ name: home, side: "Home" }, null, { name: away, side: "Away" }].map((t, i) =>
            t ? (
              <div key={t.side} className="flex flex-col items-center text-center gap-3 min-w-0">
                <TeamBadge name={t.name} size={64} />
                <div className="min-w-0 w-full">
                  <p className="font-display font-extrabold uppercase text-2xl sm:text-4xl leading-none text-n-0 break-words">{t.name}</p>
                  <p className="eyebrow mt-1.5">{t.side}</p>
                </div>
              </div>
            ) : (
              <span key="vs" className="font-display font-extrabold text-2xl sm:text-3xl text-n-500">VS</span>
            )
          )}
        </div>

        {prediction && (
          <div className="relative px-4 sm:px-8 pb-6">
            <div className="grid grid-cols-3 gap-2">
              {probs.map(({ key, label, value }) => (
                <div key={key} className={clsx(
                  "rounded-xl px-3 py-2.5 text-center border",
                  key === favKey ? "bg-brand-400/10 border-brand-400/40" : "bg-n-800/40 border-n-800"
                )}>
                  <p className="eyebrow">{label} · {key}</p>
                  <p className={clsx("font-display font-extrabold text-3xl sm:text-4xl leading-none mt-1 tnum", key === favKey ? "text-accent" : "text-n-0")}>
                    {value}%
                  </p>
                </div>
              ))}
            </div>
          </div>
        )}
      </section>

      {loadingAnalysis && (
        <div className="grid lg:grid-cols-[1fr_360px] gap-4">
          <div className="space-y-4">
            <div className="card p-4 space-y-3"><div className="skeleton h-4 w-1/3" /><div className="skeleton h-3 w-full" /><div className="skeleton h-3 w-5/6" /></div>
            <div className="card h-48 p-4"><div className="skeleton h-full w-full !rounded-xl" /></div>
          </div>
          <div className="card h-72 p-4"><div className="skeleton h-full w-full !rounded-xl" /></div>
        </div>
      )}
      {error && (
        <div className="card border-dashed text-center py-12 px-6">
          <p className="font-display font-bold text-xl uppercase text-n-0">Analysis unavailable</p>
          <p className="text-sm text-n-400 mt-1">The model couldn&apos;t load this match right now. Try again in a moment.</p>
        </div>
      )}

      {analysis && (
        <div className="grid lg:grid-cols-[1fr_360px] gap-4 items-start">
          {/* Right rail on desktop; first on mobile so the pick leads */}
          <aside className="space-y-4 lg:order-2 lg:sticky lg:top-24">
            {rec && (
              <div className="rounded-2xl bg-brand-400 text-ink p-4 sm:p-5 shadow-glow">
                <p className="font-display font-bold text-xs uppercase tracking-[0.14em] text-ink/60">Best pick · {rec.market}</p>
                <div className="flex items-end justify-between gap-3 mt-1">
                  <p className="font-display font-extrabold text-3xl uppercase leading-none">{rec.label}</p>
                  <p className="font-display font-extrabold text-5xl leading-none tnum">{Math.round(rec.prob * 100)}%</p>
                </div>
                <p className="text-xs font-semibold text-ink/70 mt-2">Model probability for this outcome</p>
              </div>
            )}
            <TaleOfTheTape analysis={analysis} home={home} away={away} />
          </aside>

          <div className="space-y-4 lg:order-1 min-w-0">
            <AIExplanation explanation={explanation} />

            {analysis.web_adjustment_reason && (
              <div className="rounded-2xl border border-amber-400/25 bg-amber-400/[0.05] px-4 py-3 flex items-start gap-3">
                <Search size={15} className="text-warn mt-0.5 shrink-0" />
                <div>
                  <p className="text-sm text-warn font-semibold">Team news adjusted this prediction</p>
                  <p className="text-sm text-n-400 mt-0.5">{analysis.web_adjustment_reason}</p>
                  {analysis.web_adjustment_flags && analysis.web_adjustment_flags.length > 0 && (
                    <div className="flex gap-1.5 flex-wrap mt-2">
                      {analysis.web_adjustment_flags.map(f => (
                        <span key={f} className="font-mono text-[10px] uppercase text-warn/80 bg-amber-400/10 border border-amber-400/20 px-1.5 py-0.5 rounded">
                          {f.replace(/_/g, " ")}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}

            {(analysis.live_odds_fetched || sbEvent) && (
              <div className="rounded-2xl border border-sky-400/20 bg-sky-400/[0.05] px-4 py-3 flex items-center gap-3">
                {sbEvent ? <Ticket size={15} className="text-info shrink-0" /> : <Radio size={15} className="text-info shrink-0" />}
                <p className="text-sm text-n-300">
                  {sbEvent
                    ? "Live SportyBet odds loaded. Tap any outcome to add it to your bet slip."
                    : `Live odds from ${analysis.odds_bookie || "the market"}; the model was recalibrated in real time.`}
                </p>
              </div>
            )}

            {/* Markets */}
            <div>
              <h2 className="display text-3xl text-n-0 mb-3">Markets</h2>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
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
                  return (
                    <MarketBlock
                      key={m.id}
                      market={m}
                      recommendedCode={rec?.market_id === m.id ? rec.code : undefined}
                      sbMarket={sbM}
                      onToggle={matchDate ? toggleOption(m) : undefined}
                      isInSlip={inSlip(m)}
                    />
                  );
                })}
              </div>
            </div>
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
            <div className="w-8 h-8 border-2 border-brand-400 border-t-transparent rounded-full animate-spin" />
          </div>
        }
      >
        <MatchContent />
      </Suspense>
    </AppShell>
  );
}
