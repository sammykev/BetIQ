"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { AccessError, fetchMatchAnalysis, fetchExplanation, fetchMatchFacts } from "@/lib/api";
import { useAuthedFetch } from "@/lib/useAuthedFetch";
import type { MatchAnalysis, Market, MatchExplanation, MatchFacts, FactMatch, Prediction, TeamForm, TeamAverages } from "@/lib/api";
import { kickoff } from "@/lib/matchTime";
import { TeamBadge } from "@/components/PredictionCard";
import { CompetitionBadge } from "@/components/CompetitionBadge";
import { AppShell } from "@/components/shell/AppShell";
import { useBetSlip } from "@/lib/useBetSlip";
import { bookableOnSportybet, isSelected } from "@/lib/slip";
import { fetchMatchday, matchKey, type MatchdayMatch } from "@/lib/matchday";
import { StatusCell } from "@/components/MatchdayList";
import { LiveStats } from "@/components/LiveStats";
import { Locked, Unavailable } from "@/components/FeatureGate";
import { useAccess } from "@/lib/access";

const ANALYSIS_PERKS = [
  "Every market's probability: result, goals, both score, corners, cards, shots",
  "Tale of the tape: Elo, expected goals and form side by side",
  "An AI preview written from this week's team news",
];
import {
  ArrowLeft, Sparkles, ExternalLink, Check, Ticket, Radio, Search, Flag, History, Swords, Newspaper, BarChart3,
} from "lucide-react";
import clsx from "clsx";

const API = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";

interface SbOutcome { id: string; desc: string; odds: string; }
interface SbMarket  { id: string; name: string; specifier: string; outcomes: SbOutcome[]; }
interface SbEvent   { found: boolean; eventId: string; gameId: string; homeTeam: string; awayTeam: string; markets: SbMarket[]; }


