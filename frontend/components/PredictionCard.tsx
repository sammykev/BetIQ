"use client";

import { SaveButton } from "./SaveButton";
import type { Prediction } from "@/lib/api";
import { Clock, Share2, ChevronRight } from "lucide-react";
import clsx from "clsx";

const BASE = "https://predict-withbetiq.vercel.app";

function shareMatch(p: Prediction) {
  const text = `⚽ ${p.home} vs ${p.away}\n🎯 Best Pick: ${p.tip_1x2} (${Math.round(p.goals_confidence * 100)}% confidence)\n\nvia BetIQ — AI Football Predictions\n${BASE}`;
  if (navigator.share) {
    navigator.share({ title: "BetIQ Pick", text, url: BASE }).catch(() => {});
  } else {
    navigator.clipboard.writeText(text).then(() => alert("Pick copied to clipboard!")).catch(() => {});
  }
}

interface Props {
  prediction: Prediction;
  savedKeys?: Set<string>;
  onClick?: () => void;
}

const TIP_BG: Record<string, string> = {
  "1":  "from-blue-600 to-blue-500",
  "2":  "from-purple-600 to-purple-500",
  "X":  "from-slate-600 to-slate-500",
  "1X": "from-blue-700 to-blue-500",
  "2X": "from-purple-700 to-purple-500",
};

const BANKER_BADGE = "bg-green-500/20 text-green-300 border border-green-500/40";

export function PredictionCard({ prediction: p, savedKeys, onClick }: Props) {
  const emptySet = new Set<string>();
  const conf = Math.round(p.goals_confidence * 100);
  const isBanker = p.goals_type === "Banker";
  const tipBg = TIP_BG[p.tip_code] || "from-slate-600 to-slate-500";

  return (
    <div
      onClick={onClick}
      className={clsx(
        "bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl p-4 flex flex-col gap-3 card-glow transition-all duration-200 hover:border-slate-300 dark:hover:border-slate-600",
        onClick && "cursor-pointer hover:bg-slate-100/60 dark:hover:bg-slate-800/50 active:scale-[0.98]"
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-slate-500 dark:text-slate-400 truncate max-w-[55%]">
          {p.flag} {p.league_name}
        </span>
        <div className="flex items-center gap-2 shrink-0">
          <div className="flex items-center gap-1 text-xs text-slate-500">
            <Clock size={11} />
            <span>{p.date}{p.time !== "TBD" ? ` · ${p.time}` : ""}</span>
          </div>
          <SaveButton prediction={p} savedKeys={savedKeys ?? emptySet} size={13} />
          <button
            onClick={e => { e.stopPropagation(); shareMatch(p); }}
            className="text-slate-400 hover:text-blue-400 transition-colors"
            title="Share this pick"
          >
            <Share2 size={13} />
          </button>
        </div>
      </div>

      {/* Teams */}
      <div className="text-center py-1">
        <p className="text-base font-bold text-slate-900 dark:text-slate-100 tracking-tight leading-snug">{p.home}</p>
        <p className="text-xs text-slate-500 my-1 uppercase tracking-widest font-medium">vs</p>
        <p className="text-base font-bold text-slate-900 dark:text-slate-100 tracking-tight leading-snug">{p.away}</p>
      </div>

      {/* Best Pick — prominent badge */}
      <div className="flex flex-col items-center gap-2 py-2">
        <p className="text-[10px] uppercase tracking-widest text-slate-500 font-semibold">Best Pick</p>
        <div className={clsx("bg-gradient-to-r text-white font-black text-2xl px-8 py-3 rounded-2xl shadow-lg tracking-wide", tipBg)}>
          {p.tip_1x2}
        </div>
        <div className="flex items-center gap-2">
          <div className="h-1.5 w-24 bg-slate-200 dark:bg-slate-800 rounded-full overflow-hidden">
            <div
              className="h-full rounded-full bg-gradient-to-r from-green-500 to-emerald-400"
              style={{ width: `${conf}%` }}
            />
          </div>
          <span className="text-xs font-semibold text-green-400">{conf}% conf.</span>
        </div>
        {isBanker && (
          <span className={clsx("text-[10px] font-bold px-2.5 py-0.5 rounded-full", BANKER_BADGE)}>
            🔒 Banker
          </span>
        )}
      </div>

      {/* Footer stats */}
      <div className="flex gap-3 pt-2 border-t border-slate-200 dark:border-slate-800">
        <div className="flex-1 text-center">
          <p className="text-[10px] text-slate-500">Home</p>
          <p className="text-sm font-semibold text-slate-700 dark:text-slate-300">{Math.round(p.p_home * 100)}%</p>
        </div>
        <div className="w-px bg-slate-200 dark:bg-slate-800" />
        <div className="flex-1 text-center">
          <p className="text-[10px] text-slate-500">Draw</p>
          <p className="text-sm font-semibold text-slate-700 dark:text-slate-300">{Math.round(p.p_draw * 100)}%</p>
        </div>
        <div className="w-px bg-slate-200 dark:bg-slate-800" />
        <div className="flex-1 text-center">
          <p className="text-[10px] text-slate-500">Away</p>
          <p className="text-sm font-semibold text-slate-700 dark:text-slate-300">{Math.round(p.p_away * 100)}%</p>
        </div>
      </div>

      {/* Click for more info */}
      {onClick && (
        <div className="flex items-center justify-center gap-1 text-[11px] text-slate-400 dark:text-slate-600 pt-0.5">
          <span>Tap for full analysis</span>
          <ChevronRight size={11} />
        </div>
      )}
    </div>
  );
}
