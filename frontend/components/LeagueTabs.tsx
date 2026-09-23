"use client";

import clsx from "clsx";
import type { League } from "@/lib/api";
import { CompetitionBadge } from "./CompetitionBadge";

interface Props {
  leagues: League[];
  selected: string;
  onSelect: (code: string) => void;
  counts: Record<string, number>;
}

export function LeagueTabs({ leagues, selected, onSelect, counts }: Props) {
  const all = [{ code: "ALL", name: "All Leagues", flag: "⚽", country: "" }, ...leagues];

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
                      ? "bg-ink/15 text-ink"
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
