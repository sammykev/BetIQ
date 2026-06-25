"use client";

import { useEffect, useState } from "react";
import { X, ChevronLeft, ChevronRight, Loader2, CheckCircle2, XCircle, Clock, ChevronDown, ChevronUp, BarChart2 } from "lucide-react";
import clsx from "clsx";
import { fetchCalendar, fetchHistory, fetchMatchAnalysis } from "@/lib/api";
import type { CalendarDay, HistoryPrediction, MatchAnalysis } from "@/lib/api";
import { MatchCard } from "./MatchCard";

const API = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";

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
    <div className="bg-slate-800/80 border border-slate-700 rounded-xl p-4 space-y-3 text-sm mt-1">
      {/* Verdict banner */}
      {p.actual_result && (
        <div className={clsx("flex items-center gap-2 px-3 py-2 rounded-lg",
          isCorrect ? "bg-green-500/15 border border-green-500/30" : "bg-red-500/15 border border-red-500/30")}>
          {isCorrect
            ? <CheckCircle2 size={15} className="text-green-400 shrink-0" />
            : <XCircle      size={15} className="text-red-400   shrink-0" />}
          <div>
            <p className={clsx("text-xs font-bold", isCorrect ? "text-green-300" : "text-red-300")}>
              {isCorrect ? "Prediction Correct" : "Prediction Incorrect"}
            </p>
            <p className="text-[10px] text-slate-400">
              Result: <span className="text-white font-semibold">{resultLabel[p.actual_result]}</span>
              &nbsp;· Our pick: <span className="text-white font-semibold">{tipLabel[p.tip_code] || p.tip_1x2}</span>
            </p>
          </div>
        </div>
      )}

      {/* Our predictions */}
      <div className="space-y-1.5">
        <p className="text-[10px] text-slate-500 uppercase tracking-wide font-semibold">Our Predictions</p>
        <div className="grid grid-cols-3 gap-2">
          {[
            { label: "Home Win", val: `${Math.round(p.p_home * 100)}%` },
            { label: "Draw",     val: `${Math.round(p.p_draw * 100)}%` },
            { label: "Away Win", val: `${Math.round(p.p_away * 100)}%` },
          ].map(({ label, val }) => (
            <div key={label} className="bg-slate-700/50 rounded-lg px-2 py-1.5 text-center">
              <p className="text-[10px] text-slate-400">{label}</p>
              <p className="text-xs font-bold text-white">{val}</p>
            </div>
          ))}
        </div>
        <div className="flex gap-2 flex-wrap text-[11px]">
          <span className="bg-slate-700 text-slate-200 px-2 py-0.5 rounded font-medium">
            1X2: {p.tip_1x2}
          </span>
          {p.tip_goals && p.tip_goals !== "Skip" && (
            <span className="bg-slate-700 text-slate-200 px-2 py-0.5 rounded font-medium">
              Goals: {p.tip_goals}
            </span>
          )}
          <span className="bg-slate-700 text-slate-400 px-2 py-0.5 rounded">
            {Math.round(p.goals_confidence * 100)}% confidence
          </span>
        </div>
      </div>

      {/* Model stats */}
      {loadingA ? (
        <div className="flex items-center gap-2 text-slate-500 text-xs py-2">
          <Loader2 size={12} className="animate-spin" /> Loading model analysis…
        </div>
      ) : analysis && (
        <div className="space-y-1.5">
          <p className="text-[10px] text-slate-500 uppercase tracking-wide font-semibold">Model Stats at Prediction Time</p>
          <div className="grid grid-cols-2 gap-2 text-[11px]">
            <div className="bg-slate-700/50 rounded-lg px-2 py-1.5">
              <p className="text-slate-400">xG {p.home}</p>
              <p className="text-white font-bold">{analysis.xg_home.toFixed(2)}</p>
            </div>
            <div className="bg-slate-700/50 rounded-lg px-2 py-1.5">
              <p className="text-slate-400">xG {p.away}</p>
              <p className="text-white font-bold">{analysis.xg_away.toFixed(2)}</p>
            </div>
            <div className="bg-slate-700/50 rounded-lg px-2 py-1.5 col-span-2">
              <p className="text-slate-400">Elo</p>
              <p className="text-white font-bold text-xs">
                {p.home} {analysis.elo.home} vs {p.away} {analysis.elo.away}
                <span className="text-slate-400 font-normal ml-1">({analysis.elo.label})</span>
              </p>
            </div>
          </div>
          {/* All markets */}
          <div className="space-y-1 mt-1">
            {analysis.markets.slice(0, 4).map(m => (
              <div key={m.id} className="bg-slate-700/30 rounded-lg px-2 py-1.5">
                <p className="text-[10px] text-slate-400 font-semibold mb-1">{m.name}</p>
                <div className="flex gap-3 flex-wrap">
                  {m.options.map(o => (
                    <div key={o.code} className="text-[10px]">
                      <span className="text-slate-300">{o.label}: </span>
                      <span className="text-white font-semibold">{Math.round(o.prob * 100)}%</span>
                      {(o as any).odds && (
                        <span className="text-green-400 ml-1">@ {(o as any).odds}</span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Feedback contribution note */}
      {p.actual_result && (
        <p className="text-[10px] text-slate-600 text-center">
          ✓ This result has been fed back into the model to improve future accuracy
        </p>
      )}

      <button onClick={onClose} className="text-[10px] text-slate-500 hover:text-slate-300 transition-colors">
        Close ↑
      </button>
    </div>
  );
}

interface Props {
  onClose: () => void;
}

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["January","February","March","April","May","June",
                 "July","August","September","October","November","December"];

function dotColor(day: CalendarDay): string {
  const settled = day.won + day.lost;
  if (settled === 0) return "bg-blue-500";               // all pending / future
  const rate = day.won / settled;
  if (rate >= 0.6) return "bg-green-500";
  if (rate >= 0.4) return "bg-yellow-400";
  return "bg-red-500";
}

function OutcomeIcon({ outcome }: { outcome: string }) {
  if (outcome === "won")     return <CheckCircle2 size={14} className="text-green-400 shrink-0" />;
  if (outcome === "lost")    return <XCircle      size={14} className="text-red-400   shrink-0" />;
  return                            <Clock        size={14} className="text-slate-400  shrink-0" />;
}

function ResultBadge({ result }: { result: string | null }) {
  if (!result) return null;
  const label = result === "H" ? "Home Win" : result === "A" ? "Away Win" : "Draw";
  const cls   = result === "H" ? "text-blue-400 bg-blue-500/10 border-blue-500/30"
              : result === "A" ? "text-purple-400 bg-purple-500/10 border-purple-500/30"
              :                  "text-slate-400 bg-slate-500/10 border-slate-500/30";
  return (
    <span className={clsx("text-[10px] font-semibold px-1.5 py-0.5 rounded border", cls)}>
      {label}
    </span>
  );
}

function DayPanel({ date, onClose }: { date: string; onClose: () => void }) {
  const [preds, setPreds] = useState<HistoryPrediction[]>([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<number | null>(null);

  useEffect(() => {
    fetchHistory(date).then(setPreds).finally(() => setLoading(false));
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
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-700 shrink-0">
        <div>
          <p className="text-sm font-bold text-white">{fmt(date)}</p>
          {accuracy !== null && (
            <p className="text-xs text-slate-400 mt-0.5">
              Accuracy: <span className={clsx("font-bold",
                accuracy >= 60 ? "text-green-400" : accuracy >= 40 ? "text-yellow-400" : "text-red-400"
              )}>{accuracy}%</span>
              &nbsp;({won}W / {lost}L / {pending} pending)
            </p>
          )}
        </div>
        <button onClick={onClose} className="p-1.5 hover:bg-slate-700 rounded-lg text-slate-400">
          <X size={14} />
        </button>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto p-4 space-y-2">
        {loading && (
          <div className="flex items-center justify-center py-12 text-slate-500">
            <Loader2 size={20} className="animate-spin mr-2" /> Loading…
          </div>
        )}
        {!loading && preds.length === 0 && (
          <p className="text-center text-slate-500 py-12">No predictions for this date.</p>
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
                p.outcome === "won"  ? "ring-1 ring-green-500/40" :
                p.outcome === "lost" ? "ring-1 ring-red-500/40"   : ""
              )}
            >
              <div className="flex flex-col items-end gap-1.5">
                <OutcomeIcon outcome={p.outcome} />
                <span className="text-[10px] font-bold bg-white/20 text-white px-2 py-0.5 rounded-full">
                  {p.tip_1x2}
                </span>
                <span className="text-[10px] text-white/50">
                  {Math.round(p.goals_confidence * 100)}%
                </span>
                {p.actual_result && <ResultBadge result={p.actual_result} />}
                {expanded === i
                  ? <ChevronUp size={10} className="text-white/40" />
                  : <ChevronDown size={10} className="text-white/40" />}
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

export function CalendarView({ onClose }: Props) {
  const now = new Date();
  const [year,  setYear]  = useState(now.getFullYear());
  const [month, setMonth] = useState(now.getMonth());  // 0-indexed
  const [summary, setSummary] = useState<Record<string, CalendarDay>>({});
  const [loadingCal, setLoadingCal] = useState(true);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);

  const monthStr = `${year}-${_pad(month + 1)}`;

  useEffect(() => {
    setLoadingCal(true);
    fetchCalendar(monthStr).then(setSummary).finally(() => setLoadingCal(false));
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

  return (
    <div
      className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4"
      onClick={e => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="w-full max-w-3xl bg-slate-900 border border-slate-700 rounded-2xl shadow-2xl overflow-hidden flex"
           style={{ height: "min(90vh, 620px)" }}>

        {/* Calendar panel */}
        <div className={clsx("flex flex-col transition-all duration-200",
          selectedDate ? "w-1/2 border-r border-slate-700" : "w-full"
        )}>
          {/* Header */}
          <div className="flex items-center justify-between px-5 py-4 border-b border-slate-700 shrink-0">
            <button onClick={prevMonth} className="p-1.5 hover:bg-slate-700 rounded-lg text-slate-400">
              <ChevronLeft size={16} />
            </button>
            <div className="text-center">
              <p className="font-bold text-white text-base">{MONTHS[month]} {year}</p>
              {!loadingCal && (
                <p className="text-[10px] text-slate-500">
                  {Object.keys(summary).length} days with predictions
                </p>
              )}
            </div>
            <div className="flex items-center gap-1">
              <button onClick={nextMonth} className="p-1.5 hover:bg-slate-700 rounded-lg text-slate-400">
                <ChevronRight size={16} />
              </button>
              <button onClick={onClose} className="p-1.5 hover:bg-slate-700 rounded-lg text-slate-400 ml-1">
                <X size={16} />
              </button>
            </div>
          </div>

          {/* Day labels */}
          <div className="grid grid-cols-7 px-3 pt-3 shrink-0">
            {DAYS.map(d => (
              <div key={d} className="text-[10px] text-slate-500 text-center font-medium pb-1">{d}</div>
            ))}
          </div>

          {/* Grid */}
          <div className="flex-1 overflow-y-auto px-3 pb-3">
            {loadingCal ? (
              <div className="flex items-center justify-center h-full text-slate-500">
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
                        "relative flex flex-col items-center justify-center rounded-xl aspect-square text-sm transition-all",
                        isSelected ? "bg-green-500/20 border border-green-500/50" :
                        isToday    ? "bg-slate-700 border border-slate-500" :
                                     "hover:bg-slate-800 border border-transparent",
                        info ? "cursor-pointer" : isPast ? "opacity-30 cursor-default" : "cursor-default"
                      )}
                      disabled={!info && isPast}
                    >
                      <span className={clsx(
                        "text-xs font-medium",
                        isToday ? "text-white" : "text-slate-300"
                      )}>
                        {day}
                      </span>
                      {info && (
                        <span className={clsx(
                          "w-1.5 h-1.5 rounded-full mt-0.5",
                          dotColor(info)
                        )} />
                      )}
                      {info && (
                        <span className="text-[9px] text-slate-500 leading-none">
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
          <div className="flex items-center justify-center gap-4 px-4 py-3 border-t border-slate-800 shrink-0">
            {[
              { color: "bg-green-500",  label: "≥60% correct" },
              { color: "bg-yellow-400", label: "40-60%"       },
              { color: "bg-red-500",    label: "<40%"         },
              { color: "bg-blue-500",   label: "Upcoming"     },
            ].map(({ color, label }) => (
              <div key={label} className="flex items-center gap-1.5">
                <span className={clsx("w-2 h-2 rounded-full shrink-0", color)} />
                <span className="text-[10px] text-slate-500">{label}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Day detail panel */}
        {selectedDate && (
          <div className="w-1/2 flex flex-col">
            <DayPanel date={selectedDate} onClose={() => setSelectedDate(null)} />
          </div>
        )}
      </div>
    </div>
  );
}
