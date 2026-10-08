"use client";

import clsx from "clsx";
import type { League } from "@/lib/api";
import { CompetitionBadge } from "./CompetitionBadge";

interface Props {
  leagues: League[];
  selected: string;
  onSelect: (code: string) => void;
  counts: Record<string, number>;
  /** Leagues with matches that day but not in `leagues` (name and flag from their matches) */
  extra?: League[];
}

/** Only the leagues with matches on the chosen day (counts), in the list's order. */
export function LeagueTabs({ leagues, selected, onSelect, counts, extra = [] }: Props) {
  const known = new Set(leagues.map(l => l.code));
  const playing = [...leagues, ...extra.filter(l => !known.has(l.code))].filter(l => (counts[l.code] || 0) > 0);
  const all = [{ code: "ALL", name: "All Leagues", flag: "⚽", country: "" }, ...playing];

  return (
    <div className="overflow-x-auto pb-1 -mx-4 px-4 sm:mx-0 sm:px-0">
      <div className="flex gap-1.5 min-w-max">
        {all.map((l) => {
          const count = l.code === "ALL"
            ? Object.values(counts).reduce((a, b) => a + b, 0)
            : counts[l.code] || 0;
          const active = selected === l.code;

          return (
            <button
              key={l.code}
              onClick={() => onSelect(l.code)}
              className={clsx("chip", active ? "chip-active" : "chip-idle")}
            >
              {l.code === "ALL"
                ? <span>{l.flag}</span>
                : <CompetitionBadge name={l.name} fallbackEmoji={l.flag} size={14} />}
              <span>{l.name}</span>
              {count > 0 && (
                <span
                  className={clsx(
                    "tnum text-[10px] px-1.5 py-0.5 rounded-full font-semibold",
                    active
                      ? "bg-canvas/15 text-canvas"
                      : "bg-n-800 text-n-400"
                  )}
                >
                  {count}
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
