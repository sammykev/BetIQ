"use client";

import { useEffect, useState } from "react";
import {
  ChevronLeft, ChevronRight, Loader2, CheckCircle2, XCircle, Clock,
  ChevronDown, ChevronUp, X,
} from "lucide-react";
import clsx from "clsx";
import { fetchCalendar, fetchHistory, fetchMatchAnalysis } from "@/lib/api";
import type { CalendarDay, HistoryPrediction, MatchAnalysis } from "@/lib/api";
import { MatchCard } from "@/components/MatchCard";
import { AppShell } from "@/components/shell/AppShell";
import { PageHeader } from "@/components/shell/PageHeader";

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["January","February","March","April","May","June",
                 "July","August","September","October","November","December"];

function dotColor(day: CalendarDay): string {
  const settled = day.won + day.lost;
  if (settled === 0) return "bg-sky-500";               // all pending / future
  const rate = day.won / settled;
  if (rate >= 0.6) return "bg-brand-500";
  if (rate >= 0.4) return "bg-amber-400";
  return "bg-rose-500";
}

function OutcomeIcon({ outcome }: { outcome: string }) {
  if (outcome === "won")  return <CheckCircle2 size={14} className="text-brand-500 shrink-0" />;
  if (outcome === "lost") return <XCircle      size={14} className="text-rose-500 shrink-0" />;
  return                         <Clock        size={14} className="text-zinc-400 shrink-0" />;
}

function ResultBadge({ result }: { result: string | null }) {
  if (!result) return null;
  const label = result === "H" ? "Home Win" : result === "A" ? "Away Win" : "Draw";
  const cls   = result === "H" ? "text-brand-700 dark:text-brand-400 bg-brand-50 dark:bg-brand-900/30 border-brand-200 dark:border-brand-800"
              : result === "A" ? "text-sky-700 dark:text-sky-400 bg-sky-50 dark:bg-sky-500/10 border-sky-200 dark:border-sky-500/30"
              :                  "text-zinc-500 dark:text-zinc-400 bg-zinc-50 dark:bg-zinc-800 border-zinc-200 dark:border-zinc-700";
  return (
    <span className={clsx("text-[10px] font-semibold px-1.5 py-0.5 rounded-md border whitespace-nowrap", cls)}>
      {label}
    </span>
  );
}

