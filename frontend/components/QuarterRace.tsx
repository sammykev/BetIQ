"use client";

import clsx from "clsx";
import { Timer } from "lucide-react";
import type { BasketballPrediction } from "@/lib/basketball";

const ROWS = [["Q1", "1st quarter"], ["Q2", "2nd quarter"], ["Q3", "3rd quarter"], ["Q4", "4th quarter"], ["EQ", "Tie"]] as const;
const pct = (v?: number) => (v == null ? "–" : `${Math.round(v * 100)}%`);

/** Our model's quarter race: expected points per quarter and each quarter's chance
 *  to be the highest or the lowest scoring one. */
export function QuarterRace({ q }: { q?: BasketballPrediction["quarters"] }) {
  if (!q) return null;
  const top = (m: Record<string, number>) => Object.entries(m).filter(([k]) => k !== "EQ").sort((a, b) => b[1] - a[1])[0]?.[0];
  const hi = top(q.highest), lo = top(q.lowest);
  return (
    <section className="card p-4">
      <h3 className="heading text-[15px] inline-flex items-center gap-2 mb-3">
        <span className="text-accent"><Timer size={16} /></span>Quarter race
      </h3>
      <div className="grid grid-cols-[1fr_auto_auto_auto] gap-x-4 gap-y-1.5 text-sm tnum">
        <span className="eyebrow">Quarter</span>
        <span className="eyebrow text-right">Pts</span>
        <span className="eyebrow text-right">Highest</span>
        <span className="eyebrow text-right">Lowest</span>
        {ROWS.map(([code, name], i) => (
          <div key={code} className="contents">
            <span className="text-n-300">{name}</span>
            <span className="text-right text-n-400">{code === "EQ" ? "" : q.expected[i]?.toFixed(1)}</span>
            <span className={clsx("text-right", code === hi ? "text-accent font-semibold" : "text-n-0")}>{pct(q.highest[code])}</span>
            <span className={clsx("text-right", code === lo ? "text-accent font-semibold" : "text-n-0")}>{pct(q.lowest[code])}</span>
          </div>
        ))}
      </div>
      <p className="text-xs text-n-500 mt-3">
        From league quarter patterns, each team&apos;s scoring trends and their meetings. SportyBet prices
        the highest quarter; the lowest quarter is our prediction only (bookable as its points over/under).
      </p>
    </section>
  );
}