/** How the appointed referee moves the cards forecast, when it clearly does. */
function refereeNote(factor?: number): string | null {
  if (!factor || Math.abs(factor - 1) < 0.05) return null;
  const pct = Math.round(Math.abs(factor - 1) * 100);
  return `usually ${pct}% ${factor > 1 ? "more" : "fewer"} cards than these teams get`;
}

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

  const news = explanation.news ?? [];
  const hasWebSearch = news.length > 0;

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
      {news.length > 0 && (
        <div className="mt-4 rounded-xl bg-n-800/40 px-3 py-3">
          <p className="eyebrow flex items-center gap-1.5 mb-2"><Newspaper size={12} /> Latest team news</p>
          <ul className="space-y-1.5">
            {news.slice(0, 6).map((n, i) => (
              <li key={i} className="flex gap-2.5 text-[13px] leading-snug">
                <span className="font-mono text-[11px] text-n-500 shrink-0 pt-px tnum">{shortDate(n.date)}</span>
                <span className="text-n-300">{n.text}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {explanation.updated_at && (
        <p className="text-[11px] text-n-500 mt-3">
          Written {clock(explanation.updated_at)}
          {explanation.news_checked_at && <> · news checked {clock(explanation.news_checked_at)}</>}
          {explanation.refreshing && <> · updating with the latest…</>}
        </p>
      )}
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
// Recent form and head-to-head
// ------------------------------------------------------------------ //
function shortDate(iso: string): string {
  const d = new Date(`${iso}T12:00:00Z`);
  return isNaN(d.getTime()) ? iso : d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "2-digit" });
}

function clock(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const today = new Date().toDateString() === d.toDateString();
  return today
    ? d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

const OUTCOME_CLS: Record<FactMatch["outcome"], string> = {
  W: "bg-brand-400 text-ink", D: "bg-zinc-500 text-white", L: "bg-rose-500 text-white",
};

function FormList({ name, rows }: { name: string; rows: FactMatch[] }) {
  return (
    <div className="min-w-0">
      <div className="flex items-center gap-2 pb-2 border-b border-n-800">
        <TeamBadge name={name} size={20} />
        <span className="text-xs font-bold text-n-0 truncate">{name}</span>
        {rows.length > 0 && <span className="ml-auto"><FormChips form={rows.map(r => r.outcome).reverse().join("")} /></span>}
      </div>
      {rows.length === 0 ? (
        <p className="text-xs text-n-500 py-3">No recent results on record.</p>
      ) : (
        <ul className="divide-y divide-n-800/70">
          {rows.map((r, i) => (
            <li key={i} className="flex items-center gap-2.5 py-2">
              <span className={clsx("font-display font-bold text-[11px] w-[18px] h-[18px] rounded flex items-center justify-center shrink-0", OUTCOME_CLS[r.outcome])}>
                {r.outcome}
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-[13px] text-n-200 truncate">
                  <span className="text-n-500 text-[11px] font-bold mr-1.5">{r.venue === "H" ? "vs" : "at"}</span>{r.opponent}
                </p>
                <p className="text-[11px] text-n-500 truncate">{shortDate(r.date)}{r.comp && <> · {r.comp}</>}</p>
              </div>
              <span className="font-mono font-bold text-sm text-n-0 tnum shrink-0">
                {r.venue === "H" ? `${r.hg}-${r.ag}` : `${r.ag}-${r.hg}`}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function RecentForm({ facts, home, away }: { facts: MatchFacts; home: string; away: string }) {
  if (facts.home.length === 0 && facts.away.length === 0) return null;
  return (
    <div className="card p-4">
      <PanelTitle>
        <span className="inline-flex items-center gap-2"><History size={15} className="text-accent" /> Last 5 matches</span>
      </PanelTitle>
      <div className="grid sm:grid-cols-2 gap-x-6 gap-y-4">
        <FormList name={home} rows={facts.home} />
        <FormList name={away} rows={facts.away} />
      </div>
      <p className="text-[11px] text-n-500 mt-3">Scores shown from each team&apos;s side, newest first.</p>
    </div>
  );
}

/** The last meetings; nothing at all when the teams have never met. */
function HeadToHead({ facts, home, away }: { facts: MatchFacts; home: string; away: string }) {
  const s = facts.summary.h2h;
  if (!facts.h2h.length || !s) return null;
  const bar = [
    { n: s.won, cls: "bg-brand-400", label: `${home} wins` },
    { n: s.drawn, cls: "bg-zinc-500", label: "Draws" },
    { n: s.lost, cls: "bg-rose-500", label: `${away} wins` },
  ];
  return (
    <div className="card p-4">
      <PanelTitle right={<span className="text-[11px] text-n-500 tnum">last {facts.h2h.length}</span>}>
        <span className="inline-flex items-center gap-2"><Swords size={15} className="text-accent" /> Head to head</span>
      </PanelTitle>
      <div className="flex h-2 rounded-full overflow-hidden gap-0.5 bg-n-800">
        {bar.map((b, i) => b.n > 0 && <div key={i} className={b.cls} style={{ flexGrow: b.n }} />)}
      </div>
      <div className="flex justify-between text-xs mt-2 gap-2">
        {bar.map((b, i) => (
          <span key={i} className={clsx("text-n-400 min-w-0 truncate", i === 1 && "text-center", i === 2 && "text-right")}>
            <span className="font-display font-bold text-lg text-n-0 tnum mr-1">{b.n}</span>{b.label}
          </span>
        ))}
      </div>
      <ul className="divide-y divide-n-800/70 mt-2">
        {facts.h2h.map((m, i) => (
          <li key={i} className="grid grid-cols-[1fr_auto_1fr] items-center gap-2 py-2">
            <span className={clsx("text-[13px] truncate text-right", m.hg > m.ag ? "text-n-0 font-semibold" : "text-n-400")}>{m.home}</span>
            <span className="font-mono font-bold text-sm text-n-0 tnum px-2 py-0.5 rounded bg-n-800/70">{m.hg}-{m.ag}</span>
            <span className={clsx("text-[13px] truncate", m.ag > m.hg ? "text-n-0 font-semibold" : "text-n-400")}>{m.away}</span>
            <span className="col-span-3 text-center text-[11px] text-n-500 -mt-1">{shortDate(m.date)}{m.comp && <> · {m.comp}</>}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

type AvgRow = { label: string; get: (a: TeamAverages) => number | null | undefined; pct?: boolean; count?: (a: TeamAverages) => number | undefined };
const AVG_GROUPS: { title: string; rows: AvgRow[] }[] = [
  { title: "Goals", rows: [
    { label: "Scored", get: a => a.goals.for },
    { label: "Conceded", get: a => a.goals.against },
    { label: "Match goals", get: a => a.goals.total },
    { label: "Over 1.5", get: a => a.over["1.5"], pct: true },
    { label: "Over 2.5", get: a => a.over["2.5"], pct: true },
    { label: "Over 3.5", get: a => a.over["3.5"], pct: true },
    { label: "Both teams scored", get: a => a.btts, pct: true },
    { label: "Clean sheets", get: a => a.clean_sheet, pct: true },
    { label: "Failed to score", get: a => a.failed_to_score, pct: true },
  ] },
  { title: "Result", rows: [
    { label: "Won", get: a => a.results.won, pct: true },
    { label: "Drawn", get: a => a.results.drawn, pct: true },
    { label: "Lost", get: a => a.results.lost, pct: true },
  ] },
  { title: "Corners", rows: [
    { label: "Won", get: a => a.corners?.for, count: a => a.corners?.matches },
    { label: "Conceded", get: a => a.corners?.against, count: a => a.corners?.matches },
    { label: "Match corners", get: a => a.corners?.total, count: a => a.corners?.matches },
  ] },
  { title: "Cards (booking points)", rows: [
    { label: "Team", get: a => a.bookings?.for, count: a => a.bookings?.matches },
    { label: "Opponents", get: a => a.bookings?.against, count: a => a.bookings?.matches },
    { label: "Match total", get: a => a.bookings?.total, count: a => a.bookings?.matches },
  ] },
  { title: "Shots", rows: [
    { label: "Shots", get: a => a.shots?.for, count: a => a.shots?.matches },
    { label: "Shots conceded", get: a => a.shots?.against, count: a => a.shots?.matches },
    { label: "On target", get: a => a.sot?.for, count: a => a.sot?.matches },
    { label: "On target conceded", get: a => a.sot?.against, count: a => a.sot?.matches },
  ] },
];

/** Each team's numbers for every market over its last matches, side by side. */
function TeamAveragesPanel({ facts, home, away }: { facts: MatchFacts; home: string; away: string }) {
  const h = facts.averages?.home, a = facts.averages?.away;
  if (!h && !a) return null;
  const show = (t: TeamAverages | null | undefined, row: AvgRow) => {
    const v = t ? row.get(t) : null;
    if (v === null || v === undefined) return "—";
    return row.pct ? `${Math.round(v * 100)}%` : v.toFixed(1);
  };
  // The group's rows only where at least one side has the stat
  const groups = AVG_GROUPS.map(g => ({ ...g, rows: g.rows.filter(r => (h && r.get(h) != null) || (a && r.get(a) != null)) }))
    .filter(g => g.rows.length);
  const statN = (t: TeamAverages | null | undefined) =>
    t ? Math.max(t.corners?.matches ?? 0, t.bookings?.matches ?? 0, t.shots?.matches ?? 0, t.sot?.matches ?? 0) : 0;
  return (
    <div className="card p-4">
      <PanelTitle right={<span className="text-[11px] text-n-500 tnum">last {facts.averages?.n ?? 10}</span>}>
        <span className="inline-flex items-center gap-2"><BarChart3 size={15} className="text-accent" /> Team averages</span>
      </PanelTitle>
      <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-x-3 pb-2 border-b border-n-800">
        <span className="flex items-center gap-2 min-w-0"><TeamBadge name={home} size={18} /><span className="text-xs font-bold text-n-0 truncate">{home}</span></span>
        <span />
        <span className="flex items-center gap-2 min-w-0 justify-end"><span className="text-xs font-bold text-n-0 truncate text-right">{away}</span><TeamBadge name={away} size={18} /></span>
      </div>
      {groups.map(g => (
        <div key={g.title} className="pt-2.5">
          <p className="eyebrow text-center mb-1">{g.title}</p>
          <ul>
            {g.rows.map(r => {
              const hv = h ? r.get(h) : null, av = a ? r.get(a) : null;
              const hiH = hv != null && av != null && hv > av, hiA = hv != null && av != null && av > hv;
              return (
                <li key={r.label} className="grid grid-cols-[1fr_auto_1fr] items-center gap-x-3 py-1">
                  <span className={clsx("font-mono tnum text-sm", hiH ? "text-n-0 font-bold" : "text-n-300")}>{show(h, r)}</span>
                  <span className="text-[12px] text-n-400 text-center">{r.label}</span>
                  <span className={clsx("font-mono tnum text-sm text-right", hiA ? "text-n-0 font-bold" : "text-n-300")}>{show(a, r)}</span>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
      <p className="text-[11px] text-n-500 mt-3">
        Averages per match over each team&apos;s last {h?.played ?? a?.played ?? 10} games in all competitions, including matches
        just played. Corners, cards and shots come from the games that recorded them
        ({[h && `${home} ${statN(h)}`, a && `${away} ${statN(a)}`].filter(Boolean).join(", ")}). Cards count yellow 1, red 2, as SportyBet does.
      </p>
    </div>
  );
}

function MatchFactsPanels({ facts, home, away }: { facts: MatchFacts | null; home: string; away: string }) {
  if (!facts) return null;
  return (
    <>
      <RecentForm facts={facts} home={home} away={away} />
      <TeamAveragesPanel facts={facts} home={home} away={away} />
      <HeadToHead facts={facts} home={home} away={away} />
    </>
  );
}

// ------------------------------------------------------------------ //
// Live: the score and stats from the match-day store
// ------------------------------------------------------------------ //
const LIVE_POLL_MS = 60_000;          // the server refreshes live scores every 3 minutes
const IDLE_POLL_MAX_MS = 30 * 60_000;

/** When to look again: soon while it's on, at kick-off before, never after. */
function nextPoll(m: MatchdayMatch | null | undefined): number | null {
  if (m === undefined) return LIVE_POLL_MS;           // not loaded yet
  if (!m || m.status === "finished" || m.status === "postponed") return null;
  if (m.status === "live") return LIVE_POLL_MS;
  const ko = Date.parse(`${m.date}T${m.time || "12:00"}:00Z`);
  if (!Number.isFinite(ko)) return null;
  const now = Date.now();
  if (now > ko + 4 * 3600_000) return null;           // should be over; the store says otherwise
  if (now > ko - 5 * 60_000) return LIVE_POLL_MS;
  return Math.min(ko - 5 * 60_000 - now, IDLE_POLL_MAX_MS);
}

function useLiveMatch(home: string, away: string, date: string): MatchdayMatch | null {
  const [md, setMd] = useState<MatchdayMatch | null>(null);
  useEffect(() => {
    if (!home || !away || !date) return;
    const k = matchKey(home, away);
    const ctl = new AbortController();
    let last: MatchdayMatch | null | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      if (document.visibilityState !== "hidden") {
        const m = await fetchMatchday(date, ctl.signal)
          .then(d => d.matches.find(x => x.key === k) ?? null)
          .catch(() => undefined);
        if (ctl.signal.aborted) return;
        if (m !== undefined) { last = m; setMd(m); }
      }
      const wait = nextPoll(last);
      if (wait !== null) timer = setTimeout(tick, wait);
    };
    tick();
    return () => { ctl.abort(); clearTimeout(timer); };
  }, [home, away, date]);
  return md;
}

function LivePanel({ m }: { m: MatchdayMatch }) {
  if (!(m.status === "live" || m.status === "finished") || !(m.stats || m.events?.length)) return null;
  const live = m.status === "live";
  return (
    <section className="card p-4">
      <PanelTitle right={<span className="text-[11px] text-n-500">{live ? "Updates every few minutes" : m.aet ? "After extra time" : "Full time"}</span>}>
        <span className="inline-flex items-center gap-2">
          {live
            ? <><span className="w-2 h-2 rounded-full bg-danger animate-pulse" aria-hidden="true" /> Live stats · {m.minute || "in play"}</>
            : <><BarChart3 size={15} className="text-accent" /> Match stats</>}
        </span>
      </PanelTitle>
      <LiveStats m={m} split />
    </section>
  );
}

// ------------------------------------------------------------------ //
// Page
// ------------------------------------------------------------------ //
function MatchContent() {
  const router = useRouter();
  const access = useAccess();
  const params = useSearchParams();
  const home = params.get("home") ?? "";
  const away = params.get("away") ?? "";
  const date = params.get("date") ?? "";

  const [prediction, setPrediction] = useState<Prediction | null>(null);
  const [analysis, setAnalysis] = useState<MatchAnalysis | null>(null);
  const [loadingAnalysis, setLoadingAnalysis] = useState(true);
  const [error, setError] = useState<false | "failed" | "sign_in_required" | "premium_required" | "feature_off">(false);
  const authFetch = useAuthedFetch();
  const [explanation, setExplanation] = useState<MatchExplanation | null>(null);
  const [sbEvent, setSbEvent] = useState<SbEvent | null>(null);
  const [facts, setFacts] = useState<MatchFacts | null>(null);
  const slip = useBetSlip();
  const refetched = useRef(false);
  const refetchTimer = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => {
    if (!home || !away) return;

    fetchMatchAnalysis(home, away, authFetch, date)
      .then(setAnalysis)
      .catch(e => setError(e instanceof AccessError ? (e.message as "sign_in_required" | "premium_required" | "feature_off") : "failed"))
      .finally(() => setLoadingAnalysis(false));

    fetchExplanation(home, away, authFetch, date)
      .then(setExplanation)
      .catch(() => setExplanation({ explanation: null, sources: [], model: null, error: "failed" }));

    fetchMatchFacts(home, away, date).then(setFacts).catch(() => {});

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
  }, [home, away, date, authFetch]);

  // A stored analysis that's being rebuilt with newer information (news,
  // odds, the model): pick up the new one once, when it's likely ready
  useEffect(() => {
    if (refetched.current || !(analysis?.refreshing || explanation?.refreshing)) return;
    refetched.current = true;
    refetchTimer.current = setTimeout(() => {
      fetchMatchAnalysis(home, away, authFetch, date).then(setAnalysis).catch(() => {});
      fetchExplanation(home, away, authFetch, date).then(e => { if (e.explanation) setExplanation(e); }).catch(() => {});
    }, 30000);
  }, [analysis?.refreshing, explanation?.refreshing, home, away, date, authFetch]);
  useEffect(() => () => clearTimeout(refetchTimer.current), []);

  const matchDate = date || prediction?.date || "";
  const md = useLiveMatch(home, away, matchDate);
  const score = md && (md.status === "live" || md.status === "finished") ? md.score : null;
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
            ) : score && md ? (
              <div key="vs" className="flex flex-col items-center gap-2">
                <span className="font-display font-extrabold text-4xl sm:text-6xl leading-none tnum text-n-0 whitespace-nowrap">
                  {score[0]}<span className="text-n-500 mx-1.5 sm:mx-2">–</span>{score[1]}
                </span>
                <StatusCell m={md} />
              </div>
            ) : (
              <span key="vs" className="font-display font-extrabold text-2xl sm:text-3xl text-n-500">VS</span>
            )
          )}
        </div>

        {prediction?.referee && (
          <p className="relative -mt-2 pb-4 px-4 text-center text-xs text-n-300 flex items-center justify-center gap-1.5 flex-wrap">
            <Flag size={13} className="text-n-400" aria-hidden="true" />
            <span>Referee <span className="font-semibold text-n-0">{prediction.referee.name}</span></span>
            {refereeNote(prediction.referee.cards_factor) && (
              <span className="text-n-400">· {refereeNote(prediction.referee.cards_factor)}</span>
            )}
          </p>
        )}

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

      {md && access.shown("live_stats") && <LivePanel m={md} />}

      {loadingAnalysis && (
        <div className="grid lg:grid-cols-[1fr_360px] gap-4">
          <div className="space-y-4">
            <div className="card p-4 space-y-3"><div className="skeleton h-4 w-1/3" /><div className="skeleton h-3 w-full" /><div className="skeleton h-3 w-5/6" /></div>
            <div className="card h-48 p-4"><div className="skeleton h-full w-full !rounded-xl" /></div>
          </div>
          <div className="card h-72 p-4"><div className="skeleton h-full w-full !rounded-xl" /></div>
        </div>
      )}
      {error === "feature_off" && <Unavailable title="Match analysis" />}
      {(error === "sign_in_required" || error === "premium_required") && (
        <Locked feature="match_analysis" title="Full match analysis" perks={ANALYSIS_PERKS} />
      )}
      {error === "failed" && (
        <div className="card border-dashed text-center py-12 px-6">
          <p className="font-display font-bold text-xl uppercase text-n-0">Analysis unavailable</p>
          <p className="text-sm text-n-400 mt-1">The model couldn&apos;t load this match right now. Try again in a moment.</p>
        </div>
      )}
      {!analysis && !loadingAnalysis && (
        <div className="grid lg:grid-cols-2 gap-4 items-start">
          <MatchFactsPanels facts={facts} home={home} away={away} />
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
            <MatchFactsPanels facts={facts} home={home} away={away} />

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
                      onToggle={matchDate && access.shown("bet_slip") ? toggleOption(m) : undefined}
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
