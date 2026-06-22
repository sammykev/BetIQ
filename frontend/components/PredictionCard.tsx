"use client";

import { ConfidenceBar } from "./ConfidenceBar";
import type { Prediction } from "@/lib/api";
import { Clock, TrendingUp } from "lucide-react";
import clsx from "clsx";

interface Props {
  prediction: Prediction;
  onClick?: () => void;
}

const TIP_CODE_COLORS: Record<string, string> = {
  "1": "bg-blue-500/20 text-blue-300 border-blue-500/30",
  "2": "bg-purple-500/20 text-purple-300 border-purple-500/30",
  "X": "bg-slate-500/20 text-slate-300 border-slate-500/30",
  "1X": "bg-blue-500/15 text-blue-200 border-blue-500/25",
  "2X": "bg-purple-500/15 text-purple-200 border-purple-500/25",
  "?": "bg-slate-700/40 text-slate-400 border-slate-600/30",
};

const GOALS_TYPE_COLORS: Record<string, string> = {
  Banker: "bg-green-500/20 text-green-300 border-green-500/40",
  Asian: "bg-yellow-500/20 text-yellow-300 border-yellow-500/30",
  Skip: "bg-slate-700/30 text-slate-500 border-slate-600/20",
};

export function PredictionCard({ prediction: p, onClick }: Props) {
  const gconf = Math.round(p.goals_confidence * 100);
  const isSkip = p.tip_goals === "Skip";

  return (
    <div
      onClick={onClick}
      className={clsx(
        "bg-slate-900 border border-slate-800 rounded-xl p-4 flex flex-col gap-4 card-glow transition-all duration-200 hover:border-slate-600",
        onClick && "cursor-pointer hover:bg-slate-800/50 active:scale-[0.98]"
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-slate-400 truncate max-w-[60%]">
          {p.flag} {p.league_name}
        </span>
        <div className="flex items-center gap-1.5 text-xs text-slate-500 shrink-0">
          <Clock size={12} />
          <span>{p.date}{p.time !== "TBD" ? ` · ${p.time}` : ""}</span>
        </div>
      </div>

      {/* Teams */}
      <div className="text-center py-1">
        <p className="text-base font-bold text-slate-100 tracking-tight leading-snug">{p.home}</p>
        <p className="text-xs text-slate-500 my-1 uppercase tracking-widest font-medium">vs</p>
        <p className="text-base font-bold text-slate-100 tracking-tight leading-snug">{p.away}</p>
      </div>

      {/* 1X2 probability row */}
      <div className="grid grid-cols-3 gap-2 text-center">
        {[
          { label: "Home", value: p.p_home },
          { label: "Draw", value: p.p_draw },
          { label: "Away", value: p.p_away },
        ].map(({ label, value }) => (
          <div key={label} className="bg-slate-800 rounded-lg px-2 py-2">
            <p className="text-xs text-slate-500 mb-1">{label}</p>
            <p className="text-sm font-bold text-slate-200">{Math.round(value * 100)}%</p>
          </div>
        ))}
      </div>

      {/* Tips + confidence — always same height */}
      <div className="space-y-2.5 flex-1">
        <div className="flex items-center justify-between">
          <span className="text-xs text-slate-400">1X2 Pick</span>
          <span className={clsx("text-xs font-semibold px-2.5 py-0.5 rounded-full border", TIP_CODE_COLORS[p.tip_code] || TIP_CODE_COLORS["?"])}>
            {p.tip_1x2}
          </span>
        </div>

        <div className="flex items-center justify-between">
          <span className="text-xs text-slate-400">Goals Pick</span>
          <span className={clsx("text-xs font-semibold px-2.5 py-0.5 rounded-full border", GOALS_TYPE_COLORS[p.goals_type] || GOALS_TYPE_COLORS["Skip"])}>
            {p.tip_goals}
          </span>
        </div>

        {/* Confidence bar — always shown, dimmed when skip */}
        <div className="pt-1">
          {isSkip ? (
            <div className="space-y-1">
              <div className="flex justify-between text-xs">
                <span className="text-slate-600">Confidence</span>
                <span className="text-slate-600">—</span>
              </div>
              <div className="h-1.5 bg-slate-800 rounded-full" />
            </div>
          ) : (
            <ConfidenceBar value={p.goals_confidence} label="Confidence" />
          )}
        </div>
      </div>

      {/* Footer — always 3 columns */}
      <div className="flex gap-3 pt-1 border-t border-slate-800">
        <div className="flex-1 text-center">
          <p className="text-xs text-slate-500">Over 1.5</p>
          <p className="text-sm font-semibold text-slate-300">{Math.round(p.p_over15 * 100)}%</p>
        </div>
        <div className="w-px bg-slate-800" />
        <div className="flex-1 text-center">
          <p className="text-xs text-slate-500">Over 2.5</p>
          <p className="text-sm font-semibold text-slate-300">{Math.round(p.p_over25 * 100)}%</p>
        </div>
        <div className="w-px bg-slate-800" />
        <div className="flex-1 text-center">
          <TrendingUp size={12} className={clsx("mx-auto mb-0.5", isSkip ? "text-slate-600" : "text-green-500")} />
          <p className={clsx("text-sm font-semibold", isSkip ? "text-slate-600" : "text-green-400")}>
            {isSkip ? "—" : `${gconf}%`}
          </p>
        </div>
      </div>
    </div>
  );
}
