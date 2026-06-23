"use client";

import { useEffect, useState } from "react";
import { X, ChevronLeft, ChevronRight, Loader2, CheckCircle2, XCircle, Clock } from "lucide-react";
import clsx from "clsx";
import { fetchCalendar, fetchHistory } from "@/lib/api";
import type { CalendarDay, HistoryPrediction } from "@/lib/api";

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

  useEffect(() => {
    fetchHistory(date).then(setPreds).finally(() => setLoading(false));
  }, [date]);

  const won     = preds.filter(p => p.outcome === "won").length;
  const lost    = preds.filter(p => p.outcome === "lost").length;
  const pending = preds.filter(p => p.outcome === "pending").length;
  const settled = won + lost;
  const accuracy = settled ? Math.round((won / settled) * 100) : null;

  const fmt = (d: string) => {
    const dt = new Date(d + "T12:00:00");
    return dt.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  };

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
          <div key={i}
            className={clsx(
              "bg-slate-800 border rounded-xl px-4 py-3 space-y-1.5",
              p.outcome === "won"  ? "border-green-500/30" :
              p.outcome === "lost" ? "border-red-500/30"   : "border-slate-700"
            )}
          >
            <div className="flex items-start justify-between gap-2">
              <div className="flex-1 min-w-0">
                <p className="text-xs text-slate-400">{p.flag} {p.league_name}</p>
                <p className="text-sm font-semibold text-white leading-snug">
                  {p.home} <span className="text-slate-500 font-normal">vs</span> {p.away}
                </p>
              </div>
              <OutcomeIcon outcome={p.outcome} />
            </div>

            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-xs bg-slate-700 text-slate-200 px-2 py-0.5 rounded font-medium">
                {p.tip_1x2}
              </span>
              <span className="text-[10px] text-slate-500">
                {Math.round(p.goals_confidence * 100)}% conf
              </span>
              {p.actual_result && <ResultBadge result={p.actual_result} />}
            </div>

            {p.outcome === "won" && (
              <p className="text-[11px] text-green-400 font-semibold">✅ Prediction correct</p>
            )}
            {p.outcome === "lost" && (
              <p className="text-[11px] text-red-400 font-semibold">❌ Prediction incorrect</p>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

export function CalendarView({ onClose }: Props) {
  const now = new Date();
  const [year,  setYear]  = useState(now.getFullYear());
  const [month, setMonth] = useState(now.getMonth());  // 0-indexed
  const [summary, setSummary] = useState<Record<string, CalendarDay>>({});
  const [loadingCal, setLoadingCal] = useState(true);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);

  const monthStr = `${year}-${String(month + 1).padStart(2, "0")}`;

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

  const todayStr = now.toISOString().slice(0, 10);

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
