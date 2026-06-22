"use client";

import clsx from "clsx";
import type { League } from "@/lib/api";

interface Props {
  leagues: League[];
  selected: string;
  onSelect: (code: string) => void;
  counts: Record<string, number>;
}

export function LeagueTabs({ leagues, selected, onSelect, counts }: Props) {
  const all = [{ code: "ALL", name: "All Leagues", flag: "⚽", country: "" }, ...leagues];

  return (
    <div className="overflow-x-auto pb-1">
      <div className="flex gap-2 min-w-max">
        {all.map((l) => {
          const count = l.code === "ALL"
            ? Object.values(counts).reduce((a, b) => a + b, 0)
            : counts[l.code] || 0;
          const active = selected === l.code;

          return (
            <button
              key={l.code}
              onClick={() => onSelect(l.code)}
              className={clsx(
                "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-all whitespace-nowrap",
                active
                  ? "bg-green-500/20 text-green-300 border border-green-500/40"
                  : "bg-slate-800 text-slate-400 border border-slate-700 hover:text-slate-200 hover:border-slate-600"
              )}
            >
              <span>{l.flag}</span>
              <span>{l.name}</span>
              {count > 0 && (
                <span
                  className={clsx(
                    "text-xs px-1.5 py-0.5 rounded-full",
                    active ? "bg-green-500/30 text-green-200" : "bg-slate-700 text-slate-400"
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
