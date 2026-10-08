"use client";

import clsx from "clsx";

// Today's matches by where they stand: in play, over, or still to play.

export type MatchStatus = "all" | "live" | "finished" | "upcoming";

export function StatusFilter({ value, onChange, counts }: {
  value: MatchStatus;
  onChange: (s: MatchStatus) => void;
  counts: { live: number; finished: number; upcoming: number };
}) {
  const opts: { key: MatchStatus; label: string; n: number }[] = [
    { key: "all", label: "All", n: counts.live + counts.finished + counts.upcoming },
    { key: "live", label: "Live", n: counts.live },
    { key: "finished", label: "Finished", n: counts.finished },
    { key: "upcoming", label: "Upcoming", n: counts.upcoming },
  ];
  return (
    <div role="group" aria-label="Show matches" className="flex gap-1.5 overflow-x-auto -mx-4 px-4 sm:mx-0 sm:px-0 pb-0.5">
      {opts.map(o => {
        const active = value === o.key;
        return (
          <button key={o.key} type="button" onClick={() => onChange(o.key)} aria-pressed={active}
            className={clsx("chip shrink-0", active ? "chip-active" : "chip-idle")}>
            {o.key === "live" && <span className={clsx("w-1.5 h-1.5 rounded-full bg-danger", o.n > 0 && "animate-pulse")} />}
            {o.label}
            <span className={clsx("tnum text-[11px] font-bold", active ? "text-canvas/60" : "text-n-500")}>{o.n}</span>
          </button>
        );
      })}
    </div>
  );
}
