"use client";

import { useEffect, useRef } from "react";
import clsx from "clsx";
import { motion } from "motion/react";
import type { StripDay } from "@/lib/matchday";

/**
 * Flashscore-style day picker: 7 days back to 14 ahead. Past days show how
 * our tips did (won/settled), today shows live matches, future days the
 * number of matches we predict.
 */
export function DateStrip({ days, today, selected, onSelect }: {
  days: Pick<StripDay, "date" | "total" | "live" | "tip">[]; today: string; selected: string; onSelect: (date: string) => void;
}) {
  const scroller = useRef<HTMLDivElement>(null);

  // Keep the selected day in view (centred) — on load and when it changes
  useEffect(() => {
    const el = scroller.current?.querySelector<HTMLElement>(`[data-date="${selected}"]`);
    el?.scrollIntoView({ block: "nearest", inline: "center", behavior: "smooth" });
  }, [selected, days.length]);

  return (
    <div ref={scroller} role="tablist" aria-label="Match day"
      className="flex gap-1.5 overflow-x-auto -mx-4 px-4 sm:mx-0 sm:px-0 py-1 snap-x no-scrollbar">
      {days.map(d => {
        const date = new Date(`${d.date}T12:00:00`);
        const active = d.date === selected;
        const isToday = d.date === today;
        const past = d.date < today;
        const settled = d.tip[0] + d.tip[1];
        return (
          <button key={d.date} data-date={d.date} role="tab" aria-selected={active}
            onClick={() => onSelect(d.date)}
            className={clsx(
              "relative snap-center shrink-0 w-[58px] rounded-xl px-1 pt-1.5 pb-2 text-center",
              "transition-[box-shadow,opacity,scale] duration-150 ease-[cubic-bezier(0.2,0,0,1)] active:scale-[0.96]",
              active ? "text-canvas" : "bg-surface [box-shadow:var(--ring-control)] hover:[box-shadow:var(--ring-control-hover)]",
              !active && !d.total && "opacity-50",
            )}>
            {/* The chosen day's fill slides along the strip */}
            {active && (
              <motion.span layoutId="date-strip-pill" aria-hidden className="absolute inset-0 rounded-xl bg-n-0"
                transition={{ type: "spring", duration: 0.35, bounce: 0 }} />
            )}
            <span className={clsx("relative block text-[10px] font-bold uppercase tracking-[0.08em]",
              active ? "text-canvas/70" : isToday ? "text-accent" : "text-n-500")}>
              {isToday ? "Today" : date.toLocaleDateString(undefined, { weekday: "short" })}
            </span>
            <span className={clsx("relative block font-display font-extrabold text-2xl leading-none mt-0.5 tnum",
              active ? "text-canvas" : "text-n-0")}>
              {date.getDate()}
            </span>
            <span className={clsx("relative block text-[10px] font-semibold tnum mt-1 h-3 leading-3",
              active ? "text-canvas/70" : "text-n-500")}>
              {d.live > 0 ? (
                <span className={clsx("inline-flex items-center gap-1", active ? "text-canvas" : "text-danger")}>
                  <span className={clsx("w-1.5 h-1.5 rounded-full animate-pulse", active ? "bg-canvas" : "bg-danger")} />
                  {d.live} live
                </span>
              ) : past && settled > 0 ? (
                <span className={clsx(!active && (d.tip[0] / settled >= 0.5 ? "text-accent" : "text-warn"))}>
                  {d.tip[0]}/{settled}
                </span>
              ) : d.total > 0 ? `${d.total}` : "–"}
            </span>
          </button>
        );
      })}
    </div>
  );
}