function MatchDetailPanel({ p, onClose }: { p: HistoryPrediction; onClose: () => void }) {
  const [analysis, setAnalysis] = useState<MatchAnalysis | null>(null);
  const [loadingA, setLoadingA] = useState(true);

  useEffect(() => {
    fetchMatchAnalysis(p.home, p.away)
      .then(setAnalysis)
      .catch(() => {})
      .finally(() => setLoadingA(false));
  }, [p.home, p.away]);

  const resultLabel: Record<string, string> = { H: "Home Win", D: "Draw", A: "Away Win" };
  const tipLabel: Record<string, string>    = { "1": "Home Win", "X": "Draw", "2": "Away Win" };
  const isCorrect = p.actual_result && p.tip_code &&
    ({ H: "1", D: "X", A: "2" } as Record<string, string>)[p.actual_result] === p.tip_code;

  return (
    <div className="card !rounded-xl p-4 space-y-3 text-sm mt-2 animate-fade-in">
      {/* Verdict banner */}
      {p.actual_result && (
        <div className={clsx("flex items-center gap-2 px-3 py-2 rounded-lg border",
          isCorrect
            ? "bg-brand-50 dark:bg-brand-900/20 border-brand-200 dark:border-brand-800"
            : "bg-rose-50 dark:bg-rose-500/10 border-rose-200 dark:border-rose-500/30")}>
          {isCorrect
            ? <CheckCircle2 size={15} className="text-brand-600 dark:text-brand-400 shrink-0" />
            : <XCircle      size={15} className="text-rose-500 shrink-0" />}
          <div>
            <p className={clsx("text-xs font-bold",
              isCorrect ? "text-brand-700 dark:text-brand-400" : "text-rose-700 dark:text-rose-400")}>
              {isCorrect ? "Prediction correct" : "Prediction incorrect"}
            </p>
            <p className="text-[10px] text-zinc-500 dark:text-zinc-400">
              Result: <span className="text-zinc-900 dark:text-white font-semibold">{resultLabel[p.actual_result]}</span>
              &nbsp;· Our pick: <span className="text-zinc-900 dark:text-white font-semibold">{tipLabel[p.tip_code] || p.tip_1x2}</span>
            </p>
          </div>
        </div>
      )}

      {/* Our predictions */}
      <div className="space-y-1.5">
        <p className="text-[10px] text-zinc-400 dark:text-zinc-500 uppercase tracking-wider font-bold">Our predictions</p>
        <div className="grid grid-cols-3 gap-2">
          {[
            { label: "Home win", val: `${Math.round(p.p_home * 100)}%` },
            { label: "Draw",     val: `${Math.round(p.p_draw * 100)}%` },
            { label: "Away win", val: `${Math.round(p.p_away * 100)}%` },
          ].map(({ label, val }) => (
            <div key={label} className="bg-zinc-50 dark:bg-zinc-800/60 rounded-lg px-2 py-1.5 text-center">
              <p className="text-[10px] text-zinc-400 dark:text-zinc-500">{label}</p>
              <p className="tnum text-xs font-bold text-zinc-900 dark:text-white">{val}</p>
            </div>
          ))}
        </div>
        <div className="flex gap-2 flex-wrap text-[11px]">
          <span className="bg-zinc-100 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-200 px-2 py-0.5 rounded-md font-medium">
            1X2: {p.tip_1x2}
          </span>
          {p.tip_goals && p.tip_goals !== "Skip" && (
            <span className="bg-zinc-100 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-200 px-2 py-0.5 rounded-md font-medium">
              Goals: {p.tip_goals}
            </span>
          )}
          <span className="tnum bg-zinc-100 dark:bg-zinc-800 text-zinc-400 dark:text-zinc-500 px-2 py-0.5 rounded-md">
            {Math.round(p.goals_confidence * 100)}% confidence
          </span>
        </div>
      </div>

      {/* Model stats */}
      {loadingA ? (
        <div className="flex items-center gap-2 text-zinc-400 dark:text-zinc-500 text-xs py-2">
          <Loader2 size={12} className="animate-spin" /> Loading model analysis…
        </div>
      ) : analysis && (
        <div className="space-y-1.5">
          <p className="text-[10px] text-zinc-400 dark:text-zinc-500 uppercase tracking-wider font-bold">Model stats at prediction time</p>
          <div className="grid grid-cols-2 gap-2 text-[11px]">
            <div className="bg-zinc-50 dark:bg-zinc-800/60 rounded-lg px-2 py-1.5">
              <p className="text-zinc-400 dark:text-zinc-500">xG {p.home}</p>
              <p className="tnum text-zinc-900 dark:text-white font-bold">{analysis.xg_home.toFixed(2)}</p>
            </div>
            <div className="bg-zinc-50 dark:bg-zinc-800/60 rounded-lg px-2 py-1.5">
              <p className="text-zinc-400 dark:text-zinc-500">xG {p.away}</p>
              <p className="tnum text-zinc-900 dark:text-white font-bold">{analysis.xg_away.toFixed(2)}</p>
            </div>
            <div className="bg-zinc-50 dark:bg-zinc-800/60 rounded-lg px-2 py-1.5 col-span-2">
              <p className="text-zinc-400 dark:text-zinc-500">Elo</p>
              <p className="tnum text-zinc-900 dark:text-white font-bold text-xs">
                {p.home} {analysis.elo.home} vs {p.away} {analysis.elo.away}
                <span className="text-zinc-400 dark:text-zinc-500 font-normal ml-1">({analysis.elo.label})</span>
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Feedback contribution note */}
      {p.actual_result && (
        <p className="text-[10px] text-zinc-400 dark:text-zinc-600 text-center">
          ✓ This result has been fed back into the model to improve future accuracy
        </p>
      )}

      <button onClick={onClose} className="text-[11px] text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300 transition-colors font-medium">
        Close ↑
      </button>
    </div>
  );
}

function DayPanel({ date, onClose }: { date: string; onClose: () => void }) {
  const [preds, setPreds] = useState<HistoryPrediction[]>([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<number | null>(null);

  useEffect(() => {
    setLoading(true);
    setExpanded(null);
    fetchHistory(date).then(d => setPreds(Array.isArray(d) ? d : [])).catch(() => setPreds([])).finally(() => setLoading(false));
  }, [date]);

  const won     = preds.filter(p => p.outcome === "won").length;
  const lost    = preds.filter(p => p.outcome === "lost").length;
  const pending = preds.filter(p => p.outcome === "pending").length;
  const settled = won + lost;
  const accuracy = settled ? Math.round((won / settled) * 100) : null;

  // Midday avoids DST/timezone flips shifting the date
  const fmt = (d: string) => new Date(d + "T12:00:00")
    .toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric" });

  return (
    <div className="card flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-zinc-100 dark:border-zinc-800 shrink-0">
        <div>
          <p className="font-display font-bold uppercase tracking-wide text-lg leading-tight text-n-0">{fmt(date)}</p>
          {accuracy !== null && (
            <p className="text-xs text-zinc-400 dark:text-zinc-500 mt-0.5">
              Accuracy: <span className={clsx("tnum font-bold",
                accuracy >= 60 ? "text-accent" :
                accuracy >= 40 ? "text-warn" : "text-danger"
              )}>{accuracy}%</span>
              &nbsp;({won}W / {lost}L / {pending} pending)
            </p>
          )}
        </div>
        <button onClick={onClose} className="p-2 hover:bg-n-800 rounded-lg text-n-400 hover:text-n-0 transition-colors">
          <X size={14} />
        </button>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto p-3 space-y-2 max-h-[480px]">
        {loading && (
          <div className="flex items-center justify-center py-12 text-zinc-400 dark:text-zinc-500">
            <Loader2 size={20} className="animate-spin mr-2" /> Loading…
          </div>
        )}
        {!loading && preds.length === 0 && (
          <p className="text-center text-zinc-400 dark:text-zinc-500 py-12 text-sm">No predictions for this date.</p>
        )}
        {!loading && preds.map((p, i) => (
          <div key={i}>
            <MatchCard
              home={p.home}
              away={p.away}
              league={p.league_name}
              flag={p.flag}
              onClick={() => setExpanded(expanded === i ? null : i)}
              className={clsx(
                "!shadow-none",
                p.outcome === "won"  ? "!border-brand-400/40" :
                p.outcome === "lost" ? "!border-rose-500/40"   : ""
              )}
            >
              <div className="flex flex-col items-end gap-1">
                <OutcomeIcon outcome={p.outcome} />
                <span className="text-[10px] font-bold bg-zinc-100 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-200 px-2 py-0.5 rounded-full whitespace-nowrap">
                  {p.tip_1x2}
                </span>
                {p.actual_result && <ResultBadge result={p.actual_result} />}
                {expanded === i
                  ? <ChevronUp size={10} className="text-zinc-300 dark:text-zinc-600" />
                  : <ChevronDown size={10} className="text-zinc-300 dark:text-zinc-600" />}
              </div>
            </MatchCard>
            {expanded === i && (
              <MatchDetailPanel p={p} onClose={() => setExpanded(null)} />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

const _pad = (n: number) => String(n).padStart(2, "0");
// Use local timezone (not UTC) so Nigerian users (UTC+1) see the correct date
const _localDateStr = (d = new Date()) =>
  `${d.getFullYear()}-${_pad(d.getMonth() + 1)}-${_pad(d.getDate())}`;

export default function HistoryPage() {
  const now = new Date();
  const [year,  setYear]  = useState(now.getFullYear());
  const [month, setMonth] = useState(now.getMonth());  // 0-indexed
  const [summary, setSummary] = useState<Record<string, CalendarDay>>({});
  const [loadingCal, setLoadingCal] = useState(true);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);

  const monthStr = `${year}-${_pad(month + 1)}`;

  useEffect(() => {
    setLoadingCal(true);
    fetchCalendar(monthStr)
      .then(d => setSummary(d && typeof d === "object" && !Array.isArray(d) && !("error" in d) ? d : {}))
      .catch(() => setSummary({}))
      .finally(() => setLoadingCal(false));
  }, [monthStr]);

  const prevMonth = () => { if (month === 0) { setYear(y => y-1); setMonth(11); } else setMonth(m => m-1); };
  const nextMonth = () => { if (month === 11) { setYear(y => y+1); setMonth(0);  } else setMonth(m => m+1); };

  // Build calendar grid
  const firstDay = new Date(year, month, 1).getDay();
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const cells: (number | null)[] = [
    ...Array(firstDay).fill(null),
    ...Array.from({ length: daysInMonth }, (_, i) => i + 1),
  ];
  while (cells.length % 7 !== 0) cells.push(null);

  const todayStr = _localDateStr(now);  // local timezone, not UTC

  const days = Object.values(summary);
  const monthWon = days.reduce((n, d) => n + (d.won || 0), 0);
  const monthLost = days.reduce((n, d) => n + (d.lost || 0), 0);
  const monthAccuracy = !loadingCal && monthWon + monthLost > 0
    ? Math.round((monthWon / (monthWon + monthLost)) * 100) : null;

  return (
    <AppShell>
      <div className="space-y-6 animate-fade-in">
        <PageHeader
          eyebrow="Track record"
          title="History"
          description="Every past prediction, graded against the real result."
        />

        {/* Month scoreboard */}
        <div className="grid grid-cols-3 gap-3 max-w-2xl">
          {[
            { label: "Accuracy", value: monthAccuracy === null ? "–" : `${monthAccuracy}%`,
              cls: monthAccuracy === null ? "text-zinc-500" : monthAccuracy >= 60 ? "text-accent" : monthAccuracy >= 40 ? "text-warn" : "text-danger" },
            { label: "Won", value: loadingCal ? "–" : monthWon, cls: "text-n-0" },
            { label: "Lost", value: loadingCal ? "–" : monthLost, cls: "text-zinc-400" },
          ].map(({ label, value, cls }) => (
            <div key={label} className="card px-4 py-3">
              <p className="eyebrow truncate">{label}</p>
              <p className={clsx("font-display font-extrabold text-3xl leading-none mt-1 tnum", cls)}>{value}</p>
            </div>
          ))}
        </div>

        <div className={clsx("grid gap-4", selectedDate ? "lg:grid-cols-2" : "grid-cols-1 max-w-2xl")}>
          {/* Calendar */}
          <div className="card overflow-hidden">
            {/* Month header */}
            <div className="flex items-center justify-between px-5 py-4 border-b border-zinc-100 dark:border-zinc-800">
              <button onClick={prevMonth} className="p-2 hover:bg-n-800 rounded-lg text-n-400 hover:text-n-0 transition-colors">
                <ChevronLeft size={16} />
              </button>
              <div className="text-center">
                <p className="font-display font-extrabold uppercase tracking-wide text-2xl leading-none text-n-0">{MONTHS[month]} {year}</p>
                {!loadingCal && (
                  <p className="text-[11px] text-zinc-500 mt-1">
                    {Object.keys(summary).length} days with predictions
                  </p>
                )}
              </div>
              <button onClick={nextMonth} className="p-2 hover:bg-n-800 rounded-lg text-n-400 hover:text-n-0 transition-colors">
                <ChevronRight size={16} />
              </button>
            </div>

            {/* Day labels */}
            <div className="grid grid-cols-7 px-3 pt-3">
              {DAYS.map(d => (
                <div key={d} className="text-[10px] text-zinc-400 dark:text-zinc-500 text-center font-semibold pb-1">{d}</div>
              ))}
            </div>

            {/* Grid */}
            <div className="px-3 pb-3">
              {loadingCal ? (
                <div className="flex items-center justify-center py-16 text-zinc-400">
                  <Loader2 size={20} className="animate-spin" />
                </div>
              ) : (
                <div className="grid grid-cols-7 gap-1">
                  {cells.map((day, i) => {
                    if (!day) return <div key={i} />;
                    const dStr = `${monthStr}-${String(day).padStart(2, "0")}`;
                    const info = summary[dStr];
                    const isToday = dStr === todayStr;
                    const isSelected = dStr === selectedDate;
                    const isPast = dStr < todayStr;

                    return (
                      <button
                        key={i}
                        onClick={() => setSelectedDate(isSelected ? null : dStr)}
                        className={clsx(
                          "relative flex flex-col items-center justify-center rounded-xl aspect-square text-sm transition-all border",
                          isSelected ? "bg-brand-400/10 border-brand-400/60" :
                          isToday    ? "bg-n-800 border-n-700" :
                          info       ? "bg-n-800/40 hover:bg-n-800/80 border-transparent" :
                                       "border-transparent",
                          info ? "cursor-pointer" : isPast ? "opacity-30 cursor-default" : "cursor-default"
                        )}
                        disabled={!info && isPast}
                      >
                        <span className={clsx(
                          "tnum text-sm font-semibold",
                          isSelected ? "text-accent" : isToday ? "text-n-0 font-bold" : info ? "text-n-200" : "text-n-500"
                        )}>
                          {day}
                        </span>
                        {info && (
                          <span className={clsx("w-1.5 h-1.5 rounded-full mt-0.5", dotColor(info))} />
                        )}
                        {info && (
                          <span className="tnum text-[9px] text-zinc-400 dark:text-zinc-500 leading-none">
                            {info.total}
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Legend */}
            <div className="flex items-center justify-center gap-4 px-4 py-3 border-t border-zinc-100 dark:border-zinc-800">
              {[
                { color: "bg-brand-500", label: "≥60% correct" },
                { color: "bg-amber-400", label: "40–60%"       },
                { color: "bg-rose-500",  label: "<40%"         },
                { color: "bg-sky-500",   label: "Upcoming"     },
              ].map(({ color, label }) => (
                <div key={label} className="flex items-center gap-1.5">
                  <span className={clsx("w-2 h-2 rounded-full shrink-0", color)} />
                  <span className="text-[10px] text-zinc-400 dark:text-zinc-500">{label}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Day detail panel */}
          {selectedDate && (
            <DayPanel date={selectedDate} onClose={() => setSelectedDate(null)} />
          )}
        </div>
      </div>
    </AppShell>
  );
}
